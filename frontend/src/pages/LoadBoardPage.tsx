import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { LoadDecisionError, getLoad, submitLoadDecision, subscribeLoads } from '../api/orders'
import type { LiveLoadsState, LoadDetail, LoadView, OrderType } from '../api/orders'
import { getLoadOpportunities } from '../utils/load-board'
import EmptyPanel from '../components/EmptyPanel'
import LoadCard from '../components/LoadCard'
import LoadDetailsPanel from '../components/LoadDetailsPanel'
import PageSection from '../components/PageSection'
import LoadNotification from '../components/LoadNotification'
import type { LoadFeedback } from '../utils/api-feedback'
import { amount } from '../utils/load-format'
import { usePageStatus } from '../context/page-status'
import useAutopilot from '../hooks/use-autopilot'
import AutopilotPanel from '../components/AutopilotPanel'
import AutopilotSetup from '../components/AutopilotSetup'
import { autopilotForm, autopilotFormErrors, autopilotPhase, defaultAutopilotForm, wallDuration, type AutopilotForm } from '../utils/autopilot'
import AutopilotSubmissionStatus from '../components/AutopilotSubmissionStatus'
import OfferProfitRatio from '../components/OfferProfitRatio'
import useRunProfitStats from '../hooks/use-run-profit-stats'
import type { RunSubmission } from '../utils/autopilot-profit'

const initialState: LiveLoadsState = {
  loads: null,
  system: null,
  connection: 'connecting',
  error: null,
}

