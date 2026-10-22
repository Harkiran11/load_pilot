"""Advisory price inference, separate from margins and bid submission."""

import math
import threading
from pathlib import Path

import torch

from loadpilot_model.features.schema import CATEGORICAL_FEATURES, build_features
from loadpilot_model.inference.errors import PredictionInputError
from loadpilot_model.inference.model_bundle import load_model_bundle
from loadpilot_model.inference.references import PricingReferences


class FreightPredictor:
    def __init__(self, bundle: Path):
        self.loaded = load_model_bundle(bundle)
        self.references = PricingReferences(bundle, self.loaded.metadata)
        self._lock = threading.Lock()

    def predict(self, shipment: dict) -> dict:
        try:
            features = build_features(shipment)
        except ValueError as error:
            raise PredictionInputError("INVALID_MODEL_INPUT", []) from error
        unknown = [
            {
                "field": name,
                "code": "UNSEEN_CATEGORY",
                "message": "Category was not present in training; review is required.",
            }
            for name in CATEGORICAL_FEATURES
            if features[name]
            not in {
                entry["value"] for entry in self.loaded.preprocessing.categorical_mappings[name]
            }
        ]
        if unknown:
            raise PredictionInputError("UNSEEN_CATEGORY", unknown)
        encoded = self.loaded.preprocessing.transform([features])
        with self._lock, torch.inference_mode():
            rpm = self.loaded.model(torch.from_numpy(encoded)).item()
        total = rpm * float(features["distance_miles"])
        if not math.isfinite(rpm) or rpm <= 0 or not math.isfinite(total) or total <= 0:
            raise RuntimeError("Prediction must be finite and positive in RPM and total dollars")
        return {
            "api_version": "v1",
            "model_version": self.loaded.metadata["model_version"],
            "predicted_bid_rpm": rpm,
            "predicted_bid_dollars": total,
            "currency": "USD",
            "distance_basis": "loaded_miles",
            "advisory_only": True,
            "release_status": self.loaded.metadata["release_status"],
            **self.references.describe(features),
        }
