"""Guardrail tests: weight ceiling, margin floor, and the simulation clock."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from schema.shared.dispatcher import Shipment, SimulationClock
from service.dispatch.dispatch_policy import dispatch_policy, evaluate_dispatch
from service.shared.errors import ProductError
from support import DatabaseTestCase

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


class DispatchPolicyConfigurationTests(unittest.TestCase):
    def test_defaults_match_the_case_requirements(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch('service.dispatch.dispatch_policy.dotenv_values', return_value={}):
            policy = dispatch_policy()
        self.assertEqual(policy.max_weight_lbs, 44000)
        self.assertEqual(policy.min_expected_margin_pct, Decimal('15'))
        self.assertTrue(policy.enforce_margin)

    def test_environment_overrides_tighten_the_policy(self):
        with patch.dict(os.environ, {'MAX_WEIGHT_LBS': '46000', 'MIN_EXPECTED_MARGIN_PCT': '20'}):
            policy = dispatch_policy()
        self.assertEqual(policy.max_weight_lbs, 46000)
        self.assertEqual(policy.min_expected_margin_pct, Decimal('20'))


class EvaluateDispatchGuardrailTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        wall_now = datetime.now(timezone.utc)
        self.session.add(SimulationClock(
            clock_id=1, wall_time_utc=wall_now, sim_time_utc=NOW,
            simulation_speed=Decimal('1'), last_synced_at=wall_now,
        ))
        self.session.commit()

    def shipment(self, weight_lbs=40000) -> Shipment:
        shipment = Shipment(
            external_id=f'S-{weight_lbs}', order_type='fixed_rate', status='open',
            weight_lbs=weight_lbs, expires_at=NOW + timedelta(hours=1),
            pickup_at=NOW + timedelta(hours=4), delivery_at=NOW + timedelta(hours=9),
        )
        self.session.add(shipment)
        self.session.commit()
        return shipment

    def test_overweight_order_is_refused(self):
        result = evaluate_dispatch(self.session, self.shipment(50000))
        self.assertFalse(result.checks['weight_passed'])
        self.assertIn('maximum_weight_exceeded', result.rejection_reasons)
        self.assertFalse(result.eligible)

    def test_weight_within_the_limit_passes_the_weight_check(self):
        result = evaluate_dispatch(self.session, self.shipment(40000))
        self.assertTrue(result.checks['weight_passed'])
        self.assertNotIn('maximum_weight_exceeded', result.rejection_reasons)

    def test_unknown_margin_never_passes_pricing_check(self):
        # Without a resolvable route and unit the economics are unknown, and an
        # unknown margin must not be treated as a passing margin.
        result = evaluate_dispatch(self.session, self.shipment(40000))
        self.assertFalse(result.checks['pricing_passed'])
        self.assertIn('expected_margin_unknown', result.rejection_reasons)

    def test_missing_simulation_clock_reports_service_error(self):
        self.session.delete(self.session.get(SimulationClock, 1))
        self.session.commit()
        with self.assertRaises(ProductError) as caught:
            evaluate_dispatch(self.session, self.shipment(40000))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('clock', caught.exception.detail)
