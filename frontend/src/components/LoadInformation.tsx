import type { LoadView } from '../api/orders'
import { amount, label, quantity, timestamp } from '../utils/load-format'
import Field from './LoadField'

export default function LoadInformation({ load }: { load: LoadView }) {
  return (
    <div className="space-y-7">
          <section className="space-y-4" aria-labelledby="load-route-title">
            <h3 id="load-route-title" className="font-semibold">Route &amp; schedule</h3>
            <dl className="grid grid-cols-2 gap-5">
              <Field title="Origin">{[load.origin_city, load.origin_state].filter(Boolean).join(', ') || '—'}</Field>
              <Field title="Destination">{[load.destination_city, load.destination_state].filter(Boolean).join(', ') || '—'}</Field>
              <Field title="Origin terminal">{load.origin_terminal_code}</Field>
              <Field title="Destination terminal">{load.destination_terminal_code}</Field>
              <Field title="Distance">{quantity(load.distance_miles, 'mi')}</Field>
              <Field title="Cargo weight">{quantity(load.weight_lbs, 'lbs')}</Field>
              <Field title="Pickup">{timestamp(load.pickup_at)}</Field>
              <Field title="Delivery">{timestamp(load.delivery_at)}</Field>
              <Field title="Offer expires">{timestamp(load.expires_at)}</Field>
              <Field title="Time left at last update">{quantity(load.seconds_remaining, 'sim seconds')}</Field>
            </dl>
          </section>

          <section className="space-y-4 border-t border-base-300 pt-6" aria-labelledby="load-price-title">
            <h3 id="load-price-title" className="font-semibold">Pricing</h3>
            <dl className="grid grid-cols-2 gap-5">
              <Field title="Offered rate">{amount(load.offered_rate)}</Field>
              <Field title="Estimated profit">{amount(load.estimated_profit)}</Field>
              <Field title="Profit category">{label(load.profit_category)}</Field>
            </dl>
          </section>

          <section className="space-y-4 border-t border-base-300 pt-6" aria-labelledby="load-other-title">
            <h3 id="load-other-title" className="font-semibold">Additional details</h3>
          <dl className="grid grid-cols-2 gap-5">
            <Field title="Cost per mile">{amount(load.cost_per_mile)}</Field>
            <Field title="Estimated loaded cost">{amount(load.estimated_cost)}</Field>
            <Field title="Estimated deadhead distance">{quantity(load.estimated_deadhead_miles, 'mi')}</Field>
            <Field title="Estimated deadhead cost">{amount(load.estimated_deadhead_cost)}</Field>
            <Field title="Estimated total cost">{amount(load.estimated_total_cost)}</Field>
            <Field title="Winning bid rate">{amount(load.winning_bid_rate)}</Field>
            <Field title="Bid decision time">{timestamp(load.bid_decision_at)}</Field>
            <Field title="Board assigned unit">{load.board_assigned_unit_id}</Field>
            <Field title="Assigned terminal">{load.assigned_terminal_code}</Field>
            <Field title="Board order ID">{load.board_order_id}</Field>
            <Field title="Origin latitude">{load.origin_latitude}</Field>
            <Field title="Origin longitude">{load.origin_longitude}</Field>
            <Field title="Destination latitude">{load.destination_latitude}</Field>
            <Field title="Destination longitude">{load.destination_longitude}</Field>
            <Field title="Created on board">{timestamp(load.board_created_at)}</Field>
            <Field title="Last synced">{timestamp(load.last_synced_at)}</Field>
            <Field title="Missing from board since">{timestamp(load.board_missing_at)}</Field>
            <Field title="Local shipment ID">{load.shipment_id}</Field>
            <Field title="Origin terminal ID">{load.origin_terminal_id}</Field>
            <Field title="Destination terminal ID">{load.destination_terminal_id}</Field>
          </dl>
          </section>
      <p className="text-xs text-base-content/60">Dates are shown in your local time. Countdown values are simulated seconds at the last update.</p>
    </div>
  )
}
