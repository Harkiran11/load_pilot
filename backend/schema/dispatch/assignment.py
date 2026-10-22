from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from schema.shared.base import Base

ACTIVE_ASSIGNMENT_STATUSES = ('confirmed', 'accepted', 'in_transit', 'active')
ACTIVE_ASSIGNMENT_SQL = "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active')"


class Assignment(Base):
    lock_retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __tablename__ = 'assignments'
    __table_args__ = (
        CheckConstraint('busy_until > busy_from', name='positive_interval'),
        CheckConstraint(
            "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active', 'completed', 'cancelled')",
            name='valid_status',
        ),
        CheckConstraint(
            '(occupied_from IS NULL AND occupied_until IS NULL) OR '
            '(occupied_from IS NOT NULL AND occupied_until IS NOT NULL AND occupied_from <= busy_from AND occupied_until >= busy_until)',
            name='occupied_covers_load',
        ),
        Index('uq_assignments_active_shipment', 'shipment_id', unique=True,
              sqlite_where=text(ACTIVE_ASSIGNMENT_SQL)),
    )

    assignment_id: Mapped[UUID] = mapped_column(
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
    unit_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('fleet_units.unit_id'),
        index=True,
        nullable=False,
    )
    origin_terminal_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('terminals.terminal_id'),
        index=True,
        nullable=False,
    )
    destination_terminal_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('terminals.terminal_id'),
        index=True,
        nullable=False,
    )
    busy_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    busy_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occupied_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occupied_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignment_status: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates('busy_from', 'busy_until', 'occupied_from', 'occupied_until', 'confirmed_at', 'completed_at', 'cancelled_at', 'lock_retired_at')
    def normalize_timestamp(self, key, value):
        from service.orders.expiration import utc
        return utc(value) if value is not None else None
