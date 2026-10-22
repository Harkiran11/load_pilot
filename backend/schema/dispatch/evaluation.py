from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class Evaluation(Base):
    __tablename__ = 'evaluations'

    evaluation_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        nullable=False,
    )
    shipment_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('shipments.shipment_id'),
        index=True,
        nullable=False,
    )
    candidate_unit_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey('fleet_units.unit_id'),
        index=True,
        nullable=True,
    )
    payout: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    loaded_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    deadhead_miles: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    deadhead_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    profit_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    suggested_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    cost_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    capacity_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schedule_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    weight_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    location_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    offer_open_unexpired: Mapped[bool] = mapped_column(Boolean, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reasons: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    policy_snapshot: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    evaluated_at_sim: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
