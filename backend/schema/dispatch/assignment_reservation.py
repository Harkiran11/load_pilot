from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, validates

from schema.shared.base import Base


class AssignmentReservation(Base):
    __tablename__ = 'assignment_reservations'
    __table_args__ = (CheckConstraint('busy_until > busy_from', name='positive_interval'),)

    action_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('dispatch_actions.action_id'), primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('fleet_units.unit_id'), index=True)
    busy_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    busy_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @validates('busy_from', 'busy_until', 'created_at')
    def normalize_timestamp(self, key, value):
        from service.orders.expiration import utc
        return utc(value)
