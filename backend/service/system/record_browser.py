"""Read-only access to the tables we own.

The board is not our database: we keep the orders, the locks, the decisions and
the audit trail ourselves. This module exposes those tables for the "Our
records" screen so the state we hold is visible, not just described.

Only the tables listed here can be read, only the listed columns are returned,
and long values are truncated — several audit tables hold large JSON payloads
that would make a page unusable.
"""
from dataclasses import dataclass, field

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

MAX_CELL_LENGTH = 240


@dataclass(frozen=True)
class RecordTable:
    """One of our tables, and how to present it."""

    table: str
    label: str
    description: str
    columns: tuple[str, ...] = ()
    order_by: str = ''
    group: str = 'Operations'
    _fallback: tuple[str, ...] = field(default=(), repr=False)


# The tables we own, in the order a reviewer would want to read them.
RECORD_TABLES: tuple[RecordTable, ...] = (
    RecordTable(
        'shipments', 'Orders',
        'Every order the board has published to us, with its costs, windows and status.',
        ('external_id', 'order_type', 'status', 'origin_terminal_code', 'destination_terminal_code',
         'offered_rate', 'estimated_total_cost', 'weight_lbs', 'pickup_at', 'delivery_at',
         'expires_at', 'board_created_at', 'board_missing_at'),
        'board_created_at DESC',
        group='Operations',
    ),
    RecordTable(
        'evaluations', 'Evaluations',
        'Every profit and feasibility decision we made, with the policy that produced it.',
        ('evaluation_id', 'shipment_id', 'eligible', 'total_cost', 'gross_profit', 'margin_pct',
         'policy_snapshot', 'created_at'),
        'created_at DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'dispatch_actions', 'Actions',
        'Every accept, bid and decline we attempted against the board.',
        ('action_id', 'shipment_id', 'action_type', 'request_status', 'bid_rate',
         'board_http_status', 'board_message', 'requested_at', 'resolved_at'),
        'requested_at DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'assignments', 'Truck locks',
        'The occupied window that stops a truck being booked twice. Busy window is the drive; '
        'occupied window also covers the empty trip to the pickup and the turnaround.',
        ('assignment_id', 'shipment_id', 'unit_id', 'assignment_status',
         'busy_from', 'busy_until', 'occupied_from', 'occupied_until', 'confirmed_at'),
        'occupied_from DESC',
        group='Operations',
    ),
    RecordTable(
        'assignment_reservations', 'Reservations',
        'Holds placed on a truck while a booking is in flight, and released when the outcome is known.',
        ('action_id', 'shipment_id', 'unit_id', 'expires_at', 'created_at'),
        'created_at DESC',
        group='Operations',
    ),
    RecordTable(
        'fleet_allocation_plans', 'Allocation plans',
        'The order-and-truck sets our planner proposed, and what it decided about them.',
        ('plan_id', 'run_id', 'status', 'created_at', 'expires_at', 'finished_at'),
        'created_at DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'fleet_allocation_reservations', 'Plan reservations',
        'Capacity the planner held while its set was being priced and approved.',
        ('reservation_id', 'plan_id', 'shipment_id', 'unit_id', 'status', 'expires_at_sim'),
        'expires_at_sim DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'autopilot_runs', 'Autopilot runs',
        'Each autopilot run, the limits it was given, and why it stopped.',
        ('run_id', 'mode', 'status', 'stop_reason', 'orders_reserved', 'max_orders',
         'max_duration_seconds', 'started_at_wall', 'stopped_at_wall'),
        'started_at_wall DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'autopilot_attempts', 'Autopilot decisions',
        'Every decision the autopilot made, and what it did with it.',
        ('attempt_id', 'run_id', 'shipment_id', 'order_type', 'decision', 'status',
         'bid_rate', 'reason_codes', 'created_at', 'decided_at_wall'),
        'created_at DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'activity', 'Event log',
        'The human-readable event log behind the Activity page.',
        ('event_id', 'event_type', 'external_id', 'message', 'occurred_at_wall', 'occurred_at_sim'),
        'occurred_at_wall DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'fleet_units', 'Fleet units',
        'Our 80 trucks: which hub they are homed to and what the board says about them.',
        ('unit_id', 'unit_number', 'board_unit_id', 'home_terminal_id', 'status',
         'current_latitude', 'current_longitude', 'available_at', 'board_missing_at'),
        'unit_number',
        group='Operations',
    ),
    RecordTable(
        'terminals', 'Terminals',
        'The 10 hubs we dispatch from.',
        ('terminal_id', 'code', 'name', 'city', 'state', 'latitude', 'longitude',
         'board_terminal_id', 'board_missing_at'),
        'code',
        group='Operations',
    ),
    RecordTable(
        'fleet_capacity', 'Terminal capacity',
        'What each hub can offer, and whether the board agrees with us.',
        ('terminal_id', 'board_total_units', 'board_available_units', 'capacity_checked_at',
         'consistent'),
        'capacity_checked_at DESC',
        group='Operations',
    ),
    RecordTable(
        'simulation_clock', 'Simulation clock',
        'The single clock row every expiry and occupancy comparison is judged against.',
        ('clock_id', 'sim_time_utc', 'wall_time_utc', 'simulation_speed', 'last_synced_at'),
        group='Operations',
    ),
    RecordTable(
        'historical_shipments', 'Historical dataset',
        'The board’s past shipments, imported once. This is what the historical lane range is built from.',
        ('source_record_key', 'lane_id', 'order_type', 'distance_miles', 'weight_lbs',
         'offered_rate', 'bid_rate', 'won', 'cost_per_mile', 'estimated_cost', 'shipped_at'),
        'shipped_at DESC',
        group='Reference',
    ),
    RecordTable(
        'live_batch_audits', 'Model batch requests',
        'Every batch we sent the model, and the response it gave back.',
        ('audit_id', 'run_id', 'http_status', 'error_code', 'requested_at_wall', 'received_at_wall'),
        'requested_at_wall DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'live_submission_audits', 'Board submissions',
        'Every bid and accept we sent to the board, and what it answered.',
        ('submission_id', 'attempt_id', 'external_id', 'unit_id', 'amount_usd',
         'status', 'submitted_at_wall', 'resolved_at_wall'),
        'submitted_at_wall DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'prediction_observations', 'Price analyses',
        'Every price analysis we asked the model for, with the cost and policy we used.',
        ('observation_id', 'external_id', 'http_status', 'error_code',
         'started_at_wall', 'finished_at_wall'),
        'started_at_wall DESC',
        group='Decisions & audit',
    ),
    RecordTable(
        'observed_cohorts', 'Reviewed cohorts',
        'Groups of shipments whose estimates a reviewer compared against actuals.',
        ('cohort_id', 'name', 'definition', 'created_at_wall'),
        'created_at_wall DESC',
        group='Reference',
    ),
    RecordTable(
        'observed_comparisons', 'Estimate vs actual',
        'How our booking-time estimates compared with the real outcome.',
        ('comparison_id', 'cohort_id', 'created_at_wall'),
        'created_at_wall DESC',
        group='Reference',
    ),
)

