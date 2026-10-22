import type { PageStatus } from '../context/page-status'

export function headerStatus(page: Pick<PageStatus, 'connection' | 'error'> | null, board: string | null, syncError: boolean, loading: boolean) {
  if (page?.error) return { label: 'Updates delayed', live: false }
  // No board configured: the bundled dataset is intentional, not a fault.
  if (board === 'disabled') return { label: 'Offline demo', live: false }
  if (board === 'disconnected') return { label: 'Disconnected', live: false }
  if (page?.connection === 'reconnecting' || board === 'reconnecting') return { label: 'Reconnecting…', live: false }
  if (syncError) return { label: 'Sync delayed', live: false }
  if (page?.connection === 'connecting' || board === 'connecting' || (!page && loading)) return { label: 'Connecting…', live: false }
  if (page?.connection === 'live' || board === 'connected') return { label: 'Live', live: true }
  return { label: 'Status unavailable', live: false }
}
