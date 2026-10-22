import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import type { PaginationParams, StartAutopilotRequest } from './types'

export async function getAutopilotRuns(params: PaginationParams = {}, options: RequestInit = {}) {
  return apiFetch(withQuery(endpoints.autopilot.runs, params), { ...options, method: 'GET' })
}

export async function getAutopilotRun(runId: string, options: RequestInit = {}) {
  return apiFetch(endpoints.autopilot.run(runId), { ...options, method: 'GET' })
}

export async function startAutopilotRun(body: StartAutopilotRequest, options: RequestInit = {}) {
  return apiFetch(endpoints.autopilot.runs, { ...options, method: 'POST', body: JSON.stringify(body) })
}

export async function stopAutopilotRun(runId: string, options: RequestInit = {}) {
  return apiFetch(endpoints.autopilot.stop(runId), { ...options, method: 'POST' })
}

export async function getAutopilotAttempts(runId: string, params: PaginationParams = {}, options: RequestInit = {}) {
  return apiFetch(withQuery(endpoints.autopilot.attempts(runId), params), { ...options, method: 'GET' })
}

export async function getAutopilotAttempt(runId: string, attemptId: string, options: RequestInit = {}) {
  return apiFetch(endpoints.autopilot.attempt(runId, attemptId), { ...options, method: 'GET' })
}

export type { AutopilotAttempt, AutopilotRun, AutopilotSettings, StartAutopilotRequest } from './types'
