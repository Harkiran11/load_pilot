import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from schema.fleet.fleet_capacity import FleetCapacity
from schema.terminal.terminal import Terminal
from schema.terminal.terminal_status import TerminalStatus, TerminalStatusMessage
from service.orders.expiration import utc

logger = logging.getLogger('uvicorn.error.terminals')
CHECK_INTERVAL_SECONDS = 0.5
HEARTBEAT_SECONDS = 30
SEND_TIMEOUT_SECONDS = 10


def terminal_statuses(session: Session) -> list[TerminalStatus]:
    rows = session.execute(
        select(Terminal, FleetCapacity).outerjoin(FleetCapacity).order_by(Terminal.code),
    ).all()
    items = []
    for terminal, capacity in rows:
        status = 'unknown'
        if capacity is not None and capacity.board_total_units is not None:
            if capacity.discrepancies:
                status = 'attention'
            elif capacity.local_total_units == 0:
                status = 'empty'
            elif capacity.local_available_units > 0:
                status = 'available'
            else:
                status = 'unavailable'
        last_synced_at = capacity.checked_at if capacity else terminal.last_synced_at
        items.append(TerminalStatus(
            terminal_id=terminal.terminal_id, code=terminal.code, name=terminal.name,
            city=terminal.city, state=terminal.state,
            latitude=terminal.latitude, longitude=terminal.longitude, status=status,
            total_units=capacity.local_total_units if capacity else None,
            available_units=capacity.local_available_units if capacity else None,
            assigned_units=capacity.local_assigned_units if capacity else None,
            board_total_units=capacity.board_total_units if capacity else None,
            board_available_units=capacity.board_available_units if capacity else None,
            board_assigned_units=capacity.board_assigned_units if capacity else None,
            utilization_pct=(Decimal(capacity.local_assigned_units) * 100 / capacity.local_total_units).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP,
            ) if capacity and capacity.board_total_units is not None and capacity.local_total_units > 0 else None,
            available_pct=(Decimal(capacity.local_available_units) * 100 / capacity.local_total_units).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP,
            ) if capacity and capacity.board_total_units is not None and capacity.local_total_units > 0 else None,
            consistent=not capacity.discrepancies if capacity else None,
            discrepancies=capacity.discrepancies if capacity else [],
            as_of_sim_time=utc(capacity.as_of_sim_time) if capacity else None,
            last_synced_at=utc(last_synced_at) if last_synced_at else None,
        ))
    return items


def read_statuses(engine: Engine) -> list[TerminalStatus]:
    # Each read gets a short transaction, also seeing commits from other workers.
    with Session(engine) as session:
        return terminal_statuses(session)


def status_fingerprint(items: list[TerminalStatus]) -> str:
    return json.dumps([
        item.model_dump(mode='json', exclude={'as_of_sim_time', 'last_synced_at'})
        for item in items
    ], sort_keys=True)


async def stream_terminal_statuses(websocket: WebSocket, engine: Engine) -> None:
    await websocket.accept()

    async def send_updates():
        previous = None
        revision = 0
        last_sent = 0
        loop = asyncio.get_running_loop()
        while True:
            items = await asyncio.to_thread(read_statuses, engine)
            fingerprint = status_fingerprint(items)
            now = loop.time()
            event = None
            if previous is None:
                event = 'terminals.snapshot'
            elif fingerprint != previous:
                event = 'terminals.updated'
            elif now - last_sent >= HEARTBEAT_SECONDS:
                event = 'terminals.heartbeat'
            if event:
                revision += 1
                message = TerminalStatusMessage(
                    event=event, revision=revision, sent_at=datetime.now(timezone.utc), items=items,
                )
                await asyncio.wait_for(
                    websocket.send_json(message.model_dump(mode='json')),
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                previous = fingerprint
                last_sent = loop.time()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def receive_disconnect():
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                return

    tasks = [asyncio.create_task(send_updates()), asyncio.create_task(receive_disconnect())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, OSError):
        pass
    except Exception:
        logger.exception('Terminal status stream failed')
        with suppress(Exception):
            await asyncio.wait_for(websocket.send_json({
                'event': 'terminals.error', 'message': 'Terminal status is temporarily unavailable',
            }), timeout=SEND_TIMEOUT_SECONDS)
            await websocket.close(code=1011)
    finally:
        for task in tasks:
            task.cancel()
        # Finish cleanup even when the ASGI server cancels the connection scope.
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
