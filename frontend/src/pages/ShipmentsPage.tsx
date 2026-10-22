import { useEffect, useRef, useState } from 'react'
import { subscribeShipments } from '../api/shipments'
import type { LiveShipmentsState, LoadView } from '../api/shipments'
import EmptyPanel from '../components/EmptyPanel'
import PageSection from '../components/PageSection'
import HistoryCleanup from '../components/HistoryCleanup'
import ShipmentDialog from '../components/ShipmentDialog'
import { amount, label, timestamp } from '../utils/load-format'
import { filterShipments, isDeliveryOverdue, sortShipments, deliveryProgress, estimatedHoursRemaining, remainingLabel, SHIPMENT_STAGES } from '../utils/shipments'
import type { ShipmentSort, ShipmentStatus } from '../utils/shipments'
import { usePageStatus } from '../context/page-status'

const pageSize = 20
const statusLabels: Record<string, string> = { accepted: 'Accepted', in_transit: 'In transit', completed: 'Delivered' }
const statusTone: Record<string, string> = { completed: 'badge-success', in_transit: 'badge-primary', accepted: 'badge-info' }

/** Three-dot stage rail showing how far a booking has travelled. */
function StageRail({ status }: { status: string }) {
  const stage = SHIPMENT_STAGES.indexOf(statusLabels[status] as (typeof SHIPMENT_STAGES)[number])
  return <ol className="shipment-rail" aria-label={`Stage ${Math.max(stage + 1, 1)} of ${SHIPMENT_STAGES.length}: ${statusLabels[status] ?? status}`}>
    {SHIPMENT_STAGES.map((name, index) => <li key={name} className={`shipment-rail-step${index <= stage ? ' is-done' : ''}${index === stage ? ' is-current' : ''}`}>
      <span aria-hidden="true" className="shipment-rail-dot" />
      <span className="shipment-rail-name">{name}</span>
    </li>)}
  </ol>
}

