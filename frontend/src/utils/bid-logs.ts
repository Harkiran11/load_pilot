import type { BidLogRow } from '../api/bids'

export type BidDateField = 'created' | 'synced' | 'decision' | 'pickup' | 'delivery' | 'expires'
export type BidSortField = 'order' | 'type' | 'status' | 'route' | 'amount' | 'outcome' | 'request' | BidDateField
export type SortDirection = 'asc' | 'desc'
export type BidStatusFilter = 'all' | 'accepted' | 'declined' | 'rejected' | 'expired' | 'pending' | 'lost' | 'in_transit' | 'completed' | 'unknown'

export function bidOrderTypeColor(orderType: string) {
  return orderType === 'fixed_rate' ? 'badge-success' : orderType === 'open_bid' ? 'badge-info' : 'badge-neutral'
}

export function bidStatusColor(status: string) {
  const colors: Record<string, string> = {
    accepted: 'badge-success', won: 'badge-success', succeeded: 'badge-success',
    bid_submitted: 'badge-warning', pending: 'badge-warning', submitting: 'badge-warning',
    rejected: 'badge-error', failed: 'badge-error',
    declined: 'badge-neutral',
    expired: 'bid-status-expired',
    bid_lost: 'bid-status-lost', lost: 'bid-status-lost',
    in_transit: 'badge-info',
    completed: 'bid-status-completed',
  }
  return colors[status] ?? 'badge-neutral'
}

export function filterBidLogs(bids: BidLogRow[], status: BidStatusFilter) {
  return bids.filter((bid) => {
    if (status === 'all') return true
    if (status === 'rejected') return bid.outcome === 'rejected' || bid.order.status === 'rejected'
    if (status === 'pending') return bid.outcome === 'pending' || bid.order.status === 'bid_submitted'
    if (status === 'lost') return bid.outcome === 'lost' || bid.order.status === 'bid_lost'
    if (status === 'unknown') return bid.outcome === 'unknown'
    return bid.order.status === status
  })
}

export function getBidDate(bid: BidLogRow, field: BidDateField): string | null {
  switch (field) {
    case 'created': return bid.order.board_created_at
    case 'synced': return bid.order.last_synced_at
    case 'decision': return bid.decision_at
    case 'pickup': return bid.order.pickup_at
    case 'delivery': return bid.order.delivery_at
    case 'expires': return bid.order.expires_at
  }
}

export const BID_LOG_PAGE_SIZE = 20

export function getBidLogPage(bids: BidLogRow[], requestedPage: number) {
  const totalPages = Math.max(1, Math.ceil(bids.length / BID_LOG_PAGE_SIZE))
  const page = Math.max(1, Math.min(requestedPage, totalPages))
  const offset = (page - 1) * BID_LOG_PAGE_SIZE
  return {
    records: bids.slice(offset, offset + BID_LOG_PAGE_SIZE),
    page,
    totalPages,
    first: bids.length === 0 ? 0 : offset + 1,
    last: Math.min(offset + BID_LOG_PAGE_SIZE, bids.length),
  }
}

function sortValue(bid: BidLogRow, field: BidSortField): string | number | null {
  switch (field) {
    case 'order': return bid.order.external_id
    case 'type': return bid.order.order_type
    case 'status': return bid.order.status
    case 'route': return `${bid.order.origin_terminal_code ?? ''} ${bid.order.destination_terminal_code ?? ''}`.trim() || null
    case 'amount': {
      const rate = bid.order.order_type === 'fixed_rate' ? bid.order.offered_rate : bid.bid_rate
      return rate === null ? null : Number(rate)
    }
    case 'outcome': return bid.outcome
    case 'request': return bid.request_status
    default: {
      const date = getBidDate(bid, field)
      return date === null ? null : Date.parse(date)
    }
  }
}

export function sortBidLogs(bids: BidLogRow[], field: BidSortField, direction: SortDirection) {
  return [...bids].sort((a, b) => {
    const left = sortValue(a, field)
    const right = sortValue(b, field)
    const leftMissing = left === null || (typeof left === 'number' && !Number.isFinite(left))
    const rightMissing = right === null || (typeof right === 'number' && !Number.isFinite(right))
    // Unknown values stay at the end in either direction.
    if (leftMissing !== rightMissing) return leftMissing ? 1 : -1
    const comparison = leftMissing || rightMissing ? 0
      : typeof left === 'number' && typeof right === 'number' ? left - right
      : String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' })
    return (direction === 'asc' ? comparison : -comparison)
      || a.order.external_id.localeCompare(b.order.external_id, undefined, { numeric: true })
  })
}
