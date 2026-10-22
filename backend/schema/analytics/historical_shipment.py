from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class HistoricalShipment(Base):
    __tablename__ = 'historical_shipments'

    historical_shipment_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        nullable=False,
    )
    source_record_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    lane_id: Mapped[str] = mapped_column(Text, nullable=False)
    origin_state: Mapped[str] = mapped_column(Text, nullable=False)
    destination_state: Mapped[str] = mapped_column(Text, nullable=False)
    origin_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    origin_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    destination_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    destination_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    distance_miles: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    weight_lbs: Mapped[int] = mapped_column(Integer, nullable=False)
    offered_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    bid_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    won: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cost_per_mile: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    margin_estimate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    shipped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
