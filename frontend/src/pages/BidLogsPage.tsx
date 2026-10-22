import { useEffect, useMemo, useState } from 'react'
import { usePageStatus } from '../context/page-status'
import { subscribeBidLogs } from '../api/bids'
import type { BidLogRow, LiveBidLogsState } from '../api/bids'
import EmptyPanel from '../components/EmptyPanel'
import PageSection from '../components/PageSection'
import HistoryCleanup from '../components/HistoryCleanup'
import BidLogDialog from '../components/BidLogDialog'
import { BID_LOG_PAGE_SIZE, bidOrderTypeColor, bidStatusColor, getBidDate } from '../utils/bid-logs'
import type { BidDateField, BidSortField, BidStatusFilter, SortDirection } from '../utils/bid-logs'

const columns: Array<{ field: BidSortField; label: string }> = [
  { field: 'order', label: 'Order' },
  { field: 'type', label: 'Order type' },
  { field: 'status', label: 'Order status' },
  { field: 'route', label: 'Route' },
  { field: 'amount', label: 'Bid / rate' },
  { field: 'outcome', label: 'Outcome' },
  { field: 'request', label: 'Submission status' },
  { field: 'decision', label: 'Decision time' },
]

const tableColumns = columns.filter((column) => column.field !== 'outcome' && column.field !== 'request')

const dateOptions: Array<{ value: BidDateField; label: string }> = [
  { value: 'created', label: 'Created' },
  { value: 'synced', label: 'Last synced' },
  { value: 'decision', label: 'Decision' },
  { value: 'pickup', label: 'Pickup' },
  { value: 'delivery', label: 'Delivery' },
  { value: 'expires', label: 'Offer expires' },
]

const statusOptions: Array<{ value: BidStatusFilter; label: string }> = [
  { value: 'all', label: 'All statuses' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'declined', label: 'Declined' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'expired', label: 'Expired' },
  { value: 'pending', label: 'Pending' },
  { value: 'lost', label: 'Lost' },
  { value: 'in_transit', label: 'In transit' },
  { value: 'completed', label: 'Completed' },
  { value: 'unknown', label: 'Unknown outcome' },
]

