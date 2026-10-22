import { useEffect, useRef, useState } from 'react'
import { getAutopilotAttempts, getAutopilotRun, getAutopilotRuns, startAutopilotRun, stopAutopilotRun } from '../api/autopilot'
import type { AutopilotAttempt, AutopilotRun, Page, StartAutopilotRequest } from '../api/types'
import { AutopilotRequestError, autopilotForm, autopilotFormErrors, autopilotRequest, readAutopilotResponse, verifyAutopilotPage, verifyAutopilotRun, type AutopilotForm } from '../utils/autopilot'

const pendingKey = 'loadpilot-autopilot-pending-start'
const pageSize = 20
type PendingStartRequest = Omit<StartAutopilotRequest, 'mode'> & { mode: 'shadow' | 'live' }

function pendingRequest(): PendingStartRequest | null {
  try {
    const request: PendingStartRequest = JSON.parse(sessionStorage.getItem(pendingKey) || 'null')
    if (!request || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(request.run_id)) return null
    if (!['shadow', 'live'].includes(request.mode)) return null
    // Preserve an unconfirmed older request; never turn it into a live start.
    if (Object.keys(autopilotFormErrors({ ...autopilotForm(request), max_bid_rate: String(request.max_bid_rate ?? '') }, false)).length) return null
    return request
  } catch { return null }
}

function savePending(request: PendingStartRequest | null) {
  try { if (request) sessionStorage.setItem(pendingKey, JSON.stringify(request)); else sessionStorage.removeItem(pendingKey) } catch { /* Run history still supports recovery when storage is unavailable. */ }
}

type View = {
  key: string
  runs: Page<AutopilotRun> | null
  activeRun: AutopilotRun | null
  currentRun: AutopilotRun | null
  selectedRun: AutopilotRun | null
  attempts: Page<AutopilotAttempt> | null
  verified: boolean
  refreshing: boolean
  error: string | null
  updatedAt: string | null
}
const emptyView: View = { key: '', runs: null, activeRun: null, currentRun: null, selectedRun: null, attempts: null, verified: false, refreshing: false, error: null, updatedAt: null }

function errorMessage(cause: unknown) {
  return cause instanceof TypeError ? 'Unable to connect to autopilot. Check the backend connection.' : cause instanceof Error ? cause.message : 'Autopilot could not be refreshed.'
}

