import type { LoadFeedback } from '../utils/api-feedback'

export default function LoadNotification({ feedback, onCheckStatus, onDismiss }: { feedback: LoadFeedback; onCheckStatus?: () => void; onDismiss?: () => void }) {
  return <div role={feedback.tone === 'success' || feedback.tone === 'info' ? 'status' : 'alert'} className={`app-notification alert items-start ${feedback.tone === 'success' ? 'alert-success' : feedback.tone === 'error' ? 'alert-error' : feedback.tone === 'info' ? 'alert-info' : 'alert-warning'}`}>
    <div className="min-w-0 space-y-2 text-sm">
      <p className={feedback.tone === 'error' || feedback.tone === 'warning' ? 'sr-only' : 'font-semibold'}>{feedback.title}</p>
      <p className="break-words font-medium leading-relaxed">{feedback.message}</p>
      <details>
        <summary className="cursor-pointer text-xs underline underline-offset-4">More information</summary>
        <div className="mt-3 space-y-3">
          {!!feedback.details?.length && <ul className="list-disc space-y-1 pl-4">{feedback.details.map((detail) => <li key={detail} className="break-words leading-relaxed">{detail}</li>)}</ul>}
          {!!feedback.suggestions?.length && <div><p className="mb-1 font-medium">What to do next</p><ul className="list-disc space-y-1 pl-4">{feedback.suggestions.map((suggestion) => <li key={suggestion} className="break-words leading-relaxed">{suggestion}</li>)}</ul></div>}
          <p className="break-all text-xs opacity-65">Order: {feedback.id}</p>
        </div>
      </details>
      {onCheckStatus && <button type="button" className="btn btn-sm btn-outline mt-1" onClick={onCheckStatus}>Check order status</button>}
    </div>
    {onDismiss && <button type="button" className="btn btn-ghost btn-circle btn-xs justify-self-end" aria-label="Dismiss notification" onClick={onDismiss}>✕</button>}
  </div>
}
