import type { AutopilotRun } from '../api/types'
import { amount, quantity } from '../utils/load-format'
import type { RunProfitStats } from '../utils/autopilot-profit'

export default function OfferProfitRatio({ run, stats }: { run?: AutopilotRun | null; stats: { data: RunProfitStats | null; error: string | null; loading: boolean } }) {
  const currentRun = run?.mode === 'live' ? run : null
  const { data, error, loading } = stats
  const ratio = data?.ratio ?? null
  const tone = ratio === null ? 'text-base-content/45' : Number(ratio) < 0 ? 'text-error' : Number(ratio) > 0 ? 'text-success' : 'text-base-content'

  return <aside aria-label="Current run profit statistics" aria-busy={loading} className="min-w-0 rounded-xl border border-base-300 bg-base-100 p-3 sm:p-4">
    <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">Current run</h2>{currentRun && <span className="badge badge-xs badge-soft">{currentRun.status === 'running' ? 'Running' : 'Stopped'}</span>}</div>
    <p className="mt-2 text-xs text-base-content/60">Profit / original bid</p>
    <p className={`mt-4 text-3xl font-bold tabular-nums ${tone}`}>{ratio === null ? '—' : quantity(ratio, '%')}</p>
    <p className="mt-1 text-[10px] text-base-content/55">Total estimated profit ÷ original offer totals</p>
    <dl className="mt-3 space-y-1.5 text-xs">
      <div className="flex justify-between gap-2"><dt className="text-base-content/60">Submissions</dt><dd className="font-medium tabular-nums">{data?.submissions ?? '—'}</dd></div>
      <div className="flex justify-between gap-2"><dt className="text-base-content/60">Original offer total</dt><dd className="font-medium tabular-nums">{amount(data?.originalBid ?? null)}</dd></div>
      <div className="flex justify-between gap-2"><dt className="text-base-content/60">Est. profit</dt><dd className={`font-medium tabular-nums ${tone}`}>{amount(data?.estimatedProfit ?? null)}</dd></div>
    </dl>
    <p role="status" className="mt-3 text-[10px] text-base-content/50">{!currentRun ? 'Start autopilot to see run statistics.' : loading ? 'Loading run statistics…' : data?.submissions === 0 ? 'No successful submissions yet.' : 'Successful submissions only. Rejected and skipped orders are excluded.'}</p>
    {data && (data.missingPrices > 0 || data.missingProfit > 0) && <p className="mt-2 text-[10px] text-warning">Missing original prices: {data.missingPrices} · Missing profit: {data.missingProfit}. Incomplete totals are unavailable.</p>}
    {!!data?.openBids && <p className="mt-2 text-[10px] text-base-content/50">{data.openBids} open bids · Profit is conditional on winning; these are estimates.</p>}
    {error && <p role="status" className="mt-2 text-[10px] text-warning">{data ? 'Showing previous totals. ' : ''}{error} Retrying automatically.</p>}
  </aside>
}
