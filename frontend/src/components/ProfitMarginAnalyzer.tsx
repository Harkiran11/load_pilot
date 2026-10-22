import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { getCostPreview } from '../api/orders'
import type { LoadView, OrderRecommendations } from '../api/orders'
import { amount, label, quantity, timestamp } from '../utils/load-format'
import { bidForMargin, marginForBid, normalizeBid, validMargin } from '../utils/profit-margin'
import { apiFeedback, readableMessage } from '../utils/api-feedback'
import PriceAnalysisPanel from './PriceAnalysisPanel'

type Preview = { key: string; report?: OrderRecommendations; error?: string }
type ReviewTab = 'price' | 'economics' | 'order'
type Props = { load: LoadView; canSubmit: boolean; pending: boolean; onSubmitBid: (rate: string) => Promise<void>; onAccept: () => Promise<void>; orderInformation: ReactNode; initialTab?: ReviewTab }

const checkNames: Record<string, string> = {
  offer_open_unexpired: 'Offer open & unexpired',
  pricing_passed: 'Profit requirements',
  deadhead_passed: 'Deadhead distance',
  weight_passed: 'Cargo weight',
  timing_passed: 'Pickup timing',
  location_passed: 'Unit location',
  schedule_passed: 'Unit schedule',
  capacity_passed: 'Terminal capacity',
}

async function preview(loadId: string, bid: string | null, signal: AbortSignal): Promise<OrderRecommendations> {
  const response = await getCostPreview(loadId, bid ? { bid_rate: bid } : {}, { signal })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const feedback = apiFeedback(body, response.status)
    throw new Error([feedback.message, ...feedback.details, ...feedback.suggestions].join(' '))
  }
  if (!body) throw new Error('The profit preview could not be read. Refresh the analysis to try again.')
  if (body.external_id !== loadId || !Array.isArray(body.ranked_units)) throw new Error('The profit preview could not be verified.')
  return body
}

