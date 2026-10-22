import { apiFetch, withQuery } from './client'
import { env } from '../config/env'
import { endpoints } from './endpoints'
import type { CostPreviewRequest, PriceAnalysisRequest, ActionRequest, ActionResult, BidRequest, LoadStreamEvent, LoadView, PaginationParams, SystemStatus } from './types'
import { isValidBidAmount } from '../utils/bid-amount'
import { apiFeedback } from '../utils/api-feedback'

export type OrderType = 'fixed_rate' | 'open_bid'
export type LoadStatus = 'open' | 'accepted' | 'declined' | 'bid_submitted' | 'bid_lost' | 'in_transit' | 'completed' | 'expired'

export interface LoadParams extends PaginationParams {
  order_type?: OrderType
  status?: LoadStatus | 'all'
  origin_terminal?: string
  include_missing?: boolean
}

export interface LoadStreamParams {
  order_type?: OrderType | 'all'
  status?: LoadStatus | 'all' | 'live'
  origin_terminal?: string
  /**
   * Set to false for a clock-only subscriber. Screens that only need the
   * simulation time and the connection state should not receive the order
   * payload at all: the board retains every closed order, so a full stream
   * was tens of megabytes a second per screen.
   */
  loads?: boolean
}

export function connectLoads(params: LoadStreamParams = {}): WebSocket {
  const path = withQuery(endpoints.loads.webSocket, {
    ...params,
    ...(params.loads === false ? { loads: 'false' } : {}),
  })
  const url = new URL(`${env.apiBaseUrl}${path}`, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(url)
}

export async function getLoads(
  params: LoadParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.loads.list, params), { ...options, method: 'GET' })
}

export async function getLoad(
  externalId: string,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.detail(externalId), { ...options, method: 'GET' })
}

/** The full order record, including its action and assignment history. */
export async function getLoadDetail(externalId: string, options: RequestInit = {}): Promise<LoadView> {
  const response = await getLoad(externalId, options)
  if (!response.ok) throw new Error(`The order record could not be loaded (HTTP ${response.status}).`)
  const body = await response.json()
  if (!body || typeof body !== 'object' || typeof body.external_id !== 'string') {
    throw new Error('The order record could not be read.')
  }
  return body as LoadView
}

