import { useEffect, useRef, useState } from 'react'
import type { BidLogRow } from '../api/bids'
import { getLoadDetail } from '../api/orders'
import type { LoadView } from '../api/types'
import { amount, label, timestamp } from '../utils/load-format'
import LoadInformation from './LoadInformation'
import Field from './LoadField'
import { bidOrderTypeColor, bidStatusColor } from '../utils/bid-logs'

export default function BidLogDialog({ bid, onClose }: { bid: BidLogRow; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [order, setOrder] = useState<LoadView | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog?.showModal()
    return () => {
      dialog?.close()
      document.body.style.overflow = previousOverflow
    }
  }, [])

  // The list carries a compact summary for every retained order, so the full
  // record is read only when a dispatcher actually opens one.
  useEffect(() => {
    const controller = new AbortController()
    void getLoadDetail(bid.order.external_id, { signal: controller.signal }).then(
      (detail) => { if (!controller.signal.aborted) setOrder(detail) },
      (cause: unknown) => {
        if (controller.signal.aborted) return
        setDetailError(cause instanceof Error ? cause.message : 'The full order record could not be loaded.')
      },
    )
    return () => controller.abort()
  }, [bid.order.external_id])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="bid-log-title"
      className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-3xl overflow-hidden rounded-2xl border border-base-300 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40"
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
      }}
    >
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 p-6">
          <div className="min-w-0">
            <p className="text-xs text-base-content/60">Bid log information</p>
            <h2 id="bid-log-title" className="mt-1 break-all text-xl font-bold text-primary">{bid.order.external_id}</h2>
          </div>
          <button type="button" className="btn btn-ghost btn-circle btn-sm shrink-0" onClick={onClose} aria-label="Close bid log information">✕</button>
        </header>
        <div className="min-h-0 space-y-7 overflow-y-auto p-6">
          <div className="flex flex-wrap gap-2">
            <span className={`badge badge-soft ${bidOrderTypeColor(bid.order.order_type)}`}>{bid.order.order_type === 'fixed_rate' ? 'Fixed rate' : 'Open bid'}</span>
            <span className={`badge badge-soft capitalize ${bidStatusColor(bid.order.status)}`}>{label(bid.order.status)}</span>
            {bid.order.order_type !== 'fixed_rate' && <span className={`badge badge-soft capitalize ${bidStatusColor(bid.outcome)}`}>{label(bid.outcome)}</span>}
          </div>
          <dl className="grid grid-cols-2 gap-5 rounded-xl bg-base-200 p-4 sm:grid-cols-3">
            <Field title={bid.order.order_type === 'fixed_rate' ? 'Fixed rate' : 'Bid amount'}>{amount(bid.order.order_type === 'fixed_rate' ? bid.order.offered_rate : bid.bid_rate)}</Field>
            <Field title="Submission status">{bid.request_status ? <span className={`badge badge-sm badge-soft capitalize ${bidStatusColor(bid.request_status)}`}>{label(bid.request_status)}</span> : 'Not available'}</Field>
            <Field title="Decision time">{timestamp(bid.decision_at)}</Field>
            <Field title="Action ID">{bid.action_id}</Field>
          </dl>
          {detailError && <p role="alert" className="text-sm text-error">{detailError}</p>}
          {!order && !detailError && <p role="status" className="text-sm text-base-content/60">Loading the full order record…</p>}
          {order && <LoadInformation load={order} />}
        </div>
        <footer className="flex shrink-0 justify-end border-t border-base-300 p-4">
          <button type="button" className="btn btn-primary btn-sm" onClick={onClose}>Close</button>
        </footer>
      </div>
    </dialog>
  )
}
