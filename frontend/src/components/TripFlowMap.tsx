import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { Pause, Play, Truck, X } from 'lucide-react'
import type { LoadView, TerminalStatus } from '../api/types'
import { Globe, GlobeSvg } from '@/components/ui/globe'
import { Button } from '@/components/ui/button'
import { layoutTerminalMarkers, terminalGlobeBounds, terminalGlobePoint, terminalMapTone } from '@/utils/terminal-map'
import {
  buildTrips, demoTrips, tripLane, tripPhaseLabel, tripPhaseTone, tripPointAt,
  type TripFlowMode, type TripLane,
} from '@/utils/trip-flow'

const toneClasses = { available: 'text-success', unavailable: 'text-base-content/50', attention: 'text-warning', unknown: 'text-info' }

const DEMO_CYCLE_MS = 60_000
/** Preview refresh rate. 60 fps re-rendered the whole dashboard every frame. */
const DEMO_FRAME_MS = 120
/** Live marker refresh rate. Faster than this is not visible at 10× speed. */
const LIVE_PROGRESS_MS = 400

function count(value: number | null) {
  return value === null ? 'Unknown' : value.toLocaleString()
}

function clockLabel(value: string | null) {
  if (!value) return 'Not scheduled'
  const parsed = Date.parse(value)
  return Number.isFinite(parsed)
    ? new Date(parsed).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Not scheduled'
}

