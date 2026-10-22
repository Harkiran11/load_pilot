"""Independent verified baseline serving when model inference is unavailable."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from loadpilot_model.baselines.fallback import load_selected_fallback
from loadpilot_model.paths import PROJECT_ROOT


@dataclass(frozen=True)
class FallbackSettings:
    mode: str = "review"
    unseen_category_action: str = "review"
    model_blocked: bool = False

    def __post_init__(self):
        if self.mode not in {"review", "baseline"} or self.unseen_category_action not in {
            "review",
            "baseline",
        }:
            raise ValueError("Invalid fallback policy")
        if not isinstance(self.model_blocked, bool):
            raise ValueError("model_blocked must be boolean")

    @classmethod
    def from_environment(cls):
        blocked = os.getenv("LOADPILOT_PRICING_MODEL_BLOCKED", "false").strip().lower()
        if blocked not in {"true", "false"}:
            raise ValueError("Invalid model-block policy")
        return cls(
            os.getenv("LOADPILOT_PRICING_FALLBACK_MODE", "review").strip().lower(),
            os.getenv("LOADPILOT_PRICING_UNSEEN_CATEGORY_ACTION", "review").strip().lower(),
            blocked == "true",
        )


class BaselineFallback:
    """Verify baseline/schema provenance without loading torch or preprocessing pickle."""

    def __init__(self, bundle: Path):
        manifest = json.loads((bundle / "manifest.json").read_text())
        payload = {key: value for key, value in manifest.items() if key != "bundle_id"}
        identity = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if manifest["schema_version"] != 1 or manifest["bundle_id"] != identity:
            raise ValueError("Invalid fallback bundle identity")

        def verify(name):
            contents = (bundle / name).read_bytes()
            if hashlib.sha256(contents).hexdigest() != manifest["files_sha256"][name]:
                raise ValueError("Fallback artifact checksum mismatch")
            return json.loads(contents)

        metadata = verify("metadata.json")
        policy = verify("baselines/fallback-policy-v1.json")
        # Fixed file names prevent policy/manifest path traversal.
        if policy["baseline_artifact"] not in {"lane-median-v1.json", "global-median-v1.json"}:
            raise ValueError("Unsupported fallback artifact")
        verify("baselines/" + policy["baseline_artifact"])
        schema_manifest = verify("preprocessing/manifest.json")
        for name in ("loadpilot_model/features/schema.py", "loadpilot_model/features/preprocessing.py"):
            if (
                hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
                != schema_manifest["contract_code_sha256"][name]
            ):
                raise ValueError("Fallback input contract differs from trained contract")
        if (
            metadata["model_version"] != manifest["model_version"]
            or metadata["source_sha256"] != policy["source_sha256"]
            or metadata["split_manifest_sha256"] != policy["dataset_manifest_sha256"]
            or metadata["live_submission_enabled"] is not False
        ):
            raise ValueError("Fallback/model lineage mismatch")
        self.selected = load_selected_fallback(bundle / "baselines/fallback-policy-v1.json")

    def recommend(self, shipment: dict, trigger: str, settings: FallbackSettings) -> dict | None:
        result = self.selected.recommend(
            shipment,
            trigger=trigger,
            fallback_mode=settings.mode,
            unseen_category_action=settings.unseen_category_action,
        )
        if result["status"] != "candidate":
            return None
        rpm, dollars = float(result["predicted_bid_rpm"]), float(result["predicted_bid_dollars"])
        if not math.isfinite(rpm) or rpm <= 0 or not math.isfinite(dollars) or dollars <= 0:
            return None
        reference = {
            "baseline_id": result["baseline_id"],
            "policy_version": result["policy_version"],
            "reference_source": result["source"],
            "reason": result["reference_reason"],
            "predicted_bid_rpm": rpm,
            "predicted_bid_dollars": dollars,
            "lane_training_row_count": result["lane_training_row_count"],
            "min_lane_samples": self.selected.policy.get("min_lane_samples"),
        }
        return {
            "api_version": "v1",
            "model_version": None,
            "pricing_source": "baseline",
            "status": "fallback_advisory",
            "trigger": trigger,
            "predicted_bid_rpm": rpm,
            "predicted_bid_dollars": dollars,
            "currency": "USD",
            "distance_basis": "loaded_miles",
            "advisory_only": True,
            "submission_enabled": False,
            "requires_guardrail_checks": True,
            "requires_review": True,
            "baseline_reference": reference,
            "policy_sha256": result["policy_sha256"],
            "baseline_sha256": result["baseline_sha256"],
            "warnings": [
                {
                    "code": "BASELINE_FALLBACK",
                    "message": "Explicit baseline advisory "
                    "fallback; validate operational rules before any authorized submission.",
                    "fields": [],
                    "details": {"trigger": trigger},
                }
            ],
            "uncertainty": {
                "status": "not_calibrated",
                "confidence_level": None,
                "rpm_interval": None,
                "dollar_interval": None,
            },
        }
