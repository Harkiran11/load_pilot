import { useEffect, useMemo, useState } from 'react'
import { getRecordRows, getRecordTables } from '../api/records'
import type { RecordPage, RecordTableSummary } from '../api/records'
import PageSection from '../components/PageSection'

const PAGE_SIZE = 25

function columnLabel(name: string) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function cell(value: string | null) {
  if (value === null || value === '') return '—'
  return value
}

export default function RecordsPage() {
  const [tables, setTables] = useState<RecordTableSummary[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<{ key: string; page: RecordPage } | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getRecordTables({ signal: controller.signal }).then(
      (result) => {
        if (controller.signal.aborted) return
        setTables(result)
        setSelected(result[0]?.table ?? null)
      },
      (cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : 'The database tables could not be listed.')
      },
    )
    return () => controller.abort()
  }, [])

  const requestKey = selected ? `${selected}:${offset}` : null

  useEffect(() => {
    if (!selected || requestKey === null) return
    const controller = new AbortController()
    void getRecordRows(selected, { limit: PAGE_SIZE, offset }, { signal: controller.signal }).then(
      (result) => { if (!controller.signal.aborted) { setLoaded({ key: requestKey, page: result }); setError(null) } },
      (cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : 'Those records could not be loaded.')
      },
    )
    return () => controller.abort()
  }, [selected, offset, requestKey])

  // Records from a previous selection are not shown against a new one.
  const page = loaded?.key === requestKey ? loaded.page : null
  const loading = !!requestKey && loaded?.key !== requestKey

  const grouped = useMemo(() => {
    const groups = new Map<string, RecordTableSummary[]>()
    for (const table of tables ?? []) {
      const list = groups.get(table.group) ?? []
      list.push(table)
      groups.set(table.group, list)
    }
    return [...groups.entries()]
  }, [tables])

  const total = page?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <PageSection title="Our records" description="Every record the dispatcher keeps in its own database.">
    <div className="rounded-xl border border-base-300 bg-base-100 p-5">
      <h2 className="font-semibold">This is our database, not the board&rsquo;s</h2>
      <p className="mt-1 text-xs text-base-content/60">
        The load board is the external world. We keep the orders, the truck locks, the
        decisions and the audit trail ourselves, so we can hold a lock the board does not
        know about and prove what we decided and why. These are the tables we own.
      </p>
    </div>

    {error && <p role="alert" className="alert alert-warning text-sm">{error}</p>}

    <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <aside className="rounded-xl border border-base-300 bg-base-100 p-3" aria-label="Database tables">
        {tables === null && <p className="px-2 py-4 text-xs text-base-content/60">Loading tables…</p>}
        {grouped.map(([group, list]) => <div key={group} className="mb-3 last:mb-0">
          <p className="px-2 pb-1 text-[10px] font-semibold tracking-wide text-base-content/50 uppercase">{group}</p>
          <ul className="space-y-0.5">
            {list.map((table) => <li key={table.table}>
              <button
                type="button"
                aria-pressed={selected === table.table}
                onClick={() => { setSelected(table.table); setOffset(0) }}
                className={`flex w-full items-baseline justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${selected === table.table ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-base-200'}`}
              >
                <span className="truncate">{table.label}</span>
                <span className="shrink-0 tabular-nums text-[10px] text-base-content/50">{table.row_count.toLocaleString()}</span>
              </button>
            </li>)}
          </ul>
        </div>)}
      </aside>

      <section className="min-w-0 rounded-xl border border-base-300 bg-base-100" aria-label="Records">
        {!page ? (
          <div className="flex items-center gap-3 p-8 text-sm text-base-content/60">
            <span className="loading loading-spinner loading-sm" /> Loading records…
          </div>
        ) : (
          <>
            <header className="border-b border-base-300 p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="font-semibold">{page.label}</h2>
                <p className="text-xs text-base-content/55 tabular-nums">{total.toLocaleString()} records</p>
              </div>
              <p className="mt-1 text-xs text-base-content/60">{page.description}</p>
            </header>
            <div className="overflow-x-auto" aria-busy={loading}>
              <table className="table table-xs">
                <thead>
                  <tr>{page.columns.map((column) => <th key={column} scope="col" className="whitespace-nowrap">{columnLabel(column)}</th>)}</tr>
                </thead>
                <tbody>
                  {page.rows.length === 0 ? (
                    <tr><td colSpan={page.columns.length} className="py-10 text-center text-base-content/55">No records yet.</td></tr>
                  ) : page.rows.map((row, index) => <tr key={index} className="hover:bg-base-200/60">
                    {row.map((value, column) => <td key={page.columns[column]} className="max-w-[22rem] truncate whitespace-nowrap text-xs" title={value ?? undefined}>{cell(value)}</td>)}
                  </tr>)}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 px-4 py-3">
              <p className="text-xs text-base-content/60" role="status">
                {total === 0 ? 'No records' : `Showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total.toLocaleString()}`}
              </p>
              <nav aria-label="Records pagination" className="flex items-center gap-3">
                <span className="text-xs text-base-content/60">Page {Math.floor(offset / PAGE_SIZE) + 1} of {totalPages}</span>
                <div className="join">
                  <button type="button" className="btn btn-xs join-item" aria-label="First page" disabled={offset === 0} onClick={() => setOffset(0)}>{'<<'}</button>
                  <button type="button" className="btn btn-xs join-item" aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{'<'}</button>
                  <button type="button" className="btn btn-xs join-item" aria-label="Next page" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>{'>'}</button>
                  <button type="button" className="btn btn-xs join-item" aria-label="Last page" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(Math.max(0, (totalPages - 1) * PAGE_SIZE))}>{'>>'}</button>
                </div>
              </nav>
            </div>
          </>
        )}
      </section>
    </div>
  </PageSection>
}
