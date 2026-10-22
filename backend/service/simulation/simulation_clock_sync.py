"""Refresh clock settings independently of slower fleet/order reconciliation."""
import asyncio
from decimal import Decimal
import logging
import math
import os

from dotenv import dotenv_values
import httpx

from schema.ingestion.board_api import BoardClock
from service.shared.database import PROJECT_ROOT, get_engine
from service.ingestion.ingest import persist_clock

logger = logging.getLogger(__name__)


def clock_sync_interval():
    config = dotenv_values(PROJECT_ROOT / '.env')
    value = float(os.environ.get('SIMULATION_CLOCK_SYNC_INTERVAL_SECONDS',
                  config.get('SIMULATION_CLOCK_SYNC_INTERVAL_SECONDS', '1.0')))
    if not math.isfinite(value) or not .1 <= value <= 60:
        raise ValueError('SIMULATION_CLOCK_SYNC_INTERVAL_SECONDS must be .1–60 real seconds')
    return value


def make_clock_client(url):
    return httpx.AsyncClient(base_url=url.rstrip('/') + '/', timeout=5, follow_redirects=False)


async def refresh_clock(client, engine):
    response = await client.get('simulation/clock')
    response.raise_for_status()
    clock = BoardClock.model_validate(response.json(parse_float=Decimal))
    await asyncio.to_thread(persist_clock, engine, clock)


async def sync_simulation_clock(url):
    interval = clock_sync_interval()
    async with make_clock_client(url) as client:
        while True:
            await asyncio.sleep(interval)
            try:
                await refresh_clock(client, get_engine())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Simulation clock refresh failed; previous source timestamp is retained', exc_info=True)
