from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from schema.shared.base import Base


class AllocationPlan(Base):
    expires_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __tablename__ = 'fleet_allocation_plans'
    __table_args__ = (Index('uq_allocation_active_run', 'run_id', unique=True,
                           sqlite_where=text("status IN ('collecting', 'planning', 'reserved')")),)
    plan_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('autopilot_runs.run_id'), index=True)
    status: Mapped[str] = mapped_column(Text)
    snapshot_fingerprint: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AllocationReservation(Base):
    expires_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __tablename__ = 'fleet_allocation_reservations'
    reservation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('fleet_allocation_plans.plan_id'), index=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('shipments.shipment_id'), index=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('fleet_units.unit_id'), index=True)
    action_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('dispatch_actions.action_id'), unique=True)
    status: Mapped[str] = mapped_column(Text)
    busy_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    busy_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates('busy_from', 'busy_until', 'created_at_wall', 'expires_at_wall')
    def utc_timestamp(self, key, value):
        from service.orders.expiration import utc
        return utc(value) if value is not None else None
