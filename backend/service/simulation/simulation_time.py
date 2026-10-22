"""Simulation business timers; wall UTC remains transport/audit evidence."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import math

import anyio
from sqlalchemy import select

from schema.autopilot.autopilot_run import AutopilotRun
from schema.simulation.simulation_clock import SimulationClock
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc


def current_sim(session, wall=None):
    clock = session.get(SimulationClock, 1)
    if (clock is None or clock.last_synced_at is None or clock.sim_time_utc is None
            or not math.isfinite(float(clock.simulation_speed)) or clock.simulation_speed < 0):
        raise ProductError(503, 'Simulation clock is unavailable; restore board synchronization')
    return simulated_now(clock, wall)


def wall_estimate(session, deadline_sim, wall):
    clock = session.get(SimulationClock, 1)
    if clock is None or clock.simulation_speed <= 0 or deadline_sim is None:
        return None
    return utc(wall) + timedelta(seconds=(utc(deadline_sim) - current_sim(session, wall)).total_seconds()
                                 / float(clock.simulation_speed))


def remaining_seconds(session, run, wall):
    if run.status != 'running' or run.deadline_at_sim is None:
        return 0
    return max(0, (utc(run.deadline_at_sim) - current_sim(session, wall)).total_seconds())


def clock_problem(session, run, wall, max_age):
    clock = session.get(SimulationClock, 1)
    try:
        current_sim(session, wall)
    except ProductError:
        return 'simulation_clock_unavailable'
    if not -5 <= (utc(wall) - utc(clock.last_synced_at)).total_seconds() <= max_age:
        return 'simulation_clock_stale'
    if (run.last_clock_synced_at_wall is not None and utc(clock.last_synced_at) > utc(run.last_clock_synced_at_wall)
            and run.last_clock_sim_at is not None
            and utc(clock.sim_time_utc) < utc(run.last_clock_sim_at) - timedelta(seconds=1)):
        return 'simulation_clock_rewound'
    run.last_clock_sim_at = clock.sim_time_utc
    run.last_clock_synced_at_wall = clock.last_synced_at
    return None


def attempt_due(session, run, interval, wall):
    return (run.last_attempt_at_sim is None or
            (current_sim(session, wall) - utc(run.last_attempt_at_sim)).total_seconds() >= interval)


def enforce_running_clock(engine, run_id):
    from service.autopilot.autopilot import transaction
    from service.autopilot.autopilot_guards import check_run_limits
    from schema.autopilot.autopilot_attempt import AutopilotAttempt
    from schema.dispatch.dispatch_action import DispatchAction
    from schema.audit.live_audit import LiveSubmissionAudit
    with transaction(engine) as session:
        run = session.get(AutopilotRun, run_id)
        if run is None:
            return 'autopilot_run_missing', False
        reason = check_run_limits(session, run)
        # Once handed off, allow HTTP to establish its outcome. The stopped run
        # cannot authorize another action, and unknown capacity stays protected.
        handed_off = session.scalar(select(LiveSubmissionAudit.submission_id).join(AutopilotAttempt).join(
            DispatchAction, LiveSubmissionAudit.action_id == DispatchAction.action_id).where(
            AutopilotAttempt.run_id == run_id, LiveSubmissionAudit.submitted_at_wall.is_not(None),
            DispatchAction.request_status == 'submitting').limit(1)) is not None
        return reason, handed_off


@asynccontextmanager
async def guard_run_clock(engine, run_id):
    """Re-read the clock during I/O so speed changes never freeze an old timeout."""
    owner = asyncio.current_task()
    reason = None

    async def watch():
        nonlocal reason
        while True:
            try:
                stopped, handed_off = await asyncio.to_thread(enforce_running_clock, engine, run_id)
            except Exception:
                stopped, handed_off = 'simulation_clock_check_failed', False
            if stopped:
                if not handed_off:
                    reason = stopped
                    owner.cancel()
                return
            await asyncio.sleep(.05)

    watcher = asyncio.create_task(watch())
    try:
        yield
    except asyncio.CancelledError:
        if reason and owner.cancelling() == 1:
            owner.uncancel()
            raise ProductError(409, f'Autopilot stopped: {reason}') from None
        raise
    finally:
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(watcher, return_exceptions=True)
