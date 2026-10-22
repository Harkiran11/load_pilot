import { apiFetch } from './client'
import { endpoints } from './endpoints'

export interface RecordTableSummary {
  table: string
  label: string
  description: string
  group: string
  row_count: number
}

export interface RecordPage {
  table: string
  label: string
  description: string
  columns: string[]
  rows: (string | null)[][]
  total: number
  limit: number
  offset: number
}

/** The tables we keep in our own dispatcher database, with row counts. */
export async function getRecordTables(options: RequestInit = {}): Promise<RecordTableSummary[]> {
  const response = await apiFetch(endpoints.system.records, { ...options, method: 'GET' })
  if (!response.ok) throw new Error(`The database tables could not be listed (HTTP ${response.status}).`)
  const body = await response.json()
  if (!Array.isArray(body)) throw new Error('The database table list was invalid.')
  return body as RecordTableSummary[]
}

/** One page of rows from a table we keep. */
export async function getRecordRows(
  table: string,
  params: { limit?: number; offset?: number } = {},
  options: RequestInit = {},
): Promise<RecordPage> {
  const search = new URLSearchParams()
  if (params.limit !== undefined) search.set('limit', String(params.limit))
  if (params.offset !== undefined) search.set('offset', String(params.offset))
  const query = search.toString()
  const response = await apiFetch(
    `${endpoints.system.recordsTable(table)}${query ? `?${query}` : ''}`,
    { ...options, method: 'GET' },
  )
  if (!response.ok) throw new Error(`Those records could not be loaded (HTTP ${response.status}).`)
  const body = await response.json()
  if (!body || typeof body !== 'object' || !Array.isArray((body as RecordPage).rows)) {
    throw new Error('The record response was invalid.')
  }
  return body as RecordPage
}
