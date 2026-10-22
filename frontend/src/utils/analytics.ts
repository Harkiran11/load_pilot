import type { ActivityView, AnalyticsOrder } from '../api/types'

const dayMs = 86_400_000
export type AnalyticsDateBasis = 'pickup' | 'delivery'
export type AnalyticsOrderType = 'all' | 'fixed_rate' | 'open_bid'
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

export function utcDay(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return null
  return new Date(value).toISOString().slice(0, 10)
}

export function analyticsRange(end: string, days: number) {
  const day = utcDay(end)
  if (!day || !Number.isInteger(days) || days < 1 || days > 366) return null
  return { start: new Date(Date.parse(day) - (days - 1) * dayMs).toISOString().slice(0, 10), end: day }
}

export function validateAnalyticsRange(start: string, end: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || utcDay(start) !== start || utcDay(end) !== end) return 'Choose a valid start and end date.'
  const count = (Date.parse(end) - Date.parse(start)) / dayMs + 1
  if (count < 1) return 'End date must be on or after start date.'
  if (count > 366) return 'Choose a range of up to 366 days.'
  return null
}

function number(value: string | null) {
  if (value === null || !value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function buildAnalytics(orders: AnalyticsOrder[], requests: ActivityView[], start: string, end: string, basis: AnalyticsDateBasis, type: AnalyticsOrderType) {
  if (validateAnalyticsRange(start, end)) return null
  const days: AnalyticsDay[] = []
  for (let time = Date.parse(start); time <= Date.parse(end); time += dayMs) {
    days.push({ date: new Date(time).toISOString().slice(0, 10), profit: 0, loss: 0, net: 0, known: 0, unknown: 0, shipments: 0, bids: 0, opportunities: 0 })
  }
  const byDay = new Map(days.map(day => [day.date, day]))
  const uniqueOrders = [...new Map(orders.map(order => [order.external_id, order])).values()]
  const ordersById = new Map(uniqueOrders.map(order => [order.external_id, order]))
  const filtered = uniqueOrders.filter(order => type === 'all' || order.order_type === type)
  const dateOf = (order: AnalyticsOrder) => utcDay(basis === 'pickup' ? order.pickup_at : order.delivery_at)
  const cohort = filtered.filter(order => byDay.has(dateOf(order) ?? ''))
  const confirmedStatuses = ['accepted', 'in_transit', 'completed']
  const confirmed = cohort.filter(order => confirmedStatuses.includes(order.status))
  let undatedShipments = 0, undatedRequests = 0, unmatchedRequests = 0, pendingProfit = 0, pendingUnknown = 0, pendingKnown = 0
  const lanes = new Map<string, { route: string; profit: number; known: number; unknown: number; shipments: number }>()
  for (const order of filtered) {
    const created = byDay.get(utcDay(order.board_created_at) ?? '')
    if (created) created.opportunities++
    if (confirmedStatuses.includes(order.status) && !dateOf(order)) undatedShipments++
  }
  for (const order of confirmed) {
    const day = byDay.get(dateOf(order)!)!
    const profit = order.profit_category === 'confirmed' ? number(order.estimated_profit) : null
    day.shipments++
    const route = `${order.origin_terminal_code || order.origin_city || 'Unknown'} → ${order.destination_terminal_code || order.destination_city || 'Unknown'}`
    const lane = lanes.get(route) ?? { route, profit: 0, known: 0, unknown: 0, shipments: 0 }
    lane.shipments++
    if (profit === null) { day.unknown++; lane.unknown++ }
    else {
      day.known++
      lane.known++
      lane.profit += profit
      if (profit >= 0) day.profit += profit
      else day.loss += profit
    }
    lanes.set(route, lane)
  }
  for (const order of cohort.filter(order => order.status === 'bid_submitted')) {
    const profit = order.profit_category === 'conditional' ? number(order.estimated_profit) : null
    if (profit === null) pendingUnknown++
    else { pendingKnown++; pendingProfit += profit }
  }
  const seen = new Set<string>()
  for (const request of requests) {
    // The backend emits this event before dispatching a bid to the board.
    if (request.event_type !== 'action.requested' || request.message.trim().toLowerCase() !== 'bid requested') continue
    const id = request.action_id ?? request.event_id
    if (seen.has(id)) continue
    seen.add(id)
    if (type === 'fixed_rate') continue
    const order = request.external_id ? ordersById.get(request.external_id) : null
    if (type !== 'all' && (!order || order.order_type !== type)) { if (!order) unmatchedRequests++; continue }
    const date = utcDay(request.occurred_at_sim)
    if (!date) { undatedRequests++; continue }
    const day = byDay.get(date)
    if (day) day.bids++
  }
  for (const day of days) day.net = day.known || !day.unknown ? day.profit + day.loss : null
  const profit = days.reduce((sum, day) => sum + day.profit, 0)
  const loss = days.reduce((sum, day) => sum + day.loss, 0)
  const known = days.reduce((sum, day) => sum + day.known, 0)
  const unknown = days.reduce((sum, day) => sum + day.unknown, 0)
  const bidCount = days.reduce((sum, day) => sum + day.bids, 0)
  const openBids = cohort.filter(order => order.order_type === 'open_bid')
  const won = openBids.filter(order => confirmedStatuses.includes(order.status)).length
  const lost = openBids.filter(order => order.status === 'bid_lost').length
  const pending = openBids.filter(order => order.status === 'bid_submitted').length
  return {
    days, profit, loss, net: known || !unknown ? profit + loss : null, known, unknown,
    shipments: confirmed.length, completed: confirmed.filter(order => order.status === 'completed').length,
    bidCount, bidsPerDay: bidCount / days.length, won, lost, pending, winRate: won + lost ? won / (won + lost) * 100 : null,
    pendingProfit: pendingKnown || !pendingUnknown ? pendingProfit : null, pendingKnown, pendingUnknown,
    newOrders: days.reduce((sum, day) => sum + day.opportunities, 0),
    fixed: cohort.filter(order => order.order_type === 'fixed_rate').length, open: openBids.length,
    undatedShipments, undatedRequests, unmatchedRequests,
    lanes: [...lanes.values()].sort((a, b) => (b.known > 0 ? 1 : 0) - (a.known > 0 ? 1 : 0) || b.profit - a.profit || a.route.localeCompare(b.route)).slice(0, 5),
  }
}

export type AnalyticsSummary = NonNullable<ReturnType<typeof buildAnalytics>>
