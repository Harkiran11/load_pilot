"""Import all dispatcher models so Alembic sees the complete metadata."""

from schema.shared.base import Base
from schema.terminal.terminal import Terminal
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.dispatch.evaluation import Evaluation
from schema.dispatch.dispatch_action import DispatchAction
from schema.dispatch.assignment import Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.analytics.historical_shipment import HistoricalShipment
from schema.simulation.simulation_clock import SimulationClock
from schema.fleet.fleet_capacity import FleetCapacity
from schema.audit.activity import Activity
from schema.autopilot.autopilot_run import AutopilotRun
from schema.allocation.allocation_plan import AllocationPlan, AllocationReservation
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.analytics.observed_cohort import ObservedCohort, ObservedComparison
from schema.audit.live_audit import LiveBatchAudit, LiveSubmissionAudit
from schema.audit.prediction_observation import PredictionObservation

__all__ = [
    'Base',
    'Terminal',
    'FleetUnit',
    'Shipment',
    'Evaluation',
    'DispatchAction',
    'Assignment',
    'AssignmentReservation',
    'HistoricalShipment',
    'SimulationClock',
    'FleetCapacity',
    'Activity',
    'AutopilotRun',
    'AutopilotAttempt',
    'AllocationPlan',
    'AllocationReservation',
    'ObservedCohort',
    'ObservedComparison',
    'LiveBatchAudit',
    'LiveSubmissionAudit',
    'PredictionObservation',
]
