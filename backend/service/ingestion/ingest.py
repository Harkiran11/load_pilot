import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from weakref import WeakKeyDictionary

import httpx
from pydantic import TypeAdapter
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from schema.ingestion.board_api import BoardClock, BoardFleetSnapshot, BoardOrder, BoardTerminal
from schema.shared.dispatcher import FleetUnit, Shipment, SimulationClock, Terminal
from service.shared.database import get_engine
from service.orders.expiration import expire_due_offers
from service.ingestion.reconciliation import reconcile_known_orders
from service.fleet.fleet import log_discrepancies, reconcile_fleet, validate_roster
from service.audit.activity import add_activity
from service.dispatch.assignments import reconcile_bookings
from service.orders.expiration import simulated_now
from service.ingestion.board_reset import retire_reset_state

logger = logging.getLogger(__name__)


def upsert(
    session: Session, model, key: str, values: dict, *, insert_only: bool = False,
) -> None:
    """Preserve local primary keys while updating the board-owned fields."""
    dialect = session.get_bind().dialect.name
    insert = {'sqlite': sqlite_insert, 'postgresql': postgres_insert}.get(dialect)
    if insert is None:
        raise ValueError(f'Board ingestion does not support database dialect {dialect}')
    statement = insert(model).values(**values)
    if insert_only:
        statement = statement.on_conflict_do_nothing(index_elements=[key])
    else:
        statement = statement.on_conflict_do_update(
            index_elements=[key],
            set_={name: getattr(statement.excluded, name) for name in values if name != key},
        )
    session.execute(statement)


def upsert_orders(
    session: Session, orders: list[BoardOrder], synced_at: datetime,
    *, created_only: bool = False,
) -> None:
    terminals_by_code = {
        row.code.upper(): row.terminal_id for row in session.scalars(select(Terminal))
    }
    # Read the orders we already know in one query. A per-order read turned a
    # board snapshot into hundreds of extra round trips inside the write lock.
    existing = {
        row.external_id: row for row in session.scalars(
            select(Shipment).where(Shipment.external_id.in_([order.external_id for order in orders])))
    } if orders else {}
    pending = []
    for order in orders:
        previous = existing.get(order.external_id)
        if previous is not None and previous.board_retired_at is not None:
            continue  # Late HTTP/WS payloads cannot resurrect a wiped generation.
        previous_status = previous.status if previous else None
        values = order.model_dump(
            exclude={'id', 'created_at', 'assigned_driver_id'}, exclude_unset=True,
        )
        values.update(board_order_id=order.id, last_synced_at=synced_at, board_missing_at=None)
        if previous_status != order.status and order.status in ('open', 'bid_submitted', 'accepted', 'in_transit'):
            # A genuinely active lifecycle must be visible even if prior closed
            # history was cleared. Unchanged terminal snapshots preserve cleanup.
            values.update(shipment_history_deleted_at=None, bid_history_deleted_at=None)
        if 'created_at' in order.model_fields_set:
            values['board_created_at'] = order.created_at
        if 'assigned_driver_id' in order.model_fields_set:
            values['board_assigned_unit_id'] = order.assigned_driver_id
        for side in ('origin', 'destination'):
            code = getattr(order, f'{side}_terminal_code')
            if f'{side}_terminal_code' in order.model_fields_set:
                values[f'{side}_terminal_id'] = terminals_by_code.get((code or '').upper())
        upsert(session, Shipment, 'external_id', values, insert_only=created_only)
        pending.append((order, previous_status))
    if not pending:
        return
    # One read-back for the whole batch instead of one query per order.
    refreshed = {
        row.external_id: row for row in session.scalars(
            select(Shipment).where(Shipment.external_id.in_([order.external_id for order, _ in pending]))
            .execution_options(populate_existing=True))
    }
    observed_orders = []
    for order, previous_status in pending:
        current = refreshed.get(order.external_id)
        if current is None:
            continue
        observed_orders.append(current)
        from service.audit.live_audit import observe_outcome
        observe_outcome(session, current, synced_at)
        from service.audit.prediction_capture import observe_outcome as observe_prediction_outcome
        observe_prediction_outcome(session, current, synced_at)
        if previous_status is None or previous_status != current.status:
            add_activity(
                session, 'order.ingested' if previous_status is None else 'order.status_changed',
                f'Order {current.external_id}: {current.status}', shipment_id=current.shipment_id,
                details={'previous_status': previous_status, 'status': current.status, 'source': 'board'},
            )
    if observed_orders:
        from service.autopilot.order_collection import observe_orders
        observe_orders(session, observed_orders)


