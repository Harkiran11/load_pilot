// Mirrors dispatcher Pydantic response schemas in backend/schema/.
// Decimal values and UTC timestamps are serialized as strings.
export type DecimalString = string

export interface Page<T> {
  items: Array<T>
  total: number
  limit: number
  offset: number
}

export interface AssignmentView {
  assignment_id: string
  shipment_id: string
  unit_id: string
  origin_terminal_id: string
  destination_terminal_id: string
  busy_from: string
  busy_until: string
  assignment_status: string
  confirmed_at: string
  completed_at: string | null
  cancelled_at: string | null
}

export interface ActionView {
  action_id: string
  shipment_id: string
  evaluation_id: string | null
  action_type: string
  bid_rate: DecimalString | null
  carrier_note: string | null
  request_status: string
  board_http_status: number | null
  board_message: string | null
  requested_at: string
  resolved_at: string | null
}

export interface EvaluationView {
  evaluation_id: string
  shipment_id: string
  candidate_unit_id: string | null
  payout: DecimalString | null
  loaded_cost: DecimalString | null
  deadhead_miles: DecimalString | null
  deadhead_cost: DecimalString | null
  total_cost: DecimalString | null
  profit_usd: DecimalString | null
  margin_pct: DecimalString | null
  suggested_bid: DecimalString | null
  cost_source: string | null
  pricing_passed: boolean
  capacity_passed: boolean
  schedule_passed: boolean
  weight_passed: boolean
  location_passed: boolean
  offer_open_unexpired: boolean
  eligible: boolean
  rejection_reasons: Record<string, unknown> | unknown[]
  policy_snapshot: Record<string, unknown> | unknown[]
  evaluated_at_sim: string
  evaluated_at_wall: string
}

export interface LoadView {
  shipment_id: string
  external_id: string
  board_order_id: number | null
  order_type: string
  status: string
  origin_terminal_code: string | null
  destination_terminal_code: string | null
  origin_terminal_id: string | null
  destination_terminal_id: string | null
  origin_city: string | null
  origin_state: string | null
  origin_latitude: DecimalString | null
  origin_longitude: DecimalString | null
  destination_city: string | null
  destination_state: string | null
  destination_latitude: DecimalString | null
  destination_longitude: DecimalString | null
  distance_miles: DecimalString | null
  weight_lbs: number | null
  offered_rate: DecimalString | null
  cost_per_mile: DecimalString | null
  estimated_cost: DecimalString | null
  estimated_deadhead_miles: DecimalString | null
  estimated_deadhead_cost: DecimalString | null
  estimated_total_cost: DecimalString | null
  pickup_at: string | null
  delivery_at: string | null
  expires_at: string | null
  board_created_at: string | null
  bid_decision_at: string | null
  winning_bid_rate: DecimalString | null
  board_assigned_unit_id: number | null
  assigned_terminal_code: string | null
  last_synced_at: string | null
  board_missing_at: string | null
  shipment_history_deleted_at: string | null
  bid_history_deleted_at: string | null
  seconds_remaining: number | null
  estimated_profit: DecimalString | null
  profit_category: string
}

export interface AnalyticsOrder {
  external_id: string
  order_type: string
  status: string
  pickup_at: string | null
  delivery_at: string | null
  board_created_at: string | null
  origin_terminal_code: string | null
  origin_city: string | null
  destination_terminal_code: string | null
  destination_city: string | null
  estimated_profit: DecimalString | null
  profit_category: string
}

export interface AnalyticsDay {
  date: string
  profit: number
  loss: number
  net: number | null
  known: number
  unknown: number
  shipments: number
  bids: number
  opportunities: number
}

export interface AnalyticsLane {
  route: string
  profit: number
  known: number
  unknown: number
  shipments: number
}

export interface AnalyticsSummary {
  days: AnalyticsDay[]
  profit: number
  loss: number
  net: number | null
  known: number
  unknown: number
  shipments: number
  completed: number
  bidCount: number
  bidsPerDay: number
  won: number
  lost: number
  pending: number
  winRate: number | null
  pendingProfit: number | null
  pendingKnown: number
  pendingUnknown: number
  newOrders: number
  fixed: number
  open: number
  undatedShipments: number
  undatedRequests: number
  unmatchedRequests: number
  lanes: AnalyticsLane[]
}

