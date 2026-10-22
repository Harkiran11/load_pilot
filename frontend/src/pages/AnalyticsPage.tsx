import { useEffect, useRef, useState } from 'react'
import { getAnalyticsSummary, initialAnalyticsState, subscribeAnalytics } from '../api/analytics'
import type { AnalyticsSummary } from '../api/types'
import AnalyticsTimeline from '../components/AnalyticsTimeline'
import PageSection from '../components/PageSection'
import { analyticsRange, utcDay, validateAnalyticsRange, type AnalyticsDateBasis, type AnalyticsOrderType } from '../utils/analytics'
import { amount, timestamp } from '../utils/load-format'
import { usePageStatus } from '../context/page-status'

function Breakdown({ title, caption, items, empty }: { title: string; caption: string; items: { label: string; count: number; color: string }[]; empty: string }) {
  const total = items.reduce((sum, item) => sum + item.count, 0)
  return <section className="rounded-xl border border-base-300 bg-base-100 p-5">
    <h2 className="font-semibold">{title}</h2><p className="mt-1 text-xs text-base-content/55">{caption}</p>
    {total ? <>
      <div className="mt-5 flex h-3 overflow-hidden rounded-full bg-base-200" aria-hidden="true">{items.filter(item => item.count).map(item => <span key={item.label} className={item.color} style={{ width: `${item.count / total * 100}%` }} />)}</div>
      <dl className="mt-4 space-y-3">{items.map(item => <div key={item.label} className="flex items-center justify-between gap-3 text-xs"><dt className="flex items-center gap-2"><span className={`size-2 rounded-sm ${item.color}`} />{item.label}</dt><dd className="font-semibold tabular-nums">{item.count.toLocaleString()} <span className="ml-2 font-normal text-base-content/50">{Math.round(item.count / total * 100)}%</span></dd></div>)}</dl>
    </> : <p className="py-8 text-center text-sm text-base-content/55">{empty}</p>}
  </section>
}

