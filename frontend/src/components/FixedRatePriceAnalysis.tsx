import type { LoadView, OrderRecommendations } from '../api/types'
import { fixedPriceHistory, type FixedPriceAnalysis } from '../utils/price-analysis'
import { amount, label, quantity, timestamp } from '../utils/load-format'
import PriceAnalysisChart from './PriceAnalysisChart'
import PriceAnalysisMetrics from './PriceAnalysisMetrics'

type Props = {
  load: LoadView
  report?: OrderRecommendations
  loading?: boolean
  error?: string
  onRefresh?: () => void
}

export default function FixedRatePriceAnalysis({ load, report, loading = false, error, onRefresh }: Props) {
  const current = report?.external_id === load.external_id ? report : null
  const evaluation = current?.dispatch_evaluation
  const economics = current?.selected?.economics ?? evaluation?.economics ?? null
  const analysis: FixedPriceAnalysis = {
    order_type: 'fixed_rate', economics,
    offered_rate: economics?.payout ?? load.offered_rate,
    distance_miles: load.distance_miles,
    market_context: current?.market_context,
  }
  const history = fixedPriceHistory(analysis)

  return <section aria-label="Price analysis" className="space-y-3" aria-busy={loading}>
    <div>
      <h3 className="text-base font-semibold">Price analysis</h3>
    </div>
    {loading && <p role="status" className="flex items-center gap-2 text-xs text-base-content/60"><span className="loading loading-spinner loading-xs" />Updating fixed-rate analysis…</p>}
    {error && <p role="alert" className="text-xs text-error">{error}</p>}
    {error && onRefresh && <button type="button" className="btn btn-ghost btn-xs" disabled={loading} onClick={onRefresh}>Retry analysis</button>}
    {current && <>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="badge badge-sm badge-soft badge-info">Fixed-rate economics</span>
        <span className="text-base-content/55">{timestamp(current.evaluated_at_sim)} · simulation time</span>
      </div>
      <PriceAnalysisChart result={analysis} bid={analysis.offered_rate} />
      <section aria-label="Historical lane data" className="rounded-lg border border-base-300 p-3 text-[11px]">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="font-semibold">Historical lane data</h4>
          {history && <span className={`badge badge-xs badge-soft ${history.sufficient_samples ? 'badge-success' : 'badge-warning'}`}>{history.payout.sample_count} payout samples{!history.sufficient_samples && ' · limited data'}</span>}
        </div>
        {history ? <>
          <p className="mt-2 text-base-content/60">{history.origin_state ?? 'Unknown'} → {history.destination_state ?? 'Unknown'} · {history.matching_method === 'directed_state_pair_and_endpoint_radius' ? `Within ${quantity(history.radius_miles, 'mi')} of both endpoints` : 'Broader state-pair match'}</p>
          <dl className="mt-2 grid grid-cols-3 gap-2">
            {[
              ['Median / mile', quantity(history.rate_per_mile.median, 'USD/mi')],
              ['Average / mile', quantity(history.rate_per_mile.mean, 'USD/mi')],
              ['Payout variation', amount(history.payout.population_stddev)],
            ].map(([name, value]) => <div key={name}><dt className="text-base-content/55">{name}</dt><dd className="mt-1 font-semibold tabular-nums">{value}</dd></div>)}
          </dl>
          {!history.sufficient_samples && <p className="mt-2 text-warning">Fewer than {history.minimum_samples} payout samples. These figures describe past shipments only.</p>}
          <details className="mt-2 text-base-content/60"><summary className="cursor-pointer">History details</summary>
            <p className="mt-2">{timestamp(history.since_sim)} – {timestamp(history.through_sim)} · simulation time</p>
            <p className="mt-1">{history.sample_count} matched shipments · {history.rate_per_mile.sample_count} rate-per-mile samples</p>
            <p className="mt-1">Lanes: {history.lane_ids.join(', ') || 'Unavailable'}</p>
            <p className="mt-1">Payout variation is the population standard deviation.</p>
            {history.warnings.map((warning) => <p key={warning} className="mt-1 text-warning">{warning}</p>)}
          </details>
        </> : <p className="mt-2 text-base-content/60">No historical fixed-rate payouts are available for this lane.</p>}
        {!history && current.market_context?.warnings.map((warning) => <p key={warning} className="mt-1 text-warning">{warning}</p>)}
        {current.market_context && current.market_context.order_type !== 'fixed_rate' && <p className="mt-1 text-warning">{label(current.market_context.order_type)} history does not match this fixed-rate offer.</p>}
      </section>
      <PriceAnalysisMetrics result={analysis} />
      {evaluation?.primary_rejection?.message && <p className="text-xs text-warning">{evaluation.primary_rejection.message}</p>}
      {economics?.missing_inputs.map((input, index) => <p key={index} className="text-xs text-warning">{input.message}</p>)}
    </>}
    <p className="text-[10px] text-base-content/50">Uses the board’s offered payout and the backend cost preview. Historical summaries describe past shipments; they are not model advice.</p>
  </section>
}
