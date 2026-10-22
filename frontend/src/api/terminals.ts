import { apiFetch, withQuery } from './client'
import { endpoints } from './endpoints'
import type { PaginationParams, TerminalStatus, TerminalStatusEvent } from './types'
import { env } from '../config/env'

export async function getTerminals(
  params: PaginationParams = {},
  options: RequestInit = {},
) {
  return apiFetch(withQuery(endpoints.terminals.list, params), { ...options, method: 'GET' })
}

export async function getTerminal(
  code: string,
  options: RequestInit = {},
) {
  return apiFetch(endpoints.terminals.detail(code), { ...options, method: 'GET' })
}

export async function getTerminalStatuses(
  options: RequestInit = {},
) {
  return apiFetch(endpoints.terminals.status, { ...options, method: 'GET' })
}

// Close on unmount and reconnect after closure. Each message replaces the status list.
export function connectTerminalStatus(): WebSocket {
  const url = new URL(
    `${env.apiBaseUrl}${endpoints.terminals.statusWebSocket}`,
    window.location.origin,
  )
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(url)
}

export interface TerminalStreamState {
  terminals: TerminalStatus[] | null
  connection: 'connecting' | 'live' | 'reconnecting'
  error: string | null
}

// Every snapshot, update, and heartbeat contains the complete terminal list.
export function subscribeTerminalStatuses(
  onChange: (state: TerminalStreamState) => void,
): () => void {
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let heartbeatTimer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let retryDelay = 1000
  let terminals: TerminalStatus[] | null = null
  let error: string | null = null

  function reconnect() {
    if (disposed) return
    clearTimeout(heartbeatTimer)
    onChange({ terminals, connection: 'reconnecting', error: error ?? 'Connection lost. Reconnecting automatically…' })
    reconnectTimer = setTimeout(connect, retryDelay)
    retryDelay = Math.min(retryDelay * 2, 30_000)
  }

  function connect() {
    if (disposed) return
    error = null

    try {
      socket = connectTerminalStatus()
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
        const message: TerminalStatusEvent = JSON.parse(event.data)
        if (message.event === 'terminals.error') {
          error = message.message
          currentSocket.close()
          return
        }

        if (!['terminals.snapshot', 'terminals.updated', 'terminals.heartbeat'].includes(message.event)
          || !Array.isArray(message.items)) {
          throw new Error('Invalid terminal status message')
        }

        terminals = [...message.items].sort((a, b) => a.code.localeCompare(b.code))
        retryDelay = 1000
        error = null
        waitForHeartbeat()
        onChange({ terminals, connection: 'live', error: null })
      } catch {
        error = 'Unable to read terminal updates. Reconnecting automatically…'
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

export type { TerminalDetail, TerminalStatus, TerminalStatusEvent, TerminalStatusMessage, TerminalView } from './types'
