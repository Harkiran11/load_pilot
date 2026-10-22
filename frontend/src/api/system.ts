import { apiFetch } from './client'
import { endpoints } from './endpoints'
import type { SimulationSettingsRequest } from './types'

export async function getSystemStatus(
  options: RequestInit = {},
) {
  return apiFetch(endpoints.systemStatus, { ...options, method: 'GET' })
}

export async function getSimulationClock(options: RequestInit = {}) {
  if (!import.meta.env.DEV) throw new Error('Temporary simulation controls are available in local development only.')
  return apiFetch(endpoints.simulationClock, { ...options, method: 'GET' })
}

export async function setSimulationSpeed(speed: number, options: RequestInit = {}) {
  if (!import.meta.env.DEV) throw new Error('Temporary simulation controls are available in local development only.')
  if (!Number.isFinite(speed) || speed < 1 || speed > 100_000) {
    throw new Error('Simulation speed must be between 1 and 100,000.')
  }
  return apiFetch(endpoints.simulationClock, {
    ...options,
    method: 'POST',
    body: JSON.stringify({ simulation_speed: speed }),
  })
}

export async function getSimulationSettings(options: RequestInit = {}) {
  if (!import.meta.env.DEV) throw new Error('Temporary simulation controls are available in local development only.')
  return apiFetch(endpoints.simulationSettings, { ...options, method: 'GET' })
}

export async function setSimulationSettings(body: SimulationSettingsRequest, options: RequestInit = {}) {
  if (!import.meta.env.DEV) throw new Error('Temporary simulation controls are available in local development only.')
  if (body.simulation_speed !== undefined && (!Number.isFinite(body.simulation_speed) || body.simulation_speed < 1 || body.simulation_speed > 100_000)) throw new Error('Simulation speed must be between 1 and 100,000.')
  if (body.order_interval_seconds !== undefined && (!Number.isFinite(body.order_interval_seconds) || body.order_interval_seconds < 0.5 || body.order_interval_seconds > 3600)) throw new Error('New order interval must be between 0.5 and 3,600 real seconds.')
  if (body.simulation_speed === undefined && body.order_interval_seconds === undefined) throw new Error('Choose a simulation setting to update.')
  return apiFetch(endpoints.simulationSettings, { ...options, method: 'POST', body: JSON.stringify(body) })
}

export async function spawnSimulationOrders(count = 6, options: RequestInit = {}) {
  if (!import.meta.env.DEV) throw new Error('Temporary simulation controls are available in local development only.')
  if (!Number.isInteger(count) || count < 1 || count > 50) throw new Error('Enter a whole number of orders from 1 to 50.')
  return apiFetch(endpoints.simulationSpawnOrders, { ...options, method: 'POST', body: JSON.stringify({ count }) })
}

export type { SimulationClock, SimulationSettings, SimulationSettingsRequest, SpawnOrdersResult, SystemStatus } from './types'
