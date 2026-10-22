import type { LoadView, TerminalStatus } from '../api/types'
import { terminalGlobePoint } from './terminal-map'

export type TripPhase = 'loading' | 'scheduled' | 'rolling' | 'delivered'
export type TripFlowMode = 'auto' | 'live'

export type TripWaypoint = {
  code: string
  city: string | null
  state: string | null
  latitude: number | null
  longitude: number | null
  x: number | null
  y: number | null
  resolved: boolean
}

export type Trip = {
  id: string
  origin: TripWaypoint
  destination: TripWaypoint
  phase: TripPhase
  label: string
  unitNumber: string | null
  unitId: string | null
  progress: number | null
  pickupAt: string | null
  deliveryAt: string | null
  payout: number | null
  /** True only for a locally confirmed booking; board cost offers stay conditional. */
  confirmed: boolean
}

const TERMINAL_ALIASES: Record<string, string> = {
  'near los angeles': 'LAX',
  'los angeles': 'LAX',
  'near dallas': 'DFW',
  'dallas': 'DFW',
  'newark': 'EWR',
  'new york': 'EWR',
  'philadelphia': 'PHL',
  'chicago': 'CHI',
  'columbus': 'CMH',
  'denver': 'DEN',
  'jacksonville': 'JAX',
  'atlanta': 'ATL',
  'seattle': 'SEA',
}

