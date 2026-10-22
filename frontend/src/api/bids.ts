import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import { subscribeLoads } from './orders'
import type { BidLogRow, HistoryCleanupParams, Page, PaginationParams } from './types'
import { validateCleanupCount } from '../utils/history-cleanup'

export async function getBids(
  params: PaginationParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.bids, params), { ...options, method: 'GET' })
}

export async function deleteOldestBids(
  params: HistoryCleanupParams,
  options: RequestInit = {},
) {
  validateCleanupCount(params.count)
  return apiFetch(withQuery(endpoints.oldestBids, params), { ...options, method: 'DELETE' })
}

/**
 * One page of bid-log records, filtered and ordered by the backend.
 *
 * The previous implementation read the bid list and then the entire retained
 * order history as paged full order records — well over a hundred requests and
 * tens of megabytes — so the page never finished loading. The backend now
 * filters, orders and pages the log, and the full order record is fetched on
 * demand when a record is opened.
 */
export async function getBidLogs(
  params: { status?: string; sort?: string; direction?: string; limit?: number; offset?: number } = {},
  options: RequestInit = {},
): Promise<Page<BidLogRow>> {
  const response = await apiFetch(withQuery(endpoints.analytics.bidLogs, params), { ...options, method: 'GET' })
  if (!response.ok) throw new Error(`Unable to load bid logs (HTTP ${response.status}).`)
  const page: Page<BidLogRow> = await response.json()
  if (!Array.isArray(page.items) || !Number.isInteger(page.total)) {
    throw new Error('The bid log response was invalid.')
  }
  return page
}

export interface LiveBidLogsState {
  page: Page<BidLogRow> | null
  connection: 'connecting' | 'live' | 'reconnecting'
  updating: boolean
  error: string | null
}

export type BidLogQuery = {
  status: string
  sort: string
  direction: string
  limit: number
  offset: number
}

/** Minimum gap between two bid-log refreshes. */
const BID_LOG_REFRESH_MS = 30_000

// Order events trigger a read of bid metadata, which is not in the load stream.
export function subscribeBidLogs(
  onChange: (state: LiveBidLogsState) => void,
  query: BidLogQuery,
): () => void {
  const controller = new AbortController()
  let disposed = false
  let updating = false
  let refreshQueued = false
  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  let lastRefresh = 0
  let page: Page<BidLogRow> | null = null
  let connection: LiveBidLogsState['connection'] = 'connecting'
  let streamError: string | null = null
  let requestError: string | null = null

  function publish() {
    if (!disposed) onChange({ page, connection, updating, error: streamError ?? requestError })
  }

  async function refreshLogs() {
    if (disposed) return
    if (updating) {
      // Serialize reads and coalesce bursts of events into one follow-up read.
      refreshQueued = true
      return
    }
    updating = true
    publish()
    try {
      const result = await getBidLogs(query, { signal: controller.signal })
      if (disposed) return
      page = result
      requestError = null
      lastRefresh = Date.now()
    } catch (cause) {
      if (disposed) return
      requestError = cause instanceof Error ? cause.message : 'Unable to update bid logs.'
    } finally {
      updating = false
      if (!disposed) {
        publish()
        if (refreshQueued) {
          refreshQueued = false
          void refreshLogs()
        }
      }
    }
  }

  void refreshLogs()
  const unsubscribe = subscribeLoads({ order_type: 'all', status: 'all', loads: false }, (state) => {
    if (disposed) return
    connection = state.connection
    streamError = state.error
    if (state.connection === 'live') {
      // The board publishes about once a second. Re-reading the whole log
      // that often starved the backend, so the refresh is throttled.
      scheduleRefresh()
    } else {
      publish()
    }
  })

  function scheduleRefresh() {
    if (disposed || refreshTimer) return
    const wait = Math.max(0, BID_LOG_REFRESH_MS - (Date.now() - lastRefresh))
    refreshTimer = setTimeout(() => {
      refreshTimer = undefined
      void refreshLogs()
    }, wait)
  }

  return () => {
    disposed = true
    refreshQueued = false
    clearTimeout(refreshTimer)
    controller.abort()
    unsubscribe()
  }
}

export type { BidLogRow, BidRequest, BidView, HistoryCleanupParams, HistoryCleanupResult } from './types'