export default function useAutopilot() {
  const [pendingStart, setPendingStart] = useState(pendingRequest)
  const pendingRef = useRef(pendingStart)
  const [selection, setSelection] = useState({ runId: pendingStart?.run_id ?? null as string | null, historyPage: 1, attemptPage: 1 })
  const [snapshot, setSnapshot] = useState<View>(emptyView)
  const viewRef = useRef(snapshot)
  const [mutation, setMutation] = useState<'start' | 'stop' | null>(null)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const busy = useRef(false)
  const mutationVersion = useRef(0)
  const mutationController = useRef<AbortController | null>(null)
  const refreshRef = useRef<(() => void) | null>(null)
  const key = JSON.stringify(selection)
  const { runId, historyPage, attemptPage } = selection

  function publish(view: View) { viewRef.current = view; setSnapshot(view) }
  function remember(request: PendingStartRequest | null) {
    pendingRef.current = request
    setPendingStart(request)
    savePending(request)
  }

  useEffect(() => () => mutationController.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    let disposed = false, updating = false, queued = false
    if (viewRef.current.key !== key) publish({ ...emptyView, key, activeRun: viewRef.current.activeRun, currentRun: viewRef.current.currentRun })

    async function refresh() {
      if (disposed) return
      if (updating || busy.current) { queued = true; return }
      updating = true
      const version = mutationVersion.current
      publish({ ...viewRef.current, refreshing: true })
      const options = { signal: controller.signal }
      const errors: string[] = []
      let next = { ...viewRef.current, verified: false }
      try {
        const results = await Promise.allSettled([
          getAutopilotRuns({ limit: pageSize, offset: 0 }, options).then(readAutopilotResponse<Page<AutopilotRun>>).then(verifyAutopilotPage),
          historyPage === 1 ? Promise.resolve(null) : getAutopilotRuns({ limit: pageSize, offset: (historyPage - 1) * pageSize }, options).then(readAutopilotResponse<Page<AutopilotRun>>).then(verifyAutopilotPage),
        ])
        if (disposed) return
        const [latest, history] = results
        if (latest.status === 'fulfilled') {
          latest.value.items.forEach(run => verifyAutopilotRun(run))
          next.verified = true
          next.activeRun = latest.value.items.find(run => run.status === 'running') ?? null
          next.currentRun = next.activeRun ?? latest.value.items[0] ?? null
          if (historyPage === 1) next.runs = latest.value
          const selectedId = runId ?? next.activeRun?.run_id ?? latest.value.items[0]?.run_id ?? null
          if (selectedId) {
            try {
              const listed = latest.value.items.find(run => run.run_id === selectedId)
              const run = listed ?? verifyAutopilotRun(await readAutopilotResponse<AutopilotRun>(await getAutopilotRun(selectedId, options)), selectedId)
              if (next.selectedRun?.run_id !== run.run_id) next.attempts = null
              next.selectedRun = run
              try {
                const attempts = verifyAutopilotPage(await readAutopilotResponse<Page<AutopilotAttempt>>(await getAutopilotAttempts(selectedId, { limit: pageSize, offset: (attemptPage - 1) * pageSize }, options)))
                if (attempts.items.some(attempt => attempt.run_id !== selectedId || typeof attempt.attempt_id !== 'string' || typeof attempt.external_id !== 'string' || typeof attempt.status !== 'string'
                  || !Array.isArray(attempt.reason_codes) || attempt.reason_codes.some(code => typeof code !== 'string')
                  || !attempt.evaluation_snapshot || typeof attempt.evaluation_snapshot !== 'object'
                  || !attempt.model_snapshot || typeof attempt.model_snapshot !== 'object')) throw new Error('The decisions did not match the selected run.')
                next.attempts = attempts
              } catch (cause) { errors.push(errorMessage(cause)) }
            } catch (cause) {
              errors.push(cause instanceof AutopilotRequestError && cause.status === 404 && pendingRef.current?.run_id === selectedId ? 'The pending start is not yet visible. Check status or retry the same start request.' : errorMessage(cause))
            }
          } else { next.selectedRun = null; next.attempts = null }
        } else errors.push(errorMessage(latest.reason))
        if (history.status === 'fulfilled' && history.value) {
          history.value.items.forEach(run => verifyAutopilotRun(run))
          next.runs = history.value
        } else if (history.status === 'rejected') errors.push(errorMessage(history.reason))
      } catch (cause) { errors.push(errorMessage(cause)); next.verified = false }
      finally {
        updating = false
        if (!disposed) {
          if (version === mutationVersion.current) {
            next = { ...next, key, refreshing: false, error: [...new Set(errors)].join(' ') || null }
            if (!errors.length) next.updatedAt = new Date().toISOString()
            publish(next)
          } else { queued = true; publish({ ...viewRef.current, refreshing: false }) }
          if (queued && !busy.current) { queued = false; void refresh() }
        }
      }
    }

    refreshRef.current = () => { void refresh() }
    void refresh()
    const poll = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 5000)
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      disposed = true
      controller.abort()
      clearInterval(poll)
      document.removeEventListener('visibilitychange', onVisible)
      refreshRef.current = null
    }
  }, [key, runId, historyPage, attemptPage])

  useEffect(() => {
    const pending = pendingRef.current
    if (pending && snapshot.selectedRun?.run_id === pending.run_id) {
      pendingRef.current = null
      savePending(null)
      setPendingStart(null)
      setMutationError(null)
      setMessage('The pending start was found in run history. Showing its current status.')
    }
  }, [snapshot.selectedRun])

  async function start(form: AutopilotForm) {
    if (busy.current || viewRef.current.activeRun || !viewRef.current.verified) return
    const pending = pendingRef.current
    if (pending?.mode === 'shadow') {
      setMutationError('An older shadow start is unconfirmed. Check its status or select Manual to stop it before enabling autopilot.')
      return
    }
    let request: StartAutopilotRequest
    try { request = pending ? { ...pending, mode: 'live' } : autopilotRequest(form, crypto.randomUUID()) }
    catch (cause) { setMutationError(errorMessage(cause)); return }
    remember(request)
    busy.current = true
    mutationVersion.current++
    setMutation('start')
    setMutationError(null)
    setMessage(null)
    const controller = new AbortController()
    mutationController.current = controller
    try {
      const run = verifyAutopilotRun(await readAutopilotResponse<AutopilotRun>(await startAutopilotRun(request, { signal: controller.signal })), request.run_id)
      if (controller.signal.aborted) return
      remember(null)
      publish({ ...viewRef.current, currentRun: run, selectedRun: run, activeRun: run.status === 'running' ? run : null })
      setSelection(value => ({ ...value, runId: run.run_id, historyPage: 1, attemptPage: 1 }))
      setMessage(run.status === 'running' ? `${run.mode === 'live' ? 'Live autopilot' : 'Shadow run'} started.` : 'The existing run was recovered. It has already stopped.')
    } catch (cause) {
      if (!controller.signal.aborted) {
        const uncertain = !(cause instanceof AutopilotRequestError) || (cause.status >= 200 && cause.status < 300) || cause.status === 408 || cause.status >= 500
        if (!uncertain) remember(null)
        else setSelection(value => ({ ...value, runId: request.run_id, attemptPage: 1 }))
        setMutationError(`${errorMessage(cause)}${uncertain ? ' The start outcome is unconfirmed. Check status; Retry start reuses the same run ID and settings.' : ''}`)
      }
    } finally {
      busy.current = false
      if (!controller.signal.aborted) { setMutation(null); refreshRef.current?.() }
    }
  }

  async function stop(id: string) {
    if (busy.current) return
    busy.current = true
    mutationVersion.current++
    setMutation('stop')
    setMutationError(null)
    setMessage(null)
    const controller = new AbortController()
    mutationController.current = controller
    try {
      const run = verifyAutopilotRun(await readAutopilotResponse<AutopilotRun>(await stopAutopilotRun(id, { signal: controller.signal })), id)
      if (controller.signal.aborted) return
      if (run.status === 'running') throw new Error('The backend has not confirmed that the run stopped.')
      if (pendingRef.current?.run_id === id) remember(null)
      publish({ ...viewRef.current, currentRun: viewRef.current.currentRun?.run_id === id ? run : viewRef.current.currentRun, activeRun: viewRef.current.activeRun?.run_id === id ? null : viewRef.current.activeRun, selectedRun: run })
      setSelection(value => ({ ...value, runId: id, attemptPage: 1 }))
      setMessage('Autopilot stopped. Requests already in flight will still reconcile.')
    } catch (cause) {
      if (!controller.signal.aborted) setMutationError(`${errorMessage(cause)} Stop was not confirmed. Refresh status and stop again if the run is still running.`)
    } finally {
      busy.current = false
      if (!controller.signal.aborted) { setMutation(null); refreshRef.current?.() }
    }
  }

  return {
    ...snapshot, pendingStart, mutation, mutationError, message, selection, start, stop,
    refresh: () => refreshRef.current?.(),
    selectRun: (id: string) => setSelection(value => ({ ...value, runId: id, attemptPage: 1 })),
    setHistoryPage: (page: number) => setSelection(value => ({ ...value, historyPage: page })),
    setAttemptPage: (page: number) => setSelection(value => ({ ...value, attemptPage: page })),
  }
}

export type AutopilotController = ReturnType<typeof useAutopilot>
