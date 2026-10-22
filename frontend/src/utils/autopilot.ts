import type { AutopilotRun, AutopilotSettings, Page, StartAutopilotRequest } from '../api/types'
import { isValidBidAmount } from './bid-amount'
import { readableMessage } from './api-feedback'

export type AutopilotForm = {
  mode: 'live'
  enable_fixed_rate: boolean
  enable_open_bid: boolean
  allow_baseline: boolean
  allocation_enabled: boolean
} & { [Key in Exclude<keyof AutopilotSettings, 'enable_fixed_rate' | 'enable_open_bid' | 'allow_baseline' | 'allocation_enabled'>]: string }

export const defaultAutopilotForm: AutopilotForm = {
  mode: 'live', enable_fixed_rate: true, enable_open_bid: true, allow_baseline: false,
  max_orders: '10', max_duration_seconds: '1800', max_estimated_loss_usd: '0.00',
  min_expected_profit_usd: '0.00', min_expected_margin_pct: '15', max_deadhead_miles: '100',
  max_bid_rate: '5000.00', min_action_interval_seconds: '5', max_input_age_seconds: '120',
  max_decision_age_seconds: '30', max_consecutive_errors: '3',
  model_baseline_id: '', model_baseline_version: '', model_min_lane_samples: '',
  allocation_enabled: true, collection_window_seconds: '0', allocation_max_candidates: '12', allocation_max_nodes: '2000',
  allocation_max_seconds: '2', allocation_submission_seconds: '20', allocation_expiry_buffer_seconds: '1', allocation_reservation_ttl_seconds: '30',
}

export const autopilotFieldNames: Record<keyof AutopilotForm, string> = {
  mode: 'Run mode', enable_fixed_rate: 'Fixed-rate offers', enable_open_bid: 'Open bids', allow_baseline: 'Historical fallback advice',
  max_orders: 'Maximum submissions', max_duration_seconds: 'Run duration (seconds)',
  max_estimated_loss_usd: 'Estimated loss budget (USD)', min_expected_profit_usd: 'Minimum profit per order (USD)',
  min_expected_margin_pct: 'Minimum margin (%)', max_deadhead_miles: 'Maximum empty miles', max_bid_rate: 'Maximum bid (USD)',
  min_action_interval_seconds: 'Gap between attempts (seconds)', max_input_age_seconds: 'Maximum input age (seconds)',
  max_decision_age_seconds: 'Maximum decision age (seconds)', max_consecutive_errors: 'Stop after consecutive errors',
  model_baseline_id: 'Baseline ID', model_baseline_version: 'Baseline version', model_min_lane_samples: 'Minimum lane samples',
  allocation_enabled: 'Optimize fleet allocation', collection_window_seconds: 'Collect orders for (seconds)',
  allocation_max_candidates: 'Maximum orders per batch', allocation_max_nodes: 'Maximum search states', allocation_max_seconds: 'Search time budget (seconds)',
  allocation_submission_seconds: 'Expected submission time (seconds)', allocation_expiry_buffer_seconds: 'Expiry headroom (seconds)', allocation_reservation_ttl_seconds: 'Unsent reservation lifetime (seconds)',
}

function validMoney(value: string) {
  return /^\d+(?:\.\d{1,2})?$/.test(value) && value.split('.')[0].replace(/^0+/, '').length <= 16 && Number.isFinite(Number(value))
}

