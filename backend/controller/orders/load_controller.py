from typing import Literal
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket

from controller.shared.dependencies import Database, Engine, Limit, Offset
from schema.shared.api import ActionRequest, ActionResult, BidRequest, LoadDetail, LoadView, Page
from schema.orders.load_stream import StreamOrderStatus, StreamOrderType
from schema.dispatch.pickup_routing import PickupRouting
from schema.dispatch.recommendation import OrderRecommendations, RecommendationsPage
from schema.dispatch.cost_preview import CostPreviewRequest
from schema.dispatch.dispatch_policy import DispatchEvaluation
from service.dispatch.dispatch_policy import dispatch_evaluation
from service.analytics import dashboard
from service.dispatch.actions import execute_action
from service.orders.load_stream import stream_loads
from service.dispatch.pickup_routing import pickup_routing
from service.dispatch.ranking import order_recommendations, recommendations
from schema.pricing.market_context import HistoricalMarketContext
from schema.pricing.bid_scenario import BidScenarioRequest, BidScenarioReport
from service.pricing.market_context import order_market_context
from service.pricing.bid_scenario import bid_scenarios

router = APIRouter(prefix='/api/loads', tags=['Loads'])
OrderType = Literal['fixed_rate', 'open_bid']
OrderStatus = Literal['open', 'accepted', 'declined', 'bid_submitted', 'bid_lost', 'in_transit', 'completed', 'expired', 'all']


@router.get('', response_model=Page[LoadView])
def loads(
    session: Database, limit: Limit = 50, offset: Offset = 0,
    order_type: OrderType | None = None, status: OrderStatus = 'open',
    origin_terminal: str | None = None, include_missing: bool = False,
):
    return dashboard.list_loads(session, limit, offset, order_type, status, origin_terminal, include_missing=include_missing)


@router.get('/recommendations', response_model=RecommendationsPage)
def ranked_recommendations(
    session: Database, limit: Limit = 50, offset: Offset = 0,
    unit_id: UUID | None = None, order_type: OrderType | None = None,
    origin_terminal: str | None = None, bookable_only: bool = False,
):
    return recommendations(session, limit, offset, unit_id, order_type, origin_terminal, bookable_only)


@router.get('/{external_id}/recommendations', response_model=OrderRecommendations)
def ranked_units(
    external_id: str, session: Database, unit_id: UUID | None = None,
    bid_rate: Decimal | None = Query(default=None, gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False),
):
    return order_recommendations(session, external_id, unit_id, bid_rate)


@router.post('/{external_id}/cost-preview', response_model=OrderRecommendations)
def cost_preview(external_id: str, body: CostPreviewRequest, session: Database):
    return order_recommendations(session, external_id, body.unit_id, body.bid_rate)


@router.post('/{external_id}/evaluate', response_model=DispatchEvaluation)
def evaluate(external_id: str, body: CostPreviewRequest, session: Database):
    return dispatch_evaluation(session, external_id, body.bid_rate, body.unit_id)


@router.websocket('/ws')
async def subscribe_loads(
    websocket: WebSocket, engine: Engine,
    order_type: StreamOrderType = 'open_bid', status: StreamOrderStatus = 'open',
    origin_terminal: str | None = None, loads: bool = True,
):
    # The engine, not a session: a WebSocket dependency lives for as long as
    # the socket is open, so a session would hold a pooled connection for the
    # whole life of the connection and starve the rest of the dispatcher.
    await stream_loads(
        websocket, engine, order_type=order_type,
        status=status, origin_terminal=origin_terminal, include_loads=loads,
    )


@router.get('/{external_id}/routing', response_model=PickupRouting)
def load_pickup_routing(external_id: str, session: Database):
    return pickup_routing(session, external_id)


@router.get('/{external_id}', response_model=LoadDetail)
def load_detail(external_id: str, session: Database):
    return dashboard.load_detail(session, external_id)


@router.post('/{external_id}/accept', response_model=ActionResult)
async def accept(external_id: str, session: Database, body: ActionRequest | None = None):
    return await execute_action(session.get_bind(), external_id, 'accept', body or ActionRequest())


@router.get('/{external_id}/market-context', response_model=HistoricalMarketContext)
def market_context(external_id: str, session: Database):
    return order_market_context(session, external_id)


@router.post('/{external_id}/bid-scenarios', response_model=BidScenarioReport)
def compare_bid_scenarios(external_id: str, body: BidScenarioRequest, session: Database):
    return bid_scenarios(session, external_id, body)


@router.post('/{external_id}/decline', response_model=ActionResult)
async def decline(external_id: str, session: Database, body: ActionRequest | None = None):
    return await execute_action(session.get_bind(), external_id, 'decline', body or ActionRequest())


@router.post('/{external_id}/bid', response_model=ActionResult)
async def bid(external_id: str, session: Database, body: BidRequest):
    return await execute_action(session.get_bind(), external_id, 'bid', body)