export default function LoadBoardPage() {
  const autopilot = useAutopilot()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [autoForm, setAutoForm] = useState<AutopilotForm>(() => autopilot.pendingStart ? autopilotForm(autopilot.pendingStart) : { ...defaultAutopilotForm })
  const setupRef = useRef<HTMLDivElement>(null)
  const autoOn = !!autopilot.activeRun
  const showSettings = settingsOpen || autoOn || !!autopilot.pendingStart
  const displayedForm = autopilot.activeRun ? autopilotForm(autopilot.activeRun.settings)
    : autopilot.pendingStart ? autopilotForm(autopilot.pendingStart) : autoForm
  const validAutoSettings = !Object.keys(autopilotFormErrors(autoForm)).length
  const [orderType, setOrderType] = useState<OrderType | 'all'>('all')
  const [{ loads: streamedLoads, system, connection, error }, setStream] = useState(initialState)
  usePageStatus(connection, error ?? (showSettings || historyOpen ? autopilot.error : null))
  const [selected, setSelected] = useState<{ load: LoadView; bid: boolean } | null>(null)
  const [decisions, setDecisions] = useState<Record<string, 'submitting' | 'sent' | 'uncertain'>>({})
  const lockedOrders = useRef(new Set<string>())
  const [feedback, setFeedback] = useState<LoadFeedback | null>(null)
  const [autoFeedback, setAutoFeedback] = useState<LoadFeedback | null>(null)
  const currentRun = autopilot.currentRun?.mode === 'live' && (!autopilot.pendingStart || autopilot.pendingStart.run_id === autopilot.currentRun.run_id) ? autopilot.currentRun : null
  const onAutoSubmitted = useCallback((items: RunSubmission[]) => {
    const first = items[0]
    const bids = items.filter(item => item.orderType === 'open_bid').length
    const accepts = items.length - bids
    const summary = [bids ? `submitted ${bids} bid${bids === 1 ? '' : 's'}` : '', accepts ? `accepted ${accepts} offer${accepts === 1 ? '' : 's'}` : ''].filter(Boolean).join(' and ')
    setAutoFeedback({
      id: first.externalId,
      title: items.length > 1 ? 'Autopilot submissions confirmed' : bids ? 'Autopilot bid submitted' : 'Autopilot offer accepted',
      tone: 'success',
      message: items.length > 1
        ? `Autopilot ${summary}. These orders have been removed from the Load Board.`
        : bids ? `Autopilot submitted a ${first.rate ? `${amount(first.rate)} ` : ''}bid for ${first.externalId}. Track its result in Bid Logs; a booking is confirmed only if you win.`
          : `Autopilot accepted ${first.externalId}${first.rate ? ` for ${amount(first.rate)}` : ''}. Follow this booking in Shipments.`,
      details: items.map(item => `${item.externalId}: ${item.orderType === 'open_bid' ? 'Bid submitted' : 'Offer accepted'}${item.rate ? ` · ${amount(item.rate)}` : ''}`),
      suggestions: bids ? ['Submitted bids await an award. Track results in Bid Logs.'] : ['Track accepted bookings in Shipments.'],
    })
    setSelected(current => current && items.some(item => item.shipmentId === current.load.shipment_id) ? null : current)
  }, [])
  const runStats = useRunProfitStats(currentRun?.run_id ?? null, onAutoSubmitted)
  const loads = streamedLoads === null ? null : getLoadOpportunities(streamedLoads, orderType).filter(load => !runStats.submittedOrders.has(load.shipment_id))
  const selectedLoad = selected ? streamedLoads?.find((load) => load.external_id === selected.load.external_id) ?? selected.load : null

  function runAutopilot() {
    if (autopilot.mutation || autoOn) return
    setSettingsOpen(true)
    setSelected(null)
    if (!autopilot.pendingStart && !validAutoSettings) {
      requestAnimationFrame(() => setupRef.current?.querySelector<HTMLInputElement>('[aria-invalid="true"]')?.focus())
      return
    }
    if (!autopilot.verified) { autopilot.refresh(); return }
    void autopilot.start(autoForm)
  }

  function selectManual() {
    if (autopilot.mutation) return
    setSettingsOpen(false)
    const runId = autopilot.activeRun?.run_id ?? autopilot.pendingStart?.run_id
    if (runId) void autopilot.stop(runId)
  }

  function stopAutopilot() {
    const runId = autopilot.activeRun?.run_id ?? autopilot.pendingStart?.run_id
    if (!autopilot.mutation && runId) void autopilot.stop(runId)
  }

  function canSubmit(load: LoadView) {
    return connection === 'live' && load.status === 'open'
      && !runStats.submittedOrders.has(load.shipment_id)
      && !load.board_missing_at && ['open_bid', 'fixed_rate'].includes(load.order_type)
      && load.seconds_remaining !== 0 && !decisions[load.external_id]
      && !!streamedLoads?.some((item) => item.external_id === load.external_id && item.status === 'open')
  }

  async function decide(load: LoadView, decision: 'accept' | 'bid' | 'decline', bidRate?: string) {
    const id = load.external_id
    if (!canSubmit(load) || lockedOrders.current.has(id)) return
    if ((decision === 'accept' && load.order_type !== 'fixed_rate') || (decision === 'bid' && load.order_type !== 'open_bid')) return
    lockedOrders.current.add(id)
    setDecisions((current) => ({ ...current, [id]: 'submitting' }))
    setFeedback(null)

    try {
      const result = await submitLoadDecision(id, decision, bidRate)
      setDecisions((current) => ({ ...current, [id]: 'sent' }))
      setStream((current) => ({ ...current, loads: current.loads?.map((item) => item.external_id === id ? result.order : item) ?? null }))
      setFeedback({
        id,
        title: decision === 'bid' ? 'Bid submitted' : decision === 'accept' ? 'Offer accepted' : 'Offer declined',
        tone: result.sync_pending ? 'warning' : 'success',
        message: `${decision === 'bid' ? `Your ${amount(bidRate ?? null)} bid was submitted. Track the outcome in Bid Logs; a booking is confirmed only if you win.` : decision === 'accept' ? 'Your acceptance was recorded. Follow this booking in Shipments.' : 'This offer was declined and has been removed from your open opportunities.'}${result.sync_pending ? ' Fleet availability is still updating.' : ''}`,
      })
      setSelected(null)
    } catch (cause) {
      const uncertain = !(cause instanceof LoadDecisionError) || cause.uncertain
      if (uncertain) {
        setDecisions((current) => ({ ...current, [id]: 'uncertain' }))
      } else {
        lockedOrders.current.delete(id)
        setDecisions((current) => {
          const next = { ...current }
          delete next[id]
          return next
        })
      }
      setFeedback({
        id,
        title: uncertain ? 'Submission not confirmed' : decision === 'bid' ? 'Bid could not be submitted' : decision === 'accept' ? 'Offer could not be accepted' : 'Offer could not be declined',
        tone: uncertain ? 'warning' : 'error',
        message: cause instanceof Error ? cause.message : 'We could not confirm the request. Check the order status before trying again.',
        details: cause instanceof LoadDecisionError ? cause.details : [],
        suggestions: cause instanceof LoadDecisionError ? cause.suggestions : [],
      })
    }
  }

  async function checkStatus(id: string) {
    try {
      const response = await getLoad(id)
      if (!response.ok) throw new Error('Unable to check the order status. Please try again.')
      const detail: LoadDetail = await response.json()
      const unresolved = detail.actions.some((action) => action.request_status === 'unknown' || action.request_status === 'submitting')
      const confirmedFailure = !unresolved && detail.status === 'open' && detail.actions.length > 0 && detail.actions.every((action) => action.request_status === 'failed')
      if (confirmedFailure) {
        lockedOrders.current.delete(id)
        setDecisions((current) => {
          const next = { ...current }
          delete next[id]
          return next
        })
      }
      setStream((current) => ({ ...current, loads: current.loads?.map((item) => item.external_id === id ? detail : item) ?? null }))
      const statusMessages: Record<string, string> = {
        accepted: 'This order is confirmed. Follow its progress in Shipments.',
        in_transit: 'This shipment is on the way. Follow its progress in Shipments.',
        completed: 'This shipment has been delivered.',
        bid_submitted: 'Your bid is recorded and awaiting an outcome. Track it in Bid Logs.',
        bid_lost: 'Your bid was not selected. You can review it in Bid Logs.',
        declined: 'This offer was declined.',
        expired: 'This offer has expired. Choose another open opportunity.',
      }
      if (!unresolved && detail.status !== 'open') setDecisions((current) => ({ ...current, [id]: 'sent' }))
      setFeedback({
        id,
        title: unresolved ? 'Submission still pending' : confirmedFailure ? 'Order available again' : 'Order status updated',
        tone: unresolved || (detail.status === 'open' && !confirmedFailure) ? 'warning' : 'info',
        message: unresolved ? 'The service is still confirming your previous submission. Wait for a confirmed outcome before trying again.' : confirmedFailure ? 'The previous submission failed and this offer is still open. Review its current eligibility before trying again.' : statusMessages[detail.status] ?? (detail.status === 'open' ? 'The offer is still open, but the previous submission has not been verified. Keep checking its status before trying again.' : 'The latest order status has been received. Review its details before continuing.'),
      })
    } catch (cause) {
      setFeedback({ id, title: 'Status check unavailable', tone: 'warning', message: cause instanceof Error ? cause.message : 'We could not read the current order status. Please try checking again.' })
    }
  }

  useEffect(() => {
    return subscribeLoads({ order_type: 'all', status: 'open' }, setStream)
  }, [])

  return (
    <PageSection title="Load Board" description="Browse open fixed-rate and open-bid opportunities. Orders update automatically.">
      <div className={`flex flex-wrap items-center justify-between gap-4 ${autoOn || autopilot.pendingStart ? 'sticky top-40 z-10 rounded-xl border border-base-300 bg-base-100 p-3 shadow-sm lg:top-28' : ''}`}>
        <label className="flex items-center gap-2 text-sm">
          Order type
          <select className="select select-sm w-44" value={orderType} onChange={(event) => setOrderType(event.target.value as OrderType | 'all')}>
            <option value="all">All order types</option>
            <option value="fixed_rate">Fixed rate</option>
            <option value="open_bid">Open bid</option>
          </select>
        </label>
        {autopilot.activeRun && <p role="status" className="text-xs text-base-content/65">{autopilotPhase(autopilot.activeRun)} · {autopilot.activeRun.orders_reserved}/{autopilot.activeRun.settings.max_orders} actions · {wallDuration(autopilot.activeRun.seconds_remaining)} left</p>}
        {showSettings && !autoOn && !autopilot.pendingStart && <p role="status" className="text-xs text-base-content/60">{autopilot.mutation === 'start' ? 'Starting autopilot…' : 'Autopilot idle'}</p>}
        <div role="group" aria-label="Load board mode" className="ml-auto flex gap-1 rounded-lg border border-base-300 bg-base-100 p-1">
          <button type="button" aria-pressed={!showSettings} disabled={!!autopilot.mutation} className={`btn btn-sm border-0 shadow-none ${!showSettings ? 'btn-primary' : 'btn-ghost'}`} onClick={selectManual}>Manual</button>
          <button type="button" aria-pressed={showSettings} aria-controls="load-board-autopilot-settings" aria-expanded={showSettings} disabled={!!autopilot.mutation} className={`btn btn-sm border-0 shadow-none ${showSettings ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setSettingsOpen(true); setSelected(null) }}>Auto pilot</button>
        </div>
        {showSettings && (autoOn || autopilot.pendingStart ? <button type="button" className="btn btn-error btn-sm" disabled={!!autopilot.mutation} onClick={stopAutopilot}>{autopilot.mutation === 'stop' ? 'Stopping…' : 'Stop'}</button> : <button type="button" className="btn btn-success btn-sm" disabled={!!autopilot.mutation || !autopilot.verified || !validAutoSettings} onClick={runAutopilot}>{autopilot.mutation === 'start' ? 'Starting…' : 'Run'}</button>)}
      </div>

      {autopilot.mutationError && <p role="alert" className="alert alert-error text-sm">{autopilot.mutationError}</p>}
      {(showSettings || historyOpen) && autopilot.error && <p role="alert" className="alert alert-warning text-sm">{autopilot.error} Updates retry automatically.</p>}
      {autopilot.message && <p role="status" className="text-sm text-success">{autopilot.message}</p>}
      {autopilot.pendingStart && <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/30 bg-warning/5 p-3"><p className="text-xs">Auto pilot start is unconfirmed. Check status before retrying the same request or stopping.</p><div className="flex gap-2"><button type="button" className="btn btn-ghost btn-xs" disabled={!!autopilot.mutation} onClick={() => { autopilot.selectRun(autopilot.pendingStart!.run_id); autopilot.refresh() }}>Check status</button><button type="button" className="btn btn-primary btn-xs" disabled={!!autopilot.mutation || !autopilot.verified || autoOn || autopilot.pendingStart.mode !== 'live'} onClick={runAutopilot}>Retry run</button></div></div>}
      <div id="load-board-autopilot-settings" ref={setupRef} hidden={!showSettings}>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <AutopilotSetup controller={autopilot} form={displayedForm} onChange={setAutoForm} onRun={runAutopilot} simulationSpeed={system?.simulation_speed ?? null} />
          <OfferProfitRatio run={currentRun} stats={runStats} />
        </div>
        {autopilot.activeRun && <div className="mt-3"><AutopilotSubmissionStatus run={autopilot.activeRun} /></div>}
        {showSettings && !autoOn && !autopilot.pendingStart && !autopilot.verified && <p role="status" className="mt-2 text-xs text-warning">Checking current run status. Run will be available once the backend is connected and settings are valid.</p>}
      </div>

      {error && (
        <div role="alert" className="alert alert-warning">
          <div>
            <p>{error}</p>
            {loads !== null && <p className="mt-1 text-sm">Showing previous orders. Information may have changed.</p>}
          </div>
        </div>
      )}

      {!selected && (autoFeedback || feedback) && (
        <div className="fixed inset-x-4 top-24 z-30 mx-auto max-h-[60dvh] max-w-lg space-y-2 overflow-y-auto rounded-xl shadow-xl sm:right-6 sm:left-auto">
          {autoFeedback && <LoadNotification feedback={autoFeedback} onDismiss={() => setAutoFeedback(null)} />}
          {feedback && !selected && <LoadNotification feedback={feedback} onDismiss={() => setFeedback(null)} onCheckStatus={decisions[feedback.id] === 'uncertain' ? () => void checkStatus(feedback.id) : undefined} />}
        </div>
      )}

      <div aria-busy={loads === null}>
        {loads === null && (
          <div role="status" className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8">
            <span className="loading loading-spinner text-primary" aria-hidden="true" />
            {connection === 'reconnecting' ? 'Waiting to reconnect for orders…' : 'Loading orders…'}
          </div>
        )}

        {loads?.length === 0 && (
          <div className="space-y-3">
            <EmptyPanel title="No open opportunities" description={Number(system?.simulation_speed) > 1 ? `At ${Number(system?.simulation_speed).toLocaleString()}× speed, offers can expire quickly. Lower the speed or increase order arrival frequency in Settings.` : 'New orders matching the selected type will appear here automatically.'} />
            {import.meta.env.DEV && Number(system?.simulation_speed) > 1 && <Link to="/settings" className="btn btn-outline btn-sm">Adjust simulation controls</Link>}
          </div>
        )}

        {loads !== null && loads.length > 0 && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-base-content/65">
              <p>{loads.length} {loads.length === 1 ? 'order' : 'orders'}</p>
            </div>
            <div className="grid items-start gap-5 xl:grid-cols-2 2xl:grid-cols-3">
              {loads.map((load) => (
                <LoadCard
                  key={load.external_id}
                  load={load}
                  disabled={!canSubmit(load)}
                  pending={decisions[load.external_id] === 'submitting'}
                  onAccept={() => setSelected({ load, bid: true })}
                  onDecline={() => void decide(load, 'decline')}
                />
              ))}
            </div>
          </div>
        )}
      </div>
      {selected && selectedLoad && (
        <LoadDetailsPanel
          key={`${selected.load.external_id}-${selected.bid}`}
          load={selectedLoad}
          initialBidOpen={selected.bid}
          canSubmit={canSubmit(selectedLoad)}
          pending={decisions[selectedLoad.external_id] === 'submitting'}
          message={null}
          notification={feedback?.id === selectedLoad.external_id ? feedback : undefined}
          autopilotNotification={autoFeedback ?? undefined}
          onDismissAutopilotNotification={() => setAutoFeedback(null)}
          onCheckStatus={decisions[selectedLoad.external_id] === 'uncertain' ? () => void checkStatus(selectedLoad.external_id) : undefined}
          onClose={() => setSelected(null)}
          onSubmitBid={(rate) => decide(selectedLoad, 'bid', rate)}
          onAccept={() => decide(selectedLoad, 'accept')}
        />
      )}
      <details className="rounded-xl border border-base-300 bg-base-100 p-4" onToggle={event => setHistoryOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-sm font-semibold">Autopilot history & decisions</summary>
        <div className="mt-4"><AutopilotPanel controller={autopilot} /></div>
      </details>
    </PageSection>
  )
}
