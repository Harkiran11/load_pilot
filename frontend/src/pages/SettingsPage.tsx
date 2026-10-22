import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { getSimulationClock, getSimulationSettings, setSimulationSettings, spawnSimulationOrders } from '../api/system'
import type { SimulationClock, SimulationSettings, SimulationSettingsRequest, SpawnOrdersResult } from '../api/system'
import PageSection from '../components/PageSection'
import { timestamp } from '../utils/load-format'

const presets = [1, 10, 60, 120, 3600]
const seconds = (value: number) => `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })}s`

async function readClock(response: Response): Promise<SimulationClock> {
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Simulator request failed (HTTP ${response.status}).`)
  }
  const clock: SimulationClock = await response.json()
  if (!Number.isFinite(clock.simulation_speed) || clock.simulation_speed < 1) throw new Error('The simulator returned an invalid clock.')
  return clock
}

async function readSettings(response: Response): Promise<SimulationSettings> {
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Simulator settings unavailable (HTTP ${response.status}).`)
  }
  const settings: SimulationSettings = await response.json()
  if (!Number.isFinite(settings.simulation_speed) || settings.simulation_speed < 1 || settings.simulation_speed > 100_000
    || !Number.isFinite(settings.order_interval_seconds) || settings.order_interval_seconds < 0.5 || settings.order_interval_seconds > 3600
    || !Number.isInteger(settings.order_ttl_sim_minutes) || settings.order_ttl_sim_minutes <= 0
    || !Number.isInteger(settings.bid_decision_sim_minutes) || settings.bid_decision_sim_minutes < 0) throw new Error('The simulator returned invalid settings.')
  return settings
}

