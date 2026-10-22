"""Original manual prediction attempts survive shipment/history cleanup."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, JSON, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class PredictionObservation(Base):
    __tablename__ = 'prediction_observations'
    observation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    external_id: Mapped[str] = mapped_column(Text, index=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    executions: Mapped[list] = mapped_column(JSON, default=list)