TABLES_BY_NAME = {table.table: table for table in RECORD_TABLES}


def _cell(value) -> str | None:
    """Render one value as short text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('utf-8', 'replace')
    elif not isinstance(value, str):
        value = str(value)
    if len(value) > MAX_CELL_LENGTH:
        return value[:MAX_CELL_LENGTH] + '…'
    return value


def available_tables(session: Session) -> list[dict]:
    """Every table we own, with its current row count."""
    inspector = inspect(session.get_bind())
    present = set(inspector.get_table_names())
    result = []
    for table in RECORD_TABLES:
        if table.table not in present:
            continue
        count = session.scalar(select(func.count()).select_from(text(table.table))) or 0
        result.append({
            'table': table.table, 'label': table.label, 'description': table.description,
            'group': table.group, 'row_count': count,
        })
    return result


def table_rows(session: Session, name: str, limit: int = 25, offset: int = 0) -> dict:
    """One page of rows from one of our tables."""
    table = TABLES_BY_NAME.get(name)
    if table is None:
        raise KeyError(name)
    inspector = inspect(session.get_bind())
    if table.table not in set(inspector.get_table_names()):
        raise KeyError(name)
    columns = [column['name'] for column in inspector.get_columns(table.table)]
    selected = [column for column in table.columns if column in columns]
    if not selected:
        selected = columns[:8]
    total = session.scalar(select(func.count()).select_from(text(table.table))) or 0
    statement = select(*[text(f'"{column}"') for column in selected]).select_from(text(table.table))
    if table.order_by and table.order_by.split()[0] in columns:
        statement = statement.order_by(text(table.order_by))
    rows = session.execute(statement.limit(limit).offset(offset)).all()
    return {
        'table': table.table, 'label': table.label, 'description': table.description,
        'columns': selected, 'rows': [[_cell(value) for value in row] for row in rows],
        'total': total, 'limit': limit, 'offset': offset,
    }
