from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class SimulationClock(Base):
    __tablename__ = 'simulation_clock'
    __table_args__ = (CheckConstraint('clock_id = 1', name='singleton'),)

    clock_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wall_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sim_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulation_speed: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    note: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
