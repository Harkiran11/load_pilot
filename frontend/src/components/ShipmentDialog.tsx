import { useEffect, useRef, useState } from 'react'
import { getLoad } from '../api/orders'
import type { LoadDetail, LoadView } from '../api/shipments'
import { label, timestamp } from '../utils/load-format'
import { shipmentStage } from '../utils/shipments'
import Field from './LoadField'
import LoadInformation from './LoadInformation'

export default function ShipmentDialog({ shipment, revision, onClose }: {
  shipment: LoadView
  revision: number
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [result, setResult] = useState<{ revision: number; detail: LoadDetail | null; error: string | null } | null>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog?.showModal()
    return () => {
      dialog?.close()
      document.body.style.overflow = previousOverflow
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getLoad(shipment.external_id, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(`Unable to load booking details (HTTP ${response.status}).`)
      const detail: LoadDetail = await response.json()
      if (!controller.signal.aborted) setResult({ revision, detail, error: null })
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setResult({ revision, detail: null, error: cause instanceof Error ? cause.message : 'Unable to load booking details.' })
    })
    return () => controller.abort()
  }, [shipment.external_id, revision])

  const assignment = result?.detail?.actual_assignment
  const history = result?.detail?.assignment_history ?? []
  const updating = !result || result.revision !== revision
  const stage = shipmentStage(shipment.status)

  return (
    <dialog ref={dialogRef} aria-labelledby="shipment-dialog-title"
      className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-3xl overflow-hidden rounded-2xl border border-base-300 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40"
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
      }}
    >
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 p-6">
          <div className="min-w-0"><p className="text-xs text-base-content/60">Shipment information</p><h2 id="shipment-dialog-title" className="mt-1 break-all text-xl font-bold text-primary">{shipment.external_id}</h2></div>
          <button type="button" className="btn btn-ghost btn-circle btn-sm shrink-0" aria-label="Close shipment information" onClick={onClose}>✕</button>
        </header>
        <div className="min-h-0 space-y-7 overflow-y-auto p-6">
          <ol className="steps steps-horizontal w-full text-xs" aria-label="Shipment progress">
            {['Accepted', 'In transit', 'Delivered'].map((title, index) => <li key={title} className={`step ${stage >= index ? 'step-primary' : ''}`} aria-current={stage === index ? 'step' : undefined}>{title}</li>)}
          </ol>
          <section className="space-y-4 rounded-xl bg-base-200 p-4" aria-labelledby="shipment-assignment-title" aria-busy={updating}>
            <h3 id="shipment-assignment-title" className="font-semibold">Confirmed assignment</h3>
            {updating && <p role="status" className="text-sm text-base-content/60">Updating booking details…</p>}
            {result?.error && <p role="alert" className="text-sm text-error">{result.error} Details will retry on the next live update.</p>}
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Field title="Board unit">{shipment.board_assigned_unit_id}</Field>
              <Field title="Assigned hub">{shipment.assigned_terminal_code}</Field>
              <Field title="Assignment status">{assignment ? label(assignment.assignment_status) : 'Not available'}</Field>
              <Field title="Confirmed">{timestamp(assignment?.confirmed_at ?? null)}</Field>
              <Field title="Busy from">{timestamp(assignment?.busy_from ?? null)}</Field>
              <Field title="Busy until">{timestamp(assignment?.busy_until ?? null)}</Field>
              {assignment?.completed_at && <Field title="Completed">{timestamp(assignment.completed_at)}</Field>}
            </dl>
          </section>
          <LoadInformation load={shipment} />
          <section className="space-y-4 border-t border-base-300 pt-5" aria-labelledby="shipment-history-title">
            <h3 id="shipment-history-title" className="font-semibold">Assignment history</h3>
            {history.length === 0 ? <p className="text-sm text-base-content/60">{updating ? 'Loading assignment history…' : 'No assignment history available.'}</p> : (
              <ol className="space-y-3">{history.map((item) => (
                <li key={item.assignment_id} className="rounded-lg border border-base-300 p-4">
                  <div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-medium">Confirmed {timestamp(item.confirmed_at)}</p><span className="badge badge-sm badge-soft capitalize">{label(item.assignment_status)}</span></div>
                  <dl className="mt-3 grid grid-cols-2 gap-4">
                    <Field title="Busy from">{timestamp(item.busy_from)}</Field><Field title="Busy until">{timestamp(item.busy_until)}</Field>
                    {item.completed_at && <Field title="Completed">{timestamp(item.completed_at)}</Field>}
                    {item.cancelled_at && <Field title="Cancelled">{timestamp(item.cancelled_at)}</Field>}
                    <Field title="Local unit ID">{item.unit_id}</Field>
                  </dl>
                </li>
              ))}</ol>
            )}
          </section>
        </div>
        <footer className="flex shrink-0 justify-end border-t border-base-300 p-4"><button type="button" className="btn btn-primary btn-sm" onClick={onClose}>Close</button></footer>
      </div>
    </dialog>
  )
}