export function autopilotFormErrors(form: AutopilotForm, requireBaselinePolicy = true) {
  const errors: Partial<Record<keyof AutopilotForm, string>> = {}
  if (form.mode !== 'live') errors.mode = 'Auto pilot uses live submissions.'
  if (!form.enable_fixed_rate && !form.enable_open_bid) errors.enable_open_bid = 'Enable at least one booking type.'
  const integerLimits = { max_orders: [1, 10000], max_duration_seconds: [1, 86400], max_input_age_seconds: [1, 3600], max_decision_age_seconds: [1, 120], max_consecutive_errors: [1, 100], allocation_max_candidates: [1, 30], allocation_max_nodes: [1, 100000], allocation_reservation_ttl_seconds: [1, 120] } as const
  for (const [key, [min, max]] of Object.entries(integerLimits)) {
    const field = key as keyof typeof integerLimits
    if (!/^\d+$/.test(form[field].trim()) || Number(form[field]) < min || Number(form[field]) > max) errors[field] = `Enter a whole number from ${min.toLocaleString()} to ${max.toLocaleString()}.`
  }
  for (const field of ['max_estimated_loss_usd', 'min_expected_profit_usd'] as const) {
    if (!validMoney(form[field].trim())) errors[field] = 'Enter a nonnegative amount with up to two decimal places.'
  }
  const decimal = (value: string) => /^\d+(?:\.\d+)?$/.test(value.trim()) && Number.isFinite(Number(value))
  if (!decimal(form.min_expected_margin_pct) || Number(form.min_expected_margin_pct) >= 100) errors.min_expected_margin_pct = 'Enter a margin from 0 up to, but below, 100%.'
  if (!decimal(form.max_deadhead_miles)) errors.max_deadhead_miles = 'Enter a nonnegative distance.'
  if (!decimal(form.min_action_interval_seconds) || Number(form.min_action_interval_seconds) < 1 || Number(form.min_action_interval_seconds) > 3600) errors.min_action_interval_seconds = 'Enter a number from 1 to 3,600.'
  if (form.enable_open_bid && !isValidBidAmount(form.max_bid_rate.trim())) errors.max_bid_rate = 'Enter a positive bid cap with up to two decimal places.'
  const decimalLimits = { collection_window_seconds: [0, 300], allocation_max_seconds: [0.05, 10], allocation_submission_seconds: [1, 120], allocation_expiry_buffer_seconds: [0, 60] } as const
  for (const [key, [min, max]] of Object.entries(decimalLimits)) {
    const field = key as keyof typeof decimalLimits
    if (!decimal(form[field]) || Number(form[field]) < min || Number(form[field]) > max) errors[field] = `Enter a number from ${min} to ${max}.`
  }
  if (!form.allocation_enabled && Number(form.collection_window_seconds) > 0) errors.collection_window_seconds = 'Order collection requires fleet allocation.'
  if (form.allow_baseline && form.enable_open_bid && requireBaselinePolicy) {
    if (!form.model_baseline_id.trim()) errors.model_baseline_id = 'Enter the baseline ID configured by the model.'
    if (!form.model_baseline_version.trim()) errors.model_baseline_version = 'Enter the model baseline version.'
    if (!/^\d+$/.test(form.model_min_lane_samples.trim()) || !Number.isSafeInteger(Number(form.model_min_lane_samples)) || Number(form.model_min_lane_samples) < 1) errors.model_min_lane_samples = 'Enter a positive whole-number sample requirement.'
  } else if (form.model_min_lane_samples.trim() && (!/^\d+$/.test(form.model_min_lane_samples.trim()) || !Number.isSafeInteger(Number(form.model_min_lane_samples)) || Number(form.model_min_lane_samples) < 1)) errors.model_min_lane_samples = 'Enter a positive whole number or leave blank.'
  return errors
}

export function autopilotRequest(form: AutopilotForm, runId: string): StartAutopilotRequest {
  const errors = autopilotFormErrors(form)
  if (Object.keys(errors).length) throw new Error(Object.entries(errors).map(([key, error]) => `${autopilotFieldNames[key as keyof AutopilotForm]}: ${error}`).join(' '))
  const settings = { ...form, run_id: runId }
  return {
    ...settings,
    max_orders: Number(form.max_orders), max_duration_seconds: Number(form.max_duration_seconds),
    max_estimated_loss_usd: form.max_estimated_loss_usd.trim(), min_expected_profit_usd: form.min_expected_profit_usd.trim(),
    min_expected_margin_pct: form.min_expected_margin_pct.trim(), max_deadhead_miles: form.max_deadhead_miles.trim(),
    max_bid_rate: form.enable_open_bid ? form.max_bid_rate.trim() : null,
    min_action_interval_seconds: Number(form.min_action_interval_seconds), max_input_age_seconds: Number(form.max_input_age_seconds),
    max_decision_age_seconds: Number(form.max_decision_age_seconds), max_consecutive_errors: Number(form.max_consecutive_errors),
    model_baseline_id: form.model_baseline_id.trim() || null, model_baseline_version: form.model_baseline_version.trim() || null,
    model_min_lane_samples: form.model_min_lane_samples.trim() ? Number(form.model_min_lane_samples) : null,
    collection_window_seconds: Number(form.collection_window_seconds), allocation_max_candidates: Number(form.allocation_max_candidates),
    allocation_max_nodes: Number(form.allocation_max_nodes), allocation_max_seconds: Number(form.allocation_max_seconds),
    allocation_submission_seconds: Number(form.allocation_submission_seconds), allocation_expiry_buffer_seconds: Number(form.allocation_expiry_buffer_seconds),
    allocation_reservation_ttl_seconds: Number(form.allocation_reservation_ttl_seconds),
  }
}

