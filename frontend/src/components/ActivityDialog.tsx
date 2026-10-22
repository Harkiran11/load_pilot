import { useEffect, useRef } from 'react'
import type { ActivityView } from '../api/activity'
import { activityColor } from '../utils/activity'
import { timestamp } from '../utils/load-format'
import Field from './LoadField'

export default function ActivityDialog({ event, onClose }: { event: ActivityView; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)

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

  return (
    <dialog ref={dialogRef} aria-labelledby="activity-dialog-title"
      className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-2xl overflow-hidden rounded-2xl border border-base-300 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40"
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
      }}
    >
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 p-5">
          <h2 id="activity-dialog-title" className="text-lg font-semibold">Activity details</h2>
          <button type="button" className="btn btn-ghost btn-circle btn-sm" aria-label="Close activity details" onClick={onClose}>✕</button>
        </header>
        <div className="min-h-0 space-y-6 overflow-y-auto p-5">
          <span className={`badge badge-soft ${activityColor(event.event_type)}`}>{event.event_type}</span>
          <p className="whitespace-pre-wrap break-words">{event.message}</p>
          <dl className="grid grid-cols-2 gap-4">
            <Field title="Order">{event.external_id ?? 'System event'}</Field>
            <Field title="Recorded time">{timestamp(event.occurred_at_wall)}</Field>
            <Field title="Simulation time">{timestamp(event.occurred_at_sim)}</Field>
            <Field title="Event ID">{event.event_id}</Field>
            <Field title="Shipment ID">{event.shipment_id}</Field>
            <Field title="Action ID">{event.action_id}</Field>
          </dl>
          <section aria-labelledby="activity-context-title" className="space-y-3 border-t border-base-300 pt-5">
            <h3 id="activity-context-title" className="font-semibold">Event context</h3>
            {Object.keys(event.details).length === 0 ? <p className="text-sm text-base-content/60">No additional details recorded.</p> : (
              <dl className="space-y-3">{Object.entries(event.details).map(([key, value]) => (
                <div key={key} className="rounded-lg bg-base-200 p-3">
                  <dt className="text-xs font-medium text-base-content/60">{key.replaceAll('_', ' ')}</dt>
                  <dd className="mt-1 whitespace-pre-wrap break-words text-sm">{value === null ? '—' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}</dd>
                </div>
              ))}</dl>
            )}
          </section>
          <p className="text-xs text-base-content/60">Dates shown in your local time. Simulation time is separate from the recorded server time.</p>
        </div>
        <footer className="flex shrink-0 justify-end border-t border-base-300 p-4"><button type="button" className="btn btn-primary btn-sm" onClick={onClose}>Close</button></footer>
      </div>
    </dialog>
  )
}
