"""Temporal lock tests: the windows and reservations that prevent double-booking."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from schema.dispatch.scheduling import ScheduleConflict
from schema.shared.dispatcher import AssignmentReservation, DispatchAction
from service.dispatch.scheduling import reservation_conflicts, schedule_conflicts, unit_schedule_rejections
from support import DatabaseTestCase, add_assignment, seed_terminal_unit_shipment

BASE = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


class ScheduleConflictTests(DatabaseTestCase):
    """The unit is busy whenever an active assignment's occupied window overlaps."""

    def setUp(self):
        super().setUp()
        self.terminal, self.shipment, self.unit = seed_terminal_unit_shipment(self.session)

    def conflicts(self, pickup_at, delivery_at, unit_id=None):
        target = unit_id or self.unit.unit_id
        return schedule_conflicts(self.session, [target], pickup_at, delivery_at)[target]

    def test_overlapping_window_reports_the_active_lock(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        found = self.conflicts(BASE + timedelta(hours=1), BASE + timedelta(hours=2))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].assignment_status, 'confirmed')
        self.assertEqual(found[0].busy_from, BASE)
        self.assertEqual(found[0].busy_until, BASE + timedelta(hours=4))
        self.assertEqual(found[0].shipment_id, self.shipment.shipment_id)

    def test_partially_overlapping_window_is_blocked(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        found = self.conflicts(BASE + timedelta(hours=3), BASE + timedelta(hours=5))
        self.assertEqual(len(found), 1)

    def test_window_ending_exactly_at_lock_start_is_free(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        self.assertEqual(self.conflicts(BASE - timedelta(hours=2), BASE), [])

    def test_window_starting_exactly_at_lock_end_is_free(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        self.assertEqual(self.conflicts(BASE + timedelta(hours=4), BASE + timedelta(hours=6)), [])

    def test_window_after_the_lock_is_free(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        self.assertEqual(self.conflicts(BASE + timedelta(hours=5), BASE + timedelta(hours=7)), [])

    def test_other_units_are_unaffected(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
        )
        other_unit = uuid4()
        self.assertEqual(self.conflicts(BASE, BASE + timedelta(hours=1), unit_id=other_unit), [])

    def test_no_units_returns_an_empty_mapping(self):
        self.assertEqual(schedule_conflicts(self.session, [], BASE, BASE + timedelta(hours=1)), {})

    def test_retired_lock_no_longer_blocks(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=4),
            lock_retired_at=BASE + timedelta(hours=1),
        )
        self.assertEqual(self.conflicts(BASE + timedelta(hours=2), BASE + timedelta(hours=3)), [])

    def test_completed_assignment_still_blocks_its_retained_tail(self):
        # A completed load whose occupied window runs past busy_until keeps the
        # tail locked (the "missing order" retention path).
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=2), status='completed',
            occupied_from=BASE, occupied_until=BASE + timedelta(hours=3),
        )
        found = self.conflicts(BASE + timedelta(hours=2), BASE + timedelta(hours=2, minutes=30))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].assignment_status, 'completed')

    def test_completed_assignment_without_extension_is_released(self):
        add_assignment(
            self.session, self.terminal, self.shipment, self.unit,
            busy_from=BASE, busy_until=BASE + timedelta(hours=2), status='completed',
        )
        self.assertEqual(self.conflicts(BASE + timedelta(hours=2), BASE + timedelta(hours=3)), [])


class ReservationConflictTests(DatabaseTestCase):
    """Reserve-then-submit: an in-flight dispatch action holds the unit."""

    def setUp(self):
        super().setUp()
        self.terminal, self.shipment, self.unit = seed_terminal_unit_shipment(self.session)
        action = DispatchAction(
            shipment_id=self.shipment.shipment_id, action_type='accept',
            request_status='submitting', requested_at=BASE,
        )
        self.session.add(action)
        self.session.flush()
        self.action = action
        self.session.add(AssignmentReservation(
            action_id=action.action_id, unit_id=self.unit.unit_id,
            busy_from=BASE, busy_until=BASE + timedelta(hours=2), created_at=BASE,
        ))
        self.session.commit()

    def conflicts(self, pickup_at, delivery_at):
        return reservation_conflicts(self.session, [self.unit.unit_id], pickup_at, delivery_at)[self.unit.unit_id]

    def test_overlapping_window_reports_the_reservation(self):
        found = self.conflicts(BASE + timedelta(minutes=30), BASE + timedelta(hours=1))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].action_id, self.action.action_id)
        self.assertEqual(found[0].request_status, 'submitting')
        self.assertEqual(found[0].busy_from, BASE)
        self.assertEqual(found[0].busy_until, BASE + timedelta(hours=2))

    def test_window_after_the_reservation_is_free(self):
        self.assertEqual(self.conflicts(BASE + timedelta(hours=2), BASE + timedelta(hours=3)), [])


class UnitScheduleRejectionTests(unittest.TestCase):
    """Per-unit schedule reasons derived without touching the database."""

    @staticmethod
    def unit(status='available', available_at=None):
        return SimpleNamespace(status=status, available_at=available_at)

    @staticmethod
    def conflict():
        return ScheduleConflict(
            assignment_id=uuid4(), shipment_id=uuid4(),
            busy_from=BASE, busy_until=BASE + timedelta(hours=2), assignment_status='confirmed',
        )

    def test_available_unit_without_conflicts_is_accepted(self):
        self.assertEqual(unit_schedule_rejections(self.unit(), BASE, []), [])

    def test_maintenance_unit_is_rejected(self):
        reasons = unit_schedule_rejections(self.unit('maintenance', BASE), BASE, [])
        self.assertEqual(reasons, ['unit_status_unavailable'])

    def test_unit_available_only_after_pickup_is_rejected(self):
        reasons = unit_schedule_rejections(
            self.unit('assigned', BASE + timedelta(hours=2)), BASE, [],
        )
        self.assertEqual(reasons, ['unit_available_after_pickup'])

    def test_assigned_unit_without_known_availability_is_rejected(self):
        reasons = unit_schedule_rejections(self.unit('assigned'), BASE, [])
        self.assertEqual(reasons, ['unit_availability_unknown'])

    def test_overlapping_assignment_is_rejected(self):
        reasons = unit_schedule_rejections(self.unit(), BASE, [self.conflict()])
        self.assertEqual(reasons, ['assignment_window_overlap'])
