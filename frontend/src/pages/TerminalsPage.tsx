import { Fragment, useEffect, useState } from 'react'
import { subscribeTerminalStatuses } from '../api/terminals'
import type { TerminalStatus, TerminalStreamState } from '../api/terminals'
import EmptyPanel from '../components/EmptyPanel'
import PageSection from '../components/PageSection'
import TerminalFleetSchedules from '../components/TerminalFleetSchedules'
import { subscribeLoads } from '../api/orders'
import { usePageStatus } from '../context/page-status'

const statusLabels: Record<TerminalStatus['status'], string> = {
  available: 'Available',
  unavailable: 'Unavailable',
  empty: 'Empty',
  attention: 'Needs attention',
  unknown: 'Unknown',
}

const statusColors: Record<TerminalStatus['status'], string> = {
  available: 'badge-success',
  unavailable: 'badge-neutral',
  empty: 'badge-ghost',
  attention: 'badge-warning',
  unknown: 'badge-info',
}

export default function TerminalsPage() {
  const [{ terminals, connection, error }, setStream] = useState<TerminalStreamState>({
    terminals: null,
    connection: 'connecting',
    error: null,
  })

  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [revision, setRevision] = useState(0)
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  const hasExpanded = expanded.size > 0
  usePageStatus(connection, error ?? (hasExpanded ? scheduleError : null))

  useEffect(() => subscribeTerminalStatuses((state) => {
    setStream(state)
    if (state.connection === 'live' && state.terminals) {
      setRevision((value) => value + 1)
      const present = new Set(state.terminals.map((terminal) => terminal.terminal_id))
      setExpanded((current) => new Set([...current].filter((id) => present.has(id))))
    }
  }), [])

  useEffect(() => {
    if (!hasExpanded) return
    return subscribeLoads({ order_type: 'all', status: 'all', loads: false }, (state) => {
      setScheduleError(state.error)
      if (state.connection === 'live') setRevision((value) => value + 1)
    })
  }, [hasExpanded])

  function toggleTerminal(id: string) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const terminalCodes = Object.fromEntries((terminals ?? []).map((terminal) => [terminal.terminal_id, terminal.code]))

  const availableUnits = terminals?.length && terminals.every((terminal) => terminal.available_units !== null)
    ? terminals.reduce((total, terminal) => total + (terminal.available_units ?? 0), 0)
    : null

  return (
    <PageSection title="Terminals" description="Compare current capacity across hubs.">
      {error && (
        <div role="alert" className="alert alert-warning">
          <div>
            <p>{error}</p>
            {terminals !== null && <p className="mt-1 text-sm">Showing the previous results. Capacity may have changed.</p>}
          </div>
        </div>
      )}

      {hasExpanded && scheduleError && <p role="status" className="text-sm text-warning">Schedule updates are reconnecting. Expanded schedules refresh when live updates resume.</p>}
      <div aria-busy={terminals === null}>
        {terminals === null && (
          <div role="status" className="flex items-center gap-3 rounded-xl border border-base-300 bg-base-100 p-8">
            <span className="loading loading-spinner text-primary" aria-hidden="true" />
            {connection === 'reconnecting' ? 'Waiting to reconnect for terminal capacity…' : 'Loading terminal capacity…'}
          </div>
        )}

        {terminals?.length === 0 && (
          <EmptyPanel title="No terminals yet" description="Terminal capacity will appear after the first terminal sync." />
        )}

        {terminals !== null && terminals.length > 0 && (
          <div className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-3">
              {[
                { label: 'Terminals', value: terminals.length },
                { label: 'Available units', value: availableUnits ?? '—' },
                { label: 'Needs attention', value: terminals.filter((terminal) => terminal.status === 'attention').length },
              ].map((summary) => (
                <div key={summary.label} className="stat rounded-xl border border-base-300 bg-base-100">
                  <div className="stat-title">{summary.label}</div>
                  <div className="stat-value text-primary">{summary.value}</div>
                </div>
              ))}
            </div>

            <div className="overflow-x-auto rounded-xl border border-base-300 bg-base-100">
              <table className="table">
                <caption className="px-4 py-4 text-left text-sm text-base-content/65">
                  Local fleet counts compared with board availability. Expand a terminal to inspect each unit’s schedule.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Terminal</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="text-right">Total units</th>
                    <th scope="col" className="text-right">Available</th>
                    <th scope="col" className="text-right">Assigned</th>
                    <th scope="col" className="text-right">Board available</th>
                    <th scope="col">Fleet schedules</th>
                  </tr>
                </thead>
                <tbody>
                  {terminals.map((terminal) => (
                    <Fragment key={terminal.terminal_id}>
                    <tr className={expanded.has(terminal.terminal_id) ? 'bg-primary/5' : 'hover:bg-base-200/60'}>
                      <th scope="row" className="min-w-44 font-normal">
                        <span className="font-semibold text-primary">{terminal.code}</span>
                        <p className="font-medium">{terminal.name}</p>
                        <p className="text-xs text-base-content/60">{terminal.city}, {terminal.state}</p>
                      </th>
                      <td><span className={`badge badge-soft whitespace-nowrap ${statusColors[terminal.status]}`}>{statusLabels[terminal.status]}</span></td>
                      <td className="text-right tabular-nums">{terminal.total_units ?? '—'}</td>
                      <td className="text-right font-semibold tabular-nums">{terminal.available_units ?? '—'}</td>
                      <td className="text-right tabular-nums">{terminal.assigned_units ?? '—'}</td>
                      <td className="text-right tabular-nums">{terminal.board_available_units ?? '—'}</td>
                      <td><button type="button" className="btn btn-ghost btn-sm whitespace-nowrap text-primary" aria-expanded={expanded.has(terminal.terminal_id)} aria-controls={`terminal-fleet-${terminal.terminal_id}`} onClick={() => toggleTerminal(terminal.terminal_id)}><span aria-hidden="true">{expanded.has(terminal.terminal_id) ? '▾' : '▸'}</span>{expanded.has(terminal.terminal_id) ? 'Hide fleet' : 'View fleet'}</button></td>
                    </tr>
                    {expanded.has(terminal.terminal_id) && <tr><td colSpan={7} className="bg-base-200/50 p-0"><div id={`terminal-fleet-${terminal.terminal_id}`}><TerminalFleetSchedules code={terminal.code} revision={revision} terminalCodes={terminalCodes} /></div></td></tr>}
                  </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </PageSection>
  )
}
