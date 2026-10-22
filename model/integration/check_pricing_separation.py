"""Run with the backend interpreter/cwd; use only its isolated test database.

From the backend directory:
  .venv/bin/python -c "import runpy; runpy.run_path(
      '../project-model/model/integration/check_pricing_separation.py', run_name='__main__')"
"""

import copy
import os
import unittest
from contextlib import ExitStack
from datetime import timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from schema.fleet_unit import FleetUnit
from schema.simulation_clock import SimulationClock
from service.database import get_session
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from main import app
from tests import test_api


class PricingSeparationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_api.ProductApiTests(
            "test_all_read_endpoints_and_documented_paths_are_available"
        )
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        fixture.fixture.payloads["/orders"][0].update(order_type="open_bid", offered_rate=None)
        fixture.sync()
        self.engine = fixture.engine
        with Session(self.engine) as session:
            self.unit_id = str(session.scalar(select(FleetUnit.unit_id)))
            fixed_now = session.scalar(select(SimulationClock)).sim_time_utc.replace(
                tzinfo=timezone.utc
            )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in ("ranking", "dispatch_policy", "market_context", "unit_projection"):
            self.stack.enter_context(
                patch(f"service.{module}.simulated_now", return_value=fixed_now)
            )
        self.stack.enter_context(patch("startup.load_dotenv"))
        self.stack.enter_context(patch("startup.periodic_sync", new_callable=AsyncMock))
        self.stack.enter_context(
            patch.dict(os.environ, {"EXTERNAL_API_URL": "", "LOADPILOT_MODEL_BUNDLE": "synthetic"})
        )
        self.predictor = Mock()
        self.predictor.predict.return_value = {
            "api_version": "v1",
            "model_version": "synthetic-separation-v1",
            "predicted_bid_rpm": 3.0,
            "predicted_bid_dollars": 300.0,
            "currency": "USD",
            "distance_basis": "loaded_miles",
            "advisory_only": True,
            "release_status": "unverified",
            "baseline_reference": {
                "baseline_id": "lane_median_bid_rpm",
                "policy_version": "baseline-fallback-v1",
                "reference_source": "lane_median",
                "reason": "SUFFICIENT_TRAINING_SAMPLES",
                "predicted_bid_rpm": 2.9,
                "predicted_bid_dollars": 290.0,
                "lane_training_row_count": 10,
                "min_lane_samples": 5,
            },
            "warnings": [],
            "support_policy_version": "pricing-support-v1",
            "uncertainty": {
                "status": "not_calibrated",
                "confidence_level": None,
                "rpm_interval": None,
                "dollar_interval": None,
            },
        }
        self.factory = self.stack.enter_context(
            patch("loadpilot_model.inference.predictor.FreightPredictor", return_value=self.predictor)
        )
        self.shipment = {
            "distance_miles": 100.0,
            "weight_lbs": 10000.0,
            "origin_latitude": 33.0,
            "origin_longitude": -84.0,
            "destination_latitude": 33.0,
            "destination_longitude": -97.0,
            "lane_id": "ATL-DFW",
            "origin_state": "GA",
            "destination_state": "TX",
        }
        self.manual = {"unit_id": self.unit_id, "bid_rate": "1800.12"}

    def snapshot(self):
        with self.engine.connect() as connection:
            return {
                name: sorted(
                    repr(tuple(row))
                    for row in connection.execute(
                        text('SELECT * FROM "' + name.replace('"', '""') + '"')
                    )
                )
                for name in inspect(self.engine).get_table_names()
            }

    def preview(self, client):
        response = client.post("/api/loads/LIVE-100/cost-preview", json=self.manual)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(Decimal(body["entered_bid_rate"]), Decimal("1800.12"))
        self.assertIsNotNone(body["selected"], body)
        self.assertEqual(Decimal(body["selected"]["economics"]["payout"]), Decimal("1800.12"))
        return {
            "entered_bid_rate": body["entered_bid_rate"],
            "unit_id": body["selected"]["unit_id"],
            "deadhead_miles": body["selected"]["deadhead_miles"],
            "economics": body["selected"]["economics"],
        }

    def block_operational_calls(self, stack):
        mocks = []
        for name in (
            "controller.load_controller.execute_action",
            "service.actions.execute_action",
            "service.actions.begin_action",
            "service.actions.make_board_client",
            "service.ranking.expected_economics",
            "service.economics.expected_economics",
        ):
            mocks.append(
                stack.enter_context(
                    patch(
                        name,
                        side_effect=AssertionError("Prediction reached an operational dependency"),
                    )
                )
            )
        dependency = Mock(side_effect=AssertionError("Prediction requested a database session"))
        stack.enter_context(patch.dict(app.dependency_overrides, {get_session: dependency}))
        mocks.append(dependency)
        return mocks

    def test_successful_prediction_does_not_use_calculator_database_or_submission(self):
        before = self.snapshot()
        original = copy.deepcopy(self.shipment)
        with TestClient(app) as client, ExitStack() as guards:
            mocks = self.block_operational_calls(guards)
            response = client.post("/api/v1/predict-bid", json=self.shipment)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["advisory_only"])
            for mock in mocks:
                mock.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.shipment, original)
        self.assertEqual(self.fixture.posts, [])

    def test_changing_model_suggestion_does_not_change_entered_bid_or_margin(self):
        with TestClient(app) as client:
            before = self.preview(client)
            persisted = self.snapshot()
            first = client.post("/api/v1/predict-bid", json=self.shipment)
            self.assertEqual(first.status_code, 200, first.text)
            self.predictor.predict.return_value = {
                **self.predictor.predict.return_value,
                "predicted_bid_rpm": 30.0,
                "predicted_bid_dollars": 3000.0,
            }
            second = client.post("/api/v1/predict-bid", json=self.shipment)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertNotEqual(
                first.json()["predicted_bid_dollars"], second.json()["predicted_bid_dollars"]
            )
            self.assertEqual(self.preview(client), before)
            self.assertEqual(self.snapshot(), persisted)
        self.assertEqual(self.manual["bid_rate"], "1800.12")
        self.assertEqual(self.fixture.posts, [])

    def test_prediction_failure_and_unavailability_do_not_block_manual_calculation(self):
        with TestClient(app) as client:
            before = self.preview(client)
            persisted = self.snapshot()
            self.predictor.predict.side_effect = RuntimeError("synthetic model failure")
            with self.assertLogs("loadpilot_model.api.pricing", level="ERROR"):
                response = client.post("/api/v1/predict-bid", json=self.shipment)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.preview(client), before)
        self.factory.side_effect = ValueError("synthetic invalid package")
        with self.assertLogs("loadpilot_model.api.pricing", level="ERROR"), TestClient(app) as client:
            response = client.post("/api/v1/predict-bid", json=self.shipment)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.preview(client), before)
        self.assertEqual(self.snapshot(), persisted)
        self.assertEqual(self.fixture.posts, [])

    def test_pricing_rejects_submission_fields_without_reserving_or_posting(self):
        before = self.snapshot()
        with TestClient(app) as client, ExitStack() as guards:
            mocks = self.block_operational_calls(guards)
            for extra in (
                {"bid_rate": "1800.12"},
                {"unit_id": self.unit_id},
                {"submit": True},
                {"external_id": "LIVE-100"},
            ):
                response = client.post("/api/v1/predict-bid", json={**self.shipment, **extra})
                self.assertEqual(response.status_code, 422, response.text)
            for mock in mocks:
                mock.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.fixture.posts, [])


if __name__ == "__main__":
    unittest.main()
