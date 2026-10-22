from fastapi import APIRouter

from controller.shared.dependencies import Database
from service.analytics import dashboard
from schema.shared.api import SystemStatus
from schema.dispatch.dispatch_policy import DispatchPolicy
from service.dispatch.dispatch_policy import dispatch_policy
from schema.analytics.recommendation_comparison import RecommendationComparison, RecommendationComparisonRequest
from service.analytics.recommendation_comparison import compare_recommendations
from schema.system.records import RecordPage, RecordTableSummary
from service.shared.errors import ProductError
from service.system import record_browser

router = APIRouter(prefix='/api/system', tags=['System'])


@router.get('/status', response_model=SystemStatus)
def system_status(session: Database):
    return dashboard.system_status(session)


@router.get('/records', response_model=list[RecordTableSummary], summary='The tables we own')
def record_tables(session: Database):
    """Every table in our own database, with its current row count.

    The load board is the external world; this is the state we keep about it.
    """
    return record_browser.available_tables(session)


@router.get('/records/{table}', response_model=RecordPage, summary='Rows from one of our tables')
def record_rows(table: str, session: Database, limit: int = 25, offset: int = 0):
    """One page of rows from a table we own."""
    try:
        return record_browser.table_rows(session, table, max(1, min(limit, 200)), max(0, offset))
    except KeyError as exc:
        raise ProductError(404, 'No such table in the dispatcher database.') from exc


@router.get('/policy', response_model=DispatchPolicy)
def current_dispatch_policy():
    return dispatch_policy()


@router.post('/recommendation-comparison', response_model=RecommendationComparison)
def recommendation_comparison(body: RecommendationComparisonRequest):
    return compare_recommendations(body)
