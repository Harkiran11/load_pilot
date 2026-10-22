import { useEffect, useRef, useState } from 'react'
import { getRunProfitStats, type RunProfitStats, type RunSubmission } from '../utils/autopilot-profit'

type Snapshot = { runId: string; data: RunProfitStats | null; error: string | null }

export default function useRunProfitStats(runId: string | null, onSubmitted: (items: RunSubmission[]) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [submittedOrders, setSubmittedOrders] = useState<Set<string>>(() => new Set())
  const seen = useRef(new Set<string>())
  const observedSince = useRef(Date.now())
  useEffect(() => {
    if (!runId) return
    const id = runId
    const controller = new AbortController()
    const originalRates = new Map<string, string | null>()
    let busy = false
    let initial = true
    async function refresh() {
      if (busy || controller.signal.aborted) return
      busy = true
      try {
        const data = await getRunProfitStats(id, originalRates, controller.signal)
        if (!controller.signal.aborted) {
          const fresh = data.submitted.filter(item => !seen.current.has(item.attemptId) && (!initial || Date.parse(item.finishedAt ?? '') >= observedSince.current))
          data.submitted.forEach(item => seen.current.add(item.attemptId))
          initial = false
          setSubmittedOrders(previous => {
            if (data.submitted.every(item => previous.has(item.shipmentId))) return previous
            return new Set([...previous, ...data.submitted.map(item => item.shipmentId)])
          })
          setSnapshot({ runId: id, data, error: null })
          if (fresh.length) onSubmitted(fresh)
        }
      } catch (cause) {
        if (!controller.signal.aborted) setSnapshot(previous => ({ runId: id, data: previous?.runId === id ? previous.data : null, error: cause instanceof Error ? cause.message : 'Run statistics could not be refreshed.' }))
      } finally { busy = false }
    }
    void refresh()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 5000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [runId, onSubmitted])
  const current = snapshot?.runId === runId ? snapshot : null
  return { data: current?.data ?? null, error: current?.error ?? null, loading: !!runId && !current, submittedOrders }
}