export default function ProfitMarginAnalyzer({ load, canSubmit, pending, onSubmitBid, onAccept, orderInformation, initialTab }: Props) {
  const [tab, setTab] = useState<ReviewTab>(initialTab ?? 'price')
  const [margin, setMargin] = useState('15')
  const [manualBid, setManualBid] = useState('')
  const [mode, setMode] = useState<'margin' | 'manual'>('margin')
  const [retry, setRetry] = useState(0)
  const [base, setBase] = useState<Preview | null>(null)
  const [quote, setQuote] = useState<Preview | null>(null)
  const loadId = load.external_id
  const isFixedRate = load.order_type === 'fixed_rate'
  const baseKey = JSON.stringify([loadId, load.order_type, load.last_synced_at, load.status, load.offered_rate, load.estimated_cost, load.cost_per_mile, load.pickup_at, load.delivery_at, load.expires_at, retry])
  const baseReport = base?.key === baseKey ? base.report : undefined
  const cost = baseReport?.selected?.economics.total_cost ?? null
  const proposedBid = isFixedRate ? null : mode === 'margin' ? bidForMargin(cost, margin) : normalizeBid(manualBid)
  const quoteKey = JSON.stringify([baseKey, proposedBid])
  const currentQuote = quote?.key === quoteKey ? quote : null
  const report = isFixedRate ? baseReport : currentQuote?.report
  const selected = report ? report.selected : baseReport?.selected
  const economics = selected?.economics
  const displayedMargin = mode === 'manual' ? marginForBid(report ? economics?.total_cost ?? null : cost, manualBid) ?? '' : margin
  const sliderMargin = Math.min(99.99, Math.max(0, Number(displayedMargin) || 0))
  const evaluation = report?.dispatch_evaluation
  const ready = !!report && (isFixedRate || (report.entered_bid_rate !== null && normalizeBid(report.entered_bid_rate) === proposedBid))
    && !!report.selected?.booking_ready && evaluation?.eligible === true
  const error = (base?.key === baseKey ? base.error : undefined) ?? currentQuote?.error
  const loading = !error && (!baseReport || (!!proposedBid && !report))

  useEffect(() => {
    const controller = new AbortController()
    void preview(loadId, null, controller.signal).then(
      (report) => { if (!controller.signal.aborted) setBase({ key: baseKey, report }) },
      (error: unknown) => { if (!controller.signal.aborted) setBase({ key: baseKey, error: error instanceof TypeError ? 'Unable to connect to the profit analyzer. Check your connection and refresh the analysis.' : error instanceof Error ? error.message : 'Unable to evaluate profit.' }) },
    )
    return () => controller.abort()
  }, [loadId, baseKey])

  useEffect(() => {
    if (isFixedRate || !baseReport || !proposedBid) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void preview(loadId, proposedBid, controller.signal).then(
        (report) => { if (!controller.signal.aborted) setQuote({ key: quoteKey, report }) },
        (error: unknown) => { if (!controller.signal.aborted) setQuote({ key: quoteKey, error: error instanceof TypeError ? 'Unable to connect to the profit analyzer. Check your connection and refresh the analysis.' : error instanceof Error ? error.message : 'Unable to evaluate this bid.' }) },
      )
    }, 350)
    return () => { clearTimeout(timer); controller.abort() }
  }, [loadId, baseReport, proposedBid, quoteKey, isFixedRate])

  return (
    <form className="flex min-h-0 flex-1 flex-col" onSubmit={(event) => {
      event.preventDefault()
      if (!canSubmit || pending || !ready) return
      if (isFixedRate) void onAccept()
      else if (proposedBid) void onSubmitBid(proposedBid)
    }}>
      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[1fr_1.05fr] lg:overflow-hidden">
      <section aria-label={isFixedRate ? 'Review fixed-rate offer' : 'Adjust your bid'} className="space-y-3 bg-primary/5 p-4 lg:overflow-y-auto lg:p-5">
      <div>
        <h3 className="text-lg font-bold text-primary">{isFixedRate ? 'Fixed-rate profit analysis' : 'Set your profit margin'}</h3>
      </div>
      {isFixedRate ? <div className="rounded-xl border border-primary/20 bg-base-100 p-4">
        <p className="text-xs text-base-content/60">Offered rate · USD</p>
        <p className="mt-2 text-3xl font-bold tabular-nums text-primary">{amount(load.offered_rate)}</p>
      </div> : <>
      <div>
        <label htmlFor="profit-margin" className="mb-2 flex items-center justify-between text-sm font-medium">Target profit margin <span>% of revenue</span></label>
        <input id="profit-margin" type="number" min={mode === 'manual' ? Math.min(0, Number(displayedMargin)) : 0} max={mode === 'manual' ? 100 : 99.99} step="0.01" className="input input-sm input-bordered w-full" value={displayedMargin} disabled={pending} onChange={(event) => { setMargin(event.target.value); setMode('margin') }} aria-invalid={mode === 'margin' && !validMargin(margin)} />
        <input aria-label="Target profit margin percentage" type="range" min="0" max="99.99" step="0.01" className="range range-primary range-sm mt-3 w-full" value={sliderMargin} disabled={pending} onChange={(event) => { setMargin(event.target.value); setMode('margin') }} />
        <div className="mt-1 flex justify-between text-xs text-base-content/50"><span>0%</span><span>50%</span><span>99.99%</span></div>
        {mode === 'margin' && !validMargin(margin) && <p className="mt-2 text-xs text-error">Enter a margin from 0 to 99.99% with up to two decimal places.</p>}
        {mode === 'manual' && Number(displayedMargin) < 0 && <p className="mt-2 text-xs text-warning">This bid is below cost. The slider stays at 0%; the margin above shows the loss.</p>}
      </div>
      <div>
        <label htmlFor="profit-bid" className="mb-2 block text-sm font-medium">Bid amount (USD)</label>
        <input id="profit-bid" type="text" inputMode="decimal" className="input input-sm input-bordered w-full font-semibold" placeholder="2100.25" value={mode === 'manual' ? manualBid : proposedBid ?? ''} disabled={pending} onChange={(event) => { setManualBid(event.target.value); setMode('manual') }} aria-invalid={mode === 'manual' && !proposedBid} />
        <p className="mt-2 text-xs text-base-content/60">{mode === 'margin' ? 'Cost ÷ (1 − margin), rounded up to the nearest cent.' : 'Using your entered bid.'}</p>
        {mode === 'manual' && !proposedBid && <p className="mt-2 text-xs text-error">Enter a positive amount with up to 16 whole digits and two decimal places.</p>}
        {baseReport && mode === 'margin' && !proposedBid && <p className="mt-2 text-xs text-warning">A bid cannot be calculated from the available cost and margin. You can enter a bid to request a preview.</p>}
      </div>

      </>}
      <dl className="grid grid-cols-3 gap-2 rounded-xl border border-primary/15 bg-base-100 p-3">
        {[
          ['Total cost', amount(economics?.total_cost ?? null)],
          ['Gross profit', report ? amount(economics?.gross_profit ?? null) : '—'],
          ['Actual margin', report ? quantity(economics?.margin_pct ?? null, '%') : '—'],
        ].map(([name, value]) => <div key={name}><dt className="text-xs text-base-content/60">{name}</dt><dd className={`mt-2 break-words text-base font-bold tabular-nums ${name !== 'Total cost' && report && Number(economics?.gross_profit) < 0 ? 'text-error' : 'text-primary'}`}>{value}</dd></div>)}
      </dl>
      {economics && <p className="flex justify-between gap-2 text-xs text-base-content/65"><span>{isFixedRate ? 'Minimum profitable rate' : 'Minimum profitable bid'}</span><strong className="text-base-content">{amount(economics.minimum_profitable_bid)}</strong></p>}
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-base-content/65"><span className="loading loading-spinner loading-xs" />Evaluating current costs and eligibility…</p>}
      {error && <p role="alert" className="text-sm text-error">{error}</p>}
      {(report ?? baseReport) && !selected && <p className="text-sm text-warning">{(report ?? baseReport)?.dispatch_evaluation?.primary_rejection?.message ?? 'No recommended unit is available for this evaluation. Dispatch checks must pass before continuing.'}</p>}
        <section aria-label={isFixedRate ? 'Offer eligibility' : 'Bid eligibility'} className="space-y-2 rounded-lg border border-base-300 bg-base-100 p-3">
      {evaluation && <div className="space-y-2 text-[11px]">
        <p className={ready ? 'font-semibold text-success' : 'font-semibold text-warning'}>{ready ? isFixedRate ? 'Eligible to accept' : 'Eligible for bid submission' : 'Eligibility checks'}</p>
        {!ready && evaluation.primary_rejection?.message && <p className="text-warning">{evaluation.primary_rejection.message}</p>}
        <ul className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          {Object.entries(evaluation.checks).map(([check, passed]) => {
            const skipped = evaluation.skipped_checks.includes(check)
            const status = skipped ? 'Skipped' : passed ? 'Passed' : 'Blocked'
            return <li key={check} className="flex items-center gap-1.5">
              <span className={`flex size-4 shrink-0 items-center justify-center rounded-full border ${skipped ? 'border-base-content/25 text-base-content/45' : passed ? 'border-success bg-success/10 text-success' : 'border-error bg-error/10 text-error'}`}>
                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="size-2.5"><path strokeLinecap="round" strokeLinejoin="round" d={skipped ? 'M6 12h12' : passed ? 'm5 12 4 4L19 6' : 'm6 6 12 12M18 6 6 18'} /></svg>
              </span>
              <span>{checkNames[check] ?? label(check).replace(/ passed$/, '')}<span className={skipped ? 'ml-1 text-base-content/50' : 'sr-only'}>{skipped ? '(Skipped)' : `: ${status}`}</span></span>
            </li>
          })}
        </ul>
        <details className="text-base-content/60">
          <summary className="cursor-pointer text-[11px]">Check details</summary>
          <div className="mt-2 space-y-2">
        {!evaluation.policy.enforce_margin && <p className="text-base-content/65">The backend’s profit-margin guard is disabled. Profit targets are informational.</p>}
        {evaluation.rejection_details.map((reason, index) => <p key={index} className="text-warning">{reason.message} {reason.suggested_action}</p>)}
        {evaluation.capacity_warnings.length > 0 && <p className="text-warning">The evaluation includes {evaluation.capacity_warnings.length} capacity warning(s). Review available fleet capacity.</p>}
      {(report ?? baseReport)?.rejection_reasons.map((reason, index) => <p key={index} className="text-xs text-warning">{readableMessage(reason)}</p>)}
      {(report ?? baseReport) && <p className="text-xs text-base-content/50">Evaluated {timestamp((report ?? baseReport)!.evaluated_at_sim)} (simulation time). The board assigns the final unit; costs and eligibility are checked again on submission.</p>}
          </div>
        </details>
      </div>}
      {!evaluation && <p className="text-[11px] text-base-content/60">{loading ? 'Checking eligibility…' : isFixedRate ? 'Refresh analysis to review this offer’s eligibility.' : 'Enter a valid bid to review dispatch eligibility.'}</p>}
      </section>
      </section>
      <section aria-label="Order review" className="flex min-h-64 flex-col border-t border-base-300 lg:min-h-0 lg:border-t-0 lg:border-l">
        <div className="flex shrink-0 gap-1 border-b border-base-300 px-4 pt-3" aria-label="Review sections">
          {([['price', 'Price analysis'], ['economics', 'Economics'], ['order', 'Order details']] as const).map(([value, name]) => <button key={value} type="button" aria-pressed={tab === value} aria-controls={`bid-review-${value}`} className={`min-w-0 flex-1 border-b-2 px-2 py-3 text-xs font-medium ${tab === value ? 'border-primary text-primary' : 'border-transparent text-base-content/60 hover:text-base-content'}`} onClick={() => setTab(value)}>{name}</button>)}
        </div>
        <div className="min-h-0 flex-1 space-y-4 p-5 lg:overflow-y-auto lg:p-6">
        <div id="bid-review-order" hidden={tab !== 'order'}>{orderInformation}</div>
        <div id="bid-review-price" hidden={tab !== 'price'}>{tab === 'price' && <PriceAnalysisPanel load={load} bid={proposedBid} fixedPreview={baseReport} fixedLoading={loading} fixedError={error} onRefreshFixed={() => setRetry((value) => value + 1)} />}</div>
        <div id="bid-review-economics" hidden={tab !== 'economics'}>
      {selected && economics && <div className="space-y-3">
        <p className="text-xs text-base-content/60">Recommended unit <strong className="text-base-content">{selected.unit_number}</strong> · {quantity(economics.projected_deadhead_miles, 'mi deadhead')}</p>
        <dl className="grid grid-cols-2 gap-3 rounded-lg bg-base-100 p-3 text-sm">
          {[
            ['Loaded cost', amount(economics.loaded_cost)],
            ['Deadhead cost', amount(economics.projected_deadhead_cost)],
            ['Total cost', amount(economics.total_cost)],
            [isFixedRate ? 'Offered revenue' : 'Bid revenue', report ? amount(economics.payout) : '—'],
            ['Gross profit', report ? amount(economics.gross_profit) : '—'],
            ['Profit margin', report ? quantity(economics.margin_pct, '%') : '—'],
          ].map(([name, value]) => <div key={name}><dt className="text-xs text-base-content/60">{name}</dt><dd className={`mt-1 font-semibold tabular-nums ${report && Number(economics.gross_profit) < 0 && ['Gross profit', 'Profit margin'].includes(name) ? 'text-error' : ''}`}>{value}</dd></div>)}
        </dl>
        <div className="flex justify-between gap-3 text-sm"><span>{isFixedRate ? 'Minimum profitable rate' : 'Minimum profitable bid'}</span><strong>{amount(economics.minimum_profitable_bid)}</strong></div>
        <p className="text-xs text-base-content/60">{economics.bid_floor.explanation}</p>
        {report && (isFixedRate ? economics.payout !== null : proposedBid !== null) && economics.minimum_profitable_bid !== null && Number(isFixedRate ? economics.payout : proposedBid) < Number(economics.minimum_profitable_bid) && <p className="text-xs text-warning">{isFixedRate ? 'This offer is below the minimum profitable rate for this unit.' : 'Your bid is below the minimum profitable bid for this unit.'}</p>}
        <section className="rounded-lg border border-base-300 bg-base-100 p-4 text-xs">
          <h4 className="text-sm font-medium">Economics breakdown</h4>
          <div className="mt-4 space-y-4">
            {[
              { title: 'Time & earnings', rows: [
                ['Time committed', quantity(economics.occupied_hours, 'hours')],
                ['Profit per hour', report ? amount(economics.profit_per_hour) : '—'],
              ] },
              { title: 'Profit adjustments', rows: [
                ['Opportunity cost', amount(economics.opportunity_cost)],
                ['Positioning credit', amount(economics.positioning_value)],
                ['Adjusted profit', report ? amount(economics.adjusted_profit) : '—'],
              ] },
              { title: 'Profit requirements', rows: [
                ['Minimum profit', amount(economics.bid_floor.minimum_profit_usd)],
                ['Minimum margin', quantity(economics.bid_floor.minimum_margin_pct, '%')],
              ] },
            ].map(({ title, rows }) => <section key={title}>
              <h4 className="mb-2 font-semibold text-base-content/60">{title}</h4>
              <dl className="divide-y divide-base-200">{rows.map(([name, value]) => <div key={name} className="flex justify-between gap-3 py-2"><dt>{name}</dt><dd className="text-right font-medium tabular-nums">{value}</dd></div>)}</dl>
            </section>)}
            {economics.profit_adjustments.warnings.map((warning, index) => <p key={index} className="text-warning">{warning}</p>)}
          </div>
        </section>
        {economics.missing_inputs.map((input, index) => <p key={index} className="text-xs text-warning">{input.message}</p>)}
      </div>}

        </div>
        </div>
      </section>
      </div>
      <footer className="shrink-0 space-y-3 border-t border-base-300 bg-base-100 px-5 py-4 lg:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p role="status" className={`text-xs font-medium ${ready && canSubmit ? 'text-success' : 'text-base-content/65'}`}>{pending ? isFixedRate ? 'Accepting this offer…' : 'Submitting your bid…' : !canSubmit ? 'Order unavailable for submission' : error ? 'Profit preview unavailable' : loading ? 'Updating your profit preview…' : ready ? isFixedRate ? 'Ready to accept this offer' : 'Ready to submit your bid' : 'Review eligibility before continuing'}</p>
          <button type="button" className="btn btn-ghost btn-xs" disabled={pending || loading} onClick={() => setRetry((value) => value + 1)}>Refresh analysis</button>
        </div>
        <button type="submit" className="btn btn-primary w-full" disabled={!canSubmit || pending || !ready || (!isFixedRate && !proposedBid)}>{pending ? isFixedRate ? 'Accepting…' : 'Submitting bid…' : isFixedRate ? `Accept offer · ${amount(load.offered_rate)}` : proposedBid ? `Submit bid · ${amount(proposedBid)}` : 'Submit bid'}</button>
        <p className="text-center text-xs text-base-content/50">{isFixedRate ? 'Availability and eligibility are checked again when you accept.' : 'A booking is confirmed only if your bid wins.'}</p>
      </footer>
    </form>
  )
}