export interface UnitView {
  unit_id: string
  board_unit_id: number
  home_terminal_id: string
  unit_number: string
  employee_code: string
  first_name: string
  last_name: string
  status: string
  current_latitude: DecimalString | null
  current_longitude: DecimalString | null
  available_at: string | null
  last_synced_at: string | null
  board_missing_at: string | null
  terminal_code: string | null
  current_terminal_id: string | null
  current_terminal_code: string | null
  next_terminal_id: string | null
  next_terminal_code: string | null
}

export interface TerminalView {
  terminal_id: string
  board_terminal_id: number
  code: string
  name: string
  city: string
  state: string
  latitude: DecimalString | null
  longitude: DecimalString | null
  seeded_unit_capacity: number
  last_synced_at: string | null
  capacity: FleetCapacityResponse | null
}

export interface ActivityView {
  event_id: string
  shipment_id: string | null
  action_id: string | null
  event_type: string
  message: string
  details: Record<string, unknown>
  occurred_at_wall: string
  occurred_at_sim: string | null
  external_id: string | null
}

export interface LoadDetail extends LoadView {
  actions: Array<ActionView>
  actual_assignment: AssignmentView | null
  latest_evaluation: EvaluationView | null
  assignment_history: AssignmentView[]
}

export interface UnitDetail extends UnitView {
  actual_assignment: AssignmentView | null
}

export interface ScheduledAssignment extends AssignmentView {
  external_id: string
  occupied_from: string | null
  occupied_until: string | null
  lock_retired_at: string | null
}

export interface ScheduledReservation {
  action_id: string
  external_id: string
  action_type: string
  request_status: string
  busy_from: string
  busy_until: string
}

export interface UnitSchedule {
  unit_id: string
  board_unit_id: number
  unit_number: string
  terminal_code: string
  status: string
  board_missing: boolean
  available_at: string | null
  as_of_sim_time: string | null
  assignments: ScheduledAssignment[]
  reservations: ScheduledReservation[]
}

export interface TerminalDetail extends TerminalView {
  roster: Array<UnitView>
}

export interface BidView {
  action_id: string | null
  bid_rate: DecimalString | null
  request_status: string | null
  outcome: string
  decision_at: string | null
  order: LoadView
}

/** Compact order summary used by the bid-log list. */
export interface BidLogOrder {
  external_id: string
  order_type: string
  status: string
  origin_terminal_code: string | null
  destination_terminal_code: string | null
  offered_rate: DecimalString | null
  pickup_at: string | null
  delivery_at: string | null
  expires_at: string | null
  board_created_at: string | null
  last_synced_at: string | null
}

/** One bid-log record: the recorded action plus its order summary. */
export interface BidLogRow {
  action_id: string | null
  bid_rate: DecimalString | null
  request_status: string | null
  outcome: string
  decision_at: string | null
  order: BidLogOrder
}

export interface SystemStatus {
  sim_time_utc: string | null
  simulation_speed: DecimalString | null
  connection_status: string
  connection_changed_at: string | null
  last_sync_at: string | null
  last_error: string | null
}

export interface SimulationClock {
  wall_time_utc: string
  sim_time_utc: string
  simulation_speed: number
  note: string
}

export interface SimulationSettings {
  simulation_speed: number
  order_interval_seconds: number
  order_ttl_sim_minutes: number
  bid_decision_sim_minutes: number
  note: string
}

export interface SimulationSettingsRequest {
  simulation_speed?: number
  order_interval_seconds?: number
}

export interface SpawnOrdersResult {
  created: number
  fixed_rate: number
  open_bid: number
  external_ids: string[]
}

export interface ProfitSummary {
  confirmed_estimated_profit: DecimalString
  conditional_pending_bid_profit: DecimalString
  confirmed_estimated_count: number
  pending_estimated_count: number
  confirmed_unknown_count: number
  pending_unknown_count: number
}

export interface Overview {
  counts: Record<string, number>
  profit: ProfitSummary
  loads: Array<LoadView>
  bids: Array<BidView>
  terminals: Array<TerminalView>
  fleet: Array<UnitView>
  shipments: Array<LoadView>
  activity: Array<ActivityView>
}

export interface ActionResult {
  action: ActionView
  order: LoadDetail
  sync_pending: boolean
}

