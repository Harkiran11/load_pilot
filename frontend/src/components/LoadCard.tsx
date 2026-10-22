import type { LoadView } from '../api/orders'
import { Flame } from 'lucide-react'
import { amount, quantity, timestamp } from '../utils/load-format'
import { isHotDeal } from '../utils/load-board'
import Field from './LoadField'

type LoadCardProps = {
  load: LoadView
  disabled: boolean
  pending: boolean
  onAccept: () => void
  onDecline: () => void
}

export default function LoadCard({ load, disabled, pending, onAccept, onDecline }: LoadCardProps) {
  const origin = [load.origin_city, load.origin_state].filter(Boolean).join(', ') || load.origin_terminal_code || 'Unknown location'
  const destination = [load.destination_city, load.destination_state].filter(Boolean).join(', ') || load.destination_terminal_code || 'Unknown location'

  return (
    <article className="card h-full min-w-0 border border-base-300 bg-base-100">
      <div className="card-body gap-4 p-5">
        <header className="flex items-start justify-between gap-3">
          <h2 className="min-w-0 truncate text-sm font-semibold" title={load.external_id}>{load.external_id}</h2>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            {isHotDeal(load) && <span className="badge badge-sm border-warning/30 bg-warning/10 text-warning" title="Offered rate gives at least 20% margin against the board estimated total cost."><Flame aria-hidden="true" className="size-3" />Hot deal</span>}
            <span className={`badge badge-sm badge-soft ${load.order_type === 'fixed_rate' ? 'badge-success' : 'badge-info'}`}>{load.order_type === 'fixed_rate' ? 'Fixed rate' : 'Open bid'}</span>
          </div>
        </header>
        <h3 className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 text-base font-semibold">
          <span className="min-w-0 break-words"><span className="mb-1 block text-xs font-normal text-base-content/60">From</span>{origin}</span>
          <span aria-hidden="true" className="text-primary">→</span>
          <span className="min-w-0 break-words"><span className="mb-1 block text-xs font-normal text-base-content/60">To</span>{destination}</span>
        </h3>

        <dl className="flex items-center justify-between gap-3 border-y border-base-300 py-3">
          <dt className="text-sm text-base-content/65">Estimated total cost</dt>
          <dd className="text-xl font-bold tabular-nums text-primary">{load.estimated_total_cost === null ? 'Unavailable' : amount(load.estimated_total_cost)}</dd>
        </dl>

        {load.order_type === 'fixed_rate' && <dl className="flex items-center justify-between gap-3 text-sm"><dt className="text-base-content/65">Offered rate</dt><dd className="font-semibold tabular-nums">{amount(load.offered_rate)}</dd></dl>}

        <dl className="grid grid-cols-2 gap-3">
          <Field title="Distance">{quantity(load.distance_miles, 'mi')}</Field>
          <Field title="Cargo weight">{quantity(load.weight_lbs, 'lbs')}</Field>
        </dl>
        <dl className="space-y-2 text-xs">
          {[
            ['Pickup', load.pickup_at],
            ['Delivery', load.delivery_at],
            ['Offer expires', load.expires_at],
          ].map(([title, value]) => (
            <div key={title} className="flex justify-between gap-3">
              <dt className="shrink-0 text-base-content/60">{title}</dt>
              <dd className="text-right">{timestamp(value)}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-auto grid grid-cols-2 gap-3 border-t border-base-300 pt-3">
          <button type="button" className="btn btn-outline btn-error btn-sm" disabled={disabled} onClick={onDecline} aria-label={`Decline order ${load.external_id}`}>Decline</button>
          <button type="button" className="btn btn-primary btn-sm" disabled={pending} onClick={onAccept} aria-haspopup="dialog" aria-label={`View or accept order ${load.external_id}: review profit analysis`}>View / Accept</button>
        </div>
      </div>
    </article>
  )
}
