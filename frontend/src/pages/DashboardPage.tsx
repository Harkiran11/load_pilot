import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { subscribeOverview } from '../api/overview'
import type { LiveOverviewState } from '../api/overview'
import PageSection from '../components/PageSection'
import { dashboardRoute, dashboardSummary } from '../utils/dashboard'
import { amount, timestamp } from '../utils/load-format'
import { deliveryProgress, estimatedHoursRemaining, remainingLabel } from '../utils/shipments'
import { usePageStatus } from '../context/page-status'

const initialState: LiveOverviewState = {
  overview: null, loads: null, terminals: null, system: null,
  connection: 'connecting', updating: false, error: null, updatedAt: null,
}

const TerminalCapacityMap = lazy(() => import('../components/TerminalCapacityMap'))
const TripFlowMap = lazy(() => import('../components/TripFlowMap'))

type DashboardView = 'trips' | 'capacity'

function MapTabs({ view, onChange }: { view: DashboardView; onChange: (next: DashboardView) => void }) {
  const tabs: Array<[DashboardView, string]> = [['capacity', 'Hub capacity'], ['trips', 'Trip flow']]
  return <div role="group" aria-label="Map view" className="flex gap-1 rounded-lg border border-base-300 bg-base-100 p-0.5">
    {tabs.map(([value, name]) => <button
      key={value}
      type="button"
      aria-pressed={view === value}
      className={`btn btn-sm border-0 shadow-none ${view === value ? 'btn-primary' : 'btn-ghost'}`}
      onClick={() => onChange(value)}
    >{name}</button>)}
  </div>
}

function Panel({ title, to, caption, actions, children }: { title: string; to: string; caption?: string; actions?: ReactNode; children: ReactNode }) {
  return <section className="min-w-0 overflow-hidden rounded-xl border border-base-300 bg-base-100">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-base-300 p-5">
      <div><h2 className="font-semibold">{title}</h2>{caption && <p className="mt-1 text-xs text-base-content/60">{caption}</p>}</div>
      <div className="flex items-center gap-3">
        {actions}
        <Link to={to} className="link link-primary shrink-0 text-xs no-underline hover:underline" aria-label={`View all ${title.toLowerCase()}`}>View all →</Link>
      </div>
    </header>
    {children}
  </section>
}

function NoData({ loading, children }: { loading: boolean; children: ReactNode }) {
  return <p className="px-5 py-8 text-center text-sm text-base-content/55">{loading ? 'Waiting for operations data…' : children}</p>
}

function count(value: number | null) {
  return value === null ? '—' : value.toLocaleString()
}

