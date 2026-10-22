import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import type { HistoryCleanupParams, LoadView, Page, PaginationParams, SystemStatus } from './types'
import { validateCleanupCount } from '../utils/history-cleanup'
import { subscribeLoads } from './orders'
import { getSystemStatus } from './system'
import { confirmedShipments } from '../utils/shipments'

export async function getShipments(
  params: PaginationParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.shipments, params), { ...options, method: 'GET' })
}

export async function deleteOldestShipments(
  params: HistoryCleanupParams,
  options: RequestInit = {},
) {
  validateCleanupCount(params.count)
  return apiFetch(withQuery(endpoints.oldestShipments, params), { ...options, method: 'DELETE' })
}

export async function getAllShipments(options: RequestInit = {}): Promise<LoadView[]> {
  const shipments: LoadView[] = []
  let offset = 0
  while (true) {
    const response = await getShipments({ limit: 200, offset }, options)
    if (!response.ok) throw new Error(`Unable to load shipments (HTTP ${response.status}).`)
    const page: Page<LoadView> = await response.json()
    if (!Array.isArray(page.items) || !Number.isFinite(page.total)) throw new Error('Invalid shipment response.')
    shipments.push(...page.items)
    offset += page.items.length
    if (page.items.length === 0 || offset >= page.total) break
  }
  return [...new Map(shipments.map((shipment) => [shipment.external_id, shipment])).values()]
}

export interface LiveShipmentsState {
  shipments: LoadView[] | null
  system: SystemStatus | null
  connection: 'connecting' | 'live' | 'reconnecting'
  error: string | null
  revision: number
}

export function subscribeShipments(onChange: (state: LiveShipmentsState) => void): () => void {
  const controller = new AbortController()
  let disposed = false
  let receivedSnapshot = false
  let shipments: LoadView[] | null = null
  let liveSystem: SystemStatus | null = null
  let restSystem: SystemStatus | null = null
  let connection: LiveShipmentsState['connection'] = 'connecting'
  let streamError: string | null = null
  let requestError: string | null = null
  let revision = 0

  function publish() {
    if (!disposed) onChange({ shipments, system: liveSystem ?? restSystem, connection, revision, error: streamError ?? requestError })
  }

  // Populate even while connecting. A later REST response cannot overwrite live data.
  void getAllShipments({ signal: controller.signal }).then((items) => {
    if (disposed || receivedSnapshot) return
    shipments = confirmedShipments(items)
    revision += 1
    publish()
  }).catch((cause: unknown) => {
    if (disposed || receivedSnapshot) return
    requestError = cause instanceof Error ? cause.message : 'Unable to load shipments.'
    publish()
  })

  // The simulation clock also comes from REST, so trip progress is available
  // immediately instead of waiting for the socket to finish connecting.
  void getSystemStatus({ signal: controller.signal }).then(async (response) => {
    if (!response.ok) return
    const status = await response.json() as SystemStatus
    if (disposed) return
    restSystem = status
    publish()
  }).catch(() => { /* the live stream will supply the clock once it connects */ })

  const unsubscribe = subscribeLoads({ order_type: 'all', status: 'live' }, (state) => {
    if (disposed) return
    connection = state.connection
    streamError = state.error
    // Keep the newest clock from any tick, not just live ones: progress bars
    // depend on it, and a null clock makes every trip look unscheduled.
    if (state.system) liveSystem = state.system
    if (state.loads !== null) {
      receivedSnapshot = true
      shipments = confirmedShipments(state.loads)
      requestError = null
      revision += 1
    }
    publish()
  })

  return () => {
    disposed = true
    controller.abort()
    unsubscribe()
  }
}

export type { AssignmentView, HistoryCleanupParams, HistoryCleanupResult, LoadDetail, LoadView } from './types'
