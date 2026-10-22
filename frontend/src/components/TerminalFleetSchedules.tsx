import { useEffect, useState } from 'react'
import { getFleetSchedules } from '../api/fleet'
import type { UnitSchedule } from '../api/fleet'
import type { Page } from '../api/types'
import { label, timestamp } from '../utils/load-format'

const pageSize = 20

export default function TerminalFleetSchedules({ code, revision, terminalCodes }: {
  code: string
  revision: number
  terminalCodes: Record<string, string>
}) {
  const [page, setPage] = useState(1)
  const [history, setHistory] = useState(false)
  const [retry, setRetry] = useState(0)
  const [result, setResult] = useState<{ key: string; scope: string; data: Page<UnitSchedule> | null; error: string | null } | null>(null)
  const scope = JSON.stringify([code, page, history])
  const requestKey = JSON.stringify([scope, revision, retry])
  const data = result?.scope === scope ? result.data : null
  const updating = result?.key !== requestKey
  const error = result?.key === requestKey ? result.error : null

  useEffect(() => {
    const controller = new AbortController()
    // Coalesce the initial load and closely spaced order/terminal snapshots.
    const timer = setTimeout(() => {
      void getFleetSchedules({ terminal: code, limit: pageSize, offset: (page - 1) * pageSize, include_history: history }, { signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error('Unable to load this terminal’s fleet schedules. Please try again.')
          const data: Page<UnitSchedule> = await response.json()
          if (!Array.isArray(data.items) || !Number.isFinite(data.total) || !data.items.every((unit) => Array.isArray(unit.assignments) && Array.isArray(unit.reservations))) throw new Error('The fleet schedule response could not be verified.')
          if (controller.signal.aborted) return
          const pages = Math.max(1, Math.ceil(data.total / pageSize))
          setPage((current) => Math.min(current, pages))
          setResult({ key: requestKey, scope, data, error: null })
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted) setResult((current) => ({ key: requestKey, scope, data: current?.scope === scope ? current.data : null, error: cause instanceof Error && !(cause instanceof TypeError) ? cause.message : 'Unable to connect to fleet schedules. Please try again.' }))
        })
    }, 150)
    return () => { clearTimeout(timer); controller.abort() }
  }, [code, page, history, scope, requestKey])

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))
  return <section aria-label={`${code} fleet schedules`} className="space-y-4 p-4 sm:p-5" aria-busy={updating}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="text-sm font-semibold">{code} · Fleet &amp; schedules</h3><p className="mt-1 text-xs text-base-content/60">{data ? `${data.total} units · ` : ''}Expand a unit to see its assignments and provisional holds.</p></div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" className="checkbox checkbox-xs" checked={history} onChange={(event) => { setHistory(event.target.checked); setPage(1) }} />Include past assignments</label>
        <button type="button" className="btn btn-ghost btn-xs" disabled={updating} onClick={() => setRetry((value) => value + 1)}>Refresh</button>
      </div>
    </div>
    {updating && <p role="status" className="flex items-center gap-2 text-xs text-base-content/60"><span className="loading loading-spinner loading-xs" />{data ? 'Updating schedules…' : 'Loading fleet schedules…'}</p>}
    {error && <p role="alert" className="text-xs text-error">{error}{data ? ' Showing previous schedules.' : ''}</p>}
    {data?.total === 0 && <p className="rounded-lg bg-base-100 p-4 text-sm text-base-content/60">No fleet units are currently assigned to this terminal.</p>}
    {data && data.items.length > 0 && <div className="space-y-2">{data.items.map((unit) => <details key={unit.unit_id} className="group rounded-lg border border-base-300 bg-base-100">
      <summary className="cursor-pointer px-4 py-3 marker:text-primary">
        <span className="ml-1 inline-flex w-[calc(100%_-_1.5rem)] flex-wrap items-center justify-between gap-2 align-middle">
          <span className="text-sm font-semibold text-primary">Unit {unit.unit_number}</span>
          <span className="inline-flex flex-wrap items-center gap-3 text-xs">
            <span className={`badge badge-sm badge-soft capitalize ${unit.status === 'available' ? 'badge-success' : ['assigned', 'in_transit'].includes(unit.status) ? 'badge-info' : 'badge-neutral'}`}>{label(unit.status)}</span>
            <span>{unit.assignments.length} assignments · {unit.reservations.length} holds</span>
            <span className="text-primary underline underline-offset-2 group-open:hidden">View schedule</span>
          </span>
        </span>
      </summary>
      <div className="space-y-4 border-t border-base-300 p-4">
        <div className="flex flex-wrap justify-between gap-2 text-xs text-base-content/60"><p>Available at: {timestamp(unit.available_at)}</p><p>Simulation time: {timestamp(unit.as_of_sim_time)}</p></div>
        <section className="space-y-2"><h4 className="text-xs font-semibold">Confirmed assignments</h4>
          {unit.assignments.length === 0 ? <p className="text-xs text-base-content/55">No {history ? 'recorded' : 'current or upcoming'} confirmed assignments.</p> : unit.assignments.map((assignment) => <article key={assignment.assignment_id} className="rounded-lg bg-base-200 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><p className="break-all text-sm font-medium">{assignment.external_id}</p><span className={`badge badge-xs badge-soft capitalize ${assignment.assignment_status === 'completed' ? 'badge-success' : assignment.assignment_status === 'cancelled' ? 'badge-neutral' : 'badge-info'}`}>{label(assignment.assignment_status)}</span></div>
            <p className="mt-2 text-xs text-base-content/65">{terminalCodes[assignment.origin_terminal_id] ?? 'Unknown origin'} → {terminalCodes[assignment.destination_terminal_id] ?? 'Unknown destination'}</p>
            <dl className="mt-3 grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
              {[
                ['Pickup', assignment.busy_from], ['Delivery', assignment.busy_until],
                ['Occupied from', assignment.occupied_from ?? assignment.busy_from],
                ['Occupied until (including turnaround)', assignment.occupied_until],
              ].map(([title, time]) => <div key={title}><dt className="text-base-content/55">{title}</dt><dd className="mt-1">{timestamp(time)}</dd></div>)}
            </dl>
          </article>)}
        </section>
        <section className="space-y-2"><h4 className="text-xs font-semibold">Provisional holds</h4>
          {unit.reservations.length === 0 ? <p className="text-xs text-base-content/55">No provisional reservations.</p> : unit.reservations.map((hold) => <article key={hold.action_id} className="space-y-2 rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2"><p className="break-all font-medium">{hold.external_id}</p><span className="badge badge-xs badge-soft badge-warning capitalize">{label(hold.request_status)}</span></div>
            <p>{label(hold.action_type)} reservation · Awaiting confirmation</p>
            <p>{timestamp(hold.busy_from)} → {timestamp(hold.busy_until)}</p>
          </article>)}
        </section>
      </div>
    </details>)}</div>}
    {data && data.total > pageSize && <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-base-content/60"><p>Showing {data.offset + 1}–{Math.min(data.offset + data.items.length, data.total)} of {data.total} units</p><nav aria-label={`${code} fleet pagination`} className="flex items-center gap-2"><button type="button" className="btn btn-ghost btn-xs" disabled={updating || page === 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>{page} / {pages}</span><button type="button" className="btn btn-ghost btn-xs" disabled={updating || page >= pages} onClick={() => setPage((value) => value + 1)}>Next</button></nav></div>}
  </section>
}