export interface TerminalStatus {
  terminal_id: string
  code: string
  name: string
  city: string
  state: string
  latitude: DecimalString | null
  longitude: DecimalString | null
  status: 'available' | 'unavailable' | 'empty' | 'attention' | 'unknown'
  total_units: number | null
  available_units: number | null
  assigned_units: number | null
  board_total_units: number | null
  board_available_units: number | null
  board_assigned_units: number | null
  consistent: boolean | null
  discrepancies: Array<Record<string, unknown>>
  as_of_sim_time: string | null
  last_synced_at: string | null
}

export interface TerminalStatusMessage {
  event: 'terminals.snapshot' | 'terminals.updated' | 'terminals.heartbeat'
  revision: number
  sent_at: string
  items: Array<TerminalStatus>
}

export interface CapacityCounts {
  total_units: number
  available_units: number
  assigned_units: number
}

export interface FleetCapacityResponse {
  terminal_code: string
  board: CapacityCounts | null
  local: CapacityCounts
  consistent: boolean
  discrepancies: Array<Record<string, unknown>>
  as_of_sim_time: string
  checked_at: string
}

export interface OrderExpiration {
  external_id: string
  status: string
  expires_at: string | null
  seconds_remaining: number | null
  sim_time_utc: string
  board_missing_at: string | null
}

export interface HealthResponse {
  status: 'ok'
}

export interface ActionRequest {
  carrier_note?: string | null
}

export interface BidRequest extends ActionRequest {
  bid_rate: DecimalString | number
}

export interface TerminalStatusError {
  event: 'terminals.error'
  message: string
}

export type TerminalStatusEvent = TerminalStatusMessage | TerminalStatusError

export interface PaginationParams {
  limit?: number
  offset?: number
}

export interface LoadStreamMessage {
  event: 'loads.snapshot' | 'loads.updated' | 'loads.heartbeat'
  revision: number
  sent_at: string
  items: LoadView[]
  system: SystemStatus
}

export interface LoadStreamError {
  event: 'loads.error'
  message: string
}

export type LoadStreamEvent = LoadStreamMessage | LoadStreamError

export interface ProjectedDelivery {
  assignment_id: string
  shipment_id: string
  external_id: string
  pickup_at: string
  delivery_at: string
  terminal_id: string | null
  terminal_code: string | null
  terminal_resolution: 'destination_code' | 'nearest_destination' | 'unresolved'
  latitude: DecimalString | null
  longitude: DecimalString | null
}

export interface UnitProjection {
  unit_id: string
  board_unit_id: number
  baseline_sim_time: string | null
  at: string | null
  status: 'available' | 'busy' | 'unavailable' | 'unknown'
  terminal_id: string | null
  terminal_code: string | null
  latitude: DecimalString | null
  longitude: DecimalString | null
  available_at: string | null
  source: 'snapshot' | 'booked_delivery' | 'in_transit' | 'unknown'
  source_assignment_id: string | null
  next_terminal_id: string | null
  next_terminal_code: string | null
  deliveries: ProjectedDelivery[]
  uncertain_action_ids: string[]
  rejection_reasons: string[]
}

// Read-only order economics and dispatch-preview responses.
export interface EconomicsSource {
  method: string
  inputs: Record<string, string | null>
  formula: string | null
}

export interface EconomicsSources {
  payout: EconomicsSource
  loaded_cost: EconomicsSource
  cost_per_mile: EconomicsSource
  projected_deadhead_miles: EconomicsSource
  projected_deadhead_cost: EconomicsSource
  total_cost: EconomicsSource
  gross_profit: EconomicsSource
  deadhead_travel_hours: EconomicsSource
  shipment_duration_hours: EconomicsSource
  turnaround_hours: EconomicsSource
  occupied_hours: EconomicsSource
  profit_per_hour: EconomicsSource
  margin_pct: EconomicsSource
  minimum_profitable_bid: EconomicsSource
  opportunity_cost: EconomicsSource
  positioning_value: EconomicsSource
  adjusted_profit: EconomicsSource
}

export interface BidFloorRequirements {
  minimum_profit_usd: DecimalString
  minimum_margin_pct: DecimalString
  dollar_profit_floor: DecimalString | null
  percentage_margin_floor: DecimalString | null
  determining_requirement: 'dollar_profit' | 'percentage_margin' | 'both' | null
  explanation: string
}