export function autopilotForm(settings: Partial<AutopilotSettings> = {}): AutopilotForm {
  return Object.fromEntries(Object.entries(defaultAutopilotForm).map(([key, fallback]) => {
    if (key === 'mode') return [key, 'live']
    const value = settings[key as keyof AutopilotSettings]
    const missing = value == null || (typeof value === 'string' && !value.trim())
    return [key, missing ? fallback : typeof fallback === 'boolean' ? value : String(value)]
  })) as AutopilotForm
}

export class AutopilotRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = 'AutopilotRequestError' }
}

export async function readAutopilotResponse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { loc?: string[]; msg?: string }) => {
      const field = item.loc?.at(-1) as keyof AutopilotForm
      return `${autopilotFieldNames[field] ?? field ?? 'Settings'}: ${item.msg ?? 'Check this value.'}`
    }).join(' ') : `Autopilot request failed (HTTP ${response.status}).`
    throw new AutopilotRequestError(message, response.status)
  }
  if (!body || typeof body !== 'object') throw new AutopilotRequestError('The autopilot response could not be read.', response.status)
  return body as T
}

export function verifyAutopilotRun(run: AutopilotRun, runId?: string) {
  let validSettings = false
  try {
    validSettings = !!run?.settings && ['enable_fixed_rate', 'enable_open_bid', 'allow_baseline'].every(key => typeof run.settings[key as keyof AutopilotSettings] === 'boolean')
      // A draft default must not stand in for an active run's actual bid cap.
      && (!run.settings.enable_open_bid || isValidBidAmount(String(run.settings.max_bid_rate ?? '').trim()))
      && Object.keys(autopilotFormErrors(autopilotForm(run.settings), false)).length === 0
  } catch { validSettings = false }
  if (!run || typeof run.run_id !== 'string' || (runId && run.run_id !== runId)
    || !['shadow', 'live'].includes(run.mode) || !['running', 'stopped'].includes(run.status) || !validSettings
    || !Number.isInteger(run.orders_reserved) || run.orders_reserved < 0 || !Number.isFinite(run.seconds_remaining) || run.seconds_remaining < 0
    || !['booking_time_estimates', 'shadow_decision_estimates'].includes(run.loss_basis)
    || typeof run.estimated_loss_usd !== 'string' || typeof run.pending_estimated_loss_usd !== 'string'
    || !validMoney(run.estimated_loss_usd) || !validMoney(run.pending_estimated_loss_usd)
    || typeof run.submissions_enabled !== 'boolean' || !Array.isArray(run.inflight_attempt_ids)) {
    throw new AutopilotRequestError('The autopilot run could not be verified. Refresh its status.', 200)
  }
  return run
}

export function verifyAutopilotPage<T>(page: Page<T>) {
  if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 || !Number.isInteger(page.offset) || page.offset < 0) throw new Error('The autopilot history response could not be verified.')
  return page
}

