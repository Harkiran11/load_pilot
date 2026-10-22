import { useEffect, useId, useRef, useState } from 'react'
import { deleteOldestActivity } from '../api/activity'
import { deleteOldestBids } from '../api/bids'
import { deleteOldestShipments } from '../api/shipments'
import { getOverview } from '../api/overview'
import type { HistoryCleanupResult } from '../api/types'
import { readableMessage } from '../utils/api-feedback'
import { cleanupResultMessage, validateCleanupCount } from '../utils/history-cleanup'

type Props = {
  resource: 'activity' | 'bids' | 'shipments'
  description: string
  scope?: { external_id?: string; event_type?: string }
  onComplete: (result: HistoryCleanupResult | null) => void
}

export default function HistoryCleanup({ resource, description, scope = {}, onComplete }: Props) {
  const inputId = useId()
  const [count, setCount] = useState('20')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [messageTone, setMessageTone] = useState<'success' | 'warning'>('success')
  const [error, setError] = useState<string | null>(null)
  const locked = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)
  useEffect(() => () => controllerRef.current?.abort(), [])
  const value = Number(count)
  const valid = count.trim() !== '' && Number.isInteger(value) && value >= 1 && value <= 10_000

  async function removeOldest() {
    if (locked.current) return
    if (!count.trim()) { setError('Enter how many oldest records to delete.'); return }
    try { validateCleanupCount(value) } catch (cause) { setError(cause instanceof Error ? cause.message : 'Enter a valid count.'); return }
    locked.current = true
    setBusy(true)
    setMessage(null)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller
    let confirmed = false
    let status: number | null = null
    try {
      const params = { count: value, dry_run: false }
      const options = { signal: controller.signal }
      const response = resource === 'activity' ? await deleteOldestActivity({ ...params, ...scope }, options)
        : resource === 'bids' ? await deleteOldestBids(params, options) : await deleteOldestShipments(params, options)
      status = response.status
      const body = await response.json().catch(() => null)
      if (!response.ok) throw new Error(typeof body?.detail === 'string' ? readableMessage(body.detail) : response.status === 503 ? 'Cleanup is busy. Please try again shortly.' : 'Unable to delete history. Please try again.')
      const result = body as HistoryCleanupResult | null
      if (!result || result.resource !== resource || result.dry_run !== false || !Number.isInteger(result.deleted_count) || !Number.isInteger(result.protected_count) || !Array.isArray(result.record_ids)) throw new Error('The cleanup result could not be verified.')
      confirmed = true
      if (controller.signal.aborted) return
      setMessageTone(result.deleted_count === 0 ? 'warning' : 'success')
      setMessage(cleanupResultMessage(result, value))
      onComplete(result)
      // Cleanup has no dedicated stream event; refresh overview explicitly too.
      try {
        const overview = await getOverview({ signal: controller.signal })
        if (!overview.ok) throw new Error('Overview refresh failed')
        window.dispatchEvent(new Event('history-cleaned'))
      } catch {
        if (!controller.signal.aborted) setMessage((current) => `${current ?? 'History cleanup completed.'} Dashboard refresh is delayed.`)
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        const uncertain = !confirmed && (status === null || status === 200 || (status >= 500 && status !== 503))
        setError(`${cause instanceof Error && !(cause instanceof TypeError) ? cause.message : 'The cleanup connection was interrupted.'}${uncertain ? ' The deletion outcome is unknown. Refresh the list before trying again.' : ''}`)
        if (uncertain) onComplete(null)
      }
    } finally {
      locked.current = false
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return <section aria-label="Delete oldest history" className="space-y-3 rounded-xl border border-base-300 bg-base-100 p-4">
    <form className="flex flex-wrap items-end justify-between gap-3" onSubmit={(event) => { event.preventDefault(); void removeOldest() }}>
      <div className="min-w-0"><h2 className="text-sm font-semibold">{resource === 'bids' ? 'Delete oldest submitted bid history' : 'Delete oldest records'}</h2><p id={`${inputId}-help`} className="mt-1 text-xs text-base-content/60">{description}</p></div>
      <div className="flex items-end gap-2">
        <label htmlFor={inputId} className="space-y-1 text-xs font-medium"><span className="block">How many</span><input id={inputId} type="number" min="1" max="10000" step="1" required className="input input-sm w-28" value={count} disabled={busy} aria-describedby={`${inputId}-help`} onChange={(event) => { setCount(event.target.value); setError(null) }} /></label>
        <button type="submit" className="btn btn-error btn-outline btn-sm" disabled={!valid || busy}>{busy ? 'Deleting…' : 'Delete oldest'}</button>
      </div>
    </form>
    {message && <p role="status" className={`text-xs ${messageTone === 'warning' ? 'text-warning' : 'text-success'}`}>{message}</p>}
    {error && <p role="alert" className="text-xs text-error">{error}</p>}
  </section>
}
