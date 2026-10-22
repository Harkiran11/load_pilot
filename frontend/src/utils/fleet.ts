import type { ProjectedDelivery, UnitView } from '../api/fleet'

export function filterFleet(units: UnitView[], search: string, terminal: string, status: string) {
  const query = search.trim().toLowerCase()
  return units.filter((unit) =>
    (terminal === 'all' || unit.terminal_code === terminal)
    && (status === 'all' || unit.status === status)
    && (!query || [unit.unit_number, unit.first_name, unit.last_name, unit.employee_code, unit.terminal_code]
      .some((value) => value?.toLowerCase().includes(query))),
  ).sort((a, b) => a.unit_number.localeCompare(b.unit_number, undefined, { numeric: true }))
}

export function deliveryPhase(delivery: ProjectedDelivery, simTime: string | null) {
  const now = simTime ? Date.parse(simTime) : NaN
  const pickup = Date.parse(delivery.pickup_at)
  const arrival = Date.parse(delivery.delivery_at)
  if (![now, pickup, arrival].every(Number.isFinite)) return 'unknown'
  if (now >= arrival) return 'completed'
  if (now >= pickup) return 'in_transit'
  return 'scheduled'
}