function money(value: number | null) {
  return value === null ? null : `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

type Props = {
  terminals: TerminalStatus[] | null
  loads: LoadView[] | null
  simTimeUtc?: string | null
  simulationSpeed?: string | null
  mode?: TripFlowMode
  title?: string
}

/**
 * Live trip-flow view: fixed terminal anchors, animated shipment lanes, and a
 * hoverable trip readout. All motion is skipped for prefers-reduced-motion.
 */
export default function TripFlowMap({ terminals, loads, simTimeUtc = null, simulationSpeed = null, mode = 'auto', title = 'Trip flow' }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 1200, height: 640 })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [playing, setPlaying] = useState(true)
  const [reducedMotion, setReducedMotion] = useState(false)
  const [demoProgress, setDemoProgress] = useState(0)

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReducedMotion(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  const liveTrips = useMemo(() => buildTrips(loads ?? [], terminals ?? [], simTimeUtc), [loads, terminals, simTimeUtc])
  const usingDemo = mode === 'auto' && liveTrips.length === 0
  const demoTemplates = useMemo(() => (usingDemo ? demoTrips(terminals ?? []) : []), [usingDemo, terminals])

  // The size box only needs to change when the panel is resized.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
        setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
      }
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const animating = playing && !reducedMotion
  useEffect(() => {
    if (!animating) return
    let frame = 0
    let last = 0
    const tick = (now: number) => {
      // Repainting the whole map on every animation frame made the dashboard
      // stutter, so the preview advances at a fixed, cheap rate.
      if (now - last >= DEMO_FRAME_MS) {
        last = now
        setDemoProgress(now / DEMO_CYCLE_MS)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [animating])

  const trips = useMemo(() => {
    if (usingDemo) {
      return demoTemplates.map((trip) => ({ ...trip, progress: ((trip.progress ?? 0) + demoProgress) % 1 }))
    }
    return liveTrips.map((trip) => ({ ...trip, progress: trip.progress ?? null }))
  }, [usingDemo, demoTemplates, demoProgress, liveTrips])

  const speed = simulationSpeed ? Number(simulationSpeed) : null

  // Rolling trips advance from the simulation clock; the demo loop uses its own clock.
  const [liveProgress, setLiveProgress] = useState<Record<string, number>>({})
  useEffect(() => {
    if (usingDemo || liveTrips.length === 0) return
    const measure = () => {
      const base = simTimeUtc ? Date.parse(simTimeUtc) : Date.now()
      const now = Number.isFinite(base) ? base : Date.now()
      const next: Record<string, number> = {}
      for (const trip of liveTrips) {
        const pickup = trip.pickupAt ? Date.parse(trip.pickupAt) : NaN
        const delivery = trip.deliveryAt ? Date.parse(trip.deliveryAt) : NaN
        if (!Number.isFinite(pickup) || !Number.isFinite(delivery) || delivery <= pickup) continue
        next[trip.id] = Math.min(1, Math.max(0, (now - pickup) / (delivery - pickup)))
      }
      setLiveProgress(next)
    }
    measure()
    // Repaint the markers a few times a second. Any faster and the dashboard
    // spends its time in layout instead of responding to input.
    const interval = setInterval(measure, reducedMotion || !playing ? 2000 : LIVE_PROGRESS_MS)
    return () => clearInterval(interval)
  }, [usingDemo, liveTrips, simTimeUtc, reducedMotion, playing])

  const placedTrips = useMemo(
    () => trips.map((trip) => ({ ...trip, progress: usingDemo ? trip.progress : liveProgress[trip.id] ?? null })),
    [trips, usingDemo, liveProgress],
  )
  const rolling = placedTrips.filter((trip) => trip.phase === 'rolling')
  const active = rolling.filter((trip) => (trip.progress ?? 0) < 1)

  const mapped = useMemo(() => (terminals ?? []).flatMap((terminal) => {
    const point = terminalGlobePoint(terminal)
    return point ? [{ id: terminal.terminal_id, ...point }] : []
  }).sort((a, b) => a.id.localeCompare(b.id)), [terminals])

  const { bounds, placed, overflow } = useMemo(() => {
    const bounds = terminalGlobeBounds(mapped, size.width / size.height)
    return { bounds, ...layoutTerminalMarkers(mapped, bounds, size) }
  }, [mapped, size])

  const unit = Math.max(bounds.width / size.width, bounds.height / size.height)
  const terminalsById = new Map((terminals ?? []).map((terminal) => [terminal.terminal_id, terminal]))
  const placedById = new Map(placed.map((marker) => [marker.id, marker]))
  const tripById = new Map(placedTrips.map((trip) => [trip.id, trip]))
  const selected = selectedId ? tripById.get(selectedId) : undefined
  const detail = hoveredId ? tripById.get(hoveredId) : selected

  const lanes = useMemo(() => {
    // One arc per origin→destination pair. Several shipments share a lane and
    // ride on it, which keeps the map readable on a busy board.
    const grouped = new Map<string, { lane: TripLane; trips: typeof placedTrips }>()
    for (const trip of placedTrips) {
      const lane = tripLane(trip)
      if (!lane) continue
      const entry = grouped.get(lane.key)
      if (entry) entry.trips.push(trip)
      else grouped.set(lane.key, { lane, trips: [trip] })
    }
    return [...grouped.values()].map(({ lane, trips }) => ({
      lane,
      trips,
      rolling: trips.some((trip) => trip.phase === 'rolling'),
      markers: trips.flatMap((trip) => {
        if (trip.phase !== 'rolling') return []
        const point = tripPointAt(trip, trip.progress ?? 0)
        return point ? [{ trip, x: point.x, y: point.y }] : []
      }),
    }))
  }, [placedTrips])

  function terminalButton(terminal: TerminalStatus) {
    const marker = placedById.get(terminal.terminal_id)
    const traffic = rolling.filter((trip) => trip.origin.code === terminal.code || trip.destination.code === terminal.code).length
    return <button
      key={terminal.terminal_id}
      type="button"
      className={`terminal-globe-marker ${toneClasses[terminalMapTone(terminal)]}${traffic ? ' terminal-globe-marker--busy' : ''}`}
      aria-label={`${terminal.code}, ${terminal.city}. ${count(terminal.available_units)} available trucks. ${traffic} moving ${traffic === 1 ? 'trip' : 'trips'}. View capacity.`}
      aria-pressed={false}
      title={`${terminal.code} · ${count(terminal.available_units)} available${traffic ? ` · ${traffic} moving` : ''}${marker?.displaced ? ' · label offset for clarity' : ''}`}
      onClick={() => {
        const first = placedTrips.find((trip) => trip.origin.code === terminal.code || trip.destination.code === terminal.code)
        if (first) setSelectedId(first.id)
      }}
    >
      <span className="terminal-globe-code" title={terminal.code}>{terminal.code}</span>
      {traffic > 0 && <span aria-hidden="true" className="terminal-globe-traffic">{traffic}</span>}
    </button>
  }

  const summary = [
    { label: 'Moving now', value: active.length.toLocaleString() },
    { label: 'Scheduled', value: placedTrips.filter((trip) => trip.phase === 'scheduled').length.toLocaleString() },
    { label: 'Active lanes', value: lanes.length.toLocaleString() },
    { label: 'Sim speed', value: speed && Number.isFinite(speed) ? `${speed.toLocaleString()}×` : '—' },
  ]

  return <div>
    <Globe ref={containerRef} role="region" aria-label={`${title}. ${active.length} trips moving between ${terminals?.length ?? 0} terminals.`} className="isolate h-[460px] overflow-hidden bg-base-100 sm:h-[560px] lg:h-[640px]">
      <GlobeSvg viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`} textureScale={unit} className="relative" aria-label="Geographic terminal locations and active shipment lanes">
        <defs>
          <linearGradient id="trip-flow-lane" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--color-warning)" stopOpacity="0.15" />
            <stop offset="100%" stopColor="var(--color-primary)" stopOpacity="0.75" />
          </linearGradient>
        </defs>
        <g aria-hidden="true" pointerEvents="none">
          <g className="trip-flow-lanes">
            {lanes.map(({ lane, rolling, markers }) => {
              const ox = lane.origin.x as number
              const oy = lane.origin.y as number
              const dx = lane.destination.x as number
              const dy = lane.destination.y as number
              const selected = markers.some((marker) => marker.trip.id === selectedId)
              return <g key={lane.key}>
                <path
                  d={`M ${ox} ${oy} Q ${lane.control.x} ${lane.control.y} ${dx} ${dy}`}
                  className={`trip-flow-lane ${rolling ? 'is-rolling' : 'is-scheduled'}`}
                  vectorEffect="non-scaling-stroke"
                />
                {rolling && <circle cx={dx} cy={dy} r={4.5 * unit} className={`trip-flow-head ${selected ? 'is-selected' : ''}`} vectorEffect="non-scaling-stroke" />}
                {markers.map((marker) => <circle
                  key={marker.trip.id}
                  cx={marker.x}
                  cy={marker.y}
                  r={5 * unit}
                  className={`trip-flow-shipment ${marker.trip.id === selectedId ? 'is-selected' : ''}`}
                  vectorEffect="non-scaling-stroke"
                />)}
              </g>
            })}
          </g>
          {placed.filter((marker) => marker.displaced).map((marker) => <line key={marker.id} x1={(marker as { x: number }).x} y1={(marker as { y: number }).y} x2={marker.labelX} y2={marker.labelY} className="terminal-globe-connector" vectorEffect="non-scaling-stroke" />)}
          {mapped.map((point) => {
            const terminal = terminalsById.get(point.id)
            return terminal && <circle key={point.id} cx={point.x} cy={point.y} r={5 * unit} className={`terminal-globe-location ${toneClasses[terminalMapTone(terminal)]}`} vectorEffect="non-scaling-stroke" />
          })}
        </g>
      </GlobeSvg>
      <div className="terminal-globe-overlay">{placed.map((marker) => {
        const terminal = terminalsById.get(marker.id)
        return terminal && <div key={marker.id} className="terminal-globe-position" style={{ left: marker.screenX, top: marker.screenY, width: 88, height: 46 }}>{terminalButton(terminal)}</div>
      })}</div>

      <div className="pointer-events-none absolute inset-x-0 top-0 flex flex-wrap items-start justify-between gap-3 p-4 sm:p-5">
        <div className="pointer-events-auto flex flex-wrap items-center gap-2">
          <p className="trip-flow-pill">{active.length} moving · {placedTrips.length} trips</p>
          {usingDemo && <p className="trip-flow-pill trip-flow-pill--demo">Preview flow</p>}
        </div>
        <div className="pointer-events-auto flex items-center gap-2">
          <button type="button" className="trip-flow-control" aria-pressed={playing} aria-label={playing ? 'Pause trip animation' : 'Play trip animation'} onClick={() => setPlaying((value) => !value)}>
            {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
            <span className="hidden sm:inline">{playing ? 'Pause' : 'Play'}</span>
          </button>
        </div>
      </div>

      {(terminals === null || mapped.length === 0) && <p role="status" className="pointer-events-none absolute inset-x-4 top-20 mx-auto max-w-sm rounded-lg border border-base-300 bg-base-100/95 p-4 text-center text-sm shadow-sm">{terminals === null ? 'Loading terminals…' : terminals.length === 0 ? 'No terminals synced.' : 'Coordinates unavailable.'}</p>}

      {detail && <div className="absolute inset-x-4 bottom-4 mx-auto max-w-md rounded-xl border border-base-300 bg-base-100/95 p-4 shadow-lg backdrop-blur sm:inset-x-auto sm:right-5 sm:left-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold"><Truck size={15} aria-hidden="true" className="text-primary" /><span className="truncate">{detail.label}</span></p>
            <p className="mt-1 text-xs text-base-content/60">{detail.unitNumber ?? 'Unit pending board assignment'}</p>
          </div>
          <Button variant="ghost" size="icon" className="size-7 shrink-0" aria-label="Close trip detail" onClick={() => { setSelectedId(null); setHoveredId(null) }}><X size={14} /></Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
          <span className={`badge badge-sm badge-soft ${tripPhaseTone(detail.phase)}`}>{tripPhaseLabel(detail, detail.progress)}</span>
          {detail.progress !== null && <span className="trip-flow-pill">{(detail.progress * 100).toFixed(0)}% complete</span>}
          {money(detail.payout) && <span className="trip-flow-pill">{money(detail.payout)}</span>}
          {!detail.confirmed && <span className="trip-flow-pill">Estimate</span>}
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-[11px]">
          <div><dt className="text-base-content/55">Pickup</dt><dd className="mt-0.5 font-medium">{clockLabel(detail.pickupAt)}</dd></div>
          <div><dt className="text-base-content/55">Delivery</dt><dd className="mt-0.5 font-medium">{clockLabel(detail.deliveryAt)}</dd></div>
        </dl>
      </div>}
    </Globe>

    <div className="flex flex-wrap items-center justify-between gap-4 border-t border-base-300 px-5 py-3" role="group" aria-label="Trip flow summary">
      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        {summary.map((item) => <div key={item.label}><dt className="text-[10px] tracking-wide text-base-content/55 uppercase">{item.label}</dt><dd className="text-sm font-bold tabular-nums text-primary">{item.value}</dd></div>)}
      </dl>
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-base-content/55">
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="trip-flow-legend is-rolling" />In transit</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="trip-flow-legend is-scheduled" />Scheduled lane</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="size-2 rounded-full bg-success" />Available hub</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="size-2 rounded-full bg-warning" />Needs attention</span>
      </div>
    </div>

    {overflow.length > 0 && <div className="flex flex-wrap items-center gap-4 border-t border-base-300 px-5 py-3" role="group" aria-label="Additional terminals">
      <p className="text-xs text-base-content/60">More terminals</p>
      {overflow.map((marker) => {
        const terminal = terminalsById.get(marker.id)
        return terminal && <button key={terminal.terminal_id} type="button" className={`terminal-globe-code ${toneClasses[terminalMapTone(terminal)]}`} onClick={() => { const first = placedTrips.find((trip) => trip.origin.code === terminal.code || trip.destination.code === terminal.code); if (first) setSelectedId(first.id) }}>{terminal.code}</button>
      })}
    </div>}

    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-5 pt-3 pb-4 text-[11px] text-base-content/55">
      <Link to="/shipments" className="font-semibold text-primary hover:underline">Shipment details →</Link>
      <p>Positions are interpolated between hub coordinates; a moving marker is a shipment in progress, not a GPS feed.</p>
    </div>
  </div>
}
