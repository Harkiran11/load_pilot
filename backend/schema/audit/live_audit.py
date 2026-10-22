"""Durable captures; source observations are never reconstructed from later state."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class LiveBatchAudit(Base):
    __tablename__ = 'live_batch_audits'
    audit_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('fleet_allocation_plans.plan_id'), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('autopilot_runs.run_id'), index=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    request: Mapped[dict] = mapped_column(JSON)
    operational_snapshot: Mapped[dict] = mapped_column(JSON)
    requested_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)


class LiveSubmissionAudit(Base):
    __tablename__ = 'live_submission_audits'
    submission_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_audit_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('live_batch_audits.audit_id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('autopilot_attempts.attempt_id'), index=True)
    action_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('dispatch_actions.action_id'), unique=True)
    decision_id: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('fleet_units.unit_id'))
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    attempted_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    submitted_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revalidated_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_passed: Mapped[bool | None] = mapped_column(Boolean)
    operational_checks_passed: Mapped[bool | None] = mapped_column(Boolean)
    atomic_reservation_acquired: Mapped[bool | None] = mapped_column(Boolean)
    rate_limit_passed: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, default='unknown')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
