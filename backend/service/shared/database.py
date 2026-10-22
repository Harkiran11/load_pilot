import asyncio
import logging
import os
import sys
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CHECKPOINT_INTERVAL_SECONDS = 10.0
# Log a write transaction that held the single SQLite writer lock this long.
SLOW_TRANSACTION_SECONDS = 1.0


def get_database_url() -> str:
    """Read the dispatcher URL without changing the process environment."""
    config = dotenv_values(PROJECT_ROOT / '.env')
    url = os.environ.get('DISPATCHER_DATABASE_URL', config.get('DISPATCHER_DATABASE_URL'))
    return url or f'sqlite:///{PROJECT_ROOT / "data" / "dispatcher.db"}'


def create_database_engine(url: str) -> Engine:
    # WAL lets readers run in parallel, but each checkout still needs a pool
    # slot. A generous pool keeps bursts (several tabs, dashboards, the live
    # stream) from queueing 30 seconds against the single SQLite writer.
    engine = create_engine(
        url, pool_pre_ping=True, pool_size=20, max_overflow=40, pool_timeout=30,
    )
    if engine.dialect.name == 'sqlite':
        database_path = engine.url.database
        file_backed = bool(database_path) and database_path != ':memory:'
        if file_backed and not database_path.startswith('file:'):
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, 'connect')
        def configure_connection(connection, connection_record):
            # Board sync, expiration monitor, clock sync, and the autopilot
            # worker all write while HTTP handlers and WebSocket streams read.
            # Without WAL plus a busy timeout, SQLite answers a second writer
            # with "database is locked" instead of waiting its turn.
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=30000')
            if file_backed:
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=NORMAL')
                # Checkpoints run in the background task instead: under the
                # simulator's sustained write load a per-commit auto-checkpoint
                # of a large WAL held the writer lock for seconds at a time.
                cursor.execute('PRAGMA wal_autocheckpoint=0')
            cursor.close()

        @event.listens_for(engine, 'begin')
        def note_begin(connection):
            connection.info['tx_started_at'] = time.perf_counter()
            connection.info['tx_lock_wait'] = 0.0
            frames = []
            frame = sys._getframe(1)
            while frame is not None and len(frames) < 10:
                module = frame.f_globals.get('__name__', '')
                if not module.startswith(('sqlalchemy', 'sqlite3')):
                    frames.append(f'{module.split(".")[-1]}.{frame.f_code.co_name}')
                frame = frame.f_back
            connection.info['tx_stack'] = '>'.join(reversed(frames))

        @event.listens_for(engine, 'commit')
        def note_commit(connection):
            started = connection.info.pop('tx_started_at', None)
            if started is None:
                return
            elapsed = time.perf_counter() - started
            wait = connection.info.pop('tx_lock_wait', 0.0)
            if elapsed >= SLOW_TRANSACTION_SECONDS:
                # Every writer shares one SQLite lock; a slow transaction here
                # means the board sync, the clock sync or the expiration
                # monitor is starving the others.
                logger.warning('Slow write transaction: total=%.2fs lock_wait=%.2fs held=%.2fs | %s',
                               elapsed, wait, elapsed - wait,
                               connection.info.pop('tx_stack', '?'))

        @event.listens_for(engine, 'before_cursor_execute')
        def note_statement_start(conn, cursor, statement, parameters, context, executemany):
            conn.info['stmt_started_at'] = time.perf_counter()
            conn.info['stmt_text'] = str(statement)[:64]

        @event.listens_for(engine, 'after_cursor_execute')
        def note_statement_end(conn, cursor, statement, parameters, context, executemany):
            started = conn.info.pop('stmt_started_at', None)
            if started is None:
                return
            if 'BEGIN IMMEDIATE' in (conn.info.get('stmt_text') or ''):
                # How long this writer waited for the single SQLite write lock.
                conn.info['tx_lock_wait'] = time.perf_counter() - started
    return engine


def checkpoint_wal_once():
    """Copy WAL pages back to the database file.

    A PASSIVE checkpoint is best-effort: it never blocks a reader or a writer,
    so the background task can run while the board stream and the dashboards
    are reading. RESTART and TRUNCATE would both wait for the last reader to
    finish *while holding the write lock*, which turned any long read into a
    stall for every writer.

    The WAL file is reused rather than truncated, so its size is a high-water
    mark instead of unbounded growth.
    """
    engine = get_engine()
    if engine.dialect.name != 'sqlite':
        return None
    database = engine.url.database
    if not database or database == ':memory:':
        return None
    with engine.connect() as connection:
        return connection.exec_driver_sql('PRAGMA wal_checkpoint(PASSIVE)').fetchall()


async def checkpoint_wal() -> None:
    """Periodic PASSIVE WAL checkpoint.

    The dispatcher writes continuously while dashboards and the live load
    stream keep reading, so the WAL never drains on its own. A regular
    automatic checkpoint inside commits made a trivial write take seconds;
    this task keeps the WAL small without touching the commit path.
    """
    while True:
        try:
            await asyncio.to_thread(checkpoint_wal_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('WAL checkpoint failed; retrying')
        await asyncio.sleep(CHECKPOINT_INTERVAL_SECONDS)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_database_engine(get_database_url())


def is_locked_error(error: BaseException) -> bool:
    """True when SQLite refused a write because another writer holds the lock."""
    return isinstance(error, OperationalError) and 'database is locked' in str(error)


def write_retry_attempts() -> int:
    """Bounded retries around a contended write.

    ``busy_timeout`` already makes SQLite wait, but a checkpoint or a long
    reader can still outlast it. Retrying a few times turns a transient lock
    into a short delay instead of a 500 that leaves the caller unsure whether
    the write happened.
    """
    try:
        return max(1, int(os.environ.get('SQLITE_WRITE_RETRIES', '5')))
    except ValueError:
        return 5


def get_session() -> Iterator[Session]:
    """Use with FastAPI Depends; callers explicitly commit writes."""
    with Session(get_engine()) as session:
        yield session