/** Progress bar across the pickup-to-delivery window at the simulation clock. */
function ProgressRail({ shipment, simTime }: { shipment: LoadView; simTime: string | null }) {
  const progress = deliveryProgress(shipment, simTime)
  const overdue = isDeliveryOverdue(shipment, simTime)
  const label2 = remainingLabel(estimatedHoursRemaining(shipment, simTime))
  if (progress === null) return <p className="text-xs text-base-content/50">No schedule</p>
  const percent = Math.round(progress * 100)
  return <div className="min-w-[120px]">
    <div
      className={`shipment-progress${overdue ? ' is-overdue' : shipment.status === 'completed' ? ' is-complete' : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-label={`Trip progress ${percent} percent${label2 ? `, ${label2}` : ''}`}
    >
      <span className="shipment-progress-fill" style={{ width: `${percent}%` }} />
    </div>
    <p className={`mt-1 text-xs tabular-nums ${overdue ? 'text-error' : 'text-base-content/60'}`}>{shipment.status === 'completed' ? 'Delivered' : label2 ?? `${percent}% of window`}</p>
  </div>
}

export default function ShipmentsPage() {
  const [{ shipments, system, connection, error, revision }, setStream] = useState<LiveShipmentsState>({
    shipments: null, system: null, connection: 'connecting', error: null, revision: 0,
  })
  const [search, setSearch] = useState('')
  usePageStatus(connection, error)
  const [status, setStatus] = useState<ShipmentStatus>('all')
  const [sort, setSort] = useState<ShipmentSort>('created')
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const filters = useRef<{ search: string; status: ShipmentStatus }>({ search: '', status: 'all' })
  const [selected, setSelected] = useState<LoadView | null>(null)
  const selectedShipment = selected ? shipments?.find((shipment) => shipment.external_id === selected.external_id) ?? selected : null

  useEffect(() => subscribeShipments((state) => {
    setStream(state)
    if (state.shipments) {
      const count = filterShipments(state.shipments, filters.current.search, filters.current.status).length
      setPage((value) => Math.min(value, Math.max(1, Math.ceil(count / pageSize))))
    }
  }), [refresh])

  const rows = shipments ?? []
  const visible = sortShipments(filterShipments(rows, search, status), sort, direction)
  const totalPages = Math.max(1, Math.ceil(visible.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const offset = (currentPage - 1) * pageSize
  const pageRows = visible.slice(offset, offset + pageSize)

  return (
    <PageSection title="Shipments" description="Track accepted shipments through delivery. Shipment statuses update automatically.">
      {error && <div role="alert" className="alert alert-warning"><div><p>{error}</p><p className="mt-1 text-sm">{shipments ? 'Showing previous shipments. ' : ''}Live updates will resume automatically when the service recovers.</p></div></div>}
      {shipments !== null && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { title: 'Total shipments', value: rows.length },
            { title: 'Accepted', value: rows.filter((shipment) => shipment.status === 'accepted').length },
            { title: 'In transit', value: rows.filter((shipment) => shipment.status === 'in_transit').length },
            { title: 'Delivered', value: rows.filter((shipment) => shipment.status === 'completed').length },
          ].map((summary) => <div key={summary.title} className="stat rounded-xl border border-base-300 bg-base-100 p-4"><div className="stat-title text-xs">{summary.title}</div><div className="stat-value text-2xl text-primary">{summary.value}</div></div>)}
        </div>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1.5 text-xs font-medium">Search<input type="search" className="input input-sm w-64" placeholder="Shipment, route, unit…" value={search} onChange={(event) => {
          setSearch(event.target.value)
          filters.current.search = event.target.value
          setPage(1)
        }} /></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Status<select className="select select-sm w-40" value={status} onChange={(event) => {
          const value = event.target.value as ShipmentStatus
          setStatus(value)
          filters.current.status = value
          setPage(1)
        }}><option value="all">All shipments</option><option value="accepted">Accepted</option><option value="in_transit">In transit</option><option value="completed">Delivered</option></select></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Sort by<select className="select select-sm w-40" value={sort} onChange={(event) => { setSort(event.target.value as ShipmentSort); setPage(1) }}><option value="created">Created date</option><option value="pickup">Pickup date</option><option value="delivery">Delivery date</option><option value="synced">Last synced</option></select></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Order<select className="select select-sm w-36" value={direction} onChange={(event) => { setDirection(event.target.value as 'asc' | 'desc'); setPage(1) }}><option value="desc">Newest first</option><option value="asc">Oldest first</option></select></label>
      </div>
      <div aria-busy={shipments === null}>
        {shipments === null && <div role="status" className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8"><span className="loading loading-spinner text-primary" aria-hidden="true" />Loading shipments…</div>}
        {shipments !== null && rows.length === 0 && <EmptyPanel title="No accepted shipments yet" description="Accepted offers and winning bids appear here after board acceptance." />}
        {shipments !== null && rows.length > 0 && visible.length === 0 && <EmptyPanel title="No matching shipments" description="Try another shipment, route, unit, or status." />}
        {shipments !== null && visible.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-base-300 bg-base-100">
            <div className="overflow-x-auto"><table className="table table-sm">
              <caption className="sr-only">Shipments, current status, schedule, unit assignment, and estimated total cost.</caption>
              <thead><tr>{['Shipment', 'Route', 'Status', 'Progress', 'Schedule', 'Unit', 'Estimated cost', 'Details'].map((title) => <th scope="col" key={title}>{title}</th>)}</tr></thead>
              <tbody>{pageRows.map((shipment) => (
                <tr key={shipment.external_id} className="hover:bg-base-200/60">
                  <th scope="row" className="font-normal"><p className="font-semibold text-primary">{shipment.external_id}</p><p className="mt-1 text-xs text-base-content/60">{label(shipment.order_type)}</p></th>
                  <td className="whitespace-nowrap"><p>{shipment.origin_terminal_code ?? 'Unknown'} → {shipment.destination_terminal_code ?? 'Unknown'}</p><p className="mt-1 text-xs text-base-content/60">{shipment.origin_city ?? '—'} → {shipment.destination_city ?? '—'}</p></td>
                  <td><span className={`badge badge-sm badge-soft whitespace-nowrap ${statusTone[shipment.status] ?? 'badge-ghost'}`}>{statusLabels[shipment.status] ?? label(shipment.status)}</span>{isDeliveryOverdue(shipment, system?.sim_time_utc ?? null) && <p className="mt-1 whitespace-nowrap text-xs text-error">Delivery overdue</p>}<div className="mt-2"><StageRail status={shipment.status} /></div></td>
                  <td className="align-top"><ProgressRail shipment={shipment} simTime={system?.sim_time_utc ?? null} /></td>
                  <td className="whitespace-nowrap text-xs"><p>Pickup: {timestamp(shipment.pickup_at)}</p><p className="mt-1 text-base-content/60">Delivery: {timestamp(shipment.delivery_at)}</p></td>
                  <td>{shipment.board_assigned_unit_id ?? '—'}<p className="mt-1 text-xs text-base-content/60">{shipment.assigned_terminal_code ?? '—'}</p></td>
                  <td className="whitespace-nowrap font-semibold tabular-nums">{amount(shipment.estimated_total_cost)}</td>
                  <td><button type="button" className="btn btn-ghost btn-xs whitespace-nowrap text-primary" aria-haspopup="dialog" aria-label={`More information about shipment ${shipment.external_id}`} onClick={() => setSelected(shipment)}>More information</button></td>
                </tr>
              ))}</tbody>
            </table></div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-4 py-3 text-xs text-base-content/60">
              <p role="status">Showing {offset + 1}–{Math.min(offset + pageSize, visible.length)} of {visible.length}</p>
              <nav aria-label="Shipments pagination" className="flex items-center gap-3"><span>Page {currentPage} of {totalPages}</span><div className="join">
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
      <HistoryCleanup resource="shipments" description="Deletes oldest eligible completed shipment history across all pages. Active bookings and unfinished turnaround are protected. Search and status filters do not change the deletion scope." onComplete={() => {
        setSelected(null)
        setPage(1)
        setRefresh((value) => value + 1)
      }} />
      {selectedShipment && <ShipmentDialog key={selectedShipment.external_id} shipment={selectedShipment} revision={revision} onClose={() => setSelected(null)} />}
    </PageSection>
  )
}
