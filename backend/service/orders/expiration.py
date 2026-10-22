import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from schema.orders.order_expiration import OrderExpiration
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.shared.database import get_engine

logger = logging.getLogger('uvicorn.error.expiration')
CHECK_INTERVAL_SECONDS = 1.0


class SimulationClockUnavailable(RuntimeError):
    pass


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def simulated_now(clock: SimulationClock, wall_now: datetime | None = None) -> datetime:
    """Advance the board sample by local elapsed time, scaled by simulation speed."""
    wall_now = wall_now or datetime.now(timezone.utc)
    elapsed = max(0.0, (utc(wall_now) - utc(clock.last_synced_at)).total_seconds())
    return utc(clock.sim_time_utc) + timedelta(seconds=elapsed * float(clock.simulation_speed))


def seconds_remaining(expires_at: datetime | None, sim_time: datetime) -> int | None:
    if expires_at is None:
        return None
    # Round up so an offer with a fraction of a second left does not show zero.
    return max(0, math.ceil((utc(expires_at) - utc(sim_time)).total_seconds()))


def expire_due_offers(session: Session, wall_now: datetime | None = None) -> list[str]:
    clock = session.scalar(select(SimulationClock).execution_options(populate_existing=True))
    if clock is None:
        return []
    statement = (
        update(Shipment)
        .where(
            Shipment.status == 'open', Shipment.board_missing_at.is_(None),
            Shipment.expires_at <= simulated_now(clock, wall_now),
        )
        .values(status='expired')
        .returning(Shipment.shipment_id, Shipment.external_id)
        .execution_options(synchronize_session=False)
    )
    # This is a local status derivation; last_synced_at still means a board sync.
    expired = session.execute(statement).all()
    if expired:
        from service.audit.activity import add_activity
        from service.allocation.allocation_holds import cancel_plans
        cancel_plans(session, 'orders_expired', shipment_ids=[shipment_id for shipment_id, _ in expired])
        for shipment_id, external_id in expired:
            add_activity(session, 'order.expired', f'Order {external_id}: expired',
                         shipment_id=shipment_id, details={'source': 'simulated_clock'})
    return [external_id for _, external_id in expired]


def refresh_expirations(engine: Engine) -> list[str]:
    with Session(engine) as session, session.begin():
        return expire_due_offers(session)


def get_order_expiration(session: Session, external_id: str) -> OrderExpiration:
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise LookupError('Order not found')
    clock = session.get(SimulationClock, 1)
    if clock is None:
        raise SimulationClockUnavailable('Simulation clock has not been synced yet')
    sim_time = simulated_now(clock)
    remaining = seconds_remaining(shipment.expires_at, sim_time)
    # Reads derive the current countdown even between background task ticks.
    status = shipment.status
    if status == 'open' and remaining == 0 and shipment.board_missing_at is None:
        status = 'expired'
    return OrderExpiration(
        external_id=shipment.external_id, status=status,
        expires_at=utc(shipment.expires_at) if shipment.expires_at is not None else None,
        seconds_remaining=(
            None if shipment.board_missing_at is not None else remaining if status == 'open' else 0
        ),
        board_missing_at=utc(shipment.board_missing_at) if shipment.board_missing_at is not None else None,
        sim_time_utc=sim_time,
    )


async def monitor_expirations() -> None:
    """Check local offers independently of incoming WebSocket traffic."""
    while True:
        try:
            expired = await asyncio.to_thread(refresh_expirations, get_engine())
            if expired:
                logger.info('Offers expired at simulated time: %s', expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Failed to refresh offer expirations; retrying')
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
