import { useEffect, useRef, useState } from 'react'
import { getPriceAnalysis } from '../api/orders'
import type { LoadView, OrderRecommendations, PriceAnalysisResult } from '../api/types'
import { apiFeedback } from '../utils/api-feedback'
import { label, timestamp } from '../utils/load-format'
import { priceAdvice, priceAnalysisKey, verifyPriceAnalysis } from '../utils/price-analysis'
import { normalizeBid } from '../utils/profit-margin'
import PriceAnalysisChart from './PriceAnalysisChart'
import PriceAnalysisMetrics from './PriceAnalysisMetrics'
import FixedRatePriceAnalysis from './FixedRatePriceAnalysis'

type Props = {
  load: LoadView; bid: string | null; unitId?: string | null
  fixedPreview?: OrderRecommendations; fixedLoading?: boolean; fixedError?: string; onRefreshFixed?: () => void
}
type AnalysisState = { key: string; error?: string }
type AnalysisSnapshot = { key: string; contextKey: string; result: PriceAnalysisResult }

const hiddenWarnings = new Set([
  'Model is advisory; release gates remain unverified.',
  'Held-out lane evidence is insufficient for verification.',
  'Value is far from the training mean; this diagnostic is not a calibrated probability or range guarantee.',
])

export default function PriceAnalysisPanel({ load, bid, unitId = null, fixedPreview, fixedLoading, fixedError, onRefreshFixed }: Props) {
  const [state, setState] = useState<AnalysisState | null>(null)
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [retry, setRetry] = useState(0)
  const started = useRef(false)
  const completedRequest = useRef<string | null>(null)
  const normalizedBid = bid ? normalizeBid(bid) : null
  // Debounce the analysed bid so dragging the margin slider coalesces into one
  // request instead of aborting and restarting on every tick.
  const [settledBid, setSettledBid] = useState<string | null>(normalizedBid)
  const key = priceAnalysisKey(load, settledBid, unitId)
  const contextKey = priceAnalysisKey(load, null, unitId)
  const requestKey = JSON.stringify([key, retry])
  const externalId = load.external_id
  const current = state?.key === key ? state : null
  const result = snapshot?.contextKey === contextKey ? snapshot.result : null
  const matchingResult = snapshot?.key === key ? result : null
  const advice = result ? priceAdvice(result) : null
  const unavailableAdvice = result?.model_response?.advisory
  const modelError = unavailableAdvice?.status === 'unavailable' ? unavailableAdvice.error : null
  const supported = load.order_type === 'open_bid'
  const loading = supported && !!settledBid && !current

  useEffect(() => {
    // While a slider is moving the analysed bid lags the input; only the settled
    // value is worth a request. An unchanged value re-renders immediately.
    if (normalizedBid === settledBid) return
    const timer = setTimeout(() => setSettledBid(normalizedBid), 400)
    return () => clearTimeout(timer)
  }, [normalizedBid, settledBid])

  useEffect(() => {
    if (!supported || !settledBid || completedRequest.current === requestKey) return
    const controller = new AbortController()
    // Opening the tab starts immediately; subsequent slider/input edits coalesce.
    const timer = setTimeout(() => {
      started.current = true
      void (async () => {
        try {
          const requestId = crypto.randomUUID()
          const response = await getPriceAnalysis(externalId, {
            bid_rate: settledBid, request_id: requestId,
            ...(unitId ? { unit_id: unitId } : {}),
          }, { signal: controller.signal })
          const body = await response.json().catch(() => null)
          if (controller.signal.aborted) return
          if (!response.ok) {
            const feedback = apiFeedback(body, response.status)
            throw new Error([feedback.message, ...feedback.details].join(' '))
          }
          const result = verifyPriceAnalysis(body, requestId, externalId, settledBid, unitId)
          completedRequest.current = requestKey
          setSnapshot({ key, contextKey, result })
          setState({ key })
        } catch (error) {
          if (!controller.signal.aborted) {
            completedRequest.current = requestKey
            setState({ key, error: error instanceof TypeError ? 'Unable to connect to price analysis. Check your connection and try again.' : error instanceof Error ? error.message : 'Price analysis could not be read.' })
          }
        }
      })()
    }, started.current ? 500 : 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [key, contextKey, requestKey, externalId, settledBid, unitId, supported])

  if (!supported) return <FixedRatePriceAnalysis load={load} report={fixedPreview} loading={fixedLoading} error={fixedError} onRefresh={onRefreshFixed} />

  const warnings = [...new Set([
    ...(result?.warnings ?? []), ...(result?.model_response?.warnings ?? []).map((warning) => warning.message),
    ...(advice?.warnings ?? []).map((warning) => warning.message),
  ])].filter(warning => !hiddenWarnings.has(warning.trim()))

  return <section aria-label="Price analysis" className="space-y-3" aria-busy={loading}>
    <div>
      <h3 className="text-base font-semibold">Price analysis</h3>
    </div>
    {loading && <p role="status" className="flex items-center gap-2 text-xs text-base-content/60"><span className="loading loading-spinner loading-xs" />Updating analysis…</p>}
    {!settledBid && <p className="text-xs text-base-content/60">Enter a valid bid amount to request price analysis.</p>}
    {result && !matchingResult && loading && <p className="text-xs text-base-content/60">Showing the last analysis while the new bid is priced…</p>}
    {current?.error && <p role="alert" className="text-xs text-error">{current.error}</p>}
    {(current?.error || matchingResult?.error) && <button type="button" className="btn btn-ghost btn-xs" disabled={loading} onClick={() => { setState(null); setRetry((value) => value + 1) }}>Retry analysis</button>}
    {result && <>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className={`badge badge-sm badge-soft ${result.advisory_status === 'model' ? 'badge-info' : 'badge-warning'}`}>{result.advisory_status === 'model' ? 'Model advisory' : result.advisory_status === 'baseline' ? 'Baseline fallback' : 'Advice unavailable'}</span>
        <span className="text-base-content/55">{timestamp(result.analyzed_at_wall)}</span>
      </div>
      {result.error && <div role="status" className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs">
        <p>{result.error.message}</p>
        {!!result.error.fields.length && <p className="mt-1 text-base-content/65">Check: {result.error.fields.map(label).join(', ')}.</p>}
      </div>}
      {modelError && <details className="text-xs text-base-content/65"><summary className="cursor-pointer">Advice details</summary>
        {modelError.message && <p className="mt-2">{modelError.message}</p>}
        {modelError.errors?.map((error, index) => <p key={index} className="mt-1">{error.field ? `${label(error.field)}: ` : ''}{error.message}</p>)}
        {modelError.code && <p className="mt-1">{label(modelError.code.toLowerCase())}</p>}
      </details>}
      {!matchingResult && <p className="text-[11px] text-base-content/60">Your bid marker uses the last analyzed cost and reference prices.{loading ? '' : ' Retry analysis to refresh these references.'}</p>}
      <PriceAnalysisChart result={result} bid={normalizedBid} />
      {matchingResult && <PriceAnalysisMetrics result={matchingResult} />}
      {result.deterministic_evaluation.primary_rejection?.message && <p className="text-xs text-warning">{result.deterministic_evaluation.primary_rejection.message}</p>}
      {warnings.length > 0 && <ul className="space-y-1.5 text-xs text-warning">{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
      {advice && <p className="text-[11px] text-base-content/55">{advice.model_version ? `Model: ${advice.model_version}. ` : ''}{advice.release_status ? `Release: ${label(advice.release_status)}. ` : ''}{advice.uncertainty.status === 'not_calibrated' ? 'No calibrated confidence interval. ' : ''}{advice.baseline_reference.reference_source ? `Historical reference: ${label(advice.baseline_reference.reference_source)}.` : ''}</p>}
    </>}
    <p className="text-[11px] text-base-content/55">Analysis does not change your bid or submit a booking. Submission uses the existing eligibility checks.</p>
  </section>
}
