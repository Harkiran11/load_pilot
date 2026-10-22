from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class ObservedCohort(Base):
    __tablename__ = 'observed_cohorts'
    cohort_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(Text)
    period_start_sim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end_sim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fleet_context: Mapped[str] = mapped_column(Text)
    available_unit_hours: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    exposure_evidence: Mapped[str] = mapped_column(Text)
    policy_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservedComparison(Base):
    __tablename__ = 'observed_comparisons'
    comparison_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    baseline_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('observed_cohorts.cohort_id'), index=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('observed_cohorts.cohort_id'), index=True)
    status: Mapped[str] = mapped_column(Text)
    comparability_notes: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSON)
    evidence_fingerprint: Mapped[str] = mapped_column(Text)
    created_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(Text)
    review_notes: Mapped[str | None] = mapped_column(Text)