export async function getCostPreview(
  externalId: string,
  body: CostPreviewRequest = {},
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.costPreview(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function evaluateOrder(
  externalId: string,
  body: CostPreviewRequest = {},
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.evaluate(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// On-demand analysis is advisory only; this never submits a booking or bid.
export async function getPriceAnalysis(
  externalId: string,
  body: PriceAnalysisRequest,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.priceAnalysis(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function acceptLoad(
  externalId: string,
  body: ActionRequest = {},
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.accept(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function declineLoad(
  externalId: string,
  body: ActionRequest = {},
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.decline(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function bidOnLoad(
  externalId: string,
  body: BidRequest,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.loads.bid(externalId), {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export interface LiveLoadsState {
  loads: LoadView[] | null
  system: SystemStatus | null
  connection: 'connecting' | 'live' | 'reconnecting'
  error: string | null
}

// Every snapshot, update, and heartbeat contains the complete order list.
export function subscribeLoads(
  params: LoadStreamParams,
  onChange: (state: LiveLoadsState) => void,
): () => void {
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let heartbeatTimer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let retryDelay = 1000
  let loads: LoadView[] | null = null
  let system: SystemStatus | null = null
  let error: string | null = null

  function reconnect() {
    if (disposed) return
    clearTimeout(heartbeatTimer)
    onChange({ loads, system, connection: 'reconnecting', error: error ?? 'Connection lost. Reconnecting automatically…' })
    reconnectTimer = setTimeout(connect, retryDelay)
    retryDelay = Math.min(retryDelay * 2, 30_000)
  }

  function connect() {
    if (disposed) return
    error = null

    try {
      socket = connectLoads(params)
    } catch {
      error = 'Unable to connect. Retrying automatically…'
      reconnect()
      return
    }


    const currentSocket = socket

    function waitForHeartbeat() {
      clearTimeout(heartbeatTimer)
      // The server sends a heartbeat every 30 seconds, even without changes.
      heartbeatTimer = setTimeout(() => {
        error = 'Updates paused. Reconnecting automatically…'
        currentSocket.close()
      }, 45_000)
    }

    currentSocket.onopen = waitForHeartbeat
    currentSocket.onmessage = (event) => {
      if (disposed) return
      try {
        const message: LoadStreamEvent = JSON.parse(event.data)
        if (message.event === 'loads.error') {
          error = 'Live order updates are temporarily unavailable. Reconnecting automatically…'
          currentSocket.close()
          return
        }

        if (!['loads.snapshot', 'loads.updated', 'loads.heartbeat'].includes(message.event)
          || !Array.isArray(message.items) || !message.system) {
          throw new Error('Invalid order message')
        }

        loads = message.items
        system = message.system
        retryDelay = 1000
        error = null
        waitForHeartbeat()
        onChange({ loads, system, connection: 'live', error: null })
      } catch {
        error = 'Unable to read order updates. Reconnecting automatically…'
        currentSocket.close()
      }
    }
    currentSocket.onerror = () => currentSocket.close()
    currentSocket.onclose = reconnect
  }

  connect()

  return () => {
    disposed = true
    clearTimeout(reconnectTimer)
    clearTimeout(heartbeatTimer)
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      socket.close()
    }
  }
}

export class LoadDecisionError extends Error {
  readonly uncertain: boolean
  readonly details: string[]
  readonly suggestions: string[]

  constructor(message: string, uncertain: boolean, feedback?: { details: string[]; suggestions: string[] }) {
    super(message)
    this.name = 'LoadDecisionError'
    this.uncertain = uncertain
    this.details = feedback?.details ?? []
    this.suggestions = feedback?.suggestions ?? []
  }
}

// Never retry booking actions automatically: a failed connection can hide success.
export async function submitLoadDecision(
  externalId: string,
  decision: 'accept' | 'bid' | 'decline',
  bidRate?: string,
): Promise<ActionResult> {
  if (decision === 'bid' && (!bidRate || !isValidBidAmount(bidRate))) {
    throw new LoadDecisionError('Enter a valid positive bid amount.', false)
  }
  let response: Response
  try {
    response = decision === 'bid'
      ? await bidOnLoad(externalId, { bid_rate: bidRate as string })
      : decision === 'accept' ? await acceptLoad(externalId) : await declineLoad(externalId)
  } catch {
    throw new LoadDecisionError('The connection was interrupted, so we could not confirm your submission. Check the order status before submitting again.', true)
  }

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const feedback = apiFeedback(body, response.status)
    const rejectedLocally = body?.evaluation?.eligible === false
    const rejectedByBoard = body?.feedback?.state_refreshed === true && body?.feedback?.action_result?.action?.request_status === 'failed'
    throw new LoadDecisionError(feedback.message, response.status >= 500 || (response.status === 409 && !rejectedLocally && !rejectedByBoard), feedback)
  }
  if (!body?.order || !body?.action || typeof body.sync_pending !== 'boolean') {
    throw new LoadDecisionError('We received an incomplete response and could not confirm your submission. Check the order status before trying again.', true)
  }
  return body as ActionResult
}

export async function getOrderExpiration(
  externalId: string,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.orderExpiration(externalId), { ...options, method: 'GET' })
}

export type { CostPreviewRequest, PriceAnalysisRequest, PriceAnalysisResult, MarketStatistics, HistoricalMarketContext, DispatchEvaluation, ExpectedEconomics, OrderRecommendations, RankedMatch, ActionRequest, ActionResult, ActionView, AssignmentView, BidRequest, EvaluationView, LoadDetail, LoadStreamEvent, LoadStreamMessage, LoadView, OrderExpiration } from './types'
