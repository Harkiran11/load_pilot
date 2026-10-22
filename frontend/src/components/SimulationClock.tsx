import { useContext, useEffect, useState } from 'react'
import { getSimulationClock, getSystemStatus } from '../api/system'
import type { SimulationClock as BoardClock, SystemStatus } from '../api/system'
import { createClockSample, simulationTime } from '../utils/simulation-clock'
import type { ClockSample } from '../utils/simulation-clock'
import { PageStatusContext } from '../context/page-status'
import { headerStatus } from '../utils/header-status'

const dateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' })
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

export default function SimulationClock() {
  const pageStatus = useContext(PageStatusContext)?.status ?? null
  const [sample, setSample] = useState<ClockSample | null>(null)
  const [now, setNow] = useState(() => performance.now())
  const [syncError, setSyncError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [boardConnection, setBoardConnection] = useState<string | null>(null)
  const [statusError, setStatusError] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let syncing = false
    let queued = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function readSample(): Promise<ClockSample> {
      const [status, clock] = await Promise.allSettled([
        getSystemStatus({ signal: controller.signal }).then(async response => {
          if (!response.ok) throw new Error('System status unavailable')
          const system: SystemStatus = await response.json()
          return system
        }),
        import.meta.env.DEV ? getSimulationClock({ signal: controller.signal }).then(async response => {
          if (!response.ok) throw new Error('Simulator clock unavailable')
          return await response.json() as BoardClock
        }) : Promise.resolve(null),
      ])
      if (disposed) throw new Error('Clock read cancelled')
      const system = status.status === 'fulfilled' ? status.value : null
      setBoardConnection(system?.connection_status ?? null)
      setStatusError(!system)
      const simulator = clock.status === 'fulfilled' ? clock.value : null
      const value = (simulator && createClockSample(simulator.sim_time_utc, simulator.simulation_speed, performance.now()))
        || (system && createClockSample(system.sim_time_utc, system.simulation_speed, performance.now()))
      if (!value) throw new Error('Simulation clock has not synced yet')
      return value
    }

    async function sync() {
      if (disposed) return
      if (syncing) { queued = true; return }
      clearTimeout(timer)
      syncing = true
      try {
        const value = await readSample()
        if (!disposed) {
          setSample(value)
          setNow(performance.now())
          setSyncError(false)
        }
      } catch {
        if (!disposed) setSyncError(true)
      } finally {
        syncing = false
        if (!disposed) {
          setLoading(false)
          if (queued) { queued = false; void sync() }
          else timer = setTimeout(() => void sync(), 10_000)
        }
      }
    }

    const refresh = () => { void sync() }
    const onVisible = () => { if (document.visibilityState === 'visible') refresh() }
    const tick = setInterval(() => setNow(performance.now()), 1000)
    window.addEventListener('simulation-speed-updated', refresh)
    document.addEventListener('visibilitychange', onVisible)
    void sync()
    return () => {
      disposed = true
      controller.abort()
      clearTimeout(timer)
      clearInterval(tick)
      window.removeEventListener('simulation-speed-updated', refresh)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  const time = simulationTime(sample, now)
  const date = time === null ? null : new Date(time)
  const status = headerStatus(pageStatus, boardConnection, syncError || statusError, loading)

  return (
    <div className="shrink-0 leading-tight" title={pageStatus?.error || (syncError ? 'Clock sync delayed; showing the last known estimate.' : 'Simulation time in UTC. Resyncs automatically.')}>
      <div role="status" className="mb-1 flex items-center gap-2 text-[11px] font-semibold text-base-content/65" aria-label={`Status: ${status.label}${sample ? `; simulation speed ${sample.speed} times` : ''}`}>
        <span aria-hidden="true" className={`size-2 rounded-full ${status.live ? 'bg-success' : boardConnection === 'disabled' ? 'bg-base-content/40' : 'bg-warning'}`} />
        <span>{status.label}{sample ? sample.speed === 0 ? ' · paused' : ` · ${sample.speed.toLocaleString()}×` : ''}</span>
      </div>
      {date ? (
        <time role="timer" aria-live="off" aria-label="Simulation clock" dateTime={date.toISOString()} className="block whitespace-nowrap text-xs font-semibold tabular-nums text-base-content sm:text-sm">
          {dateFormat.format(date)} · {timeFormat.format(date)} <span className="font-normal text-base-content/60">UTC</span>
        </time>
      ) : <p className="text-xs text-base-content/60">{loading ? 'Syncing clock…' : 'Clock unavailable'}</p>}
    </div>
  )
}
