import csv
import hashlib
import io
import logging
import os
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from schema.audit.activity import Activity
from schema.analytics.historical_shipment import HistoricalShipment
from schema.ingestion.history_import import HistoricalCsvRow
from service.audit.activity import add_activity
from service.shared.database import PROJECT_ROOT, get_engine
from service.ingestion.ingest import upsert

logger = logging.getLogger('uvicorn.error.history')


def configured_history_path():
    config = dotenv_values(PROJECT_ROOT / '.env')
    value = os.environ.get('HISTORY_CSV_PATH', config.get('HISTORY_CSV_PATH', ''))
    if not value or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def history_source_url():
    """Where the board publishes its history, if it is reachable."""
    base = os.environ.get('EXTERNAL_API_URL', '').strip()
    return f'{base.rstrip("/")}/history/shipments.csv' if base else None


def ensure_history_file(path=None):
    """Make sure the configured history file exists, fetching it once if needed.

    The board serves its history as CSV. Downloading it on first start means a
    fresh checkout has the dataset without a manual step, and the import stays
    local and atomic afterwards.
    """
    target = Path(path) if path is not None else configured_history_path()
    if target is None:
        return None
    if target.exists() and target.stat().st_size:
        return target
    url = history_source_url()
    if not url:
        logger.warning('HISTORY_CSV_PATH is set but the file is missing and no board URL is configured.')
        return None
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except Exception as exc:
        logger.warning('Historical dataset could not be fetched from %s: %s', url, exc)
        return None
    if not data:
        logger.warning('Historical dataset at %s was empty.', url)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target and rename, so a reader never sees a partial file.
    temporary = target.with_suffix(target.suffix + '.part')
    temporary.write_bytes(data)
    temporary.replace(target)
    logger.info('Fetched %d bytes of history from %s', len(data), url)
    return target


def sync_history_if_changed(engine=None, path=None):
    """Import the configured CSV atomically; unchanged bytes require no row writes."""
    path = Path(path) if path is not None else configured_history_path()
    if path is None:
        return {'enabled': False, 'imported': False, 'rows': 0}
    if not path.exists():
        # A missing dataset is not a startup failure: the dispatcher works
        # without history and the advisory context says so.
        logger.warning('Historical CSV %s is missing; continuing without history.', path)
        return {'enabled': False, 'imported': False, 'rows': 0, 'missing': True}
    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    engine = engine if engine is not None else get_engine()
    with Session(engine) as session, session.begin():
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        previous = session.scalar(select(Activity).where(Activity.event_type == 'history.imported').order_by(
            Activity.occurred_at_wall.desc(), Activity.event_id.desc(),
        ))
        if previous and previous.details.get('sha256') == checksum:
            # Unchanged bytes need no row writes, unless the imported rows were
            # removed after the import.
            stored = session.scalar(select(func.count()).select_from(HistoricalShipment))
            if stored == previous.details.get('rows'):
                return {'enabled': True, 'imported': False, 'rows': previous.details['rows'], 'sha256': checksum}
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
        required = {field.alias or name for name, field in HistoricalCsvRow.model_fields.items() if field.is_required()}
        required.discard('source_record_key')
        required.add('shipment_id')
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('Historical CSV is missing required columns: ' + ', '.join(sorted(required - set(reader.fieldnames or []))))
        rows = []
        keys = set()
        for number, raw in enumerate(reader, 2):
            if None in raw:
                raise ValueError(f'Invalid historical CSV row {number}: too many columns')
            try:
                row = HistoricalCsvRow.model_validate({key: (None if value == '' else value) for key, value in raw.items()})
            except ValueError as exc:
                raise ValueError(f'Invalid historical CSV row {number}: {exc}') from exc
            key = 'csv:' + row.source_record_key
            if key in keys:
                raise ValueError(f'Duplicate historical shipment ID at row {number}')
            keys.add(key)
            rows.append(row.model_dump() | {'source_record_key': key})
        if not rows:
            raise ValueError('Historical CSV has no data rows; existing history was preserved')
        for row in rows:
            upsert(session, HistoricalShipment, 'source_record_key', row)
        # This CSV is an authoritative dataset; retain unrelated/manual sources.
        existing = session.scalars(select(HistoricalShipment.source_record_key).where(
            HistoricalShipment.source_record_key.like('csv:%'),
        )).all()
        removed = set(existing) - keys
        if removed:
            session.execute(delete(HistoricalShipment).where(HistoricalShipment.source_record_key.in_(removed)))
        add_activity(session, 'history.imported', f'Imported {len(rows)} historical shipments', details={
            'source': str(path), 'sha256': checksum, 'rows': len(rows), 'removed': len(removed),
        })
        return {'enabled': True, 'imported': True, 'rows': len(rows), 'sha256': checksum}
