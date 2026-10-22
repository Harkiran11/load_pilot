"""Shared helpers for tests that boot the FastAPI application."""
import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from schema.shared.dispatcher import Assignment, FleetUnit, Shipment, Terminal
from service.shared.database import PROJECT_ROOT, create_database_engine, get_engine


def use_migrated_test_database() -> None:
    """Point the app at a fresh throwaway database with all migrations applied.

    The lifespan workers query their tables during startup, so an empty or
    missing database would fail before the first request, and tests must never
    read or write the developer's local data.
    """
    directory = tempfile.mkdtemp(prefix='loadpilot-tests-')
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    os.environ['DISPATCHER_DATABASE_URL'] = f'sqlite:///{Path(directory) / "dispatcher.db"}'
    command.upgrade(Config(str(PROJECT_ROOT / 'alembic.ini')), 'head')
    get_engine.cache_clear()


class DatabaseTestCase(unittest.TestCase):
    """Every test runs against its own migrated throwaway database."""

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = f'sqlite:///{Path(directory.name) / "dispatcher.db"}'
        self.environment = patch.dict(os.environ, {'DISPATCHER_DATABASE_URL': url})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        command.upgrade(Config(str(PROJECT_ROOT / 'alembic.ini')), 'head')
        self.engine = create_database_engine(url)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)


def seed_terminal_unit_shipment(session) -> tuple[Terminal, Shipment, FleetUnit]:
    """Minimal terminal, unit, and shipment rows for service-level tests."""
    terminal = Terminal(
        board_terminal_id=1, code='ATL', name='Atlanta', city='Atlanta',
        state='GA', seeded_unit_capacity=1,
    )
    shipment = Shipment(external_id='S-1', order_type='fixed_rate', status='open')
    session.add_all([terminal, shipment])
    session.flush()
    unit = FleetUnit(
        board_unit_id=1, home_terminal_id=terminal.terminal_id, unit_number='U-1',
        employee_code='E-1', first_name='Test', last_name='Driver', status='available',
    )
    session.add(unit)
    session.commit()
    return terminal, shipment, unit


def add_assignment(session, terminal, shipment, unit, *, busy_from, busy_until,
                   status='confirmed', **overrides) -> Assignment:
    """Create a confirmed (or supplied status) assignment holding the unit's window."""
    assignment = Assignment(
        shipment_id=shipment.shipment_id, unit_id=unit.unit_id,
        origin_terminal_id=terminal.terminal_id, destination_terminal_id=terminal.terminal_id,
        busy_from=busy_from, busy_until=busy_until, confirmed_at=busy_from,
        assignment_status=status, **overrides,
    )
    session.add(assignment)
    session.commit()
    return assignment