def persist_orders(
    engine: Engine, orders: list[BoardOrder], clock: BoardClock | None = None,
    *, created_only: bool = False,
) -> None:
    synced_at = datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        if clock is not None:
            stored_clock = session.get(SimulationClock, 1)
            previous_wall_time = (
                stored_clock.wall_time_utc.replace(tzinfo=timezone.utc)
                if stored_clock is not None else None
            )
            if previous_wall_time is None or clock.wall_time_utc >= previous_wall_time:
                upsert(session, SimulationClock, 'clock_id', {
                    'clock_id': 1, **clock.model_dump(), 'last_synced_at': synced_at,
                })
        upsert_orders(session, orders, synced_at, created_only=created_only)
        expire_due_offers(session)
        latest_clock = session.get(SimulationClock, 1)
        if latest_clock is not None and orders:
            reconcile_bookings(
                session, simulated_now(latest_clock),
                [order.external_id for order in orders],
            )


def persist_clock(engine: Engine, clock: BoardClock, *, received_at: datetime | None = None) -> None:
    """Store one clock sample; order and booking reconciliation belongs to board snapshots."""
    synced_at = received_at or datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        stored = session.get(SimulationClock, 1)
        if stored is not None and clock.wall_time_utc < stored.wall_time_utc.replace(tzinfo=timezone.utc):
            return
        upsert(session, SimulationClock, 'clock_id', {
            'clock_id': 1, **clock.model_dump(), 'last_synced_at': synced_at,
        })


def persist_snapshot(
    engine: Engine,
    terminals: list[BoardTerminal],
    fleet: list[BoardFleetSnapshot],
    clock: BoardClock,
    orders: list[BoardOrder],
    *, clock_received_at: datetime | None = None,
    missing_order_ids: set[str] | None = None,
    full_reconcile: bool = False,
) -> None:
    validate_roster(fleet)
    synced_at = datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        # Include roster terminals in case a terminal was added between API reads.
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        stored_clock = session.get(SimulationClock, 1)
        if stored_clock is not None and clock.wall_time_utc < stored_clock.wall_time_utc.replace(tzinfo=timezone.utc):
            return  # A slower worker cannot restore an older board snapshot.
        retire_reset_state(session, clock, orders, missing_order_ids, synced_at)
        # Plans are revalidated against this snapshot's fingerprint at
        # reservation time; cancelling every plan here would starve a board
        # that refreshes more often than one allocation evaluation takes.
        for terminal in [*terminals, *(snapshot.terminal for snapshot in fleet)]:
            values = terminal.model_dump(exclude={'id', 'unit_capacity'})
            values.update(
                board_terminal_id=terminal.id,
                seeded_unit_capacity=terminal.unit_capacity,
                last_synced_at=synced_at,
            )
            upsert(session, Terminal, 'board_terminal_id', values)

        terminal_rows = session.scalars(select(Terminal)).all()
        terminals_by_board_id = {row.board_terminal_id: row.terminal_id for row in terminal_rows}

        for snapshot in fleet:
            for unit in snapshot.units:
                terminal_id = terminals_by_board_id.get(unit.home_terminal_id)
                if terminal_id is None:
                    raise ValueError(f'Fleet unit {unit.id} references an unknown terminal')
                values = unit.model_dump(exclude={'id', 'home_terminal_id'})
                values.update(
                    board_unit_id=unit.id,
                    home_terminal_id=terminal_id,
                    last_synced_at=synced_at,
                    board_missing_at=None,
                )
                upsert(session, FleetUnit, 'board_unit_id', values)

        upsert(session, SimulationClock, 'clock_id', {
            'clock_id': 1, **clock.model_dump(),
            'last_synced_at': clock_received_at or synced_at,
        })
        upsert_orders(session, orders, synced_at)
        if missing_order_ids:
            session.execute(
                update(Shipment)
                .where(Shipment.external_id.in_(missing_order_ids))
                .values(board_missing_at=func.coalesce(Shipment.board_missing_at, synced_at))
            )

        expire_due_offers(session)
        reconcile_bookings(
            session, clock.sim_time_utc,
            None if full_reconcile else [order.external_id for order in orders],
        )
        capacity = reconcile_fleet(session, fleet, clock.sim_time_utc, synced_at)
    log_discrepancies(capacity)


