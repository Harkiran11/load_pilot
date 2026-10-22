from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schema.autopilot.autopilot import AutopilotSettings
from schema.dispatch.dispatch_policy import DispatchEvaluation
from schema.shared.money import PositiveBid
from schema.shared.api import View


class AllocationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings: AutopilotSettings = Field(default_factory=lambda: AutopilotSettings(enable_open_bid=False))
    external_ids: list[str] | None = Field(default=None, min_length=1, max_length=2000)
    bid_rates: dict[str, PositiveBid] = Field(default_factory=dict)


class AllocationCombination(BaseModel):
    external_id: str
    unit_id: UUID
    board_selectable: bool
    feasible: bool
    rejection_reasons: list[str]
    loaded_cost: Decimal | None
    deadhead_miles: Decimal | None
    deadhead_cost: Decimal | None
    total_cost: Decimal | None
    projected_profit: Decimal | None


class AllocationSelection(BaseModel):
    external_id: str
    shipment_id: UUID
    order_type: str
    unit_id: UUID
    bid_rate: Decimal | None
    expires_at: datetime
    submission_position: int
    planned_submission_at_wall: datetime
    planned_submission_at_sim: datetime | None = None
    evaluation: DispatchEvaluation
    model_snapshot: dict = Field(default_factory=dict)


class AllocationResult(BaseModel):
    plan_id: UUID | None = None
    snapshot_fingerprint: str
    evaluated_at_wall: datetime
    evaluated_at_sim: datetime
    selected: list[AllocationSelection] = Field(default_factory=list)
    combinations: list[AllocationCombination] = Field(default_factory=list)
    rejected: dict[str, list[str]] = Field(default_factory=dict)
    available_order_count: int
    candidate_count: int
    omitted_external_ids: list[str] = Field(default_factory=list)
    search_nodes: int = 0
    optimality: Literal['optimal', 'bounded'] = 'optimal'
    optimality_scope: Literal['priced_candidates_under_board_assignment_rules'] = 'priced_candidates_under_board_assignment_rules'
    projected_profit: Decimal = Decimal('0')
    upper_bound_profit: Decimal = Decimal('0')
    loaded_cost: Decimal = Decimal('0')
    deadhead_cost: Decimal = Decimal('0')
    deadhead_miles: Decimal = Decimal('0')
    warnings: list[str] = Field(default_factory=list)
    truck_sequences: list[dict] = Field(default_factory=list)
    sequence_execution_policy: Literal['reanalyze_after_each_confirmed_assignment'] = 'reanalyze_after_each_confirmed_assignment'


class AllocationPlanView(View):
    expires_at_sim: datetime | None = None
    plan_id: UUID
    run_id: UUID
    status: str
    snapshot_fingerprint: str | None
    result: dict
    created_at_wall: datetime
    expires_at_wall: datetime
    finished_at_wall: datetime | None
