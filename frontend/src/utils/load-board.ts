import type { LoadView, OrderType } from '../api/orders'

export function getLoadOpportunities(loads: LoadView[], orderType: OrderType | 'all' = 'all') {
  return loads.filter((load) =>
    (load.order_type === 'open_bid' || load.order_type === 'fixed_rate')
    && load.status === 'open'
    && !load.board_missing_at
    && (orderType === 'all' || load.order_type === orderType),
  )
}

export function isHotDeal(load: LoadView) {
  if (load.order_type !== 'fixed_rate' || load.status !== 'open' || load.board_missing_at
    || load.offered_rate === null || !load.offered_rate.trim()
    || load.estimated_total_cost === null || !load.estimated_total_cost.trim()) return false
  const payout = Number(load.offered_rate), cost = Number(load.estimated_total_cost)
  return Number.isFinite(payout) && Number.isFinite(cost) && payout > 0 && cost >= 0
    && (payout - cost) / payout >= 0.2
}
