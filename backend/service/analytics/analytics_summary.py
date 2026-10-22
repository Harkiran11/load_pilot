"""Server-side aggregation for the analytics page.

The page needs the whole retained order history to build its charts, but it
only needs a dozen fields per order and an aggregate in the end. Aggregating in
the database turns a multi-megabyte download into a single small response, and
keeps the browser from re-deriving the same numbers on every render.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schema.audit.activity import Activity
from schema.orders.shipment import Shipment
from service.analytics.order_analytics import _ranked_evaluations

CONFIRMED_STATUSES = ('accepted', 'in_transit', 'completed')


def _day(value) -> str | None:
    """The UTC calendar day of a timestamp."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


def _number(value):
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def _days(start: str, end: str) -> list[str]:
    days = []
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _order_rows(session: Session):
    """Every retained order with the fields the aggregation needs."""
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
            ranked, (ranked.c.shipment_id == Shipment.shipment_id) & (ranked.c.rank == 1),
        )
    ).all()
    orders = []
    for row in rows:
        profit, category = _profit_category(row, row.profit_usd)
        orders.append({
            'external_id': row.external_id, 'order_type': row.order_type, 'status': row.status,
            'pickup_at': row.pickup_at, 'delivery_at': row.delivery_at,
            'board_created_at': row.board_created_at,
            'origin_terminal_code': row.origin_terminal_code, 'origin_city': row.origin_city,
            'destination_terminal_code': row.destination_terminal_code, 'destination_city': row.destination_city,
            'estimated_profit': profit, 'profit_category': category,
        })
    return orders


def _profit_category(row, profit_usd):
    if row.board_missing_at is not None:
        return None, 'unavailable'
    if row.status not in (*CONFIRMED_STATUSES, 'bid_submitted'):
        return None, 'unavailable'
    if row.status in CONFIRMED_STATUSES and row.board_assigned_unit_id is None:
        return None, 'unavailable'
    if profit_usd is None:
        return None, 'unavailable'
    return profit_usd, 'conditional' if row.status == 'bid_submitted' else 'confirmed'


def _request_rows(session: Session):
    """The recorded bid requests, with the order type they belong to."""
    rows = session.execute(
        select(
            Activity.event_id, Activity.action_id, Activity.message,
            Activity.occurred_at_sim, Shipment.external_id, Shipment.order_type,
        ).outerjoin(Shipment, Shipment.shipment_id == Activity.shipment_id).where(
            Activity.event_type == 'action.requested',
        )
    ).all()
    return [{'event_id': row.event_id, 'action_id': row.action_id, 'message': row.message,
             'occurred_at_sim': row.occurred_at_sim, 'external_id': row.external_id,
             'order_type': row.order_type} for row in rows]


