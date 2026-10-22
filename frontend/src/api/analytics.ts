import { subscribeLoads } from './orders'
import { getSystemStatus } from './system'
import { apiFetch } from './client'
import { endpoints } from './endpoints'
import type { AnalyticsSummary, SystemStatus } from '../api/types'

export interface AnalyticsState {
  system: SystemStatus | null
  updating: boolean
  connection: 'connecting' | 'live' | 'reconnecting'
  error: string | null
  updatedAt: string | null
}

export const initialAnalyticsState: AnalyticsState = {
  system: null, updating: false, connection: 'connecting', error: null, updatedAt: null,
}

/** Minimum gap between two summary refreshes. */
export const SUMMARY_REFRESH_MS = 30_000

export type AnalyticsSummaryQuery = {
  start: string
  end: string
  basis: string
  type: string
}

/**
 * The analytics figures for one date range, aggregated by the backend.
 *
 * The page used to download the whole retained order history and derive the
 * numbers in the browser. The aggregation now happens in the database, so the
 * page reads a couple of kilobytes of totals instead of megabytes of orders.
 */
export async function getAnalyticsSummary(
  query: AnalyticsSummaryQuery,
  options: RequestInit = {},
): Promise<AnalyticsSummary> {
  const params = new URLSearchParams(query).toString()
  const response = await apiFetch(`${endpoints.analytics.summary}?${params}`, { ...options, method: 'GET' })
  if (!response.ok) throw new Error(`Analytics could not be loaded (HTTP ${response.status}).`)
  const body = await response.json()
  if (!body || typeof body !== 'object' || !Array.isArray((body as AnalyticsSummary).days)) {
    throw new Error('Analytics returned an invalid response.')
  }
  return body as AnalyticsSummary
}

/**
 * Tracks the simulation clock and the board connection for the analytics page.
 *
 * The figures themselves are fetched by the page for the selected range, so
 * this subscription only carries the clock and the connection state.
 */
export function subscribeAnalytics(onChange: (state: AnalyticsState) => void) {
  const controller = new AbortController()
  const state = { ...initialAnalyticsState }
  let disposed = false
  let streamError: string | null = null
  let requestError: string | null = null
  const publish = () => { if (!disposed) onChange({ ...state, error: requestError ?? streamError }) }

  async function refreshClock() {
    if (disposed) return
    try {
      const response = await getSystemStatus({ signal: controller.signal })
      if (!response.ok) throw new Error('Simulation time could not be refreshed.')
      const system: SystemStatus = await response.json()
      if (disposed) return
      state.system = system
      requestError = null
      state.updatedAt = new Date().toISOString()
    } catch (cause) {
      if (disposed) return
      requestError = cause instanceof Error ? cause.message : 'Unable to refresh the simulation clock.'
    } finally {
      if (!disposed) publish()
    }
  }

  void refreshClock()
  const stop = subscribeLoads({ order_type: 'all', status: 'all', loads: false }, update => {
    if (disposed) return
    state.connection = update.connection
    streamError = update.error
    if (update.system) { state.system = update.system; state.updatedAt = new Date().toISOString() }
    publish()
  })
  const poll = setInterval(() => { void refreshClock() }, 60_000)
  return {
    refresh: () => { void refreshClock() },
    unsubscribe: () => {
      disposed = true
      controller.abort()
      clearInterval(poll)
      stop()
    },
  }
}

export type { AnalyticsSummary, AnalyticsDay, AnalyticsLane } from '../api/types'
