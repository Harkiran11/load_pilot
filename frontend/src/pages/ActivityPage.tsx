import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { subscribeActivity } from '../api/activity'
import type { ActivityView, LiveActivityState } from '../api/activity'
import ActivityDialog from '../components/ActivityDialog'
import EmptyPanel from '../components/EmptyPanel'
import PageSection from '../components/PageSection'
import HistoryCleanup from '../components/HistoryCleanup'
import { activityColor, activityEventGroups, activityEventLabel, activityEventTypes } from '../utils/activity'
import { timestamp } from '../utils/load-format'
import { usePageStatus } from '../context/page-status'

const pageSize = 20
const initialState: LiveActivityState = { data: null, connection: 'connecting', updating: false, error: null }

export default function ActivityPage() {
  const [{ data, connection, updating, error }, setStream] = useState(initialState)
  usePageStatus(connection, error)
  const [orderId, setOrderId] = useState('')
  const [eventType, setEventType] = useState('')
  const [customEventType, setCustomEventType] = useState(false)
  const [applied, setApplied] = useState({ orderId: '', eventType: '' })
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<ActivityView | null>(null)

  useEffect(() => subscribeActivity({
    limit: pageSize,
    offset: (page - 1) * pageSize,
    external_id: applied.orderId || undefined,
    event_type: applied.eventType || undefined,
  }, (state) => {
    setStream(state)
    if (state.data) {
      const totalPages = Math.max(1, Math.ceil(state.data.total / pageSize))
      setPage((current) => Math.min(current, totalPages))
    }
  }), [page, applied.orderId, applied.eventType, refresh])

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setStream(initialState)
    setApplied({ orderId: orderId.trim(), eventType: eventType.trim() })
    setPage(1)
    setRefresh((value) => value + 1)
  }

  function resetFilters() {
    setStream(initialState)
    setOrderId('')
    setEventType('')
    setCustomEventType(false)
    setApplied({ orderId: '', eventType: '' })
    setPage(1)
    setRefresh((value) => value + 1)
  }

  function goToPage(value: number) {
    setStream(initialState)
    setPage(value)
  }

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))
  const currentPage = Math.min(page, totalPages)
  const offset = data?.offset ?? 0
  const eventGroups = activityEventGroups([...activityEventTypes, ...(data?.items.map((event) => event.event_type) ?? []), ...(eventType ? [eventType] : [])])
  const selectedEvent = selected ? data?.items.find((event) => event.event_id === selected.event_id) ?? selected : null

  return (
    <PageSection title="Activity" description="Review decisions, submissions, lifecycle events, and errors. Newest events appear first and update automatically.">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-sm text-base-content/65">{data ? `${data.total} ${data.total === 1 ? 'event' : 'events'}` : 'Loading activity…'}</p>
        <div className="flex items-center gap-3">
          <button type="button" className="btn btn-ghost btn-sm" disabled={updating} onClick={() => setRefresh((value) => value + 1)}>{error ? 'Retry' : 'Refresh'}</button>
        </div>
      </div>
      <form className="flex flex-wrap items-end gap-3" onSubmit={applyFilters}>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Order ID (exact)<input type="search" className="input input-sm w-56" placeholder="All orders and system events" value={orderId} onChange={(event) => setOrderId(event.target.value)} /></label>
        <label className="flex flex-col gap-1.5 text-xs font-medium">Event type<select className="select select-bordered select-sm w-full sm:w-64" value={customEventType ? '__custom__' : eventType} onChange={event => {
          const custom = event.target.value === '__custom__'
          setCustomEventType(custom)
          if (!custom) setEventType(event.target.value)
        }}>
          <option value="">All event types</option>
          {eventGroups.map(([group, types]) => <optgroup key={group} label={group}>{types.map(type => <option key={type} value={type} title={type}>{activityEventLabel(type)}</option>)}</optgroup>)}
          <option value="__custom__">Custom event type…</option>
        </select></label>
        {customEventType && <label className="flex flex-col gap-1.5 text-xs font-medium">Exact event name<input type="text" className="input input-bordered input-sm w-60" placeholder="e.g. sync.board.error" value={eventType} onChange={event => setEventType(event.target.value)} /></label>}
        <button type="submit" className="btn btn-primary btn-sm">Apply filters</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={resetFilters}>Reset</button>
      </form>
      {error && <div role="alert" className="alert alert-warning"><div><p>{error}</p><p className="mt-1 text-sm">{data ? 'Showing previous events. ' : ''}Updates will retry automatically, or select Retry.</p></div></div>}
      <div aria-busy={data === null || updating}>
        {!data && <div role="status" className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8"><span className="loading loading-spinner text-primary" aria-hidden="true" />Loading activity history…</div>}
        {data?.total === 0 && <EmptyPanel title={applied.orderId || applied.eventType ? 'No matching events' : 'No activity yet'} description={applied.orderId || applied.eventType ? 'Try another order ID or event type, or reset the filters.' : 'Decisions, submissions, results, and errors will appear as the dispatcher runs.'} />}
        {data && data.total > 0 && (
          <div className="overflow-hidden rounded-xl border border-base-300 bg-base-100">
            <div className="overflow-x-auto"><table className="table table-sm">
              <caption className="sr-only">Activity events ordered by recorded time, newest first. Each page contains up to 20 events.</caption>
              <thead><tr>{['Recorded time', 'Event', 'Order', 'Message', 'Details'].map((title) => <th scope="col" key={title}>{title}</th>)}</tr></thead>
              <tbody>{data.items.map((event) => (
                <tr key={event.event_id} className="hover:bg-base-200/60">
                  <td className="whitespace-nowrap text-xs"><p>{timestamp(event.occurred_at_wall)}</p><p className="mt-1 text-base-content/60">Sim: {timestamp(event.occurred_at_sim)}</p></td>
                  <td><span className={`badge badge-sm badge-soft whitespace-nowrap ${activityColor(event.event_type)}`}>{event.event_type}</span></td>
                  <td className="whitespace-nowrap text-xs font-medium text-primary">{event.external_id ?? 'System'}</td>
                  <td className="max-w-md"><p className="line-clamp-2 break-words text-sm" title={event.message}>{event.message}</p></td>
                  <td><button type="button" className="btn btn-ghost btn-xs whitespace-nowrap text-primary" aria-haspopup="dialog" aria-label={`More information about ${event.event_type}`} onClick={() => setSelected(event)}>More information</button></td>
                </tr>
              ))}</tbody>
            </table></div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-4 py-3 text-xs text-base-content/60">
              <p role="status">Showing {data.items.length ? offset + 1 : 0}–{Math.min(offset + data.items.length, data.total)} of {data.total}</p>
              <nav aria-label="Activity pagination" className="flex items-center gap-3"><span>Page {currentPage} of {totalPages}</span><div className="join">
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === 1} aria-label="First page" onClick={() => goToPage(1)}>{'<<'}</button>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === 1} aria-label="Previous page" onClick={() => goToPage(currentPage - 1)}>{'<'}</button>
                <span className="btn btn-sm join-item pointer-events-none" aria-current="page">{currentPage}</span>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === totalPages} aria-label="Next page" onClick={() => goToPage(currentPage + 1)}>{'>'}</button>
                <button type="button" className="btn btn-sm join-item" disabled={currentPage === totalPages} aria-label="Last page" onClick={() => goToPage(totalPages)}>{'>>'}</button>
              </div></nav>
            </div>
          </div>
        )}
      </div>
      <HistoryCleanup resource="activity" description={applied.orderId || applied.eventType ? 'Deletes oldest eligible events matching the applied order and event filters. The latest history import is protected.' : 'Deletes oldest eligible activity events across all pages. The latest history import is protected.'} scope={{ external_id: applied.orderId || undefined, event_type: applied.eventType || undefined }} onComplete={() => {
        setSelected(null)
        setPage(1)
        setRefresh((value) => value + 1)
      }} />
      {selectedEvent && <ActivityDialog event={selectedEvent} onClose={() => setSelected(null)} />}
    </PageSection>
  )
}
