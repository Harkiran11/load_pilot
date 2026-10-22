from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class Terminal(Base):
    __tablename__ = 'terminals'

    terminal_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        nullable=False,
    )
    board_terminal_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    seeded_unit_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
