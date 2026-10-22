import { useEffect, useRef, useState } from 'react'
import { getAutopilotAttempt } from '../api/autopilot'
import type { AutopilotAttempt } from '../api/types'
import { autopilotReason, autopilotStatusColor } from '../utils/autopilot'
import { amount, label, quantity, timestamp } from '../utils/load-format'
import Field from './LoadField'

export default function AutopilotAttemptDialog({ attempt, onClose }: { attempt: AutopilotAttempt; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ref.current?.showModal()
    const dialog = ref.current
    return () => { dialog?.close(); document.body.style.overflow = overflow }
  }, [])
  const runId = attempt.run_id
  const attemptId = attempt.attempt_id
  const [state, setState] = useState<{ attemptId: string; detail: AutopilotAttempt; loading: boolean }>({
    attemptId, detail: attempt, loading: true,
  })
  useEffect(() => {
    const controller = new AbortController()
    getAutopilotAttempt(runId, attemptId, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const body = await response.json()
        if (!controller.signal.aborted) setState({ attemptId, detail: body, loading: false })
      })
      .catch(() => { if (!controller.signal.aborted) setState({ attemptId, detail: attempt, loading: false }) })
    return () => controller.abort()
  }, [runId, attemptId, attempt])
  // A stale response for a previously opened attempt never renders.
  const fresh = state.attemptId === attemptId
  const current = fresh ? state.detail : attempt
  const loading = fresh ? state.loading : true
  const evaluation = current.evaluation_snapshot
  const economics = evaluation.economics
  return <dialog ref={ref} aria-labelledby="autopilot-attempt-title" className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-3xl overflow-hidden rounded-xl border border-base-300 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40" onCancel={event => { event.preventDefault(); onClose() }} onClick={event => {
    const box = event.currentTarget.getBoundingClientRect()
    if (event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) onClose()
  }}><div className="flex max-h-[90dvh] flex-col">
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-base-300 p-5"><h2 id="autopilot-attempt-title" className="min-w-0 truncate font-semibold">{current.external_id} · Autopilot decision</h2><button type="button" className="btn btn-ghost btn-circle btn-sm" aria-label="Close autopilot decision" onClick={onClose}>✕</button></header>
    <div className="min-h-0 space-y-5 overflow-y-auto p-5">
      <div className="flex flex-wrap gap-2"><span className={`badge badge-soft ${current.order_type === 'fixed_rate' ? 'badge-success' : 'badge-info'}`}>{label(current.order_type)}</span><span className={`badge badge-soft ${autopilotStatusColor(current.decision ?? '')}`}>{current.decision ?? 'Evaluating'}</span><span className={`badge badge-soft ${autopilotStatusColor(current.status)}`}>{label(current.status)}</span></div>
      {current.message && <p className="text-sm">{current.message}</p>}
      {current.reason_codes.length > 0 && <ul className="space-y-2 text-xs text-warning">{current.reason_codes.map(code => <li key={code}>{autopilotReason(code)}</li>)}</ul>}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Field title="Bid / offer">{amount(current.bid_rate ?? economics?.payout ?? null)}</Field><Field title="Estimated total cost">{amount(economics?.total_cost ?? null)}</Field><Field title="Estimated profit">{amount(economics?.gross_profit ?? null)}</Field><Field title="Margin">{quantity(economics?.margin_pct ?? null, '%')}</Field><Field title="Empty miles">{quantity(evaluation.deadhead_miles ?? null, 'mi')}</Field><Field title="Selected unit">{current.candidate_unit_id}</Field>
        <Field title="Evaluated">{timestamp(current.decided_at_wall)}</Field><Field title="Finished">{timestamp(current.finished_at_wall)}</Field><Field title="Action ID">{current.action_id}</Field>
        <Field title="Allocation plan">{current.allocation_plan_id}</Field>
      </dl>
      {evaluation.checks && <section><h3 className="text-sm font-semibold">Dispatch checks</h3><dl className="mt-3 grid grid-cols-2 gap-2 text-xs">{Object.entries(evaluation.checks).map(([check, passed]) => <div key={check} className="flex justify-between gap-2"><dt>{label(check).replace(/ passed$/, '')}</dt><dd className={evaluation.skipped_checks?.includes(check) ? 'text-base-content/50' : passed ? 'text-success' : 'text-error'}>{evaluation.skipped_checks?.includes(check) ? 'Skipped' : passed ? 'Passed' : 'Blocked'}</dd></div>)}</dl></section>}
      {evaluation.primary_rejection && <p className="text-xs text-warning">{evaluation.primary_rejection.message}</p>}
      {loading && <p role="status" className="text-[11px] text-base-content/55">Loading full decision details…</p>}
      <details className="rounded-lg border border-base-300 p-3"><summary className="cursor-pointer text-xs font-semibold">Model request &amp; response</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px]">{Object.keys(current.model_snapshot).length ? JSON.stringify(current.model_snapshot, null, 2) : current.order_type === 'fixed_rate' ? 'Fixed-rate offers use deterministic economics without a model request.' : 'No model response was recorded. Check the decision reasons.'}</pre></details>
      <details className="rounded-lg border border-base-300 p-3"><summary className="cursor-pointer text-xs font-semibold">Full evaluation</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px]">{JSON.stringify(evaluation, null, 2)}</pre></details>
      <p className="text-[11px] text-base-content/55">A submitted bid is awaiting its award outcome. Follow Bid Logs and Shipments for the result. All profit figures here are estimates.</p>
    </div>
  </div></dialog>
}
