"""Read-only run/decision subscriptions; reconnect always returns current state."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from schema.allocation.allocation_plan import AllocationPlan
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.autopilot.autopilot_run import AutopilotRun
from service.autopilot.autopilot import run_view
from service.shared.errors import ProductError
from service.audit.live_audit import json_value

CHECK_INTERVAL_SECONDS = 0.5
HEARTBEAT_SECONDS = 30
SEND_TIMEOUT_SECONDS = 10


def read_state(engine, run_id, limit):
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        run = session.get(AutopilotRun, run_id)
        if run is None:
            raise ProductError(404, 'Autopilot run not found')
        query = select(AutopilotAttempt).where(AutopilotAttempt.run_id == run_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        attempts = []
        for row in session.scalars(query.order_by(AutopilotAttempt.created_at_wall.desc(), AutopilotAttempt.attempt_id).limit(limit)):
            model = row.model_snapshot or {}
            attempts.append({**{name: getattr(row, name) for name in ('attempt_id', 'external_id', 'order_type', 'status',
                'decision', 'action_id', 'candidate_unit_id', 'bid_rate', 'reason_codes', 'message', 'created_at_wall',
                'decided_at_wall', 'finished_at_wall', 'allocation_plan_id')},
                'economics': row.evaluation_snapshot.get('economics'), 'comparison': model.get('comparison'),
                'model_decision': model.get('response'), 'audit_id': model.get('audit_id'),
                'observation_id': model.get('observation_id')})
        plans = []
        for row in session.scalars(select(AllocationPlan).where(AllocationPlan.run_id == run_id).order_by(
                AllocationPlan.created_at_wall.desc(), AllocationPlan.plan_id).limit(limit)):
            analysis = row.result.get('batch_analysis', {})
            response = analysis.get('response', {})
            plans.append(dict(plan_id=row.plan_id, status=row.status, created_at_wall=row.created_at_wall,
                selected=[{key: item.get(key) for key in ('external_id', 'unit_id', 'order_type', 'bid_rate', 'submission_position')}
                    for item in row.result.get('selected', [])],
                audit_id=analysis.get('audit_id'), decision=response.get('decision'),
                reason_codes=response.get('reason_codes', []), model_error=analysis.get('error'),
                scope=analysis.get('scope'), projected_profit=row.result.get('projected_profit')))
        state = dict(run=run_view(session, run).model_dump(mode='json'), attempts=attempts,
            allocations=plans, attempt_total=total, limit=limit, attempts_truncated=total > limit,
            detail_urls={'attempts': f'/api/autopilot/runs/{run_id}/attempts',
                         'allocations': f'/api/autopilot/runs/{run_id}/allocations'})
        return json.loads(json.dumps(state, default=json_value, allow_nan=False))


async def stream_run(websocket: WebSocket, engine, run_id, limit):
    await websocket.accept()

    async def send_updates():
        previous, revision, last_sent = None, 0, 0
        loop = asyncio.get_running_loop()
        while True:
            state = await asyncio.to_thread(read_state, engine, run_id, limit)
            stable = {**state, 'run': {key: value for key, value in state['run'].items() if key != 'seconds_remaining'}}
            current = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
            event = ('autopilot.snapshot' if previous is None else 'autopilot.updated' if current != previous
                     else 'autopilot.heartbeat' if loop.time() - last_sent >= HEARTBEAT_SECONDS else None)
            if event:
                revision += 1
                await asyncio.wait_for(websocket.send_json(dict(event=event, revision=revision,
                    sent_at=datetime.now(timezone.utc).isoformat(), **state)), SEND_TIMEOUT_SECONDS)
                previous, last_sent = current, loop.time()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def disconnected():
        while True:
            if (await websocket.receive())['type'] == 'websocket.disconnect':
                return

    tasks = [asyncio.create_task(send_updates()), asyncio.create_task(disconnected())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, OSError):
        pass
    except Exception as exc:
        with suppress(Exception):
            await asyncio.wait_for(websocket.send_json(dict(event='autopilot.error',
                message=exc.detail if isinstance(exc, ProductError) else 'Run status is temporarily unavailable')), SEND_TIMEOUT_SECONDS)
            await websocket.close(code=1008 if isinstance(exc, ProductError) else 1011)
    finally:
        for task in tasks:
            task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
