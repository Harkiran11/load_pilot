import { apiFetch } from './client'
import { endpoints } from './endpoints'

export async function getHealth(
  options: RequestInit = {},
) {
  return apiFetch(endpoints.health, { ...options, method: 'GET' })
}

export type { HealthResponse } from './types'
