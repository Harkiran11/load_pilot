import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from service.ingestion.ingest import ingest as call_external_api
from service.ingestion.order_stream import consume_orders
from service.orders.expiration import monitor_expirations
from service.system.system_state import connection_state
from service.ingestion.periodic_sync import periodic_sync, sync_settings
from service.ingestion.history_import import ensure_history_file, sync_history_if_changed
from service.autopilot.autopilot import autopilot_worker, recover_runs
from service.shared.database import checkpoint_wal, get_engine
from service.simulation.simulation_clock_sync import sync_simulation_clock, clock_sync_interval

logger = logging.getLogger(__name__)
ENV_FILE = Path(__file__).resolve().parent / '.env'


def recover_autopilot_runs(reason='backend_restarted'):
    recover_runs(get_engine(), reason)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run initialization before the application accepts requests."""
    load_dotenv(ENV_FILE, override=False)
    external_api_url = os.getenv('EXTERNAL_API_URL', '').strip()

    settings = sync_settings()
    clock_sync_interval()
    # Restart/reload never silently resumes an automatically booking session.
    await asyncio.to_thread(recover_autopilot_runs)
    # A configured, invalid CSV fails startup rather than silently serving partial history.
    # A missing one is fetched from the board so a fresh checkout has the dataset.
    await asyncio.to_thread(ensure_history_file)
    await asyncio.to_thread(sync_history_if_changed)
    orders_task = None
    expiration_task = None
    sync_task = None
    clock_task = None
    if external_api_url:
        connection_state.set('connecting')
        await call_external_api(external_api_url)
        if settings.websocket_enabled:
            orders_task = asyncio.create_task(consume_orders(external_api_url), name='board-orders')
            app.state.orders_task = orders_task
        else:
            connection_state.set('disabled')
        expiration_task = asyncio.create_task(monitor_expirations(), name='offer-expirations')
        app.state.expiration_task = expiration_task
        clock_task = asyncio.create_task(sync_simulation_clock(external_api_url), name='simulation-clock')
        app.state.clock_task = clock_task
    else:
        connection_state.set('disabled')
        logger.info('EXTERNAL_API_URL is unset; skipping external API startup hook.')

    sync_task = asyncio.create_task(periodic_sync(external_api_url or None, settings.interval_seconds), name='board-sync')
    app.state.sync_task = sync_task
    autopilot_task = asyncio.create_task(autopilot_worker(), name='autopilot')
    app.state.autopilot_task = autopilot_task
    checkpoint_task = asyncio.create_task(checkpoint_wal(), name='wal-checkpoint')
    app.state.checkpoint_task = checkpoint_task
    try:
        yield
    finally:
        tasks = [task for task in (orders_task, expiration_task, sync_task, clock_task, autopilot_task, checkpoint_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(recover_autopilot_runs, 'backend_shutdown')
