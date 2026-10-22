from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, JSON, Numeric, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class AutopilotRun(Base):
    __tablename__ = 'autopilot_runs'
    __table_args__ = (
        CheckConstraint("status IN ('running', 'stopped')", name='valid_status'),
        CheckConstraint("mode IN ('shadow', 'live')", name='valid_mode'),
        CheckConstraint('orders_reserved >= 0', name='nonnegative_orders'),
        Index('uq_autopilot_running', 'status', unique=True, sqlite_where=text("status = 'running'")),
    )
    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON)
    started_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_clock_sim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_clock_synced_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at_wall: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    last_attempt_at_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orders_reserved: Mapped[int] = mapped_column(Integer, default=0)
    estimated_loss_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    pending_estimated_loss_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