const reasons: Record<string, string> = {
  order_limit_reached: 'Submission limit reached', time_limit_reached: 'Run length reached — the simulated run time was used up',
  estimated_loss_limit_reached: 'Estimated loss budget reached', user_stopped: 'Stopped by user',
  consecutive_error_limit_reached: 'Consecutive error limit reached', submission_outcome_unknown: 'A submission is unconfirmed; reconcile it before another live run',
  live_disabled: 'Live autopilot is disabled on the backend', board_reset: 'Board reset detected',
  backend_restarted: 'Backend restarted', backend_shutdown: 'Backend shut down', booking_economics_missing: 'Saved booking economics are missing',
  autopilot_minimum_profit_not_met: 'Profit is below your minimum', autopilot_minimum_margin_not_met: 'Margin is below your minimum',
  autopilot_deadhead_limit_exceeded_or_unknown: 'Empty distance exceeds your limit or is unknown',
  maximum_bid_exceeded_or_unknown: 'Model bid exceeds your cap or is unavailable', baseline_not_enabled: 'Historical fallback advice is disabled',
  cash_economics_unknown: 'Cash profit could not be verified', estimated_loss_budget_exceeded: 'This order would exceed the estimated loss budget',
  MODEL_TIMEOUT: 'The price model timed out',
  unsupported_mode: 'This older run mode is no longer supported',
  LIVE_UNAUTHORIZED: 'The model rejected backend authorization', LIVE_ORDER_TYPE_DISABLED: 'This order type is disabled by the model',
  LIVE_KILL_SWITCH_ACTIVE: 'The model has paused live trading', LIVE_AUTHORIZATION_CHANGED: 'Model authorization changed; a new decision is required',
}

export function autopilotReason(code: string) { return reasons[code] ?? readableMessage(code) }

export function autopilotStatusColor(status: string) {
  if (['submitted', 'ACCEPT', 'BID', 'running'].includes(status)) return 'badge-success'
  if (['unknown', 'REVIEW', 'submitting', 'evaluating', 'ready'].includes(status)) return 'badge-warning'
  if (['rejected', 'SKIP'].includes(status)) return 'badge-error'
  if (status === 'shadow') return 'badge-info'
  return 'badge-neutral'
}

export function wallDuration(seconds: number) {
  const total = Math.max(0, Math.ceil(seconds))
  return total >= 3600 ? `${Math.floor(total / 3600)}h ${Math.floor(total % 3600 / 60)}m` : `${Math.floor(total / 60)}m ${total % 60}s`
}

/**
 * How long a simulated run lasts in real time. The run limit is expressed in
 * simulated seconds, so at 10× a "300 second" run ends after 30 real seconds.
 * Showing both numbers keeps a short demo run from looking like a crash.
 */
export function realTimeDuration(simulatedSeconds: number, simulationSpeed: number | string | null | undefined) {
  const speed = Number(simulationSpeed)
  const factor = Number.isFinite(speed) && speed > 0 ? speed : 1
  return wallDuration(Math.round(Number(simulatedSeconds) / factor))
}

/** Plain-English reason a run ended, so a short run is not read as a crash. */
export function autopilotStopReason(reason: string | null | undefined) {
  switch (reason) {
    case 'time_limit_reached': return 'Reached its run-length limit'
    case 'order_limit_reached': return 'Reached the maximum number of submissions'
    case 'loss_limit_reached': return 'Reached the loss budget'
    case 'consecutive_errors': return 'Stopped after repeated errors'
    case 'user_stopped': return 'Stopped by the dispatcher'
    case 'board_reset': return 'The board reset, so the run was retired'
    case 'backend_restarted': return 'The backend restarted, so the run was retired'
    case null: case undefined: return 'Stopped'
    default: return `Stopped (${reason.replace(/_/g, ' ')})`
  }
}

export function autopilotPhase(run: AutopilotRun) {
  if (run.status !== 'running') return `Autopilot stopped · ${autopilotStopReason(run.stop_reason)}`
  if (run.collection_plan_id && run.collection_seconds_remaining > 0) return `Collecting ${run.collected_order_count} orders · ${wallDuration(run.collection_seconds_remaining)}`
  if (!run.submissions_enabled) return 'Run active · Trading blocked'
  if (run.settings.enable_open_bid && run.open_bid_submissions_enabled === false) return 'Fixed-rate trading enabled · Open bids blocked'
  return 'Auto trading enabled'
}
