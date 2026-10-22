import { useEffect, useRef } from 'react'
import type { LoadView } from '../api/orders'
import { label } from '../utils/load-format'
import ProfitMarginAnalyzer from './ProfitMarginAnalyzer'
import LoadInformation from './LoadInformation'
import LoadNotification from './LoadNotification'
import type { LoadFeedback } from '../utils/api-feedback'

type LoadDetailsPanelProps = {
  load: LoadView
  initialBidOpen: boolean
  canSubmit: boolean
  pending: boolean
  message: string | null
  notification?: LoadFeedback
  autopilotNotification?: LoadFeedback
  onDismissAutopilotNotification?: () => void
  onCheckStatus?: () => void
  onClose: () => void
  onSubmitBid: (bidRate: string) => Promise<void>
  onAccept: () => Promise<void>
}

export default function LoadDetailsPanel({ load, initialBidOpen, canSubmit, pending, message, notification, autopilotNotification, onDismissAutopilotNotification, onCheckStatus, onClose, onSubmitBid, onAccept }: LoadDetailsPanelProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

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

  const hasAnalysis = ['open_bid', 'fixed_rate'].includes(load.order_type)

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="load-panel-title"
      className={`load-details-panel fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full border-0 bg-base-100 p-0 text-base-content shadow-2xl backdrop:bg-black/40 ${hasAnalysis ? 'max-w-5xl' : 'max-w-lg'}`}
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
      }}
    >
      <div className="flex h-full flex-col">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 px-5 py-4">
          <div className="min-w-0">
            <h2 id="load-panel-title" className="flex min-w-0 flex-wrap items-center gap-2 text-lg font-bold"><span className="min-w-0 truncate text-primary" title={load.external_id}>{load.external_id}</span><span className={`badge badge-sm badge-soft shrink-0 font-medium ${load.order_type === 'fixed_rate' ? 'badge-success' : 'badge-info'}`}>{load.order_type === 'fixed_rate' ? 'Fixed rate' : load.order_type === 'open_bid' ? 'Open bid' : label(load.order_type)}</span></h2>
          </div>
          <button type="button" className="btn btn-ghost btn-circle btn-sm shrink-0" aria-label="Close order information" onClick={onClose}>✕</button>
        </header>
        {autopilotNotification && <div className="max-h-[25dvh] shrink-0 overflow-y-auto"><LoadNotification feedback={autopilotNotification} onDismiss={onDismissAutopilotNotification} /></div>}
        {hasAnalysis ? <>
          {notification && <div className="max-h-[35dvh] shrink-0 overflow-y-auto"><LoadNotification feedback={notification} onCheckStatus={onCheckStatus} /></div>}
          {!notification && message && <div role="alert" className="alert alert-warning shrink-0 rounded-none"><p>{message}</p>{onCheckStatus && <button type="button" className="btn btn-sm btn-outline" onClick={onCheckStatus}>Check status</button>}</div>}
          <ProfitMarginAnalyzer load={load} canSubmit={canSubmit} pending={pending} onSubmitBid={onSubmitBid} onAccept={onAccept} orderInformation={<LoadInformation load={load} />} initialTab={initialBidOpen ? 'price' : 'order'} />
        </> : <div className="min-h-0 flex-1 space-y-7 overflow-y-auto p-6">
          {notification && <div className="max-h-[35dvh] shrink-0 overflow-y-auto"><LoadNotification feedback={notification} onCheckStatus={onCheckStatus} /></div>}
          {!notification && message && <div role="alert" className="alert alert-warning"><p>{message}</p>{onCheckStatus && <button type="button" className="btn btn-sm btn-outline" onClick={onCheckStatus}>Check status</button>}</div>}

          <LoadInformation load={load} />
        </div>}
      </div>
    </dialog>
  )
}