export default function SettingsPage() {
  const [clock, setClock] = useState<SimulationClock | null>(null)
  const [settings, setSettings] = useState<SimulationSettings | null>(null)
  const [speed, setSpeed] = useState('')
  const [interval, setIntervalValue] = useState('')
  const [loading, setLoading] = useState(import.meta.env.DEV)
  const [saving, setSaving] = useState(false)
  const [spawnCount, setSpawnCount] = useState('6')
  const [spawning, setSpawning] = useState(false)
  const [spawnResult, setSpawnResult] = useState<SpawnOrdersResult | null>(null)
  const [spawnError, setSpawnError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const saveController = useRef<AbortController | null>(null)
  const spawnController = useRef<AbortController | null>(null)
  const submitting = useRef(false)

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const controller = new AbortController()
    Promise.all([
      getSimulationClock({ signal: controller.signal }).then(readClock),
      getSimulationSettings({ signal: controller.signal }).then(readSettings),
    ]).then(([clock, data]) => {
      if (!controller.signal.aborted) {
        setClock(clock)
        setSettings(data)
        setSpeed(String(data.simulation_speed))
        setIntervalValue(String(data.order_interval_seconds))
        setError(null)
      }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Unable to read the simulator settings.')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [reload])

  useEffect(() => () => { saveController.current?.abort(); spawnController.current?.abort() }, [])

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loading || submitting.current || !settings || !import.meta.env.DEV) return
    const value = Number(speed)
    const arrivalInterval = Number(interval)
    if (!speed.trim() || !Number.isFinite(value) || value < 1 || value > 100_000) {
      setError('Enter a simulation speed between 1 and 100,000.')
      return
    }
    if (!interval.trim() || !Number.isFinite(arrivalInterval) || arrivalInterval < 0.5 || arrivalInterval > 3600) {
      setError('Enter a new order interval between 0.5 and 3,600 real seconds.')
      return
    }
    const changes: SimulationSettingsRequest = {}
    if (value !== settings.simulation_speed) changes.simulation_speed = value
    if (arrivalInterval !== settings.order_interval_seconds) changes.order_interval_seconds = arrivalInterval
    if (!Object.keys(changes).length) return
    const controller = new AbortController()
    saveController.current = controller
    submitting.current = true
    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const data = await readSettings(await setSimulationSettings(changes, { signal: controller.signal }))
      if (!controller.signal.aborted) {
        setSettings(data)
        setSpeed(String(data.simulation_speed))
        setIntervalValue(String(data.order_interval_seconds))
        setMessage(`Settings saved: ${data.simulation_speed.toLocaleString()}× speed, new orders every ${seconds(data.order_interval_seconds)}. Changes to arrival timing affect the next generation cycle.`)
        window.dispatchEvent(new Event('simulation-speed-updated'))
        const latest = await getSimulationClock({ signal: controller.signal }).then(readClock).catch(() => null)
        if (!controller.signal.aborted) setClock(latest)
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(`${cause instanceof Error ? cause.message : 'Unable to update simulation settings.'} Refresh settings before retrying; the change may already have reached the simulator.`)
      }
    } finally {
      submitting.current = false
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  function refreshClock() {
    setLoading(true)
    setError(null)
    setMessage(null)
    setReload((value) => value + 1)
  }

  async function spawnOrders(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loading || submitting.current || !import.meta.env.DEV) return
    const count = Number(spawnCount)
    if (!spawnCount.trim() || !Number.isInteger(count) || count < 1 || count > 50) {
      setSpawnError('Enter a whole number of orders from 1 to 50.')
      return
    }
    const controller = new AbortController()
    spawnController.current = controller
    submitting.current = true
    setSpawning(true)
    setSpawnResult(null)
    setSpawnError(null)
    let uncertain = true
    try {
      const response = await spawnSimulationOrders(count, { signal: controller.signal })
      const body = await response.json().catch(() => null)
      if (!response.ok) {
        uncertain = response.status >= 500 || response.status === 408
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Unable to spawn orders (HTTP ${response.status}).`)
      }
      const result: SpawnOrdersResult = body
      if (!result || !Number.isInteger(result.created) || result.created < 1 || result.created > 50
        || !Number.isInteger(result.fixed_rate) || result.fixed_rate < 0 || !Number.isInteger(result.open_bid) || result.open_bid < 0
        || result.fixed_rate + result.open_bid !== result.created || !Array.isArray(result.external_ids)
        || result.external_ids.length !== result.created || new Set(result.external_ids).size !== result.created
        || result.external_ids.some(id => typeof id !== 'string' || !id.trim())) throw new Error('The simulator response could not be verified.')
      if (!controller.signal.aborted) setSpawnResult(result)
    } catch (cause) {
      if (!controller.signal.aborted) setSpawnError(`${cause instanceof Error ? cause.message : 'Unable to spawn orders.'}${uncertain ? ' Check Load Board before retrying; some orders may already have been created.' : ''}`)
    } finally {
      submitting.current = false
      if (!controller.signal.aborted) setSpawning(false)
    }
  }

  const value = Number(speed)
  const arrivalInterval = Number(interval)
  const valid = !!speed.trim() && Number.isFinite(value) && value >= 1 && value <= 100_000
    && !!interval.trim() && Number.isFinite(arrivalInterval) && arrivalInterval >= 0.5 && arrivalInterval <= 3600
  const offerLifetime = settings && valid ? settings.order_ttl_sim_minutes * 60 / value : null
  const changed = settings && (value !== settings.simulation_speed || arrivalInterval !== settings.order_interval_seconds)
  const busy = saving || spawning
  const validSpawnCount = !!spawnCount.trim() && Number.isInteger(Number(spawnCount)) && Number(spawnCount) >= 1 && Number(spawnCount) <= 50

  return (
    <PageSection title="Settings" description="Simulation timing and order generation for local testing.">
      {!import.meta.env.DEV ? (
        <p className="rounded-xl border border-base-300 bg-base-100 p-6 text-base-content/65">Simulation controls are available when running the local development server.</p>
      ) : (
        <section aria-labelledby="simulation-settings-title" className="card max-w-2xl border border-base-300 bg-base-100">
          <div className="card-body gap-6">
            <div className="flex items-start justify-between gap-4">
              <div><h2 id="simulation-settings-title" className="text-lg font-semibold">Simulation controls</h2><p className="mt-1 text-sm text-base-content/60">Adjust time and new order arrivals together.</p></div>
              <span className="badge badge-soft badge-warning">Temporary</span>
            </div>
            <div aria-busy={loading} className="grid gap-4 rounded-xl bg-base-200 p-4 sm:grid-cols-3">
              <div><p className="text-xs text-base-content/60">Current speed</p><p className="mt-1 text-2xl font-bold text-primary">{loading ? 'Loading…' : settings ? `${settings.simulation_speed.toLocaleString()}×` : 'Unavailable'}</p></div>
              <div><p className="text-xs text-base-content/60">New order every</p><p className="mt-1 text-2xl font-bold">{settings ? seconds(settings.order_interval_seconds) : '—'}</p><p className="text-xs text-base-content/50">Real time</p></div>
              <div><p className="text-xs text-base-content/60">Offer lifetime</p><p className="mt-1 text-2xl font-bold">{settings ? seconds(settings.order_ttl_sim_minutes * 60 / settings.simulation_speed) : '—'}</p><p className="text-xs text-base-content/50">Real time</p></div>
            </div>
            <p className="text-xs text-base-content/60">Simulation time at last check: {timestamp(clock?.sim_time_utc ?? null)}</p>
            {error && <p role="alert" className="alert alert-error text-sm">{error}</p>}
            {message && <p role="status" className="alert alert-success text-sm">{message}</p>}
            <form className="space-y-4" onSubmit={(event) => void saveSettings(event)}>
              <label htmlFor="simulation-speed" className="block text-sm font-medium">Speed multiplier</label>
              <input id="simulation-speed" className="input input-bordered w-full" type="number" min="1" max="100000" step="any" required value={speed} disabled={loading || busy} onChange={(event) => setSpeed(event.target.value)} aria-describedby="simulation-speed-help" />
              <p id="simulation-speed-help" className="text-xs text-base-content/60">1× is real time. At 60×, one real second advances one simulated minute.</p>
              <div className="flex flex-wrap gap-2" aria-label="Simulation speed presets">
                {presets.map((preset) => <button key={preset} type="button" className={`btn btn-sm ${Number(speed) === preset ? 'btn-primary' : 'btn-outline'}`} disabled={loading || busy} aria-pressed={Number(speed) === preset} onClick={() => setSpeed(String(preset))}>{preset.toLocaleString()}×</button>)}
              </div>
              <label htmlFor="order-interval" className="block text-sm font-medium">New order interval · real seconds</label>
              <input id="order-interval" className="input input-bordered w-full" type="number" min="0.5" max="3600" step="any" required value={interval} disabled={loading || busy} onChange={event => setIntervalValue(event.target.value)} aria-describedby="order-interval-help" />
              <p id="order-interval-help" className="text-xs text-base-content/60">Arrival frequency uses real time, independently of simulation speed.</p>
              <fieldset aria-describedby="simulation-timing-help" className="space-y-2 rounded-xl border border-base-300 p-3">
                <legend className="px-1 text-xs font-medium text-base-content/65">Simulator timing · read only</legend>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label htmlFor="order-lifetime" className="mb-1 block text-xs font-medium">Offer lifetime · simulated minutes</label>
                    <input id="order-lifetime" className="input input-bordered input-sm w-full bg-base-200" type="number" value={settings?.order_ttl_sim_minutes ?? ''} readOnly disabled={loading || !settings} aria-describedby="simulation-timing-help order-lifetime-real" />
                    <p id="order-lifetime-real" className="mt-1 text-xs text-base-content/60">{settings ? `${seconds(settings.order_ttl_sim_minutes * 60 / settings.simulation_speed)} in real time at current speed` : 'Unavailable'}</p>
                  </div>
                  <div>
                    <label htmlFor="bid-decision-delay" className="mb-1 block text-xs font-medium">Bid decision delay · simulated minutes</label>
                    <input id="bid-decision-delay" className="input input-bordered input-sm w-full bg-base-200" type="number" value={settings?.bid_decision_sim_minutes ?? ''} readOnly disabled={loading || !settings} aria-describedby="simulation-timing-help bid-decision-real" />
                    <p id="bid-decision-real" className="mt-1 text-xs text-base-content/60">{settings ? `${seconds(settings.bid_decision_sim_minutes * 60 / settings.simulation_speed)} in real time at current speed` : 'Unavailable'}</p>
                  </div>
                </div>
                <p id="simulation-timing-help" className="text-xs text-base-content/50">These values come from the simulator. The current API supports editing speed and order interval only.</p>
              </fieldset>
              {offerLifetime !== null && <p className={`rounded-lg border p-3 text-sm ${offerLifetime < 5 || arrivalInterval > offerLifetime ? 'border-warning/30 bg-warning/10' : 'border-base-300 bg-base-200'}`}>At {value.toLocaleString()}×, each new offer lasts {seconds(offerLifetime)} in real time ({settings!.order_ttl_sim_minutes} simulated minutes). New orders arrive every {seconds(arrivalInterval)}.{arrivalInterval > offerLifetime ? ' Offers expire before the next arrival, leaving gaps on the Load Board.' : ''}{offerLifetime < 5 ? ' This is too brief for manual review; lower the speed.' : ''}</p>}
              <button type="button" className="btn btn-outline btn-sm" disabled={loading || busy} onClick={() => { setSpeed('10'); setIntervalValue('5') }}>Browse orders · 10× / 5s arrivals</button>
              <p className="text-xs text-base-content/60">Changing speed preserves the current simulation time. Arrival frequency uses real time. Expired offers stay in Bid Logs.</p>
              <div className="flex flex-wrap gap-3">
                <button type="submit" className="btn btn-primary btn-sm" disabled={!valid || !settings || loading || busy || !changed}>
                  {saving && <span className="loading loading-spinner loading-xs" aria-hidden="true" />}{saving ? 'Applying…' : 'Apply settings'}
                </button>
                <button type="button" className="btn btn-ghost btn-sm" disabled={loading || busy} onClick={refreshClock}>{loading ? 'Loading…' : 'Refresh settings'}</button>
              </div>
            </form>
            <section aria-labelledby="spawn-orders-title" className="space-y-3 border-t border-base-300 pt-4">
              <div><h3 id="spawn-orders-title" className="text-sm font-semibold">Spawn orders</h3><p className="mt-1 text-xs text-base-content/60">Create new offers now. Requests for 2 or more include both fixed-rate and open-bid orders.</p></div>
              <form className="flex flex-wrap items-end gap-3" onSubmit={event => void spawnOrders(event)}>
                <label htmlFor="spawn-count" className="block space-y-1 text-xs font-medium"><span>Number of orders · 1–50</span><input id="spawn-count" type="number" min="1" max="50" step="1" required className="input input-bordered input-sm block w-36" value={spawnCount} disabled={loading || busy} onChange={event => setSpawnCount(event.target.value)} /></label>
                <button type="submit" className="btn btn-primary btn-sm" disabled={loading || busy || !validSpawnCount}>{spawning && <span className="loading loading-spinner loading-xs" aria-hidden="true" />}{spawning ? 'Spawning…' : 'Spawn orders'}</button>
              </form>
              {spawnError && <p role="alert" className="alert alert-error text-sm">{spawnError}</p>}
              {spawnResult && <div role="status" className="space-y-2 rounded-lg border border-success/25 bg-success/5 p-3 text-sm"><p>Created {spawnResult.created} order{spawnResult.created === 1 ? '' : 's'}: {spawnResult.fixed_rate} fixed rate · {spawnResult.open_bid} open bid.</p><details className="text-xs"><summary className="cursor-pointer">Order IDs</summary><p className="mt-2 break-words">{spawnResult.external_ids.join(', ')}</p></details><Link to="/loads" className="link link-primary text-xs">View Load Board →</Link></div>}
              <p className="text-xs text-base-content/60">Orders appear after the dispatcher’s next sync. At fast speeds, apply the Browse orders preset first so offers have time to sync before expiring.</p>
            </section>
            <p className="text-xs text-base-content/60">Other pages will reflect the new speed after the dispatcher receives its next clock sync.</p>
          </div>
        </section>
      )}
    </PageSection>
  )
}
