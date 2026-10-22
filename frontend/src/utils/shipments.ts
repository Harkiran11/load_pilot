import type { LoadView } from '../api/shipments'

export type ShipmentStatus = 'all' | 'accepted' | 'in_transit' | 'completed'
export type ShipmentSort = 'created' | 'pickup' | 'delivery' | 'synced'

export function confirmedShipments(loads: LoadView[]) {
  return loads.filter((load) => ['accepted', 'in_transit', 'completed'].includes(load.status) && !load.board_missing_at && !load.shipment_history_deleted_at)
}

export function filterShipments(shipments: LoadView[], search: string, status: ShipmentStatus) {
  const query = search.trim().toLowerCase()
  return confirmedShipments(shipments).filter((shipment) =>
    (status === 'all' || shipment.status === status)
    && (!query || [shipment.external_id, shipment.origin_terminal_code, shipment.destination_terminal_code,
      shipment.origin_city, shipment.destination_city, shipment.assigned_terminal_code, shipment.board_assigned_unit_id?.toString()]
      .some((value) => value?.toLowerCase().includes(query))),
  )
}

export function sortShipments(shipments: LoadView[], field: ShipmentSort, direction: 'asc' | 'desc') {
  const dateKey = { created: 'board_created_at', pickup: 'pickup_at', delivery: 'delivery_at', synced: 'last_synced_at' } as const
  return [...shipments].sort((a, b) => {
    const leftDate = a[dateKey[field]]
    const rightDate = b[dateKey[field]]
    const left = leftDate ? Date.parse(leftDate) : NaN
    const right = rightDate ? Date.parse(rightDate) : NaN
    if (Number.isFinite(left) !== Number.isFinite(right)) return Number.isFinite(left) ? -1 : 1
    const delta = Number.isFinite(left) && Number.isFinite(right) ? left - right : 0
    return (direction === 'asc' ? delta : -delta) || a.external_id.localeCompare(b.external_id, undefined, { numeric: true })
  })
}

export function isDeliveryOverdue(shipment: LoadView, simTime: string | null): boolean {
  return shipment.status !== 'completed' && !!simTime && !!shipment.delivery_at
    && Date.parse(simTime) > Date.parse(shipment.delivery_at)
}

export function shipmentStage(status: string) {
  return ['accepted', 'in_transit', 'completed'].indexOf(status)
}

export const SHIPMENT_STAGES = ['Accepted', 'In transit', 'Delivered'] as const

/**
 * Fraction of the pickup-to-delivery window already elapsed at the given
 * simulation time. Null when the schedule is incomplete, so the UI can show an
 * explicit "no schedule" state instead of a misleading zero.
 */
export function deliveryProgress(shipment: LoadView, simTime: string | null): number | null {
  const pickup = shipment.pickup_at ? Date.parse(shipment.pickup_at) : NaN
  const delivery = shipment.delivery_at ? Date.parse(shipment.delivery_at) : NaN
  if (!Number.isFinite(pickup) || !Number.isFinite(delivery) || delivery <= pickup) return null
  const now = simTime ? Date.parse(simTime) : NaN
  if (!Number.isFinite(now)) return null
  if (now <= pickup) return 0
  if (now >= delivery) return 1
  return (now - pickup) / (delivery - pickup)
}

export function estimatedHoursRemaining(shipment: LoadView, simTime: string | null): number | null {
  if (shipment.status === 'completed') return 0
  const delivery = shipment.delivery_at ? Date.parse(shipment.delivery_at) : NaN
  const now = simTime ? Date.parse(simTime) : NaN
  if (!Number.isFinite(delivery) || !Number.isFinite(now)) return null
  return (delivery - now) / 3_600_000
}

/** Human-readable countdown for an active trip, rounded to sensible units. */
export function remainingLabel(hours: number | null) {
  if (hours === null) return null
  if (hours <= 0) return 'Due now'
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min left`
  if (hours < 48) return `${Math.round(hours)} h left`
  return `${Math.round(hours / 24)} d left`
}
