from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from controller.shared.dependencies import Database, Limit, Offset
from schema.shared.api import Page
from schema.analytics.financials import (
    CohortComparisonRequest, CreateObservedCohort, ObservedCohortView,
    ObservedComparisonView, ReviewRequest,
)
from schema.analytics.observed_cohort import ObservedCohort
from schema.analytics.order_analytics import (
    AnalyticsOrder, AnalyticsSummary, BidLogQuery, BidLogRow,
)
from service.analytics import financials
from service.analytics import analytics_summary as analytics_summary_service
from service.analytics import order_analytics
from service.shared.errors import ProductError

router = APIRouter(prefix='/api/analytics', tags=['Observed analytics'])


def release_read(session) -> None:
    """Release the read connection before a large response is sent.

    The session dependency closes after the response has been sent, so a
    response would otherwise hold a pooled connection and a read snapshot for
    the whole transfer. That starved every other writer.
    """
    try:
        session.close()
    except Exception:
        pass


@router.get('/orders', response_model=list[AnalyticsOrder], summary='Compact retained order history')
def analytics_orders(session: Database):
    """Every retained order reduced to the fields the analytics page aggregates.

    One request instead of a paged download of the full order view, which is
    what made the analytics page take tens of seconds to open.
    """
    rows = order_analytics.analytics_orders(session)
    release_read(session)
    return rows


@router.get('/summary', response_model=AnalyticsSummary, summary='Aggregated analytics for a date range')
def analytics_summary(session: Database, start: str, end: str,
                      basis: str = 'pickup', type: str = 'all'):
    """The analytics page's figures for one date range.

    The aggregation happens in the database, so the page downloads a couple of
    kilobytes of totals instead of the entire retained order history.
    """
    if basis not in ('pickup', 'delivery'):
        raise ProductError(422, 'Choose a shipment date basis of pickup or delivery.')
    if type not in ('all', 'fixed_rate', 'open_bid'):
        raise ProductError(422, 'Choose an order type of all, fixed_rate or open_bid.')
    try:
        summary = analytics_summary_service.build_summary(session, start, end, basis, type)
    except ValueError as exc:
        raise ProductError(422, 'Choose a valid start and end date.') from exc
    release_read(session)
    return summary


@router.get('/bid-logs', response_model=Page[BidLogRow], summary='Compact bid-log records')
def bid_logs(session: Database, status: str = 'all', sort: str = 'decision',
             direction: str = 'desc', limit: int = 20, offset: int = 0):
    """One page of bid-log records, filtered and ordered in the database.

    The page renders twenty records at a time, so the whole retained log no
    longer has to travel to the browser to be sorted there.
    """
    query = BidLogQuery(status=status, sort=sort, direction=direction,
                         limit=max(1, min(limit, 200)), offset=max(0, offset))
    items, total = order_analytics.paged_bid_logs(session, query)
    release_read(session)
    return Page(items=items, total=total, limit=query.limit, offset=query.offset)


@router.post('/cohorts', response_model=ObservedCohortView)
def create_cohort(body: CreateObservedCohort, session: Database):
    return financials.create_cohort(session, body)


@router.get('/cohorts', response_model=Page[ObservedCohortView])
def cohorts(session: Database, limit: Limit = 50, offset: Offset = 0):
    rows = session.scalars(select(ObservedCohort).order_by(ObservedCohort.created_at_wall.desc()).limit(limit).offset(offset)).all()
    return Page(items=[ObservedCohortView.model_validate(row) for row in rows],
                total=session.scalar(select(func.count()).select_from(ObservedCohort)), limit=limit, offset=offset)


@router.get('/cohorts/{cohort_id}', response_model=ObservedCohortView)
def cohort(cohort_id: UUID, session: Database):
    return ObservedCohortView.model_validate(financials.get_cohort(session, cohort_id))


@router.post('/cohort-comparisons', response_model=ObservedComparisonView)
def create_comparison(body: CohortComparisonRequest, session: Database):
    return financials.create_comparison(session, body)


@router.get('/cohort-comparisons/{comparison_id}', response_model=ObservedComparisonView)
def comparison(comparison_id: UUID, session: Database):
    return financials.comparison_view(session, financials.get_comparison(session, comparison_id))


@router.post('/cohort-comparisons/{comparison_id}/review', response_model=ObservedComparisonView)
def review_comparison(comparison_id: UUID, body: ReviewRequest, session: Database):
    return financials.review_comparison(session, comparison_id, body)
