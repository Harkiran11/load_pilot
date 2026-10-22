from datetime import datetime, timezone

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from schema.audit.activity import Activity
from schema.simulation.simulation_clock import SimulationClock


def add_activity(
    session: Session, event_type: str, message: str, *, shipment_id=None,
    action_id=None, details: dict | None = None,
) -> None:
    from service.orders.expiration import simulated_now
    clock = session.scalar(select(SimulationClock).execution_options(populate_existing=True))
    session.add(Activity(
        shipment_id=shipment_id, action_id=action_id, event_type=event_type,
        message=message, details=details or {}, occurred_at_wall=datetime.now(timezone.utc),
        occurred_at_sim=simulated_now(clock) if clock is not None else None,
    ))


def record_system_event(engine: Engine, event_type: str, message: str) -> None:
    with Session(engine) as session, session.begin():
        add_activity(session, event_type, message)
