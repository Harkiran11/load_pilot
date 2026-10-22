from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class FleetCapacity(Base):
    __tablename__ = 'fleet_capacity'

    terminal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey('terminals.terminal_id'), primary_key=True,
    )
    board_total_units: Mapped[int | None] = mapped_column(Integer)
    board_available_units: Mapped[int | None] = mapped_column(Integer)
    board_assigned_units: Mapped[int | None] = mapped_column(Integer)
    local_total_units: Mapped[int] = mapped_column(Integer)
    local_available_units: Mapped[int] = mapped_column(Integer)
    local_assigned_units: Mapped[int] = mapped_column(Integer)
    discrepancies: Mapped[list[dict]] = mapped_column(JSON)
    as_of_sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
