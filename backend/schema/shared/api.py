from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema.fleet.fleet_capacity_response import FleetCapacityResponse
from schema.shared.money import PositiveBid

T = TypeVar('T')


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator('*')
    @classmethod
    def utc_dates(cls, value):
        from service.orders.expiration import utc
        return utc(value) if isinstance(value, datetime) else value


class AssignmentView(View):
    lock_retired_at: datetime | None = None
    assignment_id: UUID
    shipment_id: UUID
    unit_id: UUID
    origin_terminal_id: UUID
    destination_terminal_id: UUID
    busy_from: datetime
    busy_until: datetime
    occupied_from: datetime | None = None
    occupied_until: datetime | None = None
    assignment_status: str
    confirmed_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None = None

class ActionView(View):
    action_id: UUID
    shipment_id: UUID
    evaluation_id: UUID | None
    action_type: str
    bid_rate: Decimal | None
    carrier_note: str | None
    request_status: str
    board_http_status: int | None
    board_message: str | None
    requested_at: datetime
    resolved_at: datetime | None

class EvaluationView(View):
    evaluation_id: UUID
    shipment_id: UUID
    candidate_unit_id: UUID | None
    payout: Decimal | None
    loaded_cost: Decimal | None
    deadhead_miles: Decimal | None
    deadhead_cost: Decimal | None
    total_cost: Decimal | None
    profit_usd: Decimal | None
    margin_pct: Decimal | None
    suggested_bid: Decimal | None
    cost_source: str | None
    pricing_passed: bool
    capacity_passed: bool
    schedule_passed: bool
    weight_passed: bool
    location_passed: bool
    offer_open_unexpired: bool
    eligible: bool
    rejection_reasons: dict | list
    policy_snapshot: dict | list
    evaluated_at_sim: datetime
    evaluated_at_wall: datetime

class LoadView(View):
    shipment_history_deleted_at: datetime | None = None
    bid_history_deleted_at: datetime | None = None
    board_retired_at: datetime | None = None
    shipment_id: UUID
    external_id: str
    board_order_id: int | None
    order_type: str
    status: str
    origin_terminal_code: str | None
    destination_terminal_code: str | None
    origin_terminal_id: UUID | None
    destination_terminal_id: UUID | None
    origin_city: str | None
    origin_state: str | None
    origin_latitude: Decimal | None
    origin_longitude: Decimal | None
    destination_city: str | None
    destination_state: str | None
    destination_latitude: Decimal | None
    destination_longitude: Decimal | None
    distance_miles: Decimal | None
    weight_lbs: int | None
    offered_rate: Decimal | None
    cost_per_mile: Decimal | None
    estimated_cost: Decimal | None
    estimated_deadhead_miles: Decimal | None
    estimated_deadhead_cost: Decimal | None
    estimated_total_cost: Decimal | None
    pickup_at: datetime | None
    delivery_at: datetime | None
    expires_at: datetime | None
    board_created_at: datetime | None
    bid_decision_at: datetime | None
    winning_bid_rate: Decimal | None
    board_assigned_unit_id: int | None
    assigned_terminal_code: str | None
    last_synced_at: datetime | None
    board_missing_at: datetime | None
    seconds_remaining: int | None = None
    estimated_profit: Decimal | None = Field(default=None, description='Persisted economics linked to a successful local booking or pending bid; not realized profit.')
    profit_category: str = 'unavailable'
    profit_source: str = 'unavailable'
    profit_evaluation_id: UUID | None = None
    profit_unit_id: UUID | None = None
    profit_unit_matches_assignment: bool | None = None
    board_estimated_profit: Decimal | None = Field(default=None, description='Separate board-cost estimate; not selected-unit calculator profit or realized profit.')
    board_estimated_profit_category: str = 'unavailable'

class UnitView(View):
    unit_id: UUID
    board_unit_id: int
    home_terminal_id: UUID
    unit_number: str
    employee_code: str
    first_name: str
    last_name: str
    status: str
    current_latitude: Decimal | None
    current_longitude: Decimal | None
    available_at: datetime | None
    last_synced_at: datetime | None
    board_missing_at: datetime | None
    terminal_code: str | None = None
    current_terminal_id: UUID | None = None
    current_terminal_code: str | None = None
    next_terminal_id: UUID | None = None
    next_terminal_code: str | None = None

class TerminalView(View):
    terminal_id: UUID
    board_terminal_id: int
    code: str
    name: str
    city: str
    state: str
    latitude: Decimal | None
    longitude: Decimal | None
    seeded_unit_capacity: int
    last_synced_at: datetime | None
    capacity: FleetCapacityResponse | None = None

class ActivityView(View):
    event_id: UUID
    shipment_id: UUID | None
    action_id: UUID | None
    event_type: str
    message: str
    details: dict
    occurred_at_wall: datetime
    occurred_at_sim: datetime | None
    external_id: str | None = None


class LoadDetail(LoadView):
    financial_outcome: dict | None = Field(default=None, description='Reported actual amounts and evidence; separate from booking-time estimates.')
    actions: list[ActionView]
    actual_assignment: AssignmentView | None
    latest_evaluation: EvaluationView | None
    assignment_history: list[AssignmentView] = Field(default_factory=list)


class UnitDetail(UnitView):
    actual_assignment: AssignmentView | None


class TerminalDetail(TerminalView):
    roster: list[UnitView]


class BidView(BaseModel):
    action_id: UUID | None
    bid_rate: Decimal | None
    request_status: str | None
    outcome: str
    decision_at: datetime | None
    order: LoadView


class SystemStatus(BaseModel):
    sim_time_utc: datetime | None
    simulation_speed: Decimal | None
    connection_status: str
    connection_changed_at: datetime | None
    last_sync_at: datetime | None
    last_error: str | None


class ProfitSummary(BaseModel):
    confirmed_estimated_profit: Decimal
    conditional_pending_bid_profit: Decimal
    confirmed_estimated_count: int
    pending_estimated_count: int
    confirmed_unknown_count: int
    pending_unknown_count: int


class Overview(BaseModel):
    counts: dict[str, int]
    profit: ProfitSummary
    loads: list[LoadView]
    bids: list[BidView]
    terminals: list[TerminalView]
    fleet: list[UnitView]
    shipments: list[LoadView]
    activity: list[ActivityView]


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    carrier_note: str | None = Field(default=None, max_length=2000)


class BidRequest(ActionRequest):
    bid_rate: PositiveBid
    price_analysis_id: UUID | None = None


class BoardActionResponse(BaseModel):
    ok: bool
    message: str
    order: 'BoardOrder'


from schema.ingestion.board_api import BoardOrder
BoardActionResponse.model_rebuild()


class ActionResult(BaseModel):
    action: ActionView
    order: LoadDetail
    sync_pending: bool