export default function AnalyticsPage() {
  const [state, setState] = useState(initialAnalyticsState)
  usePageStatus(state.connection, state.error)
  const [period, setPeriod] = useState('14')
  const [custom, setCustom] = useState({ start: '', end: '' })
  const [basis, setBasis] = useState<AnalyticsDateBasis>('pickup')
  const [type, setType] = useState<AnalyticsOrderType>('all')
  const [summaryState, setSummaryState] = useState<{ key: string; data: AnalyticsSummary } | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const refresh = useRef<(() => void) | null>(null)
  useEffect(() => {
    const subscription = subscribeAnalytics(setState)
    refresh.current = subscription.refresh
    return () => { refresh.current = null; subscription.unsubscribe() }
  }, [])
  const simDay = utcDay(state.system?.sim_time_utc)
  const range = period === 'custom' ? custom : simDay ? analyticsRange(simDay, Number(period)) : null
  const rangeError = range ? validateAnalyticsRange(range.start, range.end) : null

  // The figures are aggregated by the backend for the selected range, so the
  // page reads a couple of kilobytes of totals instead of the whole retained
  // order history.
  const rangeKey = range && !rangeError ? `${range.start}:${range.end}:${basis}:${type}` : null
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (!rangeKey) return
    const controller = new AbortController()
    const [start, end] = rangeKey.split(':')
    void getAnalyticsSummary({ start, end, basis, type }, { signal: controller.signal }).then(
      (result) => {
        if (controller.signal.aborted) return
        setSummaryState({ key: rangeKey, data: result })
        setSummaryError(null)
      },
      (cause: unknown) => {
        if (controller.signal.aborted) return
        setSummaryError(cause instanceof Error ? cause.message : 'Analytics could not be refreshed.')
      },
    )
    return () => controller.abort()
  }, [rangeKey, basis, type, reload])

  // Figures from an earlier range are not shown against a new one.
  const summary = summaryState?.key === rangeKey ? summaryState.data : null
  const summaryUpdating = !!rangeKey && summaryState?.key !== rangeKey
  const orderReady = summary !== null
  const financial = summary
  const activity = summary
  const loading = !orderReady && !summaryError && !state.error
  const net = financial?.net
  const formatMoney = (value: number | null | undefined) => amount(value === null || value === undefined ? null : value.toFixed(2))
  const formatCount = (value: number | undefined) => value === undefined ? '—' : value.toLocaleString()
  const selectionKey = JSON.stringify([range, basis, type])

  return <PageSection title="Analytics" description="Track estimated profit, daily bidding, and shipment performance.">
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-xs text-base-content/55">{state.updatedAt ? `Updated ${timestamp(state.updatedAt)}` : 'Loading retained history…'}</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" disabled={summaryUpdating} onClick={() => { refresh.current?.(); setReload((value) => value + 1) }}>{summaryUpdating ? 'Refreshing…' : state.error || summaryError ? 'Retry' : 'Refresh'}</button>
      </div>
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-base-300 bg-base-100 p-4">
        <label className="space-y-1 text-xs"><span className="block text-base-content/60">Date range</span><select className="select select-bordered select-sm w-40" value={period} onChange={event => {
          if (event.target.value === 'custom' && range) setCustom(range)
          setPeriod(event.target.value)
        }}><option value="7">Last 7 days</option><option value="14">Last 14 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="custom">Custom range</option></select></label>
        {period === 'custom' && <>
          <label className="space-y-1 text-xs"><span className="block text-base-content/60">From · UTC</span><input aria-invalid={!!rangeError} type="date" className="input input-bordered input-sm" value={custom.start} onChange={event => setCustom(value => ({ ...value, start: event.target.value }))} /></label>
          <label className="space-y-1 text-xs"><span className="block text-base-content/60">Through · UTC</span><input aria-invalid={!!rangeError} type="date" className="input input-bordered input-sm" value={custom.end} onChange={event => setCustom(value => ({ ...value, end: event.target.value }))} /></label>
        </>}
        <label className="space-y-1 text-xs"><span className="block text-base-content/60">Shipment date</span><select className="select select-bordered select-sm" value={basis} onChange={event => setBasis(event.target.value as AnalyticsDateBasis)}><option value="pickup">Pickup day</option><option value="delivery">Delivery day</option></select></label>
        <label className="space-y-1 text-xs"><span className="block text-base-content/60">Order type</span><select className="select select-bordered select-sm" value={type} onChange={event => setType(event.target.value as AnalyticsOrderType)}><option value="all">All orders</option><option value="fixed_rate">Fixed rate</option><option value="open_bid">Open bid</option></select></label>
        <p className="ml-auto text-xs text-base-content/55">{range && !rangeError ? `${range.start} → ${range.end} · UTC` : period === 'custom' ? 'Choose valid dates' : 'Waiting for simulation time'}</p>
      </div>
      {state.error && <div role="alert" className="alert alert-warning text-sm"><div><p>{state.error}</p><p className="mt-1 text-xs">Available figures remain visible. Updates retry automatically.</p></div></div>}
      {summaryError && <div role="alert" className="alert alert-warning text-sm"><div><p>{summaryError}</p><p className="mt-1 text-xs">Available figures remain visible. Updates retry automatically.</p></div></div>}
      {rangeError && <p role="alert" className="text-sm text-error">{rangeError}</p>}
      {!simDay && period !== 'custom' && <p role="status" className="rounded-lg border border-base-300 p-4 text-sm text-base-content/60">Waiting for the backend simulation clock to set the date range. You can select a custom range to review saved history.</p>}
    </div>

    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-busy={loading}>
      {[
        { title: 'Net estimated profit', value: formatMoney(net), note: financial ? `${financial.known} known · ${financial.unknown} unavailable estimates` : 'Saved estimates on confirmed shipments', color: net !== null && net !== undefined && net < 0 ? 'text-error' : 'text-primary' },
        { title: 'Bid requests', value: formatCount(activity?.bidCount), note: activity ? `${activity.bidsPerDay.toFixed(1)} per day · retained activity` : 'Actual bid requests by simulation day', color: 'text-primary' },
        { title: 'Resolved bid win rate', value: financial?.winRate === null || financial?.winRate === undefined ? '—' : `${financial.winRate.toFixed(1)}%`, note: financial ? `${financial.won} won · ${financial.lost} lost · ${financial.pending} pending` : 'Won ÷ (won + lost) · shipment-date cohort', color: 'text-primary' },
        { title: 'Confirmed shipments', value: formatCount(financial?.shipments), note: financial ? `${financial.completed} completed · ${financial.shipments - financial.completed} booked / in transit` : 'Filtered by the selected shipment date', color: 'text-primary' },
      ].map(card => <section key={card.title} className="rounded-xl border border-base-300 bg-base-100 p-5"><h2 className="text-xs text-base-content/60">{card.title}</h2><p className={`mt-3 text-3xl font-bold tabular-nums ${card.color}`}>{card.value}</p><p className="mt-2 text-[11px] text-base-content/55">{card.note}</p></section>)}
    </div>

    <section className="rounded-xl border border-base-300 bg-base-100 p-5" aria-busy={state.updating}>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div><h2 className="font-semibold">Estimated profit / loss</h2><p className="mt-1 text-xs text-base-content/55">By {basis} day · Confirmed bookings, in-transit and completed shipments · USD</p></div>
        <dl className="flex flex-wrap gap-6 text-xs">{[['Positive estimates', formatMoney(financial?.profit), 'text-success'], ['Negative estimates', formatMoney(financial?.loss), 'text-error'], ['Average / known shipment', formatMoney(financial?.known ? (financial.net ?? 0) / financial.known : null), 'text-base-content']].map(([name, value, color]) => <div key={name}><dt className="text-base-content/55">{name}</dt><dd className={`mt-1 font-semibold tabular-nums ${color}`}>{value}</dd></div>)}</dl>
      </div>
      {financial ? <AnalyticsTimeline key={`profit-${selectionKey}`} days={financial.days} kind="profit" /> : <p className="py-16 text-center text-sm text-base-content/55">{loading ? 'Loading profit history…' : 'Select a valid date range with available order data.'}</p>}
      {financial && !financial.shipments && <p className="mt-3 text-xs text-base-content/60">No confirmed shipments are scheduled in this range.</p>}
      {financial?.unknown ? <p className="mt-3 text-xs text-warning">{financial.unknown} shipment estimates are unavailable and excluded from totals. The chart marks affected days in yellow.</p> : null}
      <p className="mt-3 text-[11px] text-base-content/50">Saved booking-time estimates, not realized earnings. Pending bids are excluded. Dates reflect the selected shipment schedule.</p>
    </section>

    <section className="rounded-xl border border-base-300 bg-base-100 p-5">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">Daily bidding & opportunities</h2><p className="mt-1 text-xs text-base-content/55">Bids by actual request day · Opportunities by order creation day · Simulation UTC</p></div><p className="text-xs text-base-content/55">{orderReady ? formatCount(summary?.newOrders) : '—'} new opportunities</p></div>
      {activity && orderReady ? <AnalyticsTimeline key={`activity-${selectionKey}`} days={activity.days} kind="activity" /> : <p className="py-16 text-center text-sm text-base-content/55">{!range || rangeError ? 'Select a valid date range to review bidding history.' : !state.error ? 'Loading bidding history…' : 'Bid activity and order history are required for this chart.'}</p>}
      <p className="mt-3 text-[11px] text-base-content/50">Counts retained bid-request events, including repeat requests. Deleted activity is unavailable. Locally blocked attempts without a request event are excluded.</p>
    </section>

    <div className="grid items-start gap-5 lg:grid-cols-3">
      <Breakdown title="Bid outcomes" caption={`Current outcomes · Open-bid orders by ${basis} day`} empty={orderReady ? 'No recorded bid outcomes in this range.' : 'Loading outcomes…'} items={[
        { label: 'Won', count: financial?.won ?? 0, color: 'bg-success' },
        { label: 'Lost', count: financial?.lost ?? 0, color: 'bg-error' },
        { label: 'Pending', count: financial?.pending ?? 0, color: 'bg-warning' },
      ]} />
      <Breakdown title="Order types" caption={`All order statuses by ${basis} day`} empty={orderReady ? 'No scheduled orders in this range.' : 'Loading order types…'} items={[
        { label: 'Fixed rate', count: financial?.fixed ?? 0, color: 'bg-success' },
        { label: 'Open bid', count: financial?.open ?? 0, color: 'bg-info' },
      ]} />
      <section className="rounded-xl border border-primary/20 bg-primary/5 p-5"><h2 className="font-semibold">Pending bid estimates</h2><p className="mt-1 text-xs text-base-content/55">Conditional · Same {basis}-day range</p><p className={`mt-5 text-2xl font-bold tabular-nums ${(financial?.pendingProfit ?? 0) < 0 ? 'text-error' : 'text-primary'}`}>{formatMoney(financial?.pendingProfit)}</p><p className="mt-2 text-xs text-base-content/60">{financial ? `${financial.pendingKnown} known · ${financial.pendingUnknown} unavailable` : 'Waiting for saved bid estimates'}</p><p className="mt-4 text-[11px] text-base-content/55">Applies only if pending bids win. Kept separate from confirmed profit.</p></section>
    </div>

    <section className="overflow-hidden rounded-xl border border-base-300 bg-base-100">
      <div className="p-5"><h2 className="font-semibold">Top lanes by estimated profit</h2><p className="mt-1 text-xs text-base-content/55">Five routes · Confirmed shipments in the selected {basis}-day range · Known estimates first</p></div>
      <div className="overflow-x-auto"><table className="table table-sm"><thead><tr><th>Lane</th><th className="text-right">Shipments</th><th className="text-right">Net estimate</th><th className="text-right">Average / known</th><th className="text-right">Unavailable</th></tr></thead><tbody>
        {financial?.lanes.length ? financial.lanes.map(lane => <tr key={lane.route}><td className="font-medium">{lane.route}</td><td className="text-right tabular-nums">{lane.shipments}</td><td className={`text-right font-semibold tabular-nums ${lane.profit < 0 ? 'text-error' : 'text-primary'}`}>{formatMoney(lane.known ? lane.profit : null)}</td><td className="text-right tabular-nums">{formatMoney(lane.known ? lane.profit / lane.known : null)}</td><td className="text-right tabular-nums">{lane.unknown}</td></tr>) : <tr><td colSpan={5} className="py-10 text-center text-base-content/55">{loading ? 'Loading lane performance…' : 'No confirmed shipments in this range.'}</td></tr>}
      </tbody></table></div>
    </section>
    {summary && (summary.undatedShipments > 0 || summary.undatedRequests > 0 || summary.unmatchedRequests > 0) && <p className="text-xs text-warning">Excluded from date-based charts: {summary.undatedShipments} shipments without a {basis} date, {summary.undatedRequests} bid requests without simulation timestamps{summary.unmatchedRequests ? `, ${summary.unmatchedRequests} requests without an order match for the selected type` : ''}.</p>}
  </PageSection>
}
