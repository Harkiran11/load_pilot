import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { X } from 'lucide-react'
import type { TerminalStatus } from '@/api/terminals'
import { Globe, GlobeSvg } from '@/components/ui/globe'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/globe-utils/tooltip'
import { Button } from '@/components/ui/button'
import { layoutTerminalMarkers, TERMINAL_MARKER_WIDTH, TERMINAL_MARKER_HEIGHT, terminalGlobeBounds, terminalGlobePoint, terminalMapTone } from '@/utils/terminal-map'

const toneClasses = { available: 'text-success', unavailable: 'text-base-content/50', attention: 'text-warning', unknown: 'text-info' }

function count(value: number | null) {
  return value === null ? 'Unknown' : value.toLocaleString()
}

function CapacityDetails({ terminal }: { terminal: TerminalStatus }) {
  return <>
    <p className="font-semibold">{terminal.code} · {terminal.name}</p>
    <p className="mt-0.5 text-xs text-muted-foreground">{[terminal.city, terminal.state].filter(Boolean).join(', ') || 'Location unavailable'}</p>
    <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
      {([
        ['Available', terminal.available_units], ['Assigned', terminal.assigned_units],
        ['Fleet', terminal.total_units], ['Board available', terminal.board_available_units],
      ] as const).map(([label, value]) => <div key={label} className="flex flex-col gap-0.5"><dt className="text-muted-foreground">{label}</dt><dd className="font-semibold tabular-nums">{count(value)}</dd></div>)}
    </dl>
    {terminalMapTone(terminal) === 'attention' && <p className="mt-3 text-xs">Needs attention.</p>}
  </>
}

export default function TerminalCapacityMap({ terminals }: { terminals: TerminalStatus[] | null }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 1200, height: 640 })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const mapped = useMemo(() => (terminals ?? []).flatMap((terminal) => {
    const point = terminalGlobePoint(terminal)
    return point ? [{ id: terminal.terminal_id, ...point }] : []
  }).sort((a, b) => a.id.localeCompare(b.id)), [terminals])
  const unmapped = (terminals ?? []).filter((terminal) => terminalGlobePoint(terminal) === null)
  const selected = terminals?.find((terminal) => terminal.terminal_id === selectedId)
  const { bounds, placed, overflow } = useMemo(() => {
    const bounds = terminalGlobeBounds(mapped, size.width / size.height)
    return { bounds, ...layoutTerminalMarkers(mapped, bounds, size) }
  }, [mapped, size])
  const unit = Math.max(bounds.width / size.width, bounds.height / size.height)
  const terminalsById = new Map((terminals ?? []).map((terminal) => [terminal.terminal_id, terminal]))

  function terminalButton(terminal: TerminalStatus) {
    return <Tooltip key={terminal.terminal_id}>
      <TooltipTrigger render={
        <button type="button" className={`terminal-globe-marker ${toneClasses[terminalMapTone(terminal)]}`} aria-label={`${terminal.code}, ${terminal.city}. ${count(terminal.available_units)} available trucks. View capacity.`} aria-pressed={selectedId === terminal.terminal_id} onClick={() => setSelectedId(terminal.terminal_id)}>
          <span className="terminal-globe-code" title={terminal.code}>{terminal.code}</span>
        </button>
      } />
      <TooltipContent side="top" className="max-w-xs p-4"><CapacityDetails terminal={terminal} /></TooltipContent>
    </Tooltip>
  }

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

  return <TooltipProvider delayDuration={150}>
    <div>
      <Globe ref={containerRef} role="region" aria-label="Terminal capacity map. Focus or select a terminal code to see truck availability." className="isolate aspect-auto h-[460px] overflow-hidden bg-base-100 sm:h-[560px] lg:h-[640px]">
        <GlobeSvg viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`} textureScale={unit} className="relative" aria-label="Geographic terminal locations">
          <g aria-hidden="true" pointerEvents="none">
            {placed.filter((marker) => marker.displaced).map((marker) => <line key={marker.id} x1={marker.x} y1={marker.y} x2={marker.labelX} y2={marker.labelY} className="terminal-globe-connector" vectorEffect="non-scaling-stroke" />)}
            {mapped.map((point) => {
              const terminal = terminalsById.get(point.id)
              return terminal && <circle key={point.id} cx={point.x} cy={point.y} r={5 * unit} className={`terminal-globe-location ${toneClasses[terminalMapTone(terminal)]}`} vectorEffect="non-scaling-stroke" />
            })}
          </g>
        </GlobeSvg>
        <div className="terminal-globe-overlay">{placed.map((marker) => {
          const terminal = terminalsById.get(marker.id)
          return terminal && <div key={marker.id} className="terminal-globe-position" style={{ left: marker.screenX, top: marker.screenY, width: TERMINAL_MARKER_WIDTH, height: TERMINAL_MARKER_HEIGHT }}>{terminalButton(terminal)}</div>
        })}</div>
        {(terminals === null || mapped.length === 0) && <p role="status" className="pointer-events-none absolute inset-x-4 top-8 mx-auto max-w-sm rounded-lg border border-base-300 bg-base-100/95 p-4 text-center text-sm shadow-sm">{terminals === null ? 'Loading terminals…' : terminals.length === 0 ? 'No terminals synced.' : 'Coordinates unavailable.'}</p>}
      </Globe>
      {overflow.length > 0 && <div className="flex flex-wrap items-center gap-4 px-5 py-3" role="group" aria-label="Additional terminal capacity">
        <p className="text-xs text-base-content/60">More terminals</p>
        {overflow.map((marker) => {
          const terminal = terminalsById.get(marker.id)
          return terminal && terminalButton(terminal)
        })}
      </div>}
      {selected && <div className="flex items-start justify-between gap-4 border-t border-base-300 bg-base-200/40 px-5 py-4" role="region" aria-label={`${selected.code} capacity`}>
        <div className="min-w-0"><CapacityDetails terminal={selected} /><Link className="mt-3 inline-block text-xs font-semibold text-primary hover:underline" to="/terminals">Fleet schedules →</Link></div>
        <Button variant="ghost" size="icon" className="size-8 shrink-0" aria-label="Close terminal capacity" onClick={() => setSelectedId(null)}><X size={16} /></Button>
      </div>}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-4 text-[11px] text-base-content/55">
        <div className="flex flex-wrap gap-x-4 gap-y-2">{[
          ['bg-success', 'Available'], ['bg-base-content/50', 'Unavailable'],
          ['bg-warning', 'Attention'], ['bg-info', 'Unknown'],
        ].map(([color, label]) => <span key={label} className="inline-flex items-center gap-1.5"><span aria-hidden="true" className={`size-2 rounded-full ${color}`} />{label}</span>)}</div>
      </div>
      {unmapped.length > 0 && <p className="border-t border-base-300 px-5 py-3 text-xs text-base-content/70">No coordinates: {unmapped.map((terminal) => terminal.code).join(', ')} · <Link className="text-primary underline" to="/terminals">View terminals</Link></p>}
    </div>
  </TooltipProvider>
}
