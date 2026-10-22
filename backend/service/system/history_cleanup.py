from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import OperationalError

from schema.audit.activity import Activity
from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.allocation.allocation_plan import AllocationReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.system.history_cleanup import HistoryCleanupResult
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.audit.activity import add_activity
from service.dispatch.assignments import CONFIRMED_STATUSES
from service.analytics.dashboard import bid_history_statement, order_history_statement
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now


def count_rows(session, statement):
    return session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))


def inactive_history(session):
    clock = session.get(SimulationClock, 1)
    now = simulated_now(clock) if clock else None
    unresolved = select(DispatchAction.action_id).where(
        DispatchAction.shipment_id == Shipment.shipment_id,
        DispatchAction.request_status.in_(('submitting', 'unknown')),
    ).exists()
    holds = select(AssignmentReservation.action_id).join(DispatchAction).where(
        DispatchAction.shipment_id == Shipment.shipment_id,
    ).exists()
    locks = select(Assignment.assignment_id).where(
        Assignment.shipment_id == Shipment.shipment_id, Assignment.lock_retired_at.is_(None),
        or_(Assignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            (Assignment.assignment_status == 'completed') & (
                Assignment.occupied_until > now if now is not None else True
            )),
    ).exists()
    allocation_holds = select(AllocationReservation.reservation_id).where(
        AllocationReservation.shipment_id == Shipment.shipment_id,
    ).exists()
    return ~unresolved & ~holds & ~locks & ~allocation_holds


def cleanup_statements(session, resource, external_id, event_type, scope='order_history'):
    if resource == 'activity':
        visible = select(Activity)
        if external_id is not None:
            visible = visible.join(Shipment).where(Shipment.external_id == external_id)
        if event_type is not None:
            visible = visible.where(Activity.event_type == event_type)
        # This record is also the CSV checksum/import cursor; deleting it would
        # trigger a reimport and recreate activity on every restart.
        latest_import = select(Activity.event_id).where(Activity.event_type == 'history.imported').order_by(
            Activity.occurred_at_wall.desc(), Activity.event_id.desc(),
        ).limit(1).scalar_subquery()
        eligible = visible.where(or_(Activity.event_type != 'history.imported', Activity.event_id != latest_import))
        return visible, eligible.order_by(Activity.occurred_at_wall, Activity.event_id)
    if resource == 'shipments':
        visible = select(Shipment).where(
            Shipment.status.in_(CONFIRMED_STATUSES), Shipment.board_missing_at.is_(None),
            Shipment.shipment_history_deleted_at.is_(None),
        )
        eligible = visible.where(Shipment.status == 'completed', inactive_history(session))
        oldest = func.coalesce(Shipment.board_created_at, Shipment.pickup_at, Shipment.delivery_at, Shipment.last_synced_at)
    else:
        visible = order_history_statement() if scope == 'order_history' else bid_history_statement()
        latest_status = select(DispatchAction.request_status).where(
            DispatchAction.shipment_id == Shipment.shipment_id, DispatchAction.action_type == 'bid',
        ).order_by(DispatchAction.requested_at.desc(), DispatchAction.action_id.desc()).limit(1).scalar_subquery()
        eligible = visible.where(
            or_(Shipment.status.in_(('completed', 'bid_lost', 'declined', 'expired')),
                Shipment.board_retired_at.is_not(None),
                (Shipment.status == 'open') & (latest_status == 'failed')),
            inactive_history(session),
        )
        requested_at = select(func.max(DispatchAction.requested_at)).where(
            DispatchAction.shipment_id == Shipment.shipment_id, DispatchAction.action_type == 'bid',
        ).scalar_subquery()
        oldest = (
            func.coalesce(Shipment.board_created_at, Shipment.pickup_at, Shipment.delivery_at, Shipment.last_synced_at)
            if scope == 'order_history' else
            func.coalesce(requested_at, Shipment.board_created_at, Shipment.bid_decision_at, Shipment.last_synced_at)
        )
    # Unknown chronology comes last. external_id makes timestamp ties stable.
    return visible, eligible.order_by(oldest.is_(None), oldest, Shipment.external_id)


def cleanup_history(session, resource, count, dry_run=False, *, external_id=None, event_type=None, scope='order_history'):
    if resource not in ('activity', 'shipments', 'bids') or not 1 <= count <= 10000:
        raise ProductError(422, 'Choose activity, shipments, or bids and a count between 1 and 10000')
    if resource == 'bids' and scope not in ('order_history', 'submitted_bids'):
        raise ProductError(422, 'Bid cleanup scope must be order_history or submitted_bids')
    result_scope = scope if resource == 'bids' else 'shipment_history' if resource == 'shipments' else 'activity'
    try:
        with session.begin():
            if session.get_bind().dialect.name != 'sqlite':
                raise ProductError(503, 'Atomic history cleanup currently requires SQLite')
            # Serialize selection and mutation with dispatcher actions/ingestion.
            # A dry run is only a preview, not a reservation of these IDs.
            session.execute(text('BEGIN IMMEDIATE'))
            visible, eligible = cleanup_statements(session, resource, external_id, event_type, scope)
            total = count_rows(session, visible)
            eligible_count = count_rows(session, eligible)
            rows = list(session.scalars(eligible.limit(count)))
            ids = [str(row.event_id) if resource == 'activity' else row.external_id for row in rows]
            mode = 'delete_rows' if resource == 'activity' else 'hide_history'
            if not dry_run and rows:
                if resource == 'activity':
                    session.execute(delete(Activity).where(Activity.event_id.in_([row.event_id for row in rows])))
                else:
                    field = 'shipment_history_deleted_at' if resource == 'shipments' else 'bid_history_deleted_at'
                    for row in rows:
                        setattr(row, field, datetime.now(timezone.utc))
                add_activity(session, 'history.cleaned', f'Cleared {len(rows)} oldest {resource} records', details={
                    'resource': resource, 'requested_count': count, 'deleted_count': len(rows),
                    'scope': result_scope,
                    'deletion_mode': mode, 'record_ids': ids,
                    'filters': {'external_id': external_id, 'event_type': event_type},
                })
                session.flush()
            return HistoryCleanupResult(
                resource=resource, scope=result_scope, requested_count=count, matched_count=len(rows),
                deleted_count=0 if dry_run else len(rows), eligible_count_before=eligible_count,
                remaining_count=count_rows(session, visible), protected_count=total - eligible_count,
                record_ids=ids, dry_run=dry_run, deletion_mode=mode,
                message=(f'Preview: {len(rows)} {resource} records can be cleared.' if dry_run else
                         f'Cleared {len(rows)} oldest eligible {resource} records; active dispatch state is retained.'),
            )
    except OperationalError as exc:
        if (getattr(exc.orig, 'sqlite_errorcode', 0) & 255) in (5, 6):
            raise ProductError(503, 'Dispatcher database is busy; retry cleanup') from exc
        raise
