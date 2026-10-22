import { useEffect, useRef, useState } from 'react'
import { getFleetUnit } from '../api/fleet'
import type { UnitDetail, UnitProjection, UnitView } from '../api/fleet'
import { deliveryPhase } from '../utils/fleet'
import { label, timestamp } from '../utils/load-format'
import Field from './LoadField'

type FleetScheduleProps = {
  unit: UnitView
  projection: UnitProjection | undefined
  simTime: string | null
  revision: number
  onClose: () => void
}

export default function FleetSchedule({ unit, projection, simTime, revision, onClose }: FleetScheduleProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [result, setResult] = useState<{ id: string; revision: number; detail: UnitDetail | null; error: string | null } | null>(null)

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
    getFleetUnit(unit.unit_id, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Unable to load assignment details (HTTP ${response.status}).`)
        const detail: UnitDetail = await response.json()
        if (!controller.signal.aborted) setResult({ id: unit.unit_id, revision, detail, error: null })
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setResult({ id: unit.unit_id, revision, detail: null, error: cause instanceof Error ? cause.message : 'Unable to load assignment details.' })
      })
    return () => controller.abort()
  }, [unit.unit_id, revision])

  const detail = result?.id === unit.unit_id ? result.detail : null
  const error = result?.id === unit.unit_id ? result.error : null
  const updating = result?.id !== unit.unit_id || result.revision !== revision
  const assignment = detail?.actual_assignment
  const deliveries = [...(projection?.deliveries ?? [])].sort((a, b) => Date.parse(a.pickup_at) - Date.parse(b.pickup_at))

  return (
    <dialog
      ref={dialogRef}
      id="unit-schedule"
      aria-labelledby="unit-schedule-title"
      className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-5xl overflow-hidden rounded-2xl border border-base-300 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40"
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
      }}
    >
    <div className="flex max-h-[90dvh] flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-base-300 p-5">
        <div>
          <h2 id="unit-schedule-title" className="text-lg font-semibold">Unit {unit.unit_number} · Schedule</h2>
          <p className="mt-1 text-sm text-base-content/60">{unit.first_name} {unit.last_name} · {unit.employee_code}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="badge badge-soft badge-primary capitalize">{label(unit.status)}</span>
          <button type="button" className="btn btn-ghost btn-circle btn-sm" aria-label="Close unit schedule" onClick={onClose}>✕</button>
        </div>
      </div>
      <div className="grid min-h-0 gap-7 overflow-y-auto p-5 lg:grid-cols-[1fr_1.3fr]">
        <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-4">
            <Field title="Home hub">{unit.terminal_code}</Field>
            <Field title="Current hub">{unit.current_terminal_code}</Field>
            <Field title="Next hub">{unit.next_terminal_code ?? projection?.next_terminal_code}</Field>
            <Field title="Available at (simulation)">{timestamp(projection ? projection.available_at : unit.available_at)}</Field>
            <Field title="Latitude">{unit.current_latitude}</Field>
            <Field title="Longitude">{unit.current_longitude}</Field>
            <Field title="Last synced">{timestamp(unit.last_synced_at)}</Field>
            <Field title="Projected availability">{projection ? label(projection.status) : 'Unknown'}</Field>
            <Field title="Projection as of">{timestamp(projection?.at ?? null)}</Field>
            <Field title="Projection source">{projection ? label(projection.source) : 'Unknown'}</Field>
          </dl>
          {projection && (projection.uncertain_action_ids.length > 0 || projection.rejection_reasons.length > 0) && (
            <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm">
              <p className="font-medium">Availability needs review</p>
              <ul className="mt-2 list-inside list-disc text-base-content/70">
                {projection.rejection_reasons.map((reason) => <li key={reason}>{label(reason)}</li>)}
              </ul>
              {projection.uncertain_action_ids.length > 0 && <p className="mt-2">{projection.uncertain_action_ids.length} unresolved submission(s).</p>}
            </div>
          )}
          <div className="border-t border-base-300 pt-4" aria-busy={updating}>
            <h3 className="text-sm font-semibold">Current confirmed assignment</h3>
            {error && <p role="alert" className="mt-2 text-sm text-error">{error} Updates will retry automatically.</p>}
            {updating && <p role="status" className="mt-2 text-sm text-base-content/60">Updating assignment…</p>}
            {assignment ? (
              <dl className="mt-3 grid grid-cols-2 gap-4">
                <Field title="Status">{label(assignment.assignment_status)}</Field>
                <Field title="Confirmed">{timestamp(assignment.confirmed_at)}</Field>
                <Field title="Busy from">{timestamp(assignment.busy_from)}</Field>
                <Field title="Busy until">{timestamp(assignment.busy_until)}</Field>
              </dl>
            ) : !updating && !error && <p className="mt-2 text-sm text-base-content/60">No current confirmed assignment.</p>}
          </div>
        </div>

        <div>
          <h3 className="font-semibold">Confirmed delivery timeline</h3>
          <p className="mt-1 text-xs text-base-content/60">Bookings retained since the latest fleet snapshot. Times are displayed locally and compared with simulation time.</p>
          {deliveries.length === 0 ? (
            <p className="mt-5 rounded-lg bg-base-200 p-4 text-sm text-base-content/60">No confirmed delivery timeline is available for this unit.</p>
          ) : (
            <ol className="mt-5 space-y-5 border-l-2 border-primary/20 pl-5">
              {deliveries.map((delivery) => {
                const phase = deliveryPhase(delivery, simTime)
                return (
                  <li key={delivery.assignment_id} className="relative rounded-lg border border-base-300 p-4">
                    <span aria-hidden="true" className="absolute top-5 -left-7 size-3 rounded-full bg-primary" />
                    <div className="flex flex-wrap justify-between gap-2">
                      <p className="font-semibold text-primary">{delivery.external_id}</p>
                      <span className={`badge badge-sm badge-soft capitalize ${phase === 'in_transit' ? 'badge-primary' : phase === 'completed' ? 'badge-success' : 'badge-neutral'}`}>{label(phase)}</span>
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-3">
                      <Field title="Pickup">{timestamp(delivery.pickup_at)}</Field>
                      <Field title="Delivery">{timestamp(delivery.delivery_at)}</Field>
                      <Field title="Destination hub">{delivery.terminal_code}</Field>
                      <Field title="Hub resolution">{label(delivery.terminal_resolution)}</Field>
                    </dl>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </div>
      <footer className="flex shrink-0 justify-end border-t border-base-300 p-4"><button type="button" className="btn btn-primary btn-sm" onClick={onClose}>Close</button></footer>
    </div>
    </dialog>
  )
}