def build_summary(session: Session, start: str, end: str, basis: str, order_type: str) -> dict:
    """The analytics page's figures for one date range.

    Mirrors the page's own aggregation exactly, so the numbers on screen do
    not change; only the place the work happens does.
    """
    days = _days(start, end)
    by_day = {day: {'date': day, 'profit': 0.0, 'loss': 0.0, 'net': None, 'known': 0, 'unknown': 0,
                     'shipments': 0, 'bids': 0, 'opportunities': 0} for day in days}
    orders = {row['external_id']: row for row in _order_rows(session)}
    filtered = [row for row in orders.values() if order_type == 'all' or row['order_type'] == order_type]

    def date_of(order):
        return _day(order['pickup_at'] if basis == 'pickup' else order['delivery_at'])

    cohort = [row for row in filtered if date_of(row) in by_day]
    confirmed = [row for row in cohort if row['status'] in CONFIRMED_STATUSES]
    undated_shipments = 0
    undated_requests = 0
    unmatched_requests = 0
    pending_profit = Decimal('0')
    pending_unknown = 0
    pending_known = 0
    lanes: dict[str, dict] = {}

    for row in filtered:
        created = by_day.get(_day(row['board_created_at']) or '')
        if created is not None:
            created['opportunities'] += 1
        if row['status'] in CONFIRMED_STATUSES and not date_of(row):
            undated_shipments += 1

    for row in confirmed:
        day = by_day[date_of(row)]
        profit = _number(row['estimated_profit']) if row['profit_category'] == 'confirmed' else None
        day['shipments'] += 1
        route = '{} → {}'.format(
            row['origin_terminal_code'] or row['origin_city'] or 'Unknown',
            row['destination_terminal_code'] or row['destination_city'] or 'Unknown',
        )
        lane = lanes.setdefault(route, {'route': route, 'profit': 0.0, 'known': 0, 'unknown': 0, 'shipments': 0})
        lane['shipments'] += 1
        if profit is None:
            day['unknown'] += 1
            lane['unknown'] += 1
        else:
            value = float(profit)
            day['known'] += 1
            lane['known'] += 1
            lane['profit'] += value
            if value >= 0:
                day['profit'] += value
            else:
                day['loss'] += value

    for row in cohort:
        if row['status'] != 'bid_submitted':
            continue
        profit = _number(row['estimated_profit']) if row['profit_category'] == 'conditional' else None
        if profit is None:
            pending_unknown += 1
        else:
            pending_known += 1
            pending_profit += profit

    seen = set()
    for request in _request_rows(session):
        if (request['message'] or '').strip().lower() != 'bid requested':
            continue
        identity = request['action_id'] or request['event_id']
        if identity in seen:
            continue
        seen.add(identity)
        if order_type == 'fixed_rate':
            continue
        order = orders.get(request['external_id']) if request['external_id'] else None
        if order_type != 'all' and (order is None or order['order_type'] != order_type):
            if order is None:
                unmatched_requests += 1
            continue
        day_key = _day(request['occurred_at_sim'])
        if day_key is None:
            undated_requests += 1
            continue
        day = by_day.get(day_key)
        if day is not None:
            day['bids'] += 1

    for day in by_day.values():
        day['net'] = day['profit'] + day['loss'] if (day['known'] or not day['unknown']) else None

    profit = sum(day['profit'] for day in by_day.values())
    loss = sum(day['loss'] for day in by_day.values())
    known = sum(day['known'] for day in by_day.values())
    unknown = sum(day['unknown'] for day in by_day.values())
    bid_count = sum(day['bids'] for day in by_day.values())
    open_bids = [row for row in cohort if row['order_type'] == 'open_bid']
    won = len([row for row in open_bids if row['status'] in CONFIRMED_STATUSES])
    lost = len([row for row in open_bids if row['status'] == 'bid_lost'])
    pending = len([row for row in open_bids if row['status'] == 'bid_submitted'])
    completed = len([row for row in confirmed if row['status'] == 'completed'])
    ranked_lanes = sorted(
        lanes.values(),
        key=lambda lane: (0 if lane['known'] > 0 else 1, -lane['profit'], lane['route']),
    )[:5]
    return {
        'days': [by_day[day] for day in days],
        'profit': profit, 'loss': loss, 'net': profit + loss if (known or not unknown) else None,
        'known': known, 'unknown': unknown,
        'shipments': len(confirmed), 'completed': completed,
        'bidCount': bid_count, 'bidsPerDay': bid_count / len(days) if days else 0,
        'won': won, 'lost': lost, 'pending': pending,
        'winRate': (won / (won + lost) * 100) if (won + lost) else None,
        'pendingProfit': float(pending_profit) if (pending_known or not pending_unknown) else None,
        'pendingKnown': pending_known, 'pendingUnknown': pending_unknown,
        'newOrders': sum(day['opportunities'] for day in by_day.values()),
        'fixed': len([row for row in cohort if row['order_type'] == 'fixed_rate']),
        'open': len(open_bids),
        'undatedShipments': undated_shipments, 'undatedRequests': undated_requests,
        'unmatchedRequests': unmatched_requests,
        'lanes': ranked_lanes,
    }