export default function DashboardPage() {
  const [state, setState] = useState(initialState)
  // The terminal capacity map is the default: it is the operationally useful
  // view. Trip flow is one click away for the live lane picture.
  const [mapView, setMapView] = useState<DashboardView>('capacity')
  const refreshRef = useRef<(() => void) | null>(null)
  useEffect(() => {
    const subscription = subscribeOverview(setState)
    refreshRef.current = subscription.refresh
    return () => { refreshRef.current = null; subscription.unsubscribe() }
  }, [])
  const { overview, loads, terminals, system, connection, updating, error, updatedAt } = state
  const summary = dashboardSummary(state)
  const loading = overview === null && loads === null
  const boardDelayed = !!system && ['disconnected', 'reconnecting', 'connecting'].includes(system.connection_status)
  usePageStatus(connection, error ?? (boardDelayed ? 'Board connection delayed' : null))
  const profit = overview?.profit


  return (
    <PageSection title="Dashboard" description="Your opportunities, capacity, and shipments at a glance.">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-xs text-base-content/55">{updatedAt ? `Summary refreshed ${timestamp(updatedAt)}` : 'Connecting to operations…'}</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" disabled={updating} onClick={() => refreshRef.current?.()}>{updating ? 'Refreshing…' : error ? 'Retry' : 'Refresh'}</button>
      </div>
      {error && <div role="alert" className="alert alert-warning text-sm"><div><p>{error}</p><p className="mt-1 text-xs">{overview || loads ? 'Showing the latest available data. ' : ''}Updates retry automatically.</p></div></div>}
      {boardDelayed && !error && <p role="status" className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm">The dispatcher is reconnecting to the board. Showing the last synced operations data. Last board sync: {timestamp(system?.last_sync_at ?? null)}.</p>}
      <div aria-busy={loading} className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div aria-label="Operations summary" className="grid grid-cols-2 gap-3">
        {[
          { title: 'Live opportunities', value: summary.openCount, to: '/loads', note: 'Fixed rate & open bid' },
          { title: 'Available trucks', value: summary.availableUnits, to: '/terminals', note: summary.totalUnits === null ? 'Capacity pending' : `${count(summary.totalUnits)} total in fleet` },
          { title: 'Pending bids', value: summary.pendingCount, to: '/bids', note: 'Awaiting results' },
          { title: 'Active shipments', value: summary.activeCount, to: '/shipments', note: 'Accepted & in transit' },
        ].map((item) => <Link key={item.to} to={item.to} className="app-metric-card group min-w-0 rounded-xl border border-base-300 bg-base-100 p-3 transition-colors hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-primary sm:px-4">
          <div className="flex items-center justify-between gap-2"><h2 className="text-xs font-medium text-base-content/65">{item.title}</h2><span aria-hidden="true" className="text-xs text-primary/50 group-hover:text-primary">↗</span></div>
          <p className="mt-1.5 break-words text-2xl font-bold tabular-nums text-primary">{count(item.value)}</p>
          <p className="mt-1 text-[11px] text-base-content/55">{item.note}</p>
        </Link>)}
      </div>

      <section aria-labelledby="dashboard-profit-title" className="min-w-0 rounded-xl border border-primary/20 bg-primary/5 p-3 sm:px-4">
        <div className="flex items-baseline justify-between gap-2"><h2 id="dashboard-profit-title" className="text-sm font-semibold">Estimated profit</h2><span className="text-[10px] text-base-content/55">USD</span></div>
        <div className="mt-2 grid divide-y divide-primary/15">
          <div className="pb-2"><p className="text-[11px] text-base-content/60">Accepted shipments</p><p className={`mt-0.5 break-words text-xl font-bold tabular-nums ${profit && Number(profit.confirmed_estimated_profit) < 0 ? 'text-error' : 'text-primary'}`}>{amount(profit?.confirmed_estimated_profit ?? null)}</p><p className="mt-0.5 text-[10px] text-base-content/60">{profit ? `${profit.confirmed_estimated_count} known · ${profit.confirmed_unknown_count} unavailable` : 'Waiting for booking estimates'}</p></div>
          <div className="pt-2"><p className="text-[11px] text-base-content/60">Pending bids · Conditional</p><p className={`mt-0.5 break-words text-xl font-bold tabular-nums ${profit && Number(profit.conditional_pending_bid_profit) < 0 ? 'text-error' : 'text-base-content'}`}>{amount(profit?.conditional_pending_bid_profit ?? null)}</p><p className="mt-0.5 text-[10px] text-base-content/60">{profit ? `${profit.pending_estimated_count} known · ${profit.pending_unknown_count} unavailable` : 'Waiting for bid estimates'}</p></div>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-base-content/50">Saved estimates, not realized earnings. Pending profit applies only if bids win.</p>
      </section>
      </div>

      <Panel title="Open opportunities" to="/loads" caption="Up to five offers · Soonest expiry first">
        {summary.opportunities.length ? <ul aria-label="Open opportunity cards" tabIndex={0} className="flex snap-x gap-3 overflow-x-auto overscroll-x-contain bg-base-200/30 p-4 focus-visible:outline-2 focus-visible:outline-primary">{summary.opportunities.map((load) => <li key={load.external_id} className="flex w-56 shrink-0 snap-start flex-col gap-3 rounded-lg border border-base-300 bg-base-100 p-3.5">
          <p className="truncate text-xs font-medium text-base-content/60" title={load.external_id}>{load.external_id}</p>
          <div>
            <p className="flex items-center justify-between gap-2 text-sm font-bold"><span className="truncate">{load.origin_terminal_code ?? 'Unknown'}</span><span aria-hidden="true" className="text-primary">→</span><span className="truncate">{load.destination_terminal_code ?? 'Unknown'}</span></p>
            <p className="mt-1 truncate text-[11px] text-base-content/55" title={dashboardRoute(load)}>{dashboardRoute(load)}</p>
          </div>
          <dl className="text-[11px]"><dt className="text-base-content/55">Expires</dt><dd className="mt-0.5">{timestamp(load.expires_at)}</dd></dl>
          <div className="mt-auto flex items-end justify-between gap-2 border-t border-base-200 pt-2">
            <span className={`badge badge-sm badge-soft shrink-0 ${load.order_type === 'fixed_rate' ? 'badge-success' : 'badge-info'}`}>{load.order_type === 'fixed_rate' ? 'Fixed rate' : 'Open bid'}</span>
            <div className="min-w-0 text-right"><p className="text-[10px] text-base-content/55">{load.order_type === 'fixed_rate' ? 'Offer' : 'Est. cost'}</p><p className="break-words text-sm font-bold tabular-nums text-primary">{amount(load.order_type === 'fixed_rate' ? load.offered_rate : load.estimated_total_cost)}</p></div>
          </div>
        </li>)}</ul> : <NoData loading={loading}>No open opportunities. New orders will appear automatically.</NoData>}
      </Panel>

      <Panel
        title={mapView === 'capacity' ? 'Terminal capacity' : 'Trip flow'}
        to={mapView === 'capacity' ? '/terminals' : '/shipments'}
        caption={mapView === 'capacity'
          ? (summary.attentionCount ? `${summary.attentionCount} hubs need attention` : undefined)
          : 'Live lanes between hubs · Positions are interpolated, not GPS'}
        actions={<MapTabs view={mapView} onChange={setMapView} />}
      >
        <Suspense fallback={<div role="status" className="flex h-[460px] items-center justify-center gap-2 text-sm text-base-content/60 sm:h-[560px] lg:h-[640px]"><span className="loading loading-spinner loading-sm" />Loading terminal map…</div>}>
          {mapView === 'trips'
            ? <TripFlowMap
                terminals={terminals}
                loads={loads}
                simTimeUtc={system?.sim_time_utc ?? null}
                simulationSpeed={system?.simulation_speed ?? null}
              />
            : <TerminalCapacityMap terminals={terminals} />}
        </Suspense>
      </Panel>

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Panel title="Active trips" to="/shipments" caption="Latest five live trips · Progress from the simulation clock">
          {summary.shipments.length ? <ul className="divide-y divide-base-200">{summary.shipments.map((shipment) => {
            const overdue = shipment.status !== 'completed' && !!system?.sim_time_utc && !!shipment.delivery_at && Date.parse(shipment.delivery_at) < Date.parse(system.sim_time_utc)
            const progress = deliveryProgress(shipment, system?.sim_time_utc ?? null)
            const percent = progress === null ? null : Math.round(progress * 100)
            const remaining = remainingLabel(estimatedHoursRemaining(shipment, system?.sim_time_utc ?? null))
            return <li key={shipment.external_id} className="space-y-2 p-4">
              <div className="flex items-center justify-between gap-3"><span className="truncate text-xs text-base-content/55" title={shipment.external_id}>{shipment.external_id}</span><span className={`badge badge-sm badge-soft ${overdue ? 'badge-warning' : shipment.status === 'in_transit' ? 'badge-info' : shipment.status === 'completed' ? 'badge-success' : 'badge-ghost'}`}>{overdue ? 'Delivery overdue' : shipment.status === 'in_transit' ? 'In transit' : shipment.status === 'completed' ? 'Delivered' : 'Accepted'}</span></div>
              <p className="truncate text-sm font-medium" title={dashboardRoute(shipment)}>{shipment.origin_terminal_code ?? shipment.origin_city ?? 'Unknown'} <span aria-hidden="true" className="text-primary">→</span> {shipment.destination_terminal_code ?? shipment.destination_city ?? 'Unknown'}</p>
              {percent === null
                ? <p className="text-xs text-base-content/50">No schedule on file</p>
                : <div className="shipment-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={`Trip progress ${percent} percent`}><span className="shipment-progress-fill" style={{ width: `${percent}%` }} /></div>}
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="text-base-content/60">Delivery {timestamp(shipment.delivery_at)}</span>{percent !== null && <span className={`font-semibold tabular-nums ${overdue ? 'text-error' : 'text-primary'}`}>{shipment.status === 'completed' ? 'Delivered' : remaining ?? `${percent}% of window`}</span>}</div>
              <p className="text-xs text-base-content/55">Assigned truck: {shipment.board_assigned_unit_id ?? 'Not yet synced'}</p>
            </li>
          })}</ul> : <NoData loading={loading}>No active trips. Accepted shipments will appear here.</NoData>}
        </Panel>
        <Panel title="Pending bids" to="/bids" caption="Latest five pending bids · Newest orders first">
          {summary.pendingBids.length ? <ul className="divide-y divide-base-200">{summary.pendingBids.map((order) => {
            const bid = overview?.bids.find((item) => item.order.external_id === order.external_id)
            return <li key={order.external_id} className="space-y-1.5 p-4"><div className="flex items-center justify-between gap-3"><span className="truncate text-xs text-base-content/55" title={order.external_id}>{order.external_id}</span><span className="badge badge-sm badge-soft badge-warning">Awaiting result</span></div><p className="truncate text-sm font-medium" title={dashboardRoute(order)}>{order.origin_terminal_code ?? order.origin_city ?? 'Unknown'} → {order.destination_terminal_code ?? order.destination_city ?? 'Unknown'}</p><div className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="text-base-content/60">Decision {timestamp(order.bid_decision_at)}</span><span className="font-semibold">{bid?.bid_rate ? `Bid ${amount(bid.bid_rate)}` : 'Bid amount not loaded'}</span></div></li>
          })}</ul> : <NoData loading={loading}>No pending bids. Submitted bids will appear here.</NoData>}
        </Panel>
      </div>
    </PageSection>
  )
}
