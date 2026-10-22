import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import type { ActivityView, HistoryCleanupParams, Page, PaginationParams } from './types'
import { validateCleanupCount } from '../utils/history-cleanup'
import { subscribeLoads } from './orders'
import { subscribeTerminalStatuses } from './terminals'

export interface ActivityParams extends PaginationParams {
  external_id?: string
  event_type?: string
}

export interface ActivityCleanupParams extends HistoryCleanupParams {
  external_id?: string
  event_type?: string
}

export async function deleteOldestActivity(
  params: ActivityCleanupParams,
  options: RequestInit = {},
) {
  validateCleanupCount(params.count)
  return apiFetch(withQuery(endpoints.oldestActivity, params), { ...options, method: 'DELETE' })
}

export async function getActivity(
  params: ActivityParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.activity, params), { ...options, method: 'GET' })
}

export interface LiveActivityState {
  data: Page<ActivityView> | null
  connection: 'connecting' | 'live' | 'reconnecting'
  updating: boolean
  error: string | null
}

// Read only the requested page; event bursts queue one follow-up read.
export function subscribeActivity(
  params: ActivityParams,
  onChange: (state: LiveActivityState) => void,
): () => void {
  const controller = new AbortController()
  let disposed = false
  let updating = false
  let queued = false
  let data: Page<ActivityView> | null = null
  let requestError: string | null = null
  const streams = {
    terminals: { connection: 'connecting', error: null },
    orders: { connection: 'connecting', error: null },
  } as Record<'terminals' | 'orders', { connection: LiveActivityState['connection']; error: string | null }>

  function publish() {
    const values = Object.values(streams)
    const connection = values.every((stream) => stream.connection === 'live') ? 'live'
      : values.some((stream) => stream.connection === 'reconnecting') ? 'reconnecting' : 'connecting'
    if (!disposed) onChange({ data, updating, connection, error: requestError ?? values.find((stream) => stream.error)?.error ?? null })
  }

  async function refresh() {
    if (disposed) return
    if (updating) { queued = true; return }
    updating = true
    publish()
    try {
      const response = await getActivity(params, { signal: controller.signal })
      if (!response.ok) throw new Error(`Unable to load activity (HTTP ${response.status}).`)
      const result: Page<ActivityView> = await response.json()
      if (!Array.isArray(result.items) || !Number.isFinite(result.total)) throw new Error('Invalid activity response.')
      if (disposed) return
      data = result
      requestError = null
    } catch (cause) {
      if (!disposed) requestError = cause instanceof Error ? cause.message : 'Unable to load activity.'
    } finally {
      updating = false
      if (!disposed) {
        publish()
        if (queued) { queued = false; void refresh() }
      }
    }
  }

  void refresh()
  const stopTerminals = subscribeTerminalStatuses((state) => {
    streams.terminals = { connection: state.connection, error: state.error }
    if (state.connection === 'live') void refresh()
    else publish()
  })
  const stopOrders = subscribeLoads({ order_type: 'all', status: 'all', loads: false }, (state) => {
    streams.orders = { connection: state.connection, error: state.error }
    if (state.connection === 'live') void refresh()
    else publish()
  })

  return () => {
    disposed = true
    queued = false
    controller.abort()
    stopTerminals()
    stopOrders()
  }
}

export type { ActivityView, HistoryCleanupParams, HistoryCleanupResult } from './types'