export interface MissingEconomicsInput {
  field: string
  code: string
  message: string
}

export interface ExpectedEconomics {
  calculation_version: 'deterministic_v1'
  currency: 'USD'
  payout: DecimalString | null
  loaded_cost: DecimalString | null
  projected_deadhead_miles: DecimalString | null
  projected_deadhead_cost: DecimalString | null
  total_cost: DecimalString | null
  gross_profit: DecimalString | null
  margin_pct: DecimalString | null
  deadhead_travel_hours: DecimalString | null
  shipment_duration_hours: DecimalString | null
  turnaround_hours: DecimalString | null
  occupied_hours: DecimalString | null
  profit_per_hour: DecimalString | null
  minimum_profitable_bid: DecimalString | null
  bid_floor: BidFloorRequirements
  opportunity_cost: DecimalString | null
  positioning_value: DecimalString | null
  adjusted_profit: DecimalString | null
  profit_adjustments: ProfitAdjustmentDetails
  cost_per_mile: DecimalString | null
  sources: EconomicsSources
  missing_inputs: Array<MissingEconomicsInput>
}

export interface ProfitAdjustmentPolicy {
  opportunity_cost_enabled: boolean
  opportunity_cost_per_hour: DecimalString
  positioning_value_enabled: boolean
  positioning_credit_fraction: DecimalString
  positioning_max_credit: DecimalString
  historical_min_samples: number
  historical_lookback_days: number
  historical_origin_radius_miles: DecimalString
}

export interface ProfitAdjustmentDetails {
  calculation_version: string
  policy: ProfitAdjustmentPolicy
  opportunity_cost: Record<string, unknown>
  positioning_value: Record<string, unknown>
  warnings: Array<string>
}

export interface OccupiedWindow {
  occupied_from: string
  occupied_until: string
  pickup_buffer_hours: DecimalString
  deadhead_travel_hours: DecimalString
  shipment_duration_hours: DecimalString
  turnaround_hours: DecimalString
  occupied_hours: DecimalString
}

export interface DispatchPolicy {
  profit_adjustments: ProfitAdjustmentPolicy
  enforce_margin: boolean
  min_expected_margin_pct: DecimalString
  min_expected_margin_usd: DecimalString
  max_deadhead_miles: DecimalString
  max_weight_lbs: number
  min_pickup_lead_minutes: DecimalString
  pickup_buffer_minutes: DecimalString
  assumed_travel_speed_mph: DecimalString
  turnaround_minutes: DecimalString
  max_fleet_snapshot_age_seconds: number
}

export interface DispatchRejection {
  code: string
  check: string
  message: string
  suggested_action: string
  context: Record<string, unknown>
}

export interface DispatchUnitRejection {
  unit_id: string
  board_unit_id: number
  unit_number: string
  primary_rejection: DispatchRejection
  rejection_reasons: string[]
  distance_miles: DecimalString | null
  available_at_pickup: boolean
  conflicting_assignments: Array<{
    assignment_id: string
    shipment_id: string
    busy_from: string
    busy_until: string
    assignment_status: string
  }>
  conflicting_reservations: Array<{
    action_id: string
    busy_from: string
    busy_until: string
    request_status: string
  }>
}

export interface DispatchEvaluation {
  external_id: string
  candidate_unit_id: string | null
  evaluated_at_sim: string
  policy: DispatchPolicy
  eligible: boolean
  checks: Record<string, boolean>
  skipped_checks: Array<string>
  rejection_reasons: Array<string>
  rejection_details: Array<DispatchRejection>
  primary_rejection: DispatchRejection | null
  unit_rejections: DispatchUnitRejection[]
  capacity_warnings: Array<Record<string, unknown>>
  capacity_units_for_window: number
  economics: ExpectedEconomics
  deadhead_miles: DecimalString | null
  margin_pct_for_policy: DecimalString | null
  occupied_window: OccupiedWindow | null
  earliest_pickup_at: string | null
  projection: UnitProjection | null
}

export interface RankingPolicy {
  empty_miles_weight: DecimalString
  continuity_weight: DecimalString
  utilization_weight: DecimalString
  utilization_horizon_hours: DecimalString
}

