from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from schema.shared.base import Base


class FleetUnit(Base):
    __tablename__ = 'fleet_units'

    unit_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, nullable=False)
    board_unit_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    home_terminal_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey('terminals.terminal_id'),
        index=True,
        nullable=False,
    )
    unit_number: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    employee_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    current_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    board_missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