function text(value: string | null | undefined) {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function number(value: string | null | undefined) {
  if (value === null || value === undefined || !String(value).trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function time(value: string | null | undefined) {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function phaseOf(load: LoadView): TripPhase {
  if (load.status === 'in_transit' || load.status === 'completed') return 'rolling'
  return 'scheduled'
}

/**
 * Progress for a rolling trip. The simulation clock is authoritative when it is
 * available; wall time is only a fallback so the map never renders a stalled dot.
 */
export function tripProgress(load: LoadView, simTimeUtc: string | null | undefined): number | null {
  const pickup = time(load.pickup_at)
  const delivery = time(load.delivery_at)
  if (pickup === null || delivery === null || delivery <= pickup) return null
  const now = time(simTimeUtc) ?? Date.now()
  if (now <= pickup) return 0
  if (now >= delivery) return 1
  return (now - pickup) / (delivery - pickup)
}

function waypoint(
  code: string,
  city: string | null,
  state: string | null,
  latitude: string | null,
  longitude: string | null,
): TripWaypoint {
  const position = latitude !== null && longitude !== null
    ? terminalGlobePoint({ latitude, longitude } as Pick<TerminalStatus, 'latitude' | 'longitude'>)
    : null
  return { code, city, state, latitude: number(latitude), longitude: number(longitude), x: position?.x ?? null, y: position?.y ?? null, resolved: position !== null }
}

function unitNumber(load: LoadView): string | null {
  const boardUnitId = load.board_assigned_unit_id
  return boardUnitId === null || boardUnitId === undefined ? null : `Unit ${boardUnitId}`
}

/** A delivery stays on the map briefly so an arrival is visible, then drops off. */
const COMPLETED_VISIBLE_MS = 30 * 60 * 1000

/**
 * Builds the trip overlay from already-loaded board data. Nothing here writes, and
 * trips are only shown when both endpoints resolve to real coordinates.
 *
 * The board keeps every delivered order forever, so the flow map only shows
 * current movement: booked, in transit, and deliveries from the last half hour.
 * Without that, a long session accumulates hundreds of finished lanes and the
 * map stops being readable.
 */
export function buildTrips(loads: LoadView[], terminals: TerminalStatus[], simTimeUtc?: string | null): Trip[] {
  const terminalCodes = new Set(terminals.map((terminal) => terminal.code))
  const codeForRoute = (code: string | null, city: string | null) => {
    // "Near Los Angeles, CA" style pickup labels only resolve through their city.
    const cleaned = city ? city.toLowerCase().replace(/^near\s+/, '').trim() : null
    const direct = code && terminalCodes.has(code) ? code : null
    if (direct) return direct
    if (cleaned) return TERMINAL_ALIASES[cleaned] ?? null
    if (city) return TERMINAL_ALIASES[city.toLowerCase()] ?? null
    return null
  }

  const trips: Trip[] = []
  const now = time(simTimeUtc) ?? Date.now()
  for (const load of loads) {
    if (load.status !== 'accepted' && load.status !== 'in_transit' && load.status !== 'completed') continue
    if (load.status === 'completed') {
      const delivered = time(load.delivery_at)
      if (delivered === null || now - delivered > COMPLETED_VISIBLE_MS) continue
    }
    const originCode = codeForRoute(text(load.origin_terminal_code), text(load.origin_city))
    const destinationCode = codeForRoute(text(load.destination_terminal_code), text(load.destination_city))
    if (!originCode || !destinationCode) continue
    const origin = waypoint(originCode, text(load.origin_city), text(load.origin_state), load.origin_latitude, load.origin_longitude)
    const destination = waypoint(destinationCode, text(load.destination_city), text(load.destination_state), load.destination_latitude, load.destination_longitude)
    if (!origin.resolved || !destination.resolved) continue
    trips.push({
      id: load.external_id,
      origin,
      destination,
      phase: phaseOf(load),
      label: `${originCode} → ${destinationCode}`,
      unitNumber: unitNumber(load),
      unitId: null,
      progress: null,
      pickupAt: load.pickup_at,
      deliveryAt: load.delivery_at,
      payout: load.order_type === 'open_bid' ? number(load.winning_bid_rate) : number(load.offered_rate),
      confirmed: load.board_missing_at === null,
    })
  }
  return trips.sort((left, right) => left.id.localeCompare(right.id))
}

/** A lane is one origin→destination pair. Several shipments can share it. */
export type TripLane = {
  key: string
  origin: TripWaypoint
  destination: TripWaypoint
  /** Quadratic control point; the lane is bowed so parallel routes stay readable. */
  control: { x: number; y: number }
}

/**
 * Geometry for a lane. Drawing one arc per origin→destination pair (rather
 * than one straight line per shipment) keeps a busy board readable: routes
 * that overlap are still distinguishable, and the map does not fill up with
 * near-identical lines.
 */
export function tripLane(trip: Trip): TripLane | null {
  if (trip.origin.x === null || trip.origin.y === null || trip.destination.x === null || trip.destination.y === null) return null
  const ox = trip.origin.x
  const oy = trip.origin.y
  const dx = trip.destination.x
  const dy = trip.destination.y
  const length = Math.hypot(dx - ox, dy - oy)
  if (!length) return null
  // Bow the lane perpendicular to its direction. The offset is proportional to
  // the lane length, so short hops stay tight and long hauls stay visible.
  const bow = length * 0.12
  const normalX = -(dy - oy) / length
  const normalY = (dx - ox) / length
  return {
    key: `${trip.origin.code}>${trip.destination.code}`,
    origin: trip.origin,
    destination: trip.destination,
    control: { x: (ox + dx) / 2 + normalX * bow, y: (oy + dy) / 2 + normalY * bow },
  }
}

/** Interpolates a trip position along its lane. */
export function tripPointAt(trip: Trip, progress: number) {
  const t = Math.min(1, Math.max(0, progress))
  const lane = tripLane(trip)
  if (!lane) return null
  const ox = lane.origin.x as number
  const oy = lane.origin.y as number
  const dx = lane.destination.x as number
  const dy = lane.destination.y as number
  const inverse = 1 - t
  return {
    x: inverse * inverse * ox + 2 * inverse * t * lane.control.x + t * t * dx,
    y: inverse * inverse * oy + 2 * inverse * t * lane.control.y + t * t * dy,
  }
}

export function tripPhaseLabel(trip: Trip, progress: number | null) {
  if (trip.phase === 'rolling') {
    if (progress === null) return 'In transit'
    if (progress >= 1) return 'Delivered'
    return `In transit · ${Math.round(progress * 100)}%`
  }
  return 'Awaiting pickup'
}

export function tripPhaseTone(phase: TripPhase): 'info' | 'success' | 'warning' {
  if (phase === 'rolling') return 'info'
  if (phase === 'delivered') return 'success'
  return 'warning'
}

/** Deterministic pseudo-random so demo mode looks alive without churn between renders. */
export function demoTrips(terminals: TerminalStatus[]): Trip[] {
  const byCode = new Map(terminals.map((terminal) => [terminal.code, terminal]))
  const routes: Array<[string, string, string, string, number]> = [
    ['LAX', 'DFW', 'LAX-U014', 'LD-DEMO-1140', 0.62],
    ['EWR', 'CHI', 'EWR-U022', 'LD-DEMO-2087', 0.28],
    ['ATL', 'SEA', 'ATL-U031', 'LD-DEMO-3311', 0.84],
    ['DFW', 'JAX', 'DFW-U007', 'LD-DEMO-4402', 0.11],
    ['CMH', 'DEN', 'CMH-U019', 'LD-DEMO-5528', 0.73],
    ['PHL', 'ATL', 'PHL-U025', 'LD-DEMO-6619', 0.46],
    ['SEA', 'LAX', 'SEA-U004', 'LD-DEMO-7733', 0.95],
    ['CHI', 'EWR', 'CHI-U012', 'LD-DEMO-8811', 0.37],
  ]
  return routes.flatMap(([originCode, destinationCode, unitNumber, id, seed]) => {
    const originTerminal = byCode.get(originCode)
    const destinationTerminal = byCode.get(destinationCode)
    if (!originTerminal || !destinationTerminal) return []
    const origin = waypoint(originCode, originTerminal.city, originTerminal.state, originTerminal.latitude, originTerminal.longitude)
    const destination = waypoint(destinationCode, destinationTerminal.city, destinationTerminal.state, destinationTerminal.latitude, destinationTerminal.longitude)
    if (!origin.resolved || !destination.resolved) return []
    return [{
      id,
      origin,
      destination,
      phase: 'rolling' as TripPhase,
      label: `${originCode} → ${destinationCode}`,
      unitNumber,
      unitId: null,
      progress: seed,
      pickupAt: null,
      deliveryAt: null,
      payout: null,
      confirmed: false,
    }]
  })
}
