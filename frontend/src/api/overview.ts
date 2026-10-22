import { apiFetch } from './client'
import { endpoints } from './endpoints'
import { getSystemStatus } from './system'
import { subscribeLoads } from './orders'
import { subscribeTerminalStatuses } from './terminals'
import type { Overview, LoadView, SystemStatus, TerminalStatus } from './types'

export async function getOverview(
  options: RequestInit = {},
) {
  return apiFetch(endpoints.overview, { ...options, method: 'GET' })
}

export type { Overview, ProfitSummary } from './types'

export interface LiveOverviewState {
  overview: Overview | null
  loads: LoadView[] | null
  terminals: TerminalStatus[] | null
  system: SystemStatus | null
  connection: 'connecting' | 'live' | 'reconnecting'
  updating: boolean
  error: string | null
  updatedAt: string | null
}

export function subscribeOverview(onChange: (state: LiveOverviewState) => void) {
  const controller = new AbortController()
  let disposed = false
  let updating = false
  let queued = false
  let overview: Overview | null = null
  let loads: LoadView[] | null = null
  let terminals: TerminalStatus[] | null = null
  let liveSystem: SystemStatus | null = null
  let restSystem: SystemStatus | null = null
  let requestError: string | null = null
  let updatedAt: string | null = null
  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const streams = {
    orders: { connection: 'connecting', error: null },
    terminals: { connection: 'connecting', error: null },
  } as Record<'orders' | 'terminals', { connection: LiveOverviewState['connection']; error: string | null }>

  function publish() {
    if (disposed) return
    const values = Object.values(streams)
    const connection = values.every((stream) => stream.connection === 'live') ? 'live'
      : values.some((stream) => stream.connection === 'reconnecting') ? 'reconnecting' : 'connecting'
    onChange({ overview, loads, terminals, system: liveSystem ?? restSystem, connection, updating,
      error: requestError ?? values.find((stream) => stream.error)?.error ?? null, updatedAt })
  }

  async function refresh() {
    if (disposed) return
    clearTimeout(refreshTimer)
    refreshTimer = undefined
    if (updating) { queued = true; return }
    updating = true
    publish()
    const results = await Promise.allSettled([
      getOverview({ signal: controller.signal }).then(async (response) => {
        if (!response.ok) throw new Error('Dashboard totals could not be refreshed. Please try again.')
        const data: Overview = await response.json()
        if (!data.counts || !data.profit || ![data.loads, data.bids, data.terminals, data.fleet, data.shipments, data.activity].every(Array.isArray)) throw new Error('The dashboard response could not be verified.')
        return data
      }),
      getSystemStatus({ signal: controller.signal }).then(async (response) => {
        if (!response.ok) throw new Error('Simulation status is temporarily unavailable.')
        return await response.json() as SystemStatus
      }),
    ])
    if (disposed) return
    const [summary, status] = results
    if (summary.status === 'fulfilled') { overview = summary.value; updatedAt = new Date().toISOString(); requestError = null }
    else requestError = summary.reason instanceof Error && !(summary.reason instanceof TypeError) ? summary.reason.message : 'Unable to connect to the dashboard. Check the backend connection and retry.'
    if (status.status === 'fulfilled') restSystem = status.value
    else if (!liveSystem && !requestError) requestError = 'Simulation status is temporarily unavailable. Dashboard totals are still shown.'
    updating = false
    publish()
    if (queued) { queued = false; scheduleRefresh() }
  }

  function scheduleRefresh() {
    if (disposed || refreshTimer) return
    refreshTimer = setTimeout(() => { refreshTimer = undefined; void refresh() }, 300)
  }

  const closeLoads = subscribeLoads({ order_type: 'all', status: 'live' }, (state) => {
    streams.orders = { connection: state.connection, error: state.error }
    if (state.loads !== null) loads = state.loads
    if (state.system) liveSystem = state.system
    publish()
    if (state.connection === 'live') scheduleRefresh()
  })
  const closeTerminals = subscribeTerminalStatuses((state) => {
    streams.terminals = { connection: state.connection, error: state.error }
    if (state.terminals !== null) terminals = state.terminals
    publish()
    if (state.connection === 'live') scheduleRefresh()
  })
  void refresh()
  // Keep REST summaries available even while a WebSocket is reconnecting.
  const poll = setInterval(() => { void refresh() }, 30_000)
  const onSimulationChange = () => { void refresh() }
  window.addEventListener('simulation-speed-updated', onSimulationChange)
  window.addEventListener('history-cleaned', onSimulationChange)
  return {
    refresh: () => { void refresh() },
    unsubscribe: () => {
      disposed = true
      controller.abort()
      clearTimeout(refreshTimer)
      clearInterval(poll)
      window.removeEventListener('simulation-speed-updated', onSimulationChange)
      window.removeEventListener('history-cleaned', onSimulationChange)
      closeLoads()
      closeTerminals()
    },
  }
}
