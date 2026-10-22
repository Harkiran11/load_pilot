import type { HistoryCleanupResult } from '../api/types'

export function validateCleanupCount(count: number) {
  if (!Number.isInteger(count) || count < 1 || count > 10_000) {
    throw new Error('Enter a whole count between 1 and 10,000.')
  }
}

export function cleanupResultMessage(result: HistoryCleanupResult, requested: number) {
  const noun = result.resource === 'activity' ? 'activity events' : result.resource === 'bids' ? 'bid history entries' : 'shipment history entries'
  if (result.deleted_count === 0) {
    if (result.resource === 'bids') {
      if (result.remaining_count === 0) return 'There is no submitted bid history to delete. Fixed-rate orders and expired or declined offers without a bid also appear in this table, but the bid-cleanup API does not delete them.'
      if (result.eligible_count_before === 0) return `No bid history is eligible for deletion. ${result.protected_count.toLocaleString()} bid records are protected by active bookings, pending bids, unresolved submissions, reservations, or delivery turnaround. Fixed-rate orders and offers without bids are outside this cleanup’s scope.`
    }
    return `No eligible ${noun} were deleted. ${result.protected_count.toLocaleString()} records are protected by the backend.`
  }
  return `${result.deleted_count.toLocaleString()} oldest eligible ${noun} deleted. ${result.protected_count.toLocaleString()} protected records retained.${result.deleted_count < requested ? ` Fewer than ${requested.toLocaleString()} eligible records were available.` : ''}`
}