async def _ingest(
    external_api_url: str,
    *,
    engine: Engine | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    tracked_order_ids: set[str] | None = None,
    full_reconcile: bool = False,
) -> list[BoardOrder]:
    """Fetch and validate board state, then atomically upsert it locally."""
    clock_received_at = None
    engine = engine if engine is not None else get_engine()
    async with httpx.AsyncClient(
        base_url=external_api_url.rstrip('/') + '/', timeout=20.0, transport=transport,
    ) as client:
        async def fetch(path: str):
            nonlocal clock_received_at
            response = await client.get(path)
            response.raise_for_status()
            if path == 'simulation/clock':
                clock_received_at = datetime.now(timezone.utc)
            return response.json(parse_float=Decimal)

        terminals_data, orders_data, open_orders_data = await asyncio.gather(
            fetch('terminals'), fetch('orders?status=all'),
            fetch('orders?status=open'),
        )
        orders = TypeAdapter(list[BoardOrder]).validate_python(orders_data)
        open_orders = TypeAdapter(list[BoardOrder]).validate_python(open_orders_data)
        # A previous accelerated epoch can put old rows ahead of fresh arrivals
        # in the board's latest-200 all-status response. Discover open offers
        # independently rather than relying on that date-sorted history window.
        by_external_id = {order.external_id: order for order in orders}
        for order in open_orders:
            # The open read adds discovery, never reopens a closed row already
            # returned by the all-status read when concurrent responses differ.
            by_external_id.setdefault(order.external_id, order)
        orders = list(by_external_id.values())
        orders, missing_order_ids = await reconcile_known_orders(client, engine, orders, tracked_order_ids)
        # Order reads can advance delivery lifecycle on the board. Refresh the
        # roster afterwards so position, availability, and re-homing are current.
        fleet_data, clock_data = await asyncio.gather(
            fetch('fleet/snapshot'), fetch('simulation/clock'),
        )

    terminals = TypeAdapter(list[BoardTerminal]).validate_python(terminals_data)
    fleet = TypeAdapter(list[BoardFleetSnapshot]).validate_python(fleet_data)
    clock = BoardClock.model_validate(clock_data)
    await asyncio.to_thread(
        persist_snapshot, engine,
        terminals, fleet, clock, orders, clock_received_at=clock_received_at,
        missing_order_ids=missing_order_ids, full_reconcile=full_reconcile,
    )
    logger.info(
        'Board sync complete: %d terminals, %d units, %d orders; simulated time %s',
        len(terminals), sum(len(snapshot.units) for snapshot in fleet), len(orders),
        clock.sim_time_utc.isoformat(),
    )
    if missing_order_ids:
        logger.warning(
            '%d locally retained orders are absent from the board; history preserved',
            len(missing_order_ids),
        )
    return orders


# Periodic, WebSocket, and action-triggered snapshots must not overtake one another.
# Keep locks local to each event loop, including isolated test/application loops.
_sync_locks = WeakKeyDictionary()


async def ingest(external_api_url: str, *, engine=None, transport=None, tracked_order_ids=None,
                 full_reconcile: bool = False):
    loop = asyncio.get_running_loop()
    lock = _sync_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        return await _ingest(external_api_url, engine=engine, transport=transport,
                             tracked_order_ids=tracked_order_ids,
                             full_reconcile=full_reconcile)
