import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import { getSystemStatus } from './system'
import type { Page, PaginationParams, SystemStatus, UnitProjection, UnitView } from './types'
import { subscribeLoads } from './orders'
import { subscribeTerminalStatuses } from './terminals'

export interface FleetParams extends PaginationParams {
  terminal?: string
  status?: string
  include_missing?: boolean
}

export interface CapacityParams {
  discrepancies_only?: boolean
}

export interface FleetScheduleParams extends PaginationParams {
  unit_id?: string
  terminal?: string
  starts_at?: string
  ends_at?: string
  include_history?: boolean
  include_missing?: boolean
}

export async function getFleetSchedules(
  params: FleetScheduleParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.schedules, params), { ...options, method: 'GET' })
}

export async function getFleetUnits(
  params: FleetParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.units, params), { ...options, method: 'GET' })
}

export async function getFleetUnit(
  unitId: string,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.fleet.unit(unitId), { ...options, method: 'GET' })
}

export async function getFleetCapacity(
  params: CapacityParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.capacity, params), { ...options, method: 'GET' })
}

export async function getLegacyFleetCapacity(
  params: CapacityParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.legacyCapacity, params), { ...options, method: 'GET' })
}

export async function getFleetProjections(
  params: { at?: string } = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.projections, params), { ...options, method: 'GET' })
}

export async function getUnitProjection(
  unitId: string,
  params: { at?: string } = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.fleet.projection(unitId), params), { ...options, method: 'GET' })
}

export interface FleetData {
  units: UnitView[]
  projections: UnitProjection[]
  system: SystemStatus
}

export async function getAllFleetUnits(options: RequestInit = {}): Promise<UnitView[]> {
  const units: UnitView[] = []
  let offset = 0
  while (true) {
    const response = await getFleetUnits({ limit: 200, offset }, options)
    if (!response.ok) throw new Error(`Unable to load fleet units (HTTP ${response.status}).`)
    const page: Page<UnitView> = await response.json()
    if (!Array.isArray(page.items) || !Number.isFinite(page.total)) throw new Error('Invalid fleet response.')
    units.push(...page.items)
    offset += page.items.length
    if (page.items.length === 0 || offset >= page.total) break
  }
  return [...new Map(units.map((unit) => [unit.unit_id, unit])).values()]
}

export async function getFleetData(options: RequestInit = {}): Promise<FleetData> {
  const [units, systemResponse] = await Promise.all([getAllFleetUnits(options), getSystemStatus(options)])
  if (!systemResponse.ok) throw new Error(`Unable to load simulation time (HTTP ${systemResponse.status}).`)
  const system: SystemStatus = await systemResponse.json()
  const response = await getFleetProjections({ at: system.sim_time_utc ?? undefined }, options)
  if (!response.ok) throw new Error(`Unable to load fleet schedules (HTTP ${response.status}).`)
  const projections: UnitProjection[] = await response.json()
  if (!Array.isArray(projections)) throw new Error('Invalid fleet schedule response.')
  return { units, system, projections }
}

export interface LiveFleetState {
  data: FleetData | null
  connection: 'connecting' | 'live' | 'reconnecting'
  updating: boolean
  error: string | null
  revision: number
}

export function subscribeFleet(onChange: (state: LiveFleetState) => void): () => void {
  const controller = new AbortController()
  let disposed = false
  let updating = false
  let queued = false
  let data: FleetData | null = null
  let requestError: string | null = null
  let revision = 0
  const streams = {
    terminals: { connection: 'connecting', error: null },
    orders: { connection: 'connecting', error: null },
  } as Record<'terminals' | 'orders', { connection: LiveFleetState['connection']; error: string | null }>

  function publish() {
    const values = Object.values(streams)
    const connection = values.every((stream) => stream.connection === 'live') ? 'live'
      : values.some((stream) => stream.connection === 'reconnecting') ? 'reconnecting' : 'connecting'
    if (!disposed) onChange({ data, updating, revision, connection, error: requestError ?? values.find((stream) => stream.error)?.error ?? null })
  }

  async function refresh() {
    if (disposed) return
    if (updating) { queued = true; return }
    updating = true
    publish()
    try {
      const result = await getFleetData({ signal: controller.signal })
      if (disposed) return
      data = result
      revision += 1
      requestError = null
    } catch (cause) {
      if (!disposed) requestError = cause instanceof Error ? cause.message : 'Unable to load fleet data.'
    } finally {
      updating = false
      if (!disposed) {
        publish()
        if (queued) { queued = false; void refresh() }
      }
    }
  }

  // Populate immediately, including when a WebSocket is temporarily unavailable.
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

export type { CapacityCounts, FleetCapacityResponse, ProjectedDelivery, ScheduledAssignment, ScheduledReservation, UnitSchedule, UnitDetail, UnitProjection, UnitView } from './types'
