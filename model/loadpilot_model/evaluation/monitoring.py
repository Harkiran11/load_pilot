"""Offline feature/support and submitted-price agreement monitoring; no inference or fitting."""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from loadpilot_model.api.autopilot import TimestampInput
from loadpilot_model.features.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    MISSING_FEATURE_TOKENS,
    NUMERIC_FEATURES,
    build_features,
)


class PredictionObservation(TimestampInput):
    event_id: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)
    observed_at: AwareDatetime
    order_type: Literal["open_bid", "fixed_rate"]
    mode: Literal["manual", "autopilot", "prediction"]
    features: dict[str, str | float | int | None]
    pricing_source: Literal["model", "baseline", "unavailable"]
    model_version: str | None = None
    suggested_bid_dollars: float | None = Field(default=None, gt=0)
    final_bid_dollars: float | None = Field(default=None, gt=0)
    bid_observed_at: AwareDatetime | None = None
    estimated_cost_dollars: float | None = Field(default=None, ge=0)
    estimated_margin_percent: float | None = None
    award_status: Literal["awarded", "lost", "unknown"] = "unknown"
    award_observed_at: AwareDatetime | None = None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,80}$")

    @field_validator("features")
    @classmethod
    def feature_allowlist(cls, value):
        if set(value) - set(FEATURE_COLUMNS):
            raise ValueError("Only the nine model features may appear in features")
        for item in value.values():
            if isinstance(item, bool) or isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Feature numbers must be finite and not boolean")
        return value

    @model_validator(mode="after")
    def price_identity(self):
        if self.pricing_source == "model" and not self.model_version:
            raise ValueError("Model-source observations require model_version")
        if self.pricing_source == "unavailable" and self.suggested_bid_dollars is not None:
            raise ValueError("Unavailable observations cannot have a suggestion")
        return self


class MonitoringExport(TimestampInput):
    schema_version: Literal[1] = 1
    synthetic: bool
    currency: Literal["USD"] = "USD"
    distance_unit: Literal["miles"] = "miles"
    weight_unit: Literal["lb"] = "lb"
    exported_at: AwareDatetime
    records: list[PredictionObservation]

    @model_validator(mode="after")
    def unique_and_chronological(self):
        ids = [r.event_id for r in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate event_id; merge exports before monitoring")
        for row in self.records:
            if row.observed_at > self.exported_at:
                raise ValueError("Observation is after export cutoff")
            for timestamp, present in (
                (row.bid_observed_at, row.final_bid_dollars is not None),
                (row.award_observed_at, row.award_status != "unknown"),
            ):
                if present and (
                    timestamp is None or not row.observed_at <= timestamp <= self.exported_at
                ):
                    raise ValueError("Later bid/award requires a valid separate observation time")
        return self


def load_reference(bundle: Path):
    """Check frozen JSON hashes; never unpickle preprocessing or load model weights."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    reference = {}
    for name in ("numeric-scaler", "categorical-mappings"):
        relative = f"preprocessing/{name}.json"
        contents = (bundle / relative).read_bytes()
        if hashlib.sha256(contents).hexdigest() != manifest["files_sha256"][relative]:
            raise ValueError(f"Frozen monitoring reference checksum mismatch: {relative}")
        reference[name] = json.loads(contents)
        if reference[name]["fit_partition"] != "train":
            raise ValueError("Monitoring reference must be fitted on training data")
    return manifest, reference


def monitor(export: MonitoringExport, bundle: Path) -> dict:
    manifest, reference = load_reference(bundle)
    scaler = reference["numeric-scaler"]
    categories = {
        name: {row["value"] for row in rows}
        for name, rows in reference["categorical-mappings"]["mappings"].items()
    }
    numeric = {name: [] for name in NUMERIC_FEATURES}
    missing, unseen, errors, sources, versions = (Counter() for _ in range(5))
    invalid = 0
    agreements = defaultdict(list)
    labeled_shipments = set()
    duplicate_labels = 0
    invalid_agreement = 0
    for row in sorted(export.records, key=lambda r: (r.observed_at, r.event_id)):
        sources[row.pricing_source] += 1
        versions[row.model_version or "none"] += 1
        if row.error_code:
            errors[row.error_code] += 1
        for name in FEATURE_COLUMNS:
            value = row.features.get(name)
            if value is None or str(value).strip().lower() in MISSING_FEATURE_TOKENS:
                missing[name] += 1
        try:
            features = build_features(row.features)
        except ValueError:
            invalid += 1
            continue
        for name in NUMERIC_FEATURES:
            numeric[name].append(float(features[name]))
        for name in CATEGORICAL_FEATURES:
            if features[name] not in categories[name]:
                unseen[name] += 1
        if (
            row.order_type == "open_bid"
            and row.suggested_bid_dollars is not None
            and row.final_bid_dollars is not None
        ):
            # Repeated requests for one submitted bid must not overweight agreement metrics.
            if row.shipment_id in labeled_shipments:
                duplicate_labels += 1
                continue
            labeled_shipments.add(row.shipment_id)
            error = row.suggested_bid_dollars - row.final_bid_dollars
            rpm_error = abs(error) / float(features["distance_miles"])
            if not math.isfinite(error) or not math.isfinite(rpm_error):
                invalid_agreement += 1
                continue
            agreements[(row.pricing_source, row.model_version)].append((abs(error), rpm_error))
    shift = {}
    for name, mean, scale in zip(scaler["feature_order"], scaler["mean"], scaler["scale"]):
        values = numeric[name]
        # Descriptive support heuristic, not a calibrated drift significance test.
        shift[name] = {
            "valid_count": len(values),
            "mean_shift_training_std": (
                math.fsum(value / len(values) for value in values) / scale - mean / scale
                if values
                else None
            ),
            "outside_three_training_std_count": sum(
                abs(value - mean) / scale > 3 for value in values
            ),
        }
    agreement = [
        {
            "pricing_source": source,
            "model_version": version,
            "count": len(values),
            "mae_dollars": math.fsum(v[0] / len(values) for v in values),
            "mae_rpm": math.fsum(v[1] / len(values) for v in values),
        }
        for (source, version), values in agreements.items()
    ]
    return {
        "schema_version": 1,
        "synthetic": export.synthetic,
        "reference_bundle_id": manifest["bundle_id"],
        "record_count": len(export.records),
        "pricing_sources": dict(sources),
        "model_versions": dict(versions),
        "error_counts": dict(errors),
        "invalid_feature_record_count": invalid,
        "missing_feature_counts": dict(missing),
        "unseen_category_counts": dict(unseen),
        "numeric_support": shift,
        "submitted_price_agreement": agreement,
        "duplicate_submitted_price_labels_excluded": duplicate_labels,
        "nonrepresentable_price_agreement_count": invalid_agreement,
        "inference_repeated": False,
        "training_data_updated": False,
        "limitations": [
            "Submitted-price agreement is not realized profit or unbiased model accuracy.",
            "Autopilot submitted prices may equal suggestions by construction.",
            "Fixed-rate offers are excluded from open-bid price agreement.",
            "Numeric shifts are training-standardized heuristics, not calibrated drift tests.",
            "Export coverage and source authenticity are unknown; synthetic is not live evidence.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contents = args.input.read_bytes()
    report = monitor(MonitoringExport.model_validate_json(contents), args.bundle)
    report["source_sha256"] = hashlib.sha256(contents).hexdigest()
    report["implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Monitored {report['record_count']} records; synthetic: {report['synthetic']}.")


if __name__ == "__main__":
    main()
