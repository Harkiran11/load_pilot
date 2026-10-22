type ObjectValue = Record<string, unknown>
function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {}
}

const reasonMessages: Record<string, string> = {
  maximum_weight_exceeded: 'The cargo exceeds the allowed weight limit.',
  minimum_margin_usd_not_met: 'The expected profit is below the required minimum.',
  minimum_margin_pct_not_met: 'The profit margin is below the required minimum.',
  no_terminal_capacity_for_window: 'No verified pickup-terminal unit is available for the requested booking window.',
  no_available_units_at_pickup_terminal: 'No unit is available at the pickup terminal.',
  offer_expired: 'This offer has expired.',
  order_not_open: 'This order is no longer open.',
  order_missing_from_board: 'This order is no longer available on the board.',
  unknown_origin_terminal_code: 'The pickup terminal could not be found.',
  pickup_terminal_unresolved: 'The pickup terminal could not be identified.',
  assignment_window_overlap: 'The unit already has an assignment during this schedule.',
  unit_schedule_unavailable: 'The selected unit cannot be verified as free for the full booking window.',
  unit_not_board_selectable: 'The requested unit cannot currently be selected by the board at this pickup hub.',
  unit_missing_from_board: 'No present board unit can be verified for this offer.',
  projected_location_unavailable: 'The unit projected location or pickup hub cannot be verified.',
  projected_pickup_terminal_mismatch: 'The unit projected location or pickup hub cannot be verified.',
  deadhead_distance_unknown: 'The empty travel distance cannot be verified.',
  expected_margin_unknown: 'Expected margin cannot be verified from the available cost and payout inputs.',
  pickup_arrival_unknown: 'The pickup arrival time cannot be verified.',
  occupied_window_unknown: 'The full travel, shipment, and turnaround window cannot be verified.',
  maximum_deadhead_exceeded: 'The pickup is too far from the available unit.',
  cannot_reach_pickup_in_time: 'The unit cannot reach the pickup on time.',
  terminal_snapshot_stale: 'Terminal availability needs to be refreshed.',
  unit_snapshot_stale_or_unknown: 'Fleet availability needs to be refreshed.',
  terminal_capacity_discrepancy: 'Fleet availability does not match the board. Refresh the fleet data.',
}

export function readableMessage(value: string): string {
  const trimmed = value.trim()
  if (reasonMessages[trimmed]) return reasonMessages[trimmed]
  if (/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/.test(trimmed)) {
    const words = trimmed.replaceAll('_', ' ')
    return `${words[0].toUpperCase()}${words.slice(1)}.`
  }
  return trimmed.replace(/^Dispatch rejected:\s*/i, '')
    .replace(/\s*\([a-z][a-z0-9]*(?:_[a-z0-9]+)+\)/g, '')
    .replace(/\bbid_rate\b/g, 'bid amount')
    .replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (code) => reasonMessages[code] ?? code.replaceAll('_', ' '))
}

export function apiFeedback(payload: unknown, status: number) {
  const body = object(payload)
  const evaluation = object(body.evaluation)
  const primary = object(evaluation.primary_rejection)
  const feedback = object(body.feedback)
  const details: string[] = []
  const suggestions: string[] = []
  const add = (items: string[], value: unknown) => {
    if (typeof value === 'string' && value.trim()) {
      const text = readableMessage(value)
      if (!items.includes(text)) items.push(text)
    }
  }
  // The backend determines the cause; keep it ahead of downstream check failures.
  add(details, primary.message)
  add(suggestions, primary.suggested_action)
  for (const value of Array.isArray(evaluation.rejection_details) ? evaluation.rejection_details : []) {
    const reason = object(value)
    add(details, reason.message)
    add(suggestions, reason.suggested_action)
  }
  if (!details.length) {
    for (const reason of Array.isArray(evaluation.rejection_reasons) ? evaluation.rejection_reasons : []) add(details, reason)
  }
  // Preserve the API's rejection order and wording instead of guessing a cause.
  if (!details.length && typeof body.detail === 'string' && body.detail.trim()) {
    const messages = /^Dispatch rejected:/i.test(body.detail)
      ? body.detail.replace(/^Dispatch rejected:\s*/i, '').split(/;\s*/)
      : [body.detail]
    messages.forEach((message) => add(details, message))
  }
  const rejectionMessage = details[0]
  for (const value of Array.isArray(evaluation.unit_rejections) ? evaluation.unit_rejections : []) {
    const unit = object(value)
    const reason = object(unit.primary_rejection)
    if (typeof reason.message === 'string' && reason.message.trim()) {
      const name = typeof unit.unit_number === 'string' && unit.unit_number.trim() ? `Truck ${unit.unit_number}` : 'Truck'
      add(details, `${name}: ${reason.message}`)
      add(suggestions, reason.suggested_action)
    }
  }
  for (const value of Array.isArray(evaluation.capacity_warnings) ? evaluation.capacity_warnings : []) {
    const warning = object(value)
    add(details, warning.message)
    add(suggestions, warning.suggested_action)
  }
  if (Array.isArray(body.detail)) {
    for (const value of body.detail) {
      const validation = object(value)
      const location = Array.isArray(validation.loc) ? validation.loc : []
      const field = location.includes('bid_rate') ? 'Bid amount' : 'Input'
      add(details, location.includes('bid_rate') ? 'Bid amount must be positive and use no more than two decimal places.' : `${field}: ${typeof validation.msg === 'string' ? validation.msg : 'Check this value.'}`)
    }
  }
  add(suggestions, feedback.suggested_action)
  const fallback = status === 404 ? 'This order could not be found. Refresh the board and choose an available offer.'
    : status === 422 ? 'Check the entered values before trying again.'
    : status === 409 ? 'The order could not be submitted because its availability or eligibility changed.'
    : status >= 500 ? 'The service could not confirm the request. Check the order status before trying again.'
    : 'The request could not be completed. Refresh the order and try again.'
  const message = rejectionMessage ?? details[0] ?? fallback
  return { message, details: details.filter((detail) => detail !== message), suggestions }
}

export type LoadFeedback = {
  id: string
  title: string
  message: string
  tone: 'success' | 'warning' | 'error' | 'info'
  details?: string[]
  suggestions?: string[]
}