export interface RankedMatch {
  rank: number
  external_id: string
  shipment_id: string
  unit_id: string
  board_unit_id: number
  unit_number: string
  pickup_at: string
  delivery_at: string
  pickup_terminal_code: string
  projected_latitude: DecimalString
  projected_longitude: DecimalString
  position_source: string
  source_assignment_id: string | null
  deadhead_miles: DecimalString
  cost_per_mile: DecimalString | null
  deadhead_cost: DecimalString | null
  cost_missing_reason: string | null
  economics: ExpectedEconomics
  empty_miles_score: DecimalString
  continues_previous_route: boolean
  previous_assignment_id: string | null
  idle_gap_seconds: DecimalString | null
  continuity_score: DecimalString
  booked_hours: DecimalString
  added_loaded_hours: DecimalString
  utilization_before: DecimalString
  utilization_after: DecimalString
  score: DecimalString
  booking_ready: boolean
  dispatch_rejection_reasons: Array<string>
  dispatch_rejection_details: Array<DispatchRejection>
}

export interface RejectedUnit {
  unit_id: string
  rejection_reasons: Array<string>
}

export interface MarketStatistics {
  sample_count: number
  median: DecimalString | null
  mean: DecimalString | null
  minimum: DecimalString | null
  maximum: DecimalString | null
  population_stddev: DecimalString | null
}

export interface HistoricalMarketContext {
  origin_state: string | null
  destination_state: string | null
  order_type: 'fixed_rate' | 'open_bid'
  matching_method: string
  since_sim: string
  through_sim: string
  radius_miles: DecimalString
  minimum_samples: number
  sample_count: number
  sufficient_samples: boolean
  lane_ids: string[]
  payout: MarketStatistics
  rate_per_mile: MarketStatistics
  historical_win: { sample_count: number; wins: number; probability: DecimalString | null; label: string }
  warnings: string[]
  payout_basis: string
}

export interface OrderRecommendations {
  market_context: HistoricalMarketContext | null
  external_id: string
  evaluated_at_sim: string
  horizon_end: string
  policy: RankingPolicy
  entered_bid_rate: DecimalString | null
  dispatch_evaluation: DispatchEvaluation | null
  selected: RankedMatch | null
  ranked_units: Array<RankedMatch>
  rejected_units: Array<RejectedUnit>
  rejection_reasons: Array<string>
}

export interface CostPreviewRequest {
  bid_rate?: string
  unit_id?: string
}

export interface PriceAnalysisRequest {
  bid_rate: DecimalString
  unit_id?: string
  lane_id?: string
  request_id?: string
}

export interface PriceAnalysisWarning {
  code: string
  message: string
  fields?: string[]
  details?: Record<string, unknown>
}

export interface ModelBackendEconomics {
  source: 'backend'
  total_estimated_cost_dollars: number | null
  gross_profit_dollars: number | null
  margin_percent: number | null
}

export interface ModelPriceAdvice {
  pricing_source: 'model' | 'baseline'
  model_version: string | null
  predicted_bid_dollars: number
  predicted_bid_rpm: number
  status?: 'fallback_advisory' | null
  currency: 'USD'
  distance_basis: 'loaded_miles'
  advisory_only: true
  submission_enabled: false
  requires_guardrail_checks: true
  requires_review: true
  baseline_reference: {
    predicted_bid_dollars?: number
    predicted_bid_rpm?: number
    reference_source?: string
    policy_version?: string
    reason?: string
    [key: string]: unknown
  }
  uncertainty: { status?: string; [key: string]: unknown }
  warnings: PriceAnalysisWarning[]
  release_status?: string
  [key: string]: unknown
}

export interface ModelUnavailableAdvice {
  status: 'unavailable'
  http_status: number
  error: { code?: string; message?: string; errors?: Array<{ field?: string; message?: string }>; [key: string]: unknown }
}

export interface ModelManualResponse {
  api_version: 'v1'
  mode: 'manual'
  manual_bid_dollars: number
  backend_economics: ModelBackendEconomics
  economics_verified: false
  advisory: ModelPriceAdvice | ModelUnavailableAdvice
  comparison: {
    manual_bid_rpm: number | null
    manual_minus_advisory_dollars: number | null
    absolute_reference_deviation_percent: number | null
  }
  warnings: PriceAnalysisWarning[]
  advisory_only: true
  submission_enabled: false
  requires_guardrail_checks: true
}

