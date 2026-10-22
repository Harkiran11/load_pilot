import asyncio
import json
import logging
import os
import time
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from schema.ingestion.board_event import BoardEvent
from service.shared.database import get_engine
from service.ingestion.ingest import ingest, persist_orders
from service.audit.activity import record_system_event
from service.system.system_state import connection_state

# Inherit Uvicorn's console handler so temporary order logs are visible locally.
logger = logging.getLogger('uvicorn.error.orders')


def websocket_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError('EXTERNAL_API_URL must be an absolute HTTP(S) base URL')
    scheme = 'wss' if parts.scheme == 'https' else 'ws'
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip('/') + '/orders/ws', '', ''))


def log_orders(event: str, orders) -> None:
    if os.getenv('LOG_LIVE_ORDERS', 'true').lower() not in ('0', 'false', 'no', 'off'):
        verbose = os.getenv('LOG_LIVE_ORDERS_VERBOSE', 'false').strip().lower() in (
            '1', 'true', 'yes', 'on',
        )
        fields = {
            'external_id', 'status', 'origin_longitude', 'origin_latitude',
            'destination_longitude', 'destination_latitude',
        }
        payloads = [
            order.model_dump(exclude_unset=True) if verbose else order.model_dump(include=fields)
            for order in orders[:5]
        ]
        logger.info(
            'Live orders event=%s count=%d sample=%s', event, len(orders),
            json.dumps(payloads, default=str),
        )


# Coalesce WebSocket-triggered snapshots: a 10x board can announce arrivals
# every few seconds, and each full snapshot holds the single SQLite writer.
# Event payloads still update their own order immediately; the periodic sync
# keeps the authoritative full picture.
_last_reconcile = 0.0
RECONCILE_MIN_INTERVAL_SECONDS = float(
    os.getenv('BOARD_RECONCILE_MIN_INTERVAL_SECONDS', '10'))


async def reconcile_board(base_url: str, *, minimum_interval: float = RECONCILE_MIN_INTERVAL_SECONDS) -> None:
    global _last_reconcile
    if time.monotonic() - _last_reconcile < minimum_interval:
        return
    await ingest(base_url)
    _last_reconcile = time.monotonic()


def _note_reconcile() -> None:
    global _last_reconcile
    _last_reconcile = time.monotonic()


async def handle_event(message: str | bytes, base_url: str) -> None:
    event = BoardEvent.model_validate(json.loads(message, parse_float=Decimal))
    orders = event.orders or ([event.order] if event.order is not None else [])
    if event.event == 'snapshot':
        # A full HTTP sync runs after connecting. The queued open-only snapshot
        # may be older, so log it without overwriting that authoritative sync.
        log_orders(event.event, orders)
        return
    if event.event == 'lifecycle':
        logger.info('Board lifecycle change: %s; scheduling refresh', event.stats)
        # The board sends counts rather than the changed order payloads. A
        # throttled refresh is enough: the periodic full sync still guarantees
        # eventual consistency, and per-order events update their own rows.
        await reconcile_board(base_url)
        return
    if event.order is not None or event.orders is not None:
        if event.event == 'order.created':
            # Reconcile before inserting an arrival: a newly seeded order must
            # not hide the old generation's all-missing/reset evidence.
            await reconcile_board(base_url)
        await asyncio.to_thread(
            persist_orders, get_engine(), orders, event.clock,
            created_only=event.event == 'order.created',
        )
        log_orders(event.event, orders)
        if event.event != 'order.created' and any(
            order.status in ('accepted', 'in_transit', 'completed') for order in orders
        ):
            # New arrivals also serve as reconciliation checkpoints if a
            # lifecycle message was missed while the socket stayed connected.
            await reconcile_board(base_url)
        return
    logger.warning('Ignoring unsupported board event: %s', event.event)


async def consume_orders(base_url: str) -> None:
    """Listen continuously; reconnect and reconcile missed changes after failures."""
    url = websocket_url(base_url)
    retry_delay = 1
    while True:
        try:
            connection_state.set('connecting')
            async with connect(
                url, open_timeout=10, close_timeout=5,
                ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024,
            ) as websocket:
                # Subscribe first: messages buffer while the fresh HTTP sync runs,
                # closing the gap between recovery and consuming new arrivals.
                await ingest(base_url, full_reconcile=True)
                _note_reconcile()
                retry_delay = 1
                connection_state.set('connected')
                logger.info('Order WebSocket connected; fresh board sync complete')
                async for message in websocket:
                    await handle_event(message, base_url)
            logger.warning('Order WebSocket closed; reconnecting in %ss', retry_delay)
            connection_state.set('reconnecting')
        except asyncio.CancelledError:
            connection_state.set('disconnected')
            raise
        except Exception as exc:
            connection_state.set('reconnecting', type(exc).__name__)
            logger.exception('Order stream failed; reconnecting in %ss', retry_delay)
            try:
                await asyncio.to_thread(record_system_event, get_engine(), 'stream.error', type(exc).__name__)
            except Exception:
                logger.exception('Could not record stream failure in activity history')
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)
