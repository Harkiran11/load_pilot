import { useEffect, useRef, useState } from 'react'
import { subscribeFleet } from '../api/fleet'
import type { LiveFleetState } from '../api/fleet'
import EmptyPanel from '../components/EmptyPanel'
import FleetSchedule from '../components/FleetSchedule'
import PageSection from '../components/PageSection'
import { filterFleet } from '../utils/fleet'
import { label, timestamp } from '../utils/load-format'
import { usePageStatus } from '../context/page-status'

const pageSize = 20

export default function FleetPage() {
  const [{ data, connection, updating, error, revision }, setStream] = useState<LiveFleetState>({
    data: null, connection: 'connecting', updating: false, error: null, revision: 0,
  })
  const [search, setSearch] = useState('')
  usePageStatus(connection, error)
  const [terminal, setTerminal] = useState('all')
  const [status, setStatus] = useState('all')
  const [page, setPage] = useState(1)
  const filters = useRef({ search: '', terminal: 'all', status: 'all' })
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => subscribeFleet((state) => {
    setStream(state)
    if (state.data) {
      const current = filters.current
      const count = filterFleet(state.data.units, current.search, current.terminal, current.status).length
      setPage((value) => Math.min(value, Math.max(1, Math.ceil(count / pageSize))))
      const activeUnits = state.data.units
      setSelectedId((current) => current && activeUnits.some((unit) => unit.unit_id === current) ? current : null)
    }
  }), [])

  const units = data?.units ?? []
  const visibleUnits = filterFleet(units, search, terminal, status)
  const totalPages = Math.max(1, Math.ceil(visibleUnits.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const offset = (currentPage - 1) * pageSize
  const pageUnits = visibleUnits.slice(offset, offset + pageSize)
  const selectedUnit = units.find((unit) => unit.unit_id === selectedId)
  const projections = new Map(data?.projections.map((projection) => [projection.unit_id, projection]))
  const terminals = [...new Set(units.map((unit) => unit.terminal_code).filter((code): code is string => !!code))].sort()
  const statuses = [...new Set(units.map((unit) => unit.status))].sort()

  function changeFilter(field: 'search' | 'terminal' | 'status', value: string) {
    filters.current = { ...filters.current, [field]: value }
    if (field === 'search') setSearch(value)
    if (field === 'terminal') setTerminal(value)
    if (field === 'status') setStatus(value)
    setPage(1)
  }

  return (
    <PageSection title="Fleet & Schedule" description="Inspect fleet availability, drivers, and confirmed assignment timelines.">
      {error && <div role="alert" className="alert alert-warning"><div><p>{error}</p><p className="mt-1 text-sm">{data ? 'Showing previous fleet data. ' : ''}Updates will retry automatically.</p></div></div>}

      {data && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { title: 'Total fleet', value: units.length },
            { title: 'Available', value: units.filter((unit) => unit.status === 'available').length },
            { title: 'Assigned / in transit', value: units.filter((unit) => ['assigned', 'in_transit'].includes(unit.status)).length },
            { title: 'Maintenance', value: units.filter((unit) => unit.status === 'maintenance').length },
          ].map((summary) => <div key={summary.title} className="stat rounded-xl border border-base-300 bg-base-100 p-4"><div className="stat-title text-xs">{summary.title}</div><div className="stat-value text-2xl text-primary">{summary.value}</div></div>)}
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1.5 text-xs font-medium">Search units or drivers<input type="search" className="input input-sm w-64" placeholder="Unit, name, employee code…" value={search} onChange={(event) => changeFilter('search', event.target.value)} /></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Home hub<select className="select select-sm w-40" value={terminal} onChange={(event) => changeFilter('terminal', event.target.value)}><option value="all">All hubs</option>{terminals.map((code) => <option key={code}>{code}</option>)}</select></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Status<select className="select select-sm w-44" value={status} onChange={(event) => changeFilter('status', event.target.value)}><option value="all">All statuses</option>{statuses.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label>
      </div>

      <div aria-busy={data === null || updating}>
        {!data && <div role="status" className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8"><span className="loading loading-spinner text-primary" aria-hidden="true" />Loading fleet and schedules…</div>}
        {data && units.length === 0 && <EmptyPanel title="No fleet units yet" description="Units will appear after the backend synchronizes the fleet roster." />}
        {data && units.length > 0 && visibleUnits.length === 0 && <EmptyPanel title="No matching units" description="Try another driver, hub, or status." />}
        {data && visibleUnits.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-base-300 bg-base-100">
            <div className="overflow-x-auto">
              <table className="table table-sm">
                <caption className="sr-only">Fleet roster. Select a unit to inspect its schedule. Availability dates use simulation time.</caption>
                <thead><tr>{['Unit / driver', 'Status', 'Current hub', 'Next hub', 'Available at', 'Schedule'].map((heading) => <th key={heading} scope="col">{heading}</th>)}</tr></thead>
                <tbody>{pageUnits.map((unit) => {
                  const projection = projections.get(unit.unit_id)
                  return (
                    <tr key={unit.unit_id} className={selectedUnit?.unit_id === unit.unit_id ? 'bg-primary/5' : 'hover:bg-base-200/60'}>
                      <th scope="row" className="font-normal"><p className="font-semibold text-primary">{unit.unit_number}</p><p className="mt-1 whitespace-nowrap text-xs text-base-content/60">{unit.first_name} {unit.last_name}</p></th>
                      <td><span className={`badge badge-sm badge-soft capitalize whitespace-nowrap ${unit.status === 'available' ? 'badge-success' : ['assigned', 'in_transit'].includes(unit.status) ? 'badge-primary' : 'badge-neutral'}`}>{label(unit.status)}</span></td>
                      <td>{unit.current_terminal_code ?? '—'}</td>
                      <td>{unit.next_terminal_code ?? projection?.next_terminal_code ?? '—'}</td>
                      <td className="whitespace-nowrap text-xs">
                        {timestamp(projection ? projection.available_at : unit.available_at)}
                        {projection?.status === 'unknown' && <p className="mt-1 text-base-content/60">Availability unknown</p>}
                      </td>
                      <td><button type="button" className="btn btn-ghost btn-xs whitespace-nowrap text-primary" aria-controls="unit-schedule" aria-haspopup="dialog" aria-label={`View schedule for unit ${unit.unit_number}`} onClick={() => setSelectedId(unit.unit_id)}>View schedule</button></td>
                    </tr>
                  )
                })}</tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-4 py-3 text-xs text-base-content/60">
              <p role="status">Showing {offset + 1}–{Math.min(offset + pageSize, visibleUnits.length)} of {visibleUnits.length}</p>
              <nav aria-label="Fleet pagination" className="flex items-center gap-3"><span>Page {currentPage} of {totalPages}</span><div className="join">
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === 1} aria-label="First page" onClick={() => setPage(1)}>{'<<'}</button>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === 1} aria-label="Previous page" onClick={() => setPage(currentPage - 1)}>{'<'}</button>
                <span className="btn btn-sm join-item pointer-events-none" aria-current="page">{currentPage}</span>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === totalPages} aria-label="Next page" onClick={() => setPage(currentPage + 1)}>{'>'}</button>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === totalPages} aria-label="Last page" onClick={() => setPage(totalPages)}>{'>>'}</button>
              </div></nav>
            </div>
          </div>
        )}
      </div>
      {selectedUnit && data && <FleetSchedule key={selectedUnit.unit_id} unit={selectedUnit} projection={projections.get(selectedUnit.unit_id)} simTime={data.system.sim_time_utc} revision={revision} onClose={() => setSelectedId(null)} />}
    </PageSection>
  )
}
