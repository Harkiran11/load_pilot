from fastapi import APIRouter

from controller.shared.dependencies import CleanupCount, Database, Limit, Offset
from service.analytics import dashboard
from schema.shared.api import BidView, Page
from schema.system.history_cleanup import BidCleanupScope, HistoryCleanupResult
from service.system.history_cleanup import cleanup_history

router = APIRouter(prefix='/api', tags=['Bids'])


@router.get('/bids', response_model=Page[BidView])
def bids(session: Database, limit: Limit = 50, offset: Offset = 0):
    return dashboard.list_bids(session, limit, offset)


@router.get('/bids/logs', response_model=Page[BidView])
def bid_logs(session: Database, limit: Limit = 50, offset: Offset = 0):
    return dashboard.list_bids(session, limit, offset, include_order_history=True)


@router.delete('/bids/oldest', response_model=HistoryCleanupResult)
def delete_oldest_bids(
    session: Database, count: CleanupCount, dry_run: bool = False,
    scope: BidCleanupScope = 'order_history',
):
    return cleanup_history(session, 'bids', count, dry_run, scope=scope)
