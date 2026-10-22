from fastapi import APIRouter

from controller.shared.dependencies import CleanupCount, Database, Limit, Offset
from service.analytics import dashboard
from schema.shared.api import LoadView, Page
from schema.system.history_cleanup import HistoryCleanupResult
from service.system.history_cleanup import cleanup_history
from schema.analytics.financials import ReviewRequest, ShipmentFinancialRequest, ShipmentFinancialView
from service.analytics import financials

router = APIRouter(prefix='/api', tags=['Shipments'])


@router.get('/shipments', response_model=Page[LoadView])
def shipments(session: Database, limit: Limit = 50, offset: Offset = 0):
    return dashboard.list_loads(session, limit, offset, shipments_only=True)


@router.delete('/shipments/oldest', response_model=HistoryCleanupResult)
def delete_oldest_shipments(session: Database, count: CleanupCount, dry_run: bool = False):
    return cleanup_history(session, 'shipments', count, dry_run)


@router.get('/shipments/{external_id}/financials', response_model=ShipmentFinancialView)
def shipment_financials(external_id: str, session: Database):
    return financials.get_financials(session, external_id)


@router.put('/shipments/{external_id}/financials', response_model=ShipmentFinancialView)
def record_financials(external_id: str, body: ShipmentFinancialRequest, session: Database):
    return financials.record_financials(session, external_id, body)


@router.post('/shipments/{external_id}/financials/review', response_model=ShipmentFinancialView)
def review_financials(external_id: str, body: ReviewRequest, session: Database):
    return financials.review_financials(session, external_id, body)
