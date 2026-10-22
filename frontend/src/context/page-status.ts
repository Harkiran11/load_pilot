import { createContext, useContext, useEffect } from 'react'

export interface PageStatus {
  owner: symbol
  connection: 'connecting' | 'live' | 'reconnecting'
  error: string | null
}

export const PageStatusContext = createContext<{
  status: PageStatus | null
  report: (status: PageStatus) => () => void
} | null>(null)

export function usePageStatus(connection: PageStatus['connection'], error: string | null) {
  const report = useContext(PageStatusContext)?.report
  useEffect(() => report?.({ owner: Symbol('page-status'), connection, error }), [report, connection, error])
}
