"""Pre-inference categorical support checks against the saved training mappings."""

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import torch

from loadpilot_model.inference.errors import PredictionInputError
from loadpilot_model.inference.predictor import FreightPredictor


class PredictorSupportTests(unittest.TestCase):
    def setUp(self):
        self.record = {
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
        processor = Mock()
        processor.categorical_mappings = {
            name: [{"value": self.record[name]}]
            for name in ("lane_id", "origin_state", "destination_state")
        }
        processor.transform.return_value = np.zeros((1, 9), dtype=np.float32)
        self.loaded = SimpleNamespace(
            preprocessing=processor,
            model=Mock(return_value=torch.tensor([3.0])),
            metadata={"model_version": "synthetic-v1", "release_status": "unverified"},
        )
        self.reference = Mock()
        self.reference.describe.return_value = {
            "baseline_reference": {"predicted_bid_rpm": 2.9},
            "warnings": [],
            "support_policy_version": "pricing-support-v1",
            "uncertainty": {"status": "not_calibrated"},
        }
        with (
            patch("loadpilot_model.inference.predictor.load_model_bundle", return_value=self.loaded),
            patch("loadpilot_model.inference.predictor.PricingReferences", return_value=self.reference),
        ):
            self.predictor = FreightPredictor(Path("synthetic"))

    def test_unseen_categories_reject_before_transform_or_model(self):
        for name in ("lane_id", "origin_state", "destination_state"):
            with self.subTest(name=name), self.assertRaises(PredictionInputError) as raised:
                self.predictor.predict({**self.record, name: "UNSEEN"})
            self.assertEqual(raised.exception.code, "UNSEEN_CATEGORY")
            self.assertEqual(raised.exception.errors[0]["field"], name)
        self.loaded.preprocessing.transform.assert_not_called()
        self.loaded.model.assert_not_called()
        self.reference.describe.assert_not_called()

    def test_known_normalized_categories_produce_dollars_without_mutation(self):
        record = {**self.record, "lane_id": " atl-dfw "}
        before = copy.deepcopy(record)
        result = self.predictor.predict(record)
        self.assertEqual(result["predicted_bid_rpm"], 3)
        self.assertEqual(result["predicted_bid_dollars"], 300)
        self.assertEqual(record, before)
        self.assertEqual(result["baseline_reference"]["predicted_bid_rpm"], 2.9)

    def test_nonfinite_model_output_is_a_model_failure(self):
        self.loaded.model.return_value = torch.tensor([float("nan")])
        with self.assertRaises(RuntimeError):
            self.predictor.predict(self.record)
