import type { LiveOverviewState } from '../api/overview'
import type { LoadView } from '../api/orders'
import { getLoadOpportunities } from './load-board'

function firstByDate(loads: LoadView[], field: 'expires_at' | 'board_created_at', direction: 'asc' | 'desc' = 'asc') {
  return [...loads].sort((left, right) => {
    const a = left[field] ? Date.parse(left[field]) : NaN
    const b = right[field] ? Date.parse(right[field]) : NaN
    if (Number.isFinite(a) !== Number.isFinite(b)) return Number.isFinite(a) ? -1 : 1
    return (Number.isFinite(a) && Number.isFinite(b) ? (direction === 'desc' ? b - a : a - b) : 0) || left.external_id.localeCompare(right.external_id)
  }).slice(0, 5)
}

export function dashboardSummary({ overview, loads, terminals }: LiveOverviewState) {
  const current = (loads ?? []).filter((load) => !load.board_missing_at)
  const opportunities = getLoadOpportunities(loads ?? overview?.loads ?? [])
  const pending = current.filter((load) => load.status === 'bid_submitted' && load.order_type === 'open_bid')
  const active = (loads ?? overview?.shipments ?? []).filter((load) => !load.board_missing_at && ['accepted', 'in_transit'].includes(load.status))
  const sum = (field: 'total_units' | 'available_units' | 'assigned_units') => terminals === null || terminals.some((terminal) => terminal[field] === null)
    ? null : terminals.reduce((total, terminal) => total + (terminal[field] ?? 0), 0)
  const pendingPreview = loads !== null ? pending : (overview?.bids ?? []).filter((bid) => bid.outcome === 'pending' && !bid.order.board_missing_at).map((bid) => bid.order)
  return {
    openCount: loads !== null ? opportunities.length : overview?.counts.open_loads ?? null,
    pendingCount: loads !== null ? pending.length : overview ? overview.profit.pending_estimated_count + overview.profit.pending_unknown_count : null,
    activeCount: loads !== null ? active.length : null,
    availableUnits: sum('available_units'),
    totalUnits: sum('total_units') ?? overview?.counts.fleet_units ?? null,
    assignedUnits: sum('assigned_units'),
    attentionCount: terminals?.filter((terminal) => terminal.status === 'attention' || terminal.consistent === false).length ?? null,
    opportunities: firstByDate(opportunities, 'expires_at'),
    pendingBids: firstByDate(pendingPreview, 'board_created_at', 'desc'),
    shipments: firstByDate(active, 'board_created_at', 'desc'),
  }
}

export function dashboardRoute(load: LoadView) {
  const origin = [load.origin_city, load.origin_state].filter(Boolean).join(', ') || load.origin_terminal_code || 'Unknown pickup'
  const destination = [load.destination_city, load.destination_state].filter(Boolean).join(', ') || load.destination_terminal_code || 'Unknown destination'
  return `${origin} → ${destination}`
}
