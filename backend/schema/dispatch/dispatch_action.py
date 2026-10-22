from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class DispatchAction(Base):
    __tablename__ = 'dispatch_actions'
    __table_args__ = (Index(
        'uq_dispatch_actions_unresolved', 'shipment_id', unique=True,
        sqlite_where=text("request_status IN ('submitting', 'unknown')"),
        postgresql_where=text("request_status IN ('submitting', 'unknown')"),
    ),)

    action_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, nullable=False)
    shipment_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('shipments.shipment_id'),
        index=True,
        nullable=False,
    )
    evaluation_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey('evaluations.evaluation_id'),
        index=True,
        nullable=True,
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    bid_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    carrier_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_status: Mapped[str] = mapped_column(Text, nullable=False)
    board_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    board_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
