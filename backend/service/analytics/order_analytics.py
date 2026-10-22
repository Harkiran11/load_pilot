"""Compact order history for the analytics and bid-log pages.

Both pages need the whole retained history, but only a handful of fields per
order. Reading the full order view meant a browser download of tens of
megabytes, spread over dozens of paged requests, plus one extra query per
order for the booking evaluation. These projections answer both pages with a
single query and a fraction of the payload.
"""
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from schema.analytics.order_analytics import AnalyticsOrder, BidLogOrder, BidLogRow
from schema.dispatch.dispatch_action import DispatchAction
from schema.dispatch.evaluation import Evaluation
from schema.orders.shipment import Shipment
from service.orders.expiration import utc

CONFIRMED_STATUSES = ('accepted', 'in_transit', 'completed')


def _ranked_evaluations():
    """The booking evaluation that applies to each shipment, if any.

    Mirrors ``service.analytics.dashboard.booking_evaluation``: the most recent
    successful, eligible action for the order type. The window function keeps
    that rule without a query per order.
    """
    return (
        select(
            Shipment.shipment_id.label('shipment_id'),
            Evaluation.profit_usd.label('profit_usd'),
            func.row_number().over(
                partition_by=Shipment.shipment_id,
                order_by=(DispatchAction.requested_at.desc(), DispatchAction.action_id.desc()),
            ).label('rank'),
        )
        .join(DispatchAction, DispatchAction.shipment_id == Shipment.shipment_id)
        .join(Evaluation, Evaluation.evaluation_id == DispatchAction.evaluation_id)
        .where(
            DispatchAction.request_status == 'succeeded',
            Evaluation.eligible.is_(True),
            or_(
                and_(Shipment.order_type == 'open_bid', DispatchAction.action_type == 'bid'),
                and_(Shipment.order_type == 'fixed_rate', DispatchAction.action_type == 'accept'),
            ),
        )
        .subquery()
    )


def _profit_category(row, profit_usd):
    """The same rules the full order view applies to a booking evaluation."""
    if row.board_missing_at is not None:
        return None, 'unavailable'
    if row.status not in (*CONFIRMED_STATUSES, 'bid_submitted'):
        return None, 'unavailable'
    if row.status in CONFIRMED_STATUSES and row.board_assigned_unit_id is None:
        return None, 'unavailable'
    if profit_usd is None:
        return None, 'unavailable'
    return profit_usd, 'conditional' if row.status == 'bid_submitted' else 'confirmed'


def analytics_orders(session: Session) -> list[AnalyticsOrder]:
    """Every retained order, reduced to the fields the analytics page needs."""
    ranked = _ranked_evaluations()
    rows = session.execute(
        select(
            Shipment.external_id, Shipment.order_type, Shipment.status,
            Shipment.pickup_at, Shipment.delivery_at, Shipment.board_created_at,
            Shipment.origin_terminal_code, Shipment.origin_city,
            Shipment.destination_terminal_code, Shipment.destination_city,
            Shipment.board_missing_at, Shipment.board_assigned_unit_id,
            ranked.c.profit_usd,
        ).outerjoin(
            ranked, and_(ranked.c.shipment_id == Shipment.shipment_id, ranked.c.rank == 1),
        )
    ).all()
    orders: list[AnalyticsOrder] = []
    for row in rows:
        profit, category = _profit_category(row, row.profit_usd)
        orders.append(AnalyticsOrder(
            external_id=row.external_id, order_type=row.order_type, status=row.status,
            pickup_at=row.pickup_at, delivery_at=row.delivery_at, board_created_at=row.board_created_at,
            origin_terminal_code=row.origin_terminal_code, origin_city=row.origin_city,
            destination_terminal_code=row.destination_terminal_code, destination_city=row.destination_city,
            estimated_profit=profit, profit_category=category,
        ))
    return orders


