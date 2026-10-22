import type { AutopilotRun } from '../api/types'
import { wallDuration } from '../utils/autopilot'

export default function AutopilotSubmissionStatus({ run }: { run: AutopilotRun }) {
  const running = run.status === 'running'
  const items = [
    { label: 'Fixed-rate accepts', enabled: run.settings.enable_fixed_rate, permitted: run.fixed_rate_submissions_enabled },
    { label: 'Open bids', enabled: run.settings.enable_open_bid, permitted: run.open_bid_submissions_enabled },
  ]
  const collecting = running && !!run.collection_plan_id && run.collection_seconds_remaining > 0
  return <section aria-label="Autopilot submission access" className="space-y-2 rounded-lg border border-base-300 bg-base-100 p-3">
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
      {items.map(item => <p key={item.label}>{item.label}: <span className={`font-medium ${!item.enabled || !running ? 'text-base-content/50' : item.permitted === true ? 'text-success' : 'text-warning'}`}>{!item.enabled ? 'Disabled' : !running ? 'Stopped' : item.permitted === true ? 'Enabled' : item.permitted === false ? 'Blocked' : 'Status unavailable'}</span></p>)}
      {run.settings.enable_open_bid && <p>Model authorization: <span className={`font-medium ${run.model_live_auth_configured === true ? 'text-success' : 'text-warning'}`}>{run.model_live_auth_configured === true ? 'Configured' : run.model_live_auth_configured === false ? 'Missing' : 'Status unavailable'}</span></p>}
    </div>
    {running && run.settings.enable_open_bid && run.model_live_auth_configured === false && <p role="status" className="text-xs text-warning">Open-bid auto trading is blocked until model authorization is configured on the backend.{run.fixed_rate_submissions_enabled ? ' Enabled fixed-rate offers can still be accepted.' : ''}</p>}
    {collecting && <p role="status" className="text-xs text-primary">Collecting {run.collected_order_count} orders · {wallDuration(run.collection_seconds_remaining)} left before evaluation.</p>}
    <p className="text-[10px] text-base-content/50">These are backend access gates. Each order still needs model approval when required and must pass dispatch checks.</p>
  </section>
}
