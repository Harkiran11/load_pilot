"""Compact order rows for the analytics and bid-log pages."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class AnalyticsOrder(BaseModel):
    """The order fields the analytics page aggregates over.

    The full order view carries around forty-five fields. Aggregating the
    retained history needs a dozen of them, so the page reads this projection
    instead of downloading every full order record.
    """

    external_id: str
    order_type: str
    status: str
    pickup_at: datetime | None = None
    delivery_at: datetime | None = None
    board_created_at: datetime | None = None
    origin_terminal_code: str | None = None
    origin_city: str | None = None
    destination_terminal_code: str | None = None
    destination_city: str | None = None
    estimated_profit: Decimal | None = None
    profit_category: str = 'unavailable'


class BidLogOrder(BaseModel):
    """The order fields the bid-log table sorts, filters and summarises.

    The full order record is fetched on demand when a record is opened, so
    this stays deliberately small: the list holds every retained order.
    """

    external_id: str
    order_type: str
    status: str
    origin_terminal_code: str | None = None
    destination_terminal_code: str | None = None
    offered_rate: Decimal | None = None
    pickup_at: datetime | None = None
    delivery_at: datetime | None = None
    expires_at: datetime | None = None
    board_created_at: datetime | None = None
    last_synced_at: datetime | None = None


class BidLogRow(BaseModel):
    """One bid-log record: the recorded action plus its order summary."""

    action_id: UUID | None = None
    bid_rate: Decimal | None = None
    request_status: str | None = None
    outcome: str
    decision_at: datetime | None = None
    order: BidLogOrder = Field(description='Order summary; the full record is fetched on demand.')


class BidLogQuery(BaseModel):
    """How the bid-log page wants its records.

    The page shows twenty records at a time but the board retains every
    closed order, so filtering, sorting and paging happen in the database
    instead of downloading the whole log to the browser.
    """

    status: str = 'all'
    sort: str = 'decision'
    direction: str = 'desc'
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AnalyticsDay(BaseModel):
    date: str
    profit: float = 0
    loss: float = 0
    net: float | None = None
    known: int = 0
    unknown: int = 0
    shipments: int = 0
    bids: int = 0
    opportunities: int = 0


class AnalyticsLane(BaseModel):
    route: str
    profit: float = 0
    known: int = 0
    unknown: int = 0
    shipments: int = 0


class AnalyticsSummary(BaseModel):
    """The figures the analytics page shows for one date range."""

    days: list[AnalyticsDay]
    profit: float = 0
    loss: float = 0
    net: float | None = None
    known: int = 0
    unknown: int = 0
    shipments: int = 0
    completed: int = 0
    bidCount: int = 0
    bidsPerDay: float = 0
    won: int = 0
    lost: int = 0
    pending: int = 0
    winRate: float | None = None
    pendingProfit: float | None = None
    pendingKnown: int = 0
    pendingUnknown: int = 0
    newOrders: int = 0
    fixed: int = 0
    open: int = 0
    undatedShipments: int = 0
    undatedRequests: int = 0
    unmatchedRequests: int = 0
    lanes: list[AnalyticsLane] = Field(default_factory=list)