def _bid_actions():
    """The most recent bid action for each shipment."""
    return (
        select(
            DispatchAction.shipment_id.label('shipment_id'),
            DispatchAction.action_id.label('action_id'),
            DispatchAction.bid_rate.label('bid_rate'),
            DispatchAction.request_status.label('request_status'),
            func.row_number().over(
                partition_by=DispatchAction.shipment_id,
                order_by=(DispatchAction.requested_at.desc(), DispatchAction.action_id.desc()),
            ).label('rank'),
        )
        .where(DispatchAction.action_type == 'bid')
        .subquery()
    )


def _outcome(row, action_id, request_status):
    """The bid outcome, using the same rules as the bid-log list view."""
    if row.order_type == 'fixed_rate':
        return 'not_applicable'
    if action_id is None and row.winning_bid_rate is None and row.status not in ('bid_submitted', 'bid_lost'):
        return 'not_recorded'
    if row.board_missing_at is not None:
        return 'unknown'
    if row.status == 'bid_submitted':
        return 'pending'
    if row.status == 'bid_lost':
        return 'lost'
    if row.status in CONFIRMED_STATUSES:
        return 'won'
    if action_id is not None and request_status == 'failed':
        return 'rejected'
    return 'unknown'


def bid_logs(session: Session) -> list[BidLogRow]:
    """Every non-open order, reduced to the fields the bid-log page needs."""
    return [_bid_log_row(row) for row in _bid_log_rows(session, None)]


def _bid_log_rows(session: Session, query):
    """The bid-log rows, optionally filtered and ordered by the page's query."""
    actions = _bid_actions()
    statement = select(
        Shipment.external_id, Shipment.order_type, Shipment.status,
        Shipment.origin_terminal_code, Shipment.destination_terminal_code,
        Shipment.offered_rate,
        Shipment.pickup_at, Shipment.delivery_at, Shipment.expires_at,
        Shipment.board_created_at, Shipment.last_synced_at,
        Shipment.board_missing_at, Shipment.bid_decision_at, Shipment.winning_bid_rate,
        actions.c.action_id, actions.c.bid_rate, actions.c.request_status,
    ).outerjoin(
        actions, and_(actions.c.shipment_id == Shipment.shipment_id, actions.c.rank == 1),
    ).where(
        Shipment.order_type.in_(('fixed_rate', 'open_bid')),
        Shipment.status != 'open', Shipment.bid_history_deleted_at.is_(None),
    )
    if query is not None:
        statement = _apply_bid_log_filter(statement, query)
    return session.execute(statement).all()


def _bid_log_outcome(row):
    return _outcome(row, row.action_id, row.request_status)


def _apply_bid_log_filter(statement, query):
    """Apply the page's status filter to the bid-log statement.

    The rules mirror the dispatcher-facing filters: a record matches when its
    recorded outcome or its order status carries the requested state.
    """
    status = (query.status or 'all').lower()
    if status == 'all':
        return statement
    if status == 'rejected':
        return statement.where(or_(
            Shipment.status == 'rejected',
            and_(Shipment.order_type == 'open_bid', Shipment.status == 'bid_lost'),
        ))
    if status == 'pending':
        return statement.where(Shipment.status == 'bid_submitted')
    if status == 'lost':
        return statement.where(Shipment.status == 'bid_lost')
    if status == 'unknown':
        # "Unknown outcome" means the order left the board before it resolved.
        return statement.where(and_(
            Shipment.order_type == 'open_bid',
            Shipment.board_missing_at.is_not(None),
            Shipment.status.notin_(('bid_submitted', 'bid_lost')),
        ))
    return statement.where(Shipment.status == status)


