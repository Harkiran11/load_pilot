from typing import Literal

from pydantic import BaseModel

BidCleanupScope = Literal['order_history', 'submitted_bids']


class HistoryCleanupResult(BaseModel):
    resource: Literal['activity', 'shipments', 'bids']
    scope: Literal['activity', 'shipment_history', 'order_history', 'submitted_bids']
    requested_count: int
    matched_count: int
    deleted_count: int
    eligible_count_before: int
    remaining_count: int
    protected_count: int
    record_ids: list[str]
    dry_run: bool
    deletion_mode: Literal['delete_rows', 'hide_history']
    message: str