const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export default function BidLogsPage() {
  const [state, setStream] = useState<LiveBidLogsState>({
    page: null,
    connection: 'connecting',
    updating: false,
    error: null,
  })
  const { page: bidPage, connection, updating, error } = state
  const [sortField, setSortField] = useState<BidSortField>('decision')
  usePageStatus(connection, error)
  const [direction, setDirection] = useState<SortDirection>('desc')
  const [dateField, setDateField] = useState<BidDateField>('decision')
  const [statusFilter, setStatusFilter] = useState<BidStatusFilter>('all')
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<BidLogRow | null>(null)

  // Filtering, ordering and paging happen in the database: the board retains
  // every closed order, and sorting them in the browser meant downloading
  // the whole log on every visit.
  const query = useMemo(() => ({
    status: statusFilter,
    sort: sortField,
    direction,
    limit: BID_LOG_PAGE_SIZE,
    offset: (Math.max(1, page) - 1) * BID_LOG_PAGE_SIZE,
  }), [statusFilter, sortField, direction, page])

  useEffect(() => {
    return subscribeBidLogs(setStream, query)
  }, [query, refresh])

  function changeSort(field: BidSortField) {
    setDirection(field === sortField && direction === 'asc' ? 'desc' : 'asc')
    setSortField(field)
    setPage(1)
  }

  const total = bidPage?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / BID_LOG_PAGE_SIZE))
  const currentPage = Math.min(Math.max(1, page), totalPages)
  const records = bidPage?.items ?? []
  const first = total === 0 ? 0 : (currentPage - 1) * BID_LOG_PAGE_SIZE + 1
  const last = Math.min(currentPage * BID_LOG_PAGE_SIZE, total)
  const sortingByDate = dateOptions.some((option) => option.value === sortField)
  const dateLabel = dateOptions.find((option) => option.value === dateField)?.label ?? 'Decision'
  const displayedColumns = tableColumns.map((column) => column.field === 'decision'
    ? { field: dateField, label: `${dateLabel} date` }
    : column)

  return (
    <PageSection title="Bid Logs" description="Review non-open fixed-rate and open-bid orders and their outcomes. Logs update automatically.">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p role="status" className="text-sm text-base-content/65">
          {bidPage === null ? 'Loading bid logs…' : `${total.toLocaleString()} records`}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          Status
          <select className="select select-sm w-40" value={statusFilter} onChange={(event) => {
            setStatusFilter(event.target.value as BidStatusFilter)
            setPage(1)
          }}>
            {statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          Sort by
          <select className="select select-sm w-40" value={sortingByDate ? 'date' : sortField} onChange={(event) => {
            setSortField(event.target.value === 'date' ? dateField : event.target.value as BidSortField)
            setPage(1)
          }}>
            <option value="date">Date</option>
            {columns.filter((column) => column.field !== 'decision').map((column) => <option key={column.field} value={column.field}>{column.label}</option>)}
          </select>
        </label>
        {sortingByDate && (
          <label className="flex items-center gap-2 text-sm">
            Date
            <select className="select select-sm w-44" value={dateField} onChange={(event) => {
              const value = event.target.value as BidDateField
              setDateField(value)
              setSortField(value)
              setPage(1)
            }}>
              {dateOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              <option value="received" disabled>Received (unavailable)</option>
            </select>
          </label>
        )}
        <label className="flex items-center gap-2 text-sm">
          Order
          <select className="select select-sm w-36" value={direction} onChange={(event) => {
            setDirection(event.target.value as SortDirection)
            setPage(1)
          }}>
            <option value="asc">{sortingByDate ? 'Oldest first' : 'Ascending'}</option>
            <option value="desc">{sortingByDate ? 'Newest first' : 'Descending'}</option>
          </select>
        </label>
        <span className="text-xs text-base-content/60">20 per page</span>
      </div>

      {error && (
        <div role="alert" className="alert alert-warning">
          <div>
            <p>{error}</p>
            <p className="mt-1 text-sm">{bidPage !== null ? 'Showing previous results. ' : ''}Updates will resume automatically when the connection and service recover.</p>
          </div>
        </div>
      )}

      <div aria-busy={bidPage === null || updating}>
        {bidPage === null && (
          <div className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8">
            <span className="loading loading-spinner text-primary" aria-hidden="true" />
            {connection === 'reconnecting' ? 'Waiting to reconnect for bid records…' : 'Loading bid records…'}
          </div>
        )}

        {bidPage !== null && total === 0 && <EmptyPanel title="No bid history yet" description="Expired, accepted, declined, and other non-open orders will appear here." />}

        {bidPage !== null && total > 0 && records.length === 0 && <EmptyPanel title="No matching records" description="Choose another status to see more bid logs." />}

        {bidPage !== null && records.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-base-300 bg-base-100">
            <div className="overflow-x-auto">
            <table className="table table-sm">
              <caption className="sr-only">Non-open orders and latest bid records. Unknown sort values appear last.</caption>
              <thead>
                <tr>
                  {displayedColumns.map((column) => (
                    <th key={column.field} scope="col" aria-sort={sortField === column.field ? direction === 'asc' ? 'ascending' : 'descending' : 'none'}>
                      <button type="button" className="flex items-center gap-2 rounded p-1 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-primary" onClick={() => changeSort(column.field)}>
                        {column.label}
                        <span aria-hidden="true">{sortField === column.field ? direction === 'asc' ? '↑' : '↓' : '↕'}</span>
                      </button>
                    </th>
                  ))}
                  <th scope="col" className="text-right">Details</th>
                </tr>
              </thead>
              <tbody>
                {records.map((bid) => {
                  const date = getBidDate(bid, dateField)
                  const fixedRate = bid.order.order_type === 'fixed_rate'
                  const rate = fixedRate ? bid.order.offered_rate : bid.bid_rate
                  return (
                  <tr key={bid.action_id ?? bid.order.external_id} className="hover:bg-base-200/60">
                    <th scope="row" className="font-normal">
                      <p className="font-semibold text-primary">{bid.order.external_id}</p>
                    </th>
                    <td><span className={`badge badge-sm badge-soft whitespace-nowrap ${bidOrderTypeColor(bid.order.order_type)}`}>{fixedRate ? 'Fixed rate' : 'Open bid'}</span></td>
                    <td>
                      <span className={`badge badge-sm badge-soft capitalize whitespace-nowrap ${bidStatusColor(bid.order.status)}`}>{bid.order.status.replaceAll('_', ' ')}</span>
                      {!fixedRate && <p className="mt-1"><span className={`badge badge-xs badge-soft capitalize whitespace-nowrap ${bidStatusColor(bid.outcome)}`}>{bid.outcome.replaceAll('_', ' ')}</span></p>}
                    </td>
                    <td className="whitespace-nowrap">
                      {bid.order.origin_terminal_code ?? 'Unknown'} → {bid.order.destination_terminal_code ?? 'Unknown'}
                    </td>
                    <td className="whitespace-nowrap font-semibold tabular-nums">
                      {rate === null ? '—' : currency.format(Number(rate))}
                    </td>
                    <td className="whitespace-nowrap text-xs">
                      {date && !Number.isNaN(Date.parse(date)) ? (
                        <><p>{new Date(date).toLocaleDateString()}</p><p className="mt-1 text-base-content/60">{new Date(date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p></>
                      ) : '—'}
                    </td>
                    <td className="text-right"><button type="button" className="btn btn-ghost btn-xs whitespace-nowrap text-primary" aria-haspopup="dialog" aria-label={`More information about ${bid.order.external_id}`} onClick={() => setSelected(bid)}>More information</button></td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-4 py-3">
              <p className="text-xs text-base-content/60" role="status">Showing {first.toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}</p>
              <nav aria-label="Bid logs pagination" className="flex items-center gap-3">
                <span className="text-xs text-base-content/60">Page {currentPage} of {totalPages}</span>
                <div className="join">
                  <button type="button" className="btn btn-sm join-item" aria-label="First page" disabled={currentPage === 1} onClick={() => setPage(1)}>{'<<'}</button>
                  <button type="button" className="btn btn-sm join-item" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>{'<'}</button>
                  <span className="btn btn-sm join-item pointer-events-none tabular-nums" aria-current="page" aria-label={`Page ${currentPage}`}>{currentPage}</span>
                  <button type="button" className="btn btn-sm join-item" aria-label="Next page" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>{'>'}</button>
                  <button type="button" className="btn btn-sm join-item" aria-label="Last page" disabled={currentPage === totalPages} onClick={() => setPage(totalPages)}>{'>>'}</button>
                </div>
              </nav>
            </div>
          </div>
        )}
      </div>
      <HistoryCleanup resource="bids" description="Deletes oldest resolved open-bid history across all pages. Fixed-rate orders and active work are retained. Status and sort filters do not change the deletion scope." onComplete={() => {
        setSelected(null)
        setPage(1)
        setRefresh((value) => value + 1)
      }} />
      {selected && <BidLogDialog bid={selected} onClose={() => setSelected(null)} />}
    </PageSection>
  )
}
