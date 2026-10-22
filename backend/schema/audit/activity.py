from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class Activity(Base):
    __tablename__ = 'activity'

    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    shipment_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('shipments.shipment_id'), index=True)
    action_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('dispatch_actions.action_id'))
    event_type: Mapped[str] = mapped_column(Text, index=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON)
    occurred_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    occurred_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
