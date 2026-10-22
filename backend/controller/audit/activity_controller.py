from fastapi import APIRouter

from controller.shared.dependencies import CleanupCount, Database, Limit, Offset
from service.analytics import dashboard
from schema.shared.api import ActivityView, Page
from schema.system.history_cleanup import HistoryCleanupResult
from service.system.history_cleanup import cleanup_history

router = APIRouter(prefix='/api', tags=['Activity'])


@router.get('/activity', response_model=Page[ActivityView])
def activity(
    session: Database, limit: Limit = 50, offset: Offset = 0,
    external_id: str | None = None, event_type: str | None = None,
):
    return dashboard.list_activity(session, limit, offset, external_id, event_type)


@router.delete('/activity/oldest', response_model=HistoryCleanupResult)
def delete_oldest_activity(
    session: Database, count: CleanupCount, dry_run: bool = False,
    external_id: str | None = None, event_type: str | None = None,
):
    return cleanup_history(session, 'activity', count, dry_run, external_id=external_id, event_type=event_type)
