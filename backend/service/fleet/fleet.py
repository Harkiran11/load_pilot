import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.ingestion.board_api import BoardFleetSnapshot
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_capacity_response import CapacityCounts, FleetCapacityResponse
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.terminal.terminal import Terminal
from service.orders.expiration import utc
from service.dispatch.scheduling import locking_assignments, occupied_start, occupied_end
from service.audit.activity import add_activity

logger = logging.getLogger('uvicorn.error.fleet')


def validate_roster(snapshots: list[BoardFleetSnapshot]) -> None:
    terminal_ids = [snapshot.terminal.id for snapshot in snapshots]
    unit_ids = [unit.id for snapshot in snapshots for unit in snapshot.units]
    if len(terminal_ids) != len(set(terminal_ids)) or len(unit_ids) != len(set(unit_ids)):
        raise ValueError('Duplicate terminal or unit ID in fleet snapshot')


def reconcile_fleet(
    session: Session, snapshots: list[BoardFleetSnapshot],
    sim_time: datetime, synced_at: datetime,
) -> list[FleetCapacityResponse]:
    """Complete known bookings and compare capacity after applying the roster."""
    seen_ids = [unit.id for snapshot in snapshots for unit in snapshot.units]
    session.execute(
        update(FleetUnit).where(FleetUnit.board_unit_id.not_in(seen_ids)).values(
            board_missing_at=func.coalesce(FleetUnit.board_missing_at, synced_at),
        ),
    )
    completed_assignments = session.execute(
        select(Assignment, Shipment).join(Shipment, Assignment.shipment_id == Shipment.shipment_id)
        .where(
            Shipment.status == 'completed', Shipment.board_missing_at.is_(None),
            Assignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
        ),
    ).all()
    for assignment, shipment in completed_assignments:
        assignment.assignment_status = 'completed'
        assignment.completed_at = shipment.delivery_at or sim_time
    session.flush()

    busy_units = set(session.scalars(
        select(Assignment.unit_id).where(
            locking_assignments(),
            occupied_start() <= sim_time, occupied_end() > sim_time,
        ),
    ))
    busy_units.update(session.scalars(select(AssignmentReservation.unit_id).where(
        AssignmentReservation.busy_from <= sim_time, AssignmentReservation.busy_until > sim_time,
    )))
    from service.allocation.allocation_holds import current_holds
    busy_units.update(hold.unit_id for hold in current_holds(session)
                      if utc(hold.busy_from) <= utc(sim_time) < utc(hold.busy_until))
    terminals = session.scalars(select(Terminal).order_by(Terminal.code)).all()
    units = session.scalars(select(FleetUnit).execution_options(populate_existing=True)).all()
    board = {snapshot.terminal.id: snapshot for snapshot in snapshots}
    reports = []
    for terminal in terminals:
        snapshot = board.get(terminal.board_terminal_id)
        terminal_units = [unit for unit in units if unit.home_terminal_id == terminal.terminal_id]
        current_units = [unit for unit in terminal_units if unit.board_missing_at is None]
        issues = []
        available = assigned = 0
        for unit in current_units:
            board_free = unit.status not in ('maintenance', 'off_duty') and (
                unit.status == 'available'
                or (unit.available_at is not None and utc(unit.available_at) <= utc(sim_time))
            )
            locally_busy = unit.unit_id in busy_units
            available += int(board_free and not locally_busy)
            assigned += int((unit.status == 'assigned' and not board_free) or locally_busy)
            if board_free and locally_busy:
                issues.append({'kind': 'local_assignment_conflict', 'board_unit_id': unit.board_unit_id})
        missing = [unit.board_unit_id for unit in terminal_units if unit.board_missing_at is not None]
        if missing:
            issues.append({'kind': 'units_missing_from_snapshot', 'board_unit_ids': sorted(missing)})
        local_counts = CapacityCounts(
            total_units=len(current_units), available_units=available, assigned_units=assigned,
        )
        board_counts = None
        if snapshot is None:
            issues.append({'kind': 'terminal_missing_from_snapshot'})
        else:
            board_counts = CapacityCounts(
                total_units=snapshot.total_units, available_units=snapshot.available_units,
                assigned_units=snapshot.assigned_units,
            )
            for metric, local_value in local_counts.model_dump().items():
                board_value = getattr(board_counts, metric)
                if local_value != board_value:
                    issues.append({
                        'kind': 'capacity_mismatch', 'metric': metric,
                        'local': local_value, 'board': board_value,
                    })
            misplaced = [
                unit.id for unit in snapshot.units
                if unit.home_terminal_id != snapshot.terminal.id
            ]
            if misplaced:
                issues.append({'kind': 'roster_terminal_mismatch', 'board_unit_ids': sorted(misplaced)})
        report = FleetCapacityResponse(
            terminal_code=terminal.code, board=board_counts, local=local_counts,
            consistent=not issues, discrepancies=issues,
            as_of_sim_time=utc(sim_time), checked_at=utc(synced_at),
        )
        previous = session.get(FleetCapacity, terminal.terminal_id)
        if issues and (previous is None or previous.discrepancies != issues):
            add_activity(session, 'fleet.discrepancy', f'Fleet discrepancy at {terminal.code}',
                         details={'terminal_code': terminal.code, 'discrepancies': issues})
        elif previous is not None and previous.discrepancies and not issues:
            add_activity(session, 'fleet.reconciled', f'Fleet discrepancy resolved at {terminal.code}',
                         details={'terminal_code': terminal.code})
        session.merge(FleetCapacity(
            terminal_id=terminal.terminal_id,
            board_total_units=board_counts.total_units if board_counts else None,
            board_available_units=board_counts.available_units if board_counts else None,
            board_assigned_units=board_counts.assigned_units if board_counts else None,
            local_total_units=local_counts.total_units,
            local_available_units=local_counts.available_units,
            local_assigned_units=local_counts.assigned_units,
            discrepancies=issues, as_of_sim_time=sim_time, checked_at=synced_at,
        ))
        reports.append(report)
    return reports


def capacity_reports(session: Session, discrepancies_only: bool = False) -> list[FleetCapacityResponse]:
    rows = session.execute(
        select(FleetCapacity, Terminal.code).join(Terminal).order_by(Terminal.code),
    ).all()
    return [
        FleetCapacityResponse(
            terminal_code=code,
            board=CapacityCounts(
                total_units=row.board_total_units, available_units=row.board_available_units,
                assigned_units=row.board_assigned_units,
            ) if row.board_total_units is not None else None,
            local=CapacityCounts(
                total_units=row.local_total_units, available_units=row.local_available_units,
                assigned_units=row.local_assigned_units,
            ),
            consistent=not row.discrepancies, discrepancies=row.discrepancies,
            as_of_sim_time=utc(row.as_of_sim_time), checked_at=utc(row.checked_at),
        )
        for row, code in rows if not discrepancies_only or row.discrepancies
    ]


def log_discrepancies(reports: list[FleetCapacityResponse]) -> None:
    for report in reports:
        if not report.consistent:
            logger.warning('Fleet capacity discrepancy at %s: %s', report.terminal_code, report.discrepancies)
