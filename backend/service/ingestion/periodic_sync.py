import asyncio
import logging
import os

from dotenv import dotenv_values

from schema.ingestion.sync_settings import SyncSettings
from service.audit.activity import record_system_event
from service.shared.database import PROJECT_ROOT, get_engine
from service.ingestion.history_import import ensure_history_file, sync_history_if_changed
from service.ingestion.ingest import ingest

logger = logging.getLogger('uvicorn.error.sync')


def sync_settings():
    config = dotenv_values(PROJECT_ROOT / '.env')
    return SyncSettings(
        interval_seconds=os.environ.get('BOARD_SYNC_INTERVAL_SECONDS', config.get('BOARD_SYNC_INTERVAL_SECONDS', '30')),
        websocket_enabled=os.environ.get('BOARD_WEBSOCKET_ENABLED', config.get('BOARD_WEBSOCKET_ENABLED', 'true')),
    )


def _sync_history():
    """Refresh the history file if it is missing, then import any change."""
    ensure_history_file()
    return sync_history_if_changed()


async def periodic_sync(base_url, interval_seconds):
    """REST is authoritative even if the notification channel stays silent."""
    while True:
        # Startup already synced. Delay before each pass and never overlap passes.
        await asyncio.sleep(interval_seconds)
        for name, operation in (
            ('board', lambda: ingest(base_url, full_reconcile=True)) if base_url else ('board', None),
            ('history', lambda: asyncio.to_thread(_sync_history)),
        ):
            if operation is None:
                continue
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception('%s synchronization failed; retrying next interval', name)
                try:
                    await asyncio.to_thread(record_system_event, get_engine(), f'sync.{name}.error', type(exc).__name__)
                except Exception:
                    logger.exception('Could not persist synchronization failure')
