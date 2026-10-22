import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from schema.shared.dispatcher import Assignment, Base, Evaluation, FleetUnit, Shipment, Terminal
from service.shared.database import PROJECT_ROOT, create_database_engine, get_database_url


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        url = f'sqlite:///{Path(self.directory.name) / "dispatcher.db"}'
        self.environment = patch.dict(os.environ, {'DISPATCHER_DATABASE_URL': url})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = Config(str(PROJECT_ROOT / 'alembic.ini'))
        command.upgrade(self.config, 'head')
        self.engine = create_database_engine(url)
        self.addCleanup(self.engine.dispose)

    def test_migration_matches_guide_and_models(self):
        inspector = inspect(self.engine)
        guide = (PROJECT_ROOT / 'requirements/database-schema.md').read_text()
        entities = re.findall(r'    ([A-Z_]+) \{\n(.*?)    \}', guide, re.S)
        self.assertEqual(
            set(inspector.get_table_names()) - {'alembic_version'},
            {name.lower() for name, _ in entities},
        )
        for name, body in entities:
            table = name.lower()
            rows = [line.split() for line in body.strip().splitlines()]
            self.assertEqual(
                {column['name'] for column in inspector.get_columns(table)},
                {row[1] for row in rows},
            )
            unique_columns = {
                tuple(constraint['column_names'])
                for constraint in inspector.get_unique_constraints(table)
            }
            unique_columns.update(
                tuple(index['column_names'])
                for index in inspector.get_indexes(table) if index['unique']
            )
            foreign_keys = {
                tuple(fk['constrained_columns'])
                for fk in inspector.get_foreign_keys(table)
            }
            for _, field, *tags in rows:
                if 'UK' in ''.join(tags):
                    self.assertIn((field,), unique_columns)
                if 'FK' in ''.join(tags):
                    self.assertIn((field,), foreign_keys)
        with self.engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection), Base.metadata,
            )
        self.assertEqual(differences, [])

    def test_upgrade_is_repeatable_and_downgrade_can_be_reapplied(self):
        command.upgrade(self.config, 'head')
        command.downgrade(self.config, 'base')
        self.assertEqual(inspect(self.engine).get_table_names(), ['alembic_version'])
        command.upgrade(self.config, 'head')
        self.assertEqual(
            len(inspect(self.engine).get_table_names()),
            len(Base.metadata.tables) + 1,
        )

    def seed_assignment(self, session):
        terminal = Terminal(
            board_terminal_id=1, code='ATL', name='Atlanta', city='Atlanta',
            state='GA', seeded_unit_capacity=1,
        )
        shipment = Shipment(external_id='S-1', order_type='fixed_rate', status='open')
        session.add_all([terminal, shipment])
        session.flush()
        unit = FleetUnit(
            board_unit_id=1, home_terminal_id=terminal.terminal_id,
            unit_number='U-1', employee_code='E-1', first_name='Test',
            last_name='Driver', status='available',
        )
        session.add(unit)
        session.flush()
        now = datetime.now(timezone.utc)
        values = dict(
            shipment_id=shipment.shipment_id, unit_id=unit.unit_id,
            origin_terminal_id=terminal.terminal_id,
            destination_terminal_id=terminal.terminal_id,
            busy_from=now, busy_until=now + timedelta(hours=1), confirmed_at=now,
            assignment_status='confirmed',
        )
        session.add(Assignment(**values))
        session.commit()
        return values

    def test_assignment_is_unique_per_shipment(self):
        with Session(self.engine) as session:
            values = self.seed_assignment(session)
            session.add(Assignment(**values))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

    def test_foreign_keys_reject_unknown_units(self):
        with Session(self.engine) as session:
            values = self.seed_assignment(session)
            shipment = Shipment(external_id='S-2', order_type='open_bid', status='open')
            session.add(shipment)
            session.flush()
            values.update(shipment_id=shipment.shipment_id, unit_id=uuid4())
            session.add(Assignment(**values))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

    def test_evaluation_json_decimal_and_optional_candidate_round_trip(self):
        with Session(self.engine) as session:
            shipment = Shipment(external_id='S-1', order_type='fixed_rate', status='open')
            session.add(shipment)
            session.flush()
            now = datetime.now(timezone.utc)
            evaluation = Evaluation(
                shipment_id=shipment.shipment_id, payout=Decimal('1234.567891'),
                pricing_passed=True, capacity_passed=False, schedule_passed=False,
                weight_passed=True, location_passed=False, offer_open_unexpired=True,
                eligible=False, rejection_reasons=['no_capacity'],
                policy_snapshot={'minimum_margin': 0.15},
                evaluated_at_sim=now, evaluated_at_wall=now,
            )
            session.add(evaluation)
            session.commit()
            session.refresh(evaluation)
            self.assertIsNone(evaluation.candidate_unit_id)
            self.assertEqual(evaluation.payout, Decimal('1234.567891'))
            self.assertEqual(evaluation.rejection_reasons, ['no_capacity'])
            self.assertEqual(evaluation.policy_snapshot, {'minimum_margin': 0.15})


class DatabaseConfigurationTests(unittest.TestCase):
    def test_dotenv_url_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('DISPATCHER_DATABASE_URL=sqlite:///dotenv.db\n')
            with patch('service.shared.database.PROJECT_ROOT', root):
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(get_database_url(), 'sqlite:///dotenv.db')
                with patch.dict(os.environ, {'DISPATCHER_DATABASE_URL': 'sqlite:///env.db'}):
                    self.assertEqual(get_database_url(), 'sqlite:///env.db')

    def test_blank_url_uses_separate_project_database(self):
        with patch.dict(os.environ, {'DISPATCHER_DATABASE_URL': ''}):
            self.assertEqual(
                get_database_url(),
                f'sqlite:///{PROJECT_ROOT / "data" / "dispatcher.db"}',
            )
