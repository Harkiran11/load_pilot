export const activityEventTypes = [
  'action.requested', 'action.succeeded', 'action.failed', 'action.unknown', 'action.reconciled',
  'order.ingested', 'order.status_changed', 'order.expired',
  'assignment.confirmed', 'assignment.cancelled', 'assignment.conflict',
  'fleet.discrepancy', 'fleet.reconciled', 'reservation.released', 'sync.error',
  'dispatch.rejected', 'history.imported', 'history.cleaned', 'board.reset_detected',
  'assignment.reset_retired', 'reservation.reset_retired',
  'sync.board.error', 'sync.fleet.error', 'sync.history.error', 'sync.clock.error',
  'autopilot.started', 'autopilot.stopped', 'autopilot.evaluating', 'autopilot.decision',
  'autopilot.action_reserved', 'autopilot.attempt_finished',
]

const groupNames: Record<string, string> = {
  action: 'Submissions', order: 'Orders', dispatch: 'Eligibility', assignment: 'Assignments',
  fleet: 'Fleet', reservation: 'Reservations', sync: 'Synchronization', history: 'History',
  board: 'Board', autopilot: 'Autopilot',
}

export function activityEventLabel(eventType: string) {
  const names: Record<string, string> = {
    'action.requested': 'Submission requested', 'action.succeeded': 'Submission succeeded',
    'action.failed': 'Submission failed', 'action.unknown': 'Submission unconfirmed',
    'action.reconciled': 'Submission reconciled', 'dispatch.rejected': 'Eligibility rejected',
    'history.imported': 'History imported', 'history.cleaned': 'History cleared',
    'order.ingested': 'Order received', 'order.status_changed': 'Order status changed',
  }
  if (names[eventType]) return names[eventType]
  const words = eventType.replaceAll('.', ' ').replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function activityEventGroups(types: string[]) {
  const groups = new Map<string, string[]>()
  for (const type of [...new Set(types)].sort()) {
    const group = groupNames[type.split('.')[0]] ?? 'Other events'
    groups.set(group, [...(groups.get(group) ?? []), type])
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}

export function activityColor(eventType: string) {
  const suffix = eventType.split('.').at(-1)
  if (suffix === 'error' || suffix === 'failed') return 'badge-error'
  if (suffix === 'unknown' || suffix === 'conflict' || suffix === 'discrepancy') return 'badge-warning'
  if (suffix === 'succeeded' || suffix === 'confirmed' || suffix === 'reconciled') return 'badge-success'
  return 'badge-neutral'
}
