import { useId } from 'react'
import type { AutopilotController } from '../hooks/use-autopilot'
import { autopilotFieldNames, autopilotForm, autopilotFormErrors, defaultAutopilotForm, realTimeDuration, type AutopilotForm } from '../utils/autopilot'

type NumericField = Exclude<keyof AutopilotForm, 'mode' | 'enable_fixed_rate' | 'enable_open_bid' | 'allow_baseline' | 'allocation_enabled' | 'model_baseline_id' | 'model_baseline_version'>

type Props = {
  controller: AutopilotController
  form: AutopilotForm
  onChange: (form: AutopilotForm) => void
  onRun: () => void
  /** Current simulation speed, used to explain the real length of a run. */
  simulationSpeed?: number | string | null
}

export default function AutopilotSetup({ controller, form, onChange, onRun, simulationSpeed = null }: Props) {
  const id = useId()
  const errors = autopilotFormErrors(form)
  const busy = !!controller.mutation || !!controller.pendingStart || !!controller.activeRun
  const durationSeconds = Number(form.max_duration_seconds)
  const durationHint = Number.isFinite(durationSeconds) && durationSeconds > 0
    ? `Simulated seconds. At ${Number(simulationSpeed) > 1 ? `${Number(simulationSpeed)}×` : '1×'} this run lasts about ${realTimeDuration(durationSeconds, simulationSpeed)} of real time.`
    : 'Simulated seconds. The run ends when this much simulated time has passed.'
  function field(name: NumericField, min = 0, max?: number, step: string | number = '0.01', help?: string) {
    return <label key={name} className="block min-w-0 space-y-1 text-[11px] font-medium" htmlFor={`${id}-${name}`}>
      <span>{autopilotFieldNames[name]}</span>
      <input id={`${id}-${name}`} type="number" inputMode={step === 1 ? 'numeric' : 'decimal'} className="input input-bordered input-sm h-8 w-full text-xs tabular-nums" title={help} min={min} max={max} step={step} required value={form[name]} disabled={busy} aria-invalid={!!errors[name]} aria-describedby={`${id}-${name}-help`} onChange={event => onChange({ ...form, [name]: event.target.value })} onBlur={event => {
        if (!busy && !event.currentTarget.value.trim() && defaultAutopilotForm[name]) onChange({ ...form, [name]: defaultAutopilotForm[name] })
      }} />
      <span id={`${id}-${name}-help`} className={errors[name] ? 'block text-[11px] font-normal text-error' : 'sr-only'}>{errors[name] ?? help}</span>
    </label>
  }

  return <section aria-label="Configure autopilot" aria-disabled={busy} className={`min-w-0 rounded-xl border border-base-300 p-3 sm:p-4 ${busy ? 'bg-base-200/70' : 'bg-base-100'}`}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div className="flex flex-wrap items-baseline gap-x-3 gap-y-1"><h2 className="text-sm font-semibold">Auto pilot settings</h2><p className="text-[11px] text-base-content/55">{controller.activeRun ? 'Stop to edit limits.' : controller.pendingStart ? 'Verifying original settings.' : 'Set limits, then Run.'}</p></div>
      {controller.selectedRun && <button type="button" className="btn btn-ghost btn-xs" disabled={busy} onClick={() => onChange(autopilotForm(controller.selectedRun!.settings))}>Use selected run’s settings</button>}
    </div>
    <form className={`space-y-3 ${busy ? 'opacity-50 grayscale' : ''}`} onSubmit={event => { event.preventDefault(); if (!busy) onRun() }}>
      <fieldset disabled={busy} className="flex flex-wrap gap-x-5 gap-y-2"><legend className="sr-only">Booking types</legend>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" className="checkbox checkbox-success checkbox-xs" checked={form.enable_fixed_rate} onChange={event => onChange({ ...form, enable_fixed_rate: event.target.checked })} />Fixed-rate accepts</label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" className="checkbox checkbox-info checkbox-xs" checked={form.enable_open_bid} onChange={event => onChange({ ...form, enable_open_bid: event.target.checked })} />Open bids</label>
      </fieldset>
      {!form.enable_fixed_rate && !form.enable_open_bid && <p role="alert" className="text-xs text-error">Enable at least one booking type.</p>}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2.5 sm:grid-cols-3 xl:grid-cols-4">
        {field('max_orders', 1, 10000, 1, 'Reserved submissions, not wins.')}
        {field('max_duration_seconds', 1, 86400, 1, durationHint)}
        {field('max_estimated_loss_usd', 0, undefined, '0.01', 'Includes pending negative exposure. Zero permits no loss.')}
        {field('min_expected_profit_usd', 0, undefined, '0.01')}
        {field('min_expected_margin_pct', 0, 99.99, '0.01', 'Cash margin as a percentage of payout.')}
        {field('max_deadhead_miles', 0, undefined, '0.01', 'Maximum selected-unit empty travel to pickup.')}
        {form.enable_open_bid && field('max_bid_rate', 0.01, undefined, '0.01', 'Required for open bids. Model prices above this cap are skipped.')}
        {form.allocation_enabled && field('collection_window_seconds', 0, 300, 'any', 'Collect new orders before pricing. Zero starts immediately; the run timer includes this wait.')}
      </div>
      <p className="text-[11px] text-base-content/60">
        Run length: <span className="font-semibold text-base-content">{durationSeconds > 0 ? durationSeconds.toLocaleString() : '—'} simulated seconds</span>
        {durationSeconds > 0 && <> ≈ <span className="font-semibold text-base-content">{realTimeDuration(durationSeconds, simulationSpeed)}</span> of real time at {Number(simulationSpeed) > 1 ? `${Number(simulationSpeed)}×` : '1×'} speed.</>}
      </p>
      <label className="flex items-center gap-2 text-xs" title="Choose compatible orders and truck schedules together."><input type="checkbox" className="checkbox checkbox-xs" disabled={busy} checked={form.allocation_enabled} onChange={event => onChange({ ...form, allocation_enabled: event.target.checked, collection_window_seconds: event.target.checked ? form.collection_window_seconds : '0' })} /><span>Optimize fleet allocation</span></label>
      <details className="rounded-lg border border-base-300 px-3 py-2"><summary className="cursor-pointer text-xs font-semibold">Advanced limits & guidance</summary>
        <p className="mt-3 text-[11px] text-base-content/55">Run duration is measured in simulated seconds, so it passes faster than real time on an accelerated board. Submissions count reservations, not wins. A zero loss budget permits no loss. Minimum margin is profit ÷ payout × 100. Hover over a limit for its details.</p>
        <div className="mt-3 grid grid-cols-2 gap-3 xl:grid-cols-4">
        {field('min_action_interval_seconds', 1, 3600, 'any')}
        {field('max_input_age_seconds', 1, 3600, 1)}
        {field('max_decision_age_seconds', 1, 120, 1)}
        {field('max_consecutive_errors', 1, 100, 1)}
        <label className="col-span-full flex items-center gap-2 text-xs" title="When disabled, baseline-only model responses require review."><input type="checkbox" className="checkbox checkbox-xs" disabled={busy} checked={form.allow_baseline} onChange={event => onChange({ ...form, allow_baseline: event.target.checked })} /><span>Allow historical fallback advice</span></label>
        {form.allow_baseline && form.enable_open_bid && <>
          {(['model_baseline_id', 'model_baseline_version'] as const).map(name => <label key={name} className="block min-w-0 space-y-1 text-[11px] font-medium"><span>{autopilotFieldNames[name]}</span><input type="text" className="input input-bordered input-sm h-8 w-full text-xs" title="Must match the model’s configured baseline." required disabled={busy} value={form[name]} aria-invalid={!!errors[name]} onChange={event => onChange({ ...form, [name]: event.target.value })} />{errors[name] && <span className="block text-[11px] font-normal text-error">{errors[name]}</span>}</label>)}
          {field('model_min_lane_samples', 1, Number.MAX_SAFE_INTEGER, 1, 'Must match the model’s minimum sample policy.')}
        </>}
        {form.allocation_enabled && <>
          {field('allocation_max_candidates', 1, 30, 1)}
          {field('allocation_max_nodes', 1, 100000, 1)}
          {field('allocation_max_seconds', 0.05, 10, 'any')}
          {field('allocation_submission_seconds', 1, 120, 'any')}
          {field('allocation_expiry_buffer_seconds', 0, 60, 'any')}
          {field('allocation_reservation_ttl_seconds', 1, 120, 1)}
        </>}
      </div></details>
      <p className="text-[10px] text-base-content/50">Dispatcher checks still apply. Autopilot never automatically declines orders.</p>
    </form>
  </section>
}