def _bid_log_outcome_expression(actions):
    """The recorded outcome as a SQL expression, so it can be sorted on.

    Mirrors {@link _outcome} exactly.
    """
    return case(
        (Shipment.order_type == 'fixed_rate', 'not_applicable'),
        (and_(
            actions.c.action_id.is_(None),
            Shipment.winning_bid_rate.is_(None),
            Shipment.status.notin_(('bid_submitted', 'bid_lost')),
        ), 'not_recorded'),
        (Shipment.board_missing_at.is_not(None), 'unknown'),
        (Shipment.status == 'bid_submitted', 'pending'),
        (Shipment.status == 'bid_lost', 'lost'),
        (Shipment.status.in_(CONFIRMED_STATUSES), 'won'),
        (actions.c.request_status == 'failed', 'rejected'),
        else_='unknown',
    )


def _bid_log_sort_key(query, actions):
    """The column the page sorts by."""
    field = (query.sort or 'decision').lower()
    return {
        'order': Shipment.external_id,
        'type': Shipment.order_type,
        'status': Shipment.status,
        'route': func.coalesce(Shipment.origin_terminal_code, '') + ' ' + func.coalesce(Shipment.destination_terminal_code, ''),
        'amount': Shipment.offered_rate,
        'outcome': _bid_log_outcome_expression(actions),
        'request': actions.c.request_status,
        'decision': Shipment.bid_decision_at,
        'created': Shipment.board_created_at,
        'synced': Shipment.last_synced_at,
        'pickup': Shipment.pickup_at,
        'delivery': Shipment.delivery_at,
        'expires': Shipment.expires_at,
    }.get(field, Shipment.bid_decision_at)


def _bid_log_row(row) -> BidLogRow:
    return BidLogRow(
        action_id=row.action_id, bid_rate=row.bid_rate, request_status=row.request_status,
        outcome=_bid_log_outcome(row),
        decision_at=utc(row.bid_decision_at) if row.bid_decision_at else None,
        order=BidLogOrder(
            external_id=row.external_id, order_type=row.order_type, status=row.status,
            origin_terminal_code=row.origin_terminal_code,
            destination_terminal_code=row.destination_terminal_code,
            offered_rate=row.offered_rate,
            pickup_at=row.pickup_at, delivery_at=row.delivery_at, expires_at=row.expires_at,
            board_created_at=row.board_created_at, last_synced_at=row.last_synced_at,
        ),
    )


def paged_bid_logs(session: Session, query) -> tuple[list[BidLogRow], int]:
    """One page of bid-log records plus the total number of matches.

    The page renders twenty records at a time, so filtering, ordering and
    paging belong in the database. Downloading the whole log to sort it in the
    browser was what made the page unusable.
    """
    actions = _bid_actions()
    base = select(
        Shipment.external_id, Shipment.order_type, Shipment.status,
        Shipment.origin_terminal_code, Shipment.destination_terminal_code,
        Shipment.offered_rate,
        Shipment.pickup_at, Shipment.delivery_at, Shipment.expires_at,
        Shipment.board_created_at, Shipment.last_synced_at,
        Shipment.board_missing_at, Shipment.bid_decision_at, Shipment.winning_bid_rate,
        actions.c.action_id, actions.c.bid_rate, actions.c.request_status,
    ).outerjoin(
        actions, and_(actions.c.shipment_id == Shipment.shipment_id, actions.c.rank == 1),
    ).where(
        Shipment.order_type.in_(('fixed_rate', 'open_bid')),
        Shipment.status != 'open', Shipment.bid_history_deleted_at.is_(None),
    )
    filtered = _apply_bid_log_filter(base, query)
    total = session.scalar(
        select(func.count()).select_from(filtered.subquery())
    ) or 0
    column = _bid_log_sort_key(query, actions)
    direction = 'asc' if (query.direction or 'desc').lower() == 'asc' else 'desc'
    # Unknown values stay at the end in either direction, as the list view does.
    ordering = [
        column.is_(None).asc(),
        column.asc() if direction == 'asc' else column.desc(),
        Shipment.external_id.asc(),
    ]
    rows = session.execute(
        filtered.order_by(*ordering).limit(query.limit).offset(query.offset)
    ).all()
    return [_bid_log_row(row) for row in rows], total