export interface PriceAnalysisResult {
  request_id: string
  external_id: string
  bid_rate: DecimalString
  requested_unit_id: string | null
  order_last_synced_at: string | null
  analyzed_at_wall: string
  lane_id_source: 'request' | 'board_terminal_codes' | 'missing'
  deterministic_evaluation: DispatchEvaluation
  model_request: {
    shipment: {
      order_type: 'open_bid'
      distance_miles: number
      weight_lbs: number
      origin_latitude: number
      origin_longitude: number
      destination_latitude: number
      destination_longitude: number
      lane_id: string
      origin_state: string
      destination_state: string
    }
    manual_bid_dollars: number
    backend_economics: ModelBackendEconomics
    warning_policy: {
      minimum_profit_dollars: number | null
      minimum_margin_percent: number | null
      maximum_reference_deviation_percent: number
    }
  } | null
  model_response: ModelManualResponse | null
  advisory_status: 'model' | 'baseline' | 'unavailable'
  prediction_id: string | null
  error: { code: string; message: string; http_status?: number | null; fields: string[] } | null
  warnings: string[]
  advisory_only: true
  submission_enabled: false
  requires_guardrail_checks: true
}

export interface HistoryCleanupParams {
  count: number
  dry_run?: boolean
}

export interface AutopilotSettings {
  enable_fixed_rate: boolean
  enable_open_bid: boolean
  max_orders: number
  max_duration_seconds: number
  max_estimated_loss_usd: DecimalString
  min_expected_profit_usd: DecimalString
  min_expected_margin_pct: DecimalString
  max_deadhead_miles: DecimalString
  max_bid_rate: DecimalString | null
  min_action_interval_seconds: number
  max_input_age_seconds: number
  max_decision_age_seconds: number
  max_consecutive_errors: number
  allow_baseline: boolean
  model_baseline_id: string | null
  model_baseline_version: string | null
  model_min_lane_samples: number | null
  allocation_enabled: boolean
  collection_window_seconds: number
  allocation_max_candidates: number
  allocation_max_nodes: number
  allocation_max_seconds: number
  allocation_submission_seconds: number
  allocation_expiry_buffer_seconds: number
  allocation_reservation_ttl_seconds: number
}

export interface StartAutopilotRequest extends AutopilotSettings {
  run_id: string
  mode: 'live'
}

export interface AutopilotRun {
  run_id: string
  status: string
  mode: 'shadow' | 'live'
  settings: AutopilotSettings
  started_at_wall: string
  deadline_at_wall: string
  stopped_at_wall: string | null
  stop_reason: string | null
  orders_reserved: number
  estimated_loss_usd: DecimalString
  pending_estimated_loss_usd: DecimalString
  consecutive_errors: number
  loss_basis: 'booking_time_estimates' | 'shadow_decision_estimates'
  seconds_remaining: number
  inflight_attempt_ids: string[]
  submissions_enabled: boolean
  model_decision_mode: 'shadow' | 'live'
  model_live_submission_supported: boolean
  model_live_auth_configured: boolean
  open_bid_submissions_enabled: boolean
  fixed_rate_submissions_enabled: boolean
  collection_plan_id: string | null
  collection_seconds_remaining: number
  collected_order_count: number
}

export interface AutopilotAttempt {
  attempt_id: string
  run_id: string
  shipment_id: string
  external_id: string
  order_type: string
  status: string
  decision: 'ACCEPT' | 'BID' | 'SKIP' | 'REVIEW' | null
  action_id: string | null
  candidate_unit_id: string | null
  bid_rate: DecimalString | null
  model_snapshot: Record<string, unknown>
  evaluation_snapshot: Partial<DispatchEvaluation>
  reason_codes: string[]
  message: string | null
  created_at_wall: string
  decided_at_wall: string | null
  finished_at_wall: string | null
  allocation_plan_id: string | null
}

export interface HistoryCleanupResult {
  resource: 'activity' | 'shipments' | 'bids'
  requested_count: number
  matched_count: number
  deleted_count: number
  eligible_count_before: number
  remaining_count: number
  protected_count: number
  record_ids: string[]
  dry_run: boolean
  deletion_mode: 'delete_rows' | 'hide_history'
  message: string
}
