import { useId, useState } from 'react'
import type { AnalyticsDay } from '../utils/analytics'
import { amount } from '../utils/load-format'

const dateLabel = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })

export default function AnalyticsTimeline({ days, kind }: { days: AnalyticsDay[]; kind: 'profit' | 'activity' }) {
  const id = useId()
  const [index, setIndex] = useState(days.length - 1)
  const selectedIndex = Math.max(0, Math.min(index, days.length - 1))
  const selected = days[selectedIndex]
  const financial = kind === 'profit'
  const left = 64, right = 876, top = 22, bottom = 240
  const values = days.flatMap(day => financial ? [day.profit, day.loss, day.net ?? 0] : [day.bids, day.opportunities])
  const minimum = financial ? Math.min(0, ...values) : 0
  const maximum = Math.max(1, ...values)
  const lower = minimum < 0 ? minimum * 1.12 : 0
  const upper = financial ? maximum * 1.12 : Math.max(1, Math.ceil(maximum * 1.12))
  const y = (value: number) => bottom - (value - lower) / (upper - lower) * (bottom - top)
  const slot = (right - left) / days.length
  const x = (position: number) => left + (position + 0.5) * slot
  const width = Math.min(18, slot * 0.28)
  const axis = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: financial ? 1 : 0, ...(financial ? { style: 'currency', currency: 'USD' } : {}) })
  let connected = false
  const path = financial ? days.map((day, i) => {
    if (day.net === null) { connected = false; return '' }
    const command = connected ? 'L' : 'M'
    connected = true
    return `${command}${x(i)},${y(day.net)}`
  }).join(' ') : ''
  const tickCount = Math.min(6, days.length)
  const ticks = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(i * (days.length - 1) / Math.max(1, tickCount - 1))))]
  const metrics = financial ? [
    { label: 'Profit', value: amount(String(selected.profit)), color: 'text-success' },
    { label: 'Loss', value: amount(String(selected.loss)), color: 'text-error' },
    { label: 'Net estimate', value: amount(selected.net === null ? null : String(selected.net)), color: 'text-primary' },
  ] : [
    { label: 'Bid requests', value: selected.bids.toLocaleString(), color: 'text-primary' },
    { label: 'New opportunities', value: selected.opportunities.toLocaleString(), color: 'text-info' },
  ]

  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-base-200/60 px-3 py-2" aria-live="polite" aria-atomic="true">
      <p className="text-xs font-medium">{dateLabel(selected.date)} <span className="font-normal text-base-content/50">UTC</span></p>
      <dl className="flex flex-wrap gap-x-5 gap-y-2">{metrics.map(metric => <div key={metric.label} className="text-xs"><dt className="text-[10px] text-base-content/60">{metric.label}</dt><dd className={`mt-0.5 font-semibold tabular-nums ${metric.color}`}>{metric.value}</dd></div>)}</dl>
    </div>
    <div className="overflow-x-auto">
      <svg viewBox="0 0 900 284" className="block w-full min-w-[520px] rounded focus-visible:outline-2 focus-visible:outline-primary" tabIndex={0} role="group" aria-labelledby={`${id}-title ${id}-description`} onKeyDown={event => {
        const next = event.key === 'ArrowLeft' ? selectedIndex - 1 : event.key === 'ArrowRight' ? selectedIndex + 1 : event.key === 'Home' ? 0 : event.key === 'End' ? days.length - 1 : null
        if (next !== null) { event.preventDefault(); setIndex(Math.max(0, Math.min(days.length - 1, next))) }
      }}>
        <title id={`${id}-title`}>{financial ? 'Daily estimated profit and loss' : 'Daily bid requests and new opportunities'}</title>
        <desc id={`${id}-description`}>Hover or select a day to view its values. Use left and right arrow keys to move between days. {financial ? 'Green bars show positive estimates; red bars show losses. Blue line shows net known estimates. Missing profit estimates are excluded.' : 'Blue bars show retained bid-request events on the simulation clock. Light blue bars show newly created orders.'}</desc>
        {[0, 0.25, 0.5, 0.75, 1].map(fraction => {
          const value = lower + (upper - lower) * fraction
          return <g key={fraction} aria-hidden="true"><line x1={left} x2={right} y1={y(value)} y2={y(value)} className="stroke-base-300/70" strokeDasharray="3 5" /><text x={left - 9} y={y(value) + 4} textAnchor="end" fontSize={10} className="fill-base-content/55">{financial ? axis.format(value) : Number.isInteger(value) ? axis.format(value) : ''}</text></g>
        })}
        <rect x={left + selectedIndex * slot} y={top} width={slot} height={bottom - top} className="fill-primary/5" />
        <line x1={left} x2={right} y1={y(0)} y2={y(0)} className="stroke-base-content/30" />
        {days.map((day, i) => {
          const first = financial ? day.profit : day.bids
          const second = financial ? day.loss : day.opportunities
          return <g key={day.date}>
            <title>{`${day.date}: ${financial ? `profit ${amount(String(day.profit))}, loss ${amount(String(day.loss))}, ${day.unknown} unknown` : `${day.bids} bid requests, ${day.opportunities} new opportunities`}`}</title>
            {first !== 0 && <rect data-analytics-bar={financial ? 'profit' : 'bids'} x={x(i) - width - 1} y={Math.min(y(0), y(first))} width={width} height={Math.abs(y(first) - y(0))} rx={Math.min(2, width / 2)} className={financial ? 'fill-success/80' : 'fill-primary/80'} />}
            {second !== 0 && <rect data-analytics-bar={financial ? 'loss' : 'opportunities'} x={x(i) + 1} y={Math.min(y(0), y(second))} width={width} height={Math.abs(y(second) - y(0))} rx={Math.min(2, width / 2)} className={financial ? 'fill-error/80' : 'fill-info/45'} />}
            {financial && day.unknown > 0 && <circle cx={x(i)} cy={bottom + 6} r={2} className="fill-warning" />}
          </g>
        })}
        {path && <path data-analytics-line="net" d={path} fill="none" className="stroke-primary" strokeWidth={2} strokeLinejoin="round" />}
        {financial && selected.net !== null && <circle cx={x(selectedIndex)} cy={y(selected.net)} r={4} className="fill-primary stroke-base-100" strokeWidth={2} />}
        {ticks.map(i => <text key={i} x={x(i)} y={bottom + 26} textAnchor="middle" fontSize={10} className="fill-base-content/55">{dateLabel(days[i].date)}</text>)}
        {days.map((day, i) => <rect key={day.date} x={left + i * slot} y={top} width={slot} height={bottom - top} fill="transparent" onPointerEnter={() => setIndex(i)} onPointerDown={() => setIndex(i)}><title>{day.date}</title></rect>)}
      </svg>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-base-content/60">
      <div className="flex flex-wrap gap-4">{(financial ? [['bg-success', 'Profit'], ['bg-error', 'Loss'], ['bg-primary', 'Net estimate']] : [['bg-primary', 'Bid requests'], ['bg-info/45', 'New opportunities']]).map(([color, name]) => <span key={name} className="flex items-center gap-1.5"><span className={`size-2 rounded-sm ${color}`} />{name}</span>)}</div>
      <span>{financial ? `${selected.shipments} shipments · ${selected.unknown.toLocaleString()} unknown estimates on selected day` : 'Includes requests that later failed or were rejected'}</span>
    </div>
  </div>
}
