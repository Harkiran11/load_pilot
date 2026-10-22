from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class AutopilotAttempt(Base):
    __tablename__ = 'autopilot_attempts'
    __table_args__ = (
        UniqueConstraint('run_id', 'shipment_id'),
        Index('uq_autopilot_inflight', 'run_id', unique=True,
              sqlite_where=text("status IN ('evaluating', 'ready', 'submitting')")),
    )
    attempt_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('autopilot_runs.run_id'), index=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('shipments.shipment_id'), index=True)
    external_id: Mapped[str] = mapped_column(Text)
    order_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    action_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('dispatch_actions.action_id'), unique=True)
    candidate_unit_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('fleet_units.unit_id'))
    bid_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    model_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluation_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str | None] = mapped_column(Text)
    created_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocation_plan_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('fleet_allocation_plans.plan_id'))
