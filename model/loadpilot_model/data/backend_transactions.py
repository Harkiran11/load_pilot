"""Import backend bid transactions into an immutable research dataset; never deploy."""

import argparse
import csv
import hashlib
import json
import math
import pickle
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from loadpilot_model.data.splitting import build_partitions
from loadpilot_model.features.schema import (
    FEATURE_COLUMNS,
    METADATA_COLUMNS,
    TARGET_COLUMN,
    build_features,
)
from loadpilot_model.inference.bundle import save_inference_bundle
from loadpilot_model.paths import PROJECT_ROOT


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def timestamps(cls, value, info):
        if info.field_name.endswith("_at") and isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class PreBidFeatures(StrictRecord):
    distance_miles: float = Field(gt=0)
    weight_lbs: float = Field(gt=0)
    origin_latitude: float = Field(ge=-90, le=90)
    origin_longitude: float = Field(ge=-180, le=180)
    destination_latitude: float = Field(ge=-90, le=90)
    destination_longitude: float = Field(ge=-180, le=180)
    lane_id: str = Field(min_length=1)
    origin_state: str = Field(min_length=1)
    destination_state: str = Field(min_length=1)


class BidTransaction(StrictRecord):
    shipment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    submission_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    order_type: Literal["open_bid", "fixed_rate"]
    submission_status: Literal["confirmed", "rejected", "unknown", "failed", "cancelled"]
    submitted_at: AwareDatetime | None = None
    bid_rate: float | None = Field(default=None, gt=0)
    price_origin: Literal["manual", "external", "model", "baseline", "unknown"]
    model_influenced: bool
    pre_bid_features_verified: bool
    features_observed_at: AwareDatetime | None = None
    features: PreBidFeatures | None = None
    award_status: Literal["awarded", "lost", "pending", "unknown"] = "unknown"

    @field_validator("bid_rate")
    @classmethod
    def submitted_cents(cls, value):
        if value is not None:
            with localcontext() as context:
                context.prec = 400
                amount = Decimal(str(value))
                if amount != amount.quantize(Decimal("0.01")):
                    raise ValueError("Confirmed submitted USD price must use whole cents")
        return value


class BackendTransactionExport(StrictRecord):
    schema_version: Literal[1] = 1
    source_namespace: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
    synthetic: bool
    currency: Literal["USD"] = "USD"
    distance_unit: Literal["miles"] = "miles"
    weight_unit: Literal["lb"] = "lb"
    time_domain: Literal["wall"] = "wall"
    exported_at: AwareDatetime
    records: list[dict]


class BackendTransactionContract(BackendTransactionExport):
    """Producer schema; import validates records individually to quarantine bad rows."""

    records: list[BidTransaction]


def protected_original_test_ids():
    bundle = PROJECT_ROOT / "artifacts/models/freight-rpm-v1"
    manifest = json.loads((bundle / "manifest.json").read_text())
    metadata = (bundle / "metadata.json").read_bytes()
    if hashlib.sha256(metadata).hexdigest() != manifest["files_sha256"]["metadata.json"]:
        raise ValueError("Frozen model metadata checksum mismatch")
    contents = (PROJECT_ROOT / "artifacts/dataset/split-report.json").read_bytes()
    if hashlib.sha256(contents).hexdigest() != json.loads(metadata)["split_manifest_sha256"]:
        raise ValueError("Protected original split checksum mismatch")
    return set(json.loads(contents)["partitions"]["test"]["shipment_ids"])


def write_dataset(splits, processor, report, output):
    output.mkdir(parents=True, exist_ok=False)
    for name, partition in splits.items():
        folder = output / name
        folder.mkdir()
        for table, fields in (
            ("features", FEATURE_COLUMNS),
            ("targets", (TARGET_COLUMN,)),
            ("metadata", METADATA_COLUMNS),
        ):
            with (folder / f"{table}.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(partition[table])
        np.savez_compressed(folder / "arrays.npz", X=partition["X"], y=partition["y"])
    contents = pickle.dumps(processor)
    (output / "preprocessor.pkl").write_bytes(contents)
    report["preprocessor_sha256"] = hashlib.sha256(contents).hexdigest()
    (output / "split-report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    save_inference_bundle(processor, report, output / "inference", preprocessor_bytes=contents)


def prepare_transactions(source: Path, output: Path, *, protected_ids: set[str], now=None):
    now = now or datetime.now(timezone.utc)
    contents = source.read_bytes()
    export = BackendTransactionExport.model_validate_json(contents)
    if export.exported_at > now:
        raise ValueError("Export cutoff is in the future")
    source_hash = hashlib.sha256(contents).hexdigest()
    rows, exclusions, lineage = [], [], []
    seen_orders, seen_submissions = {}, {}
    for index, raw in enumerate(export.records):
        try:
            record = BidTransaction.model_validate(raw)
        except ValidationError as error:
            # Never persist raw invalid values or exception strings from backend payloads.
            exclusions.append(
                {
                    "record_index": index,
                    "reasons": ["INVALID_RECORD"],
                    "fields": [".".join(map(str, e["loc"])) for e in error.errors()],
                }
            )
            continue
        canonical = record.model_dump(mode="json")
        previous = seen_orders.get(record.shipment_id)
        if previous is not None:
            if previous != canonical:
                raise ValueError(
                    "Conflicting records for one shipment; export one canonical bid per shipment"
                )
            exclusions.append({"record_index": index, "reasons": ["DUPLICATE_IDENTICAL_RECORD"]})
            continue
        if record.submission_id in seen_submissions:
            raise ValueError("Submission ID reused across shipments")
        seen_orders[record.shipment_id] = canonical
        seen_submissions[record.submission_id] = record.shipment_id
        identity = f"{export.source_namespace}:{record.shipment_id}"
        reasons = []
        if record.shipment_id in protected_ids or identity in protected_ids:
            reasons.append("PROTECTED_ORIGINAL_TEST_SHIPMENT")
        if record.order_type != "open_bid":
            reasons.append("FIXED_RATE_OUTSIDE_MODEL_SCOPE")
        if record.submission_status != "confirmed":
            reasons.append("SUBMISSION_NOT_CONFIRMED")
        if record.price_origin not in {"manual", "external"} or record.model_influenced:
            reasons.append("NONINDEPENDENT_OR_UNKNOWN_PRICE")
        if not record.pre_bid_features_verified:
            reasons.append("PREBID_PROVENANCE_UNVERIFIED")
        if record.submitted_at is None or record.bid_rate is None:
            reasons.append("SUBMITTED_PRICE_OR_TIME_MISSING")
        elif record.submitted_at > export.exported_at:
            reasons.append("SUBMISSION_AFTER_EXPORT")
        if record.features is None or record.features_observed_at is None:
            reasons.append("PREBID_SNAPSHOT_MISSING")
        elif record.submitted_at is not None and record.features_observed_at > record.submitted_at:
            reasons.append("FEATURES_OBSERVED_AFTER_BID")
        features, target = None, None
        if record.features is not None:
            try:
                features = build_features(record.features.model_dump())
            except ValueError:
                reasons.append("INVALID_PREBID_FEATURES")
        if record.bid_rate is not None and record.features is not None:
            target = record.bid_rate / record.features.distance_miles
            if not math.isfinite(target) or not 0 < target <= np.finfo(np.float32).max:
                reasons.append("INVALID_TARGET_RPM")
            elif np.float32(target) <= 0:
                reasons.append("INVALID_TARGET_RPM")
        if reasons:
            exclusions.append({"record_index": index, "reasons": sorted(set(reasons))})
            continue
        # UTC submission time, floored to seconds to match existing Dataset metadata format.
        stamp = record.submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(
            {**features, "shipment_id": identity, "shipped_at": stamp, TARGET_COLUMN: repr(target)}
        )
        lineage.append(
            {
                "dataset_shipment_id": identity,
                "shipment_id": record.shipment_id,
                "submission_id": record.submission_id,
                "submitted_at": record.submitted_at.isoformat(),
                "price_origin": record.price_origin,
                "award_status": record.award_status,
            }
        )
    report = {
        "schema_version": 1,
        "source_sha256": source_hash,
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_namespace": export.source_namespace,
        "exported_at": export.exported_at.isoformat(),
        "units": {"currency": "USD", "distance": "miles", "weight": "lb"},
        "synthetic": export.synthetic,
        "input_count": len(export.records),
        "selected_count": len(rows),
        "excluded_count": len(exclusions),
        "exclusions": exclusions,
        "exclusion_reason_counts": dict(Counter(r for e in exclusions for r in e["reasons"])),
        "selected_price_origins": dict(Counter(r["price_origin"] for r in lineage)),
        "selected_award_statuses": dict(Counter(r["award_status"] for r in lineage)),
        "dataset_ready": False,
        "model_trained": False,
        "deployed": False,
        "timestamp_policy": (
            "Real submitted_at UTC, floored to seconds; shipped_at is a metadata alias, "
            "not actual shipment time"
        ),
        "limitations": [
            "Backend provenance assertions are not independently authenticated.",
            "Model/baseline-influenced and unknown-origin prices are excluded, even if awarded.",
            "Dataset readiness is structural, not statistical release approval.",
            "No automatic merge with old history, scheduling, inference or production deployment.",
        ],
    }
    try:
        splits, processor, split_report = build_partitions(rows)
    except ValueError as error:
        report["preparation_status"] = "insufficient_or_invalid_dataset"
        report["preparation_error"] = str(error)  # Local split validation; no raw backend error.
        splits = None
    output.mkdir(parents=True, exist_ok=False)
    if splits is not None:
        split_report.update(
            source=str(source),
            source_sha256=source_hash,
            timestamp_source="submitted_at",
            timezone="UTC; seconds grouped conservatively",
            synthetic=export.synthetic,
            dataset_origin="backend_transactions",
            label_policy="Confirmed independent manual/external submitted open bids only",
        )
        write_dataset(splits, processor, split_report, output / "dataset")
        report.update(
            dataset_ready=True,
            preparation_status="prepared",
            partition_counts={name: len(partition["y"]) for name, partition in splits.items()},
        )
    (output / "import-report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output / "lineage.json").write_text(json.dumps(lineage, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--output-root", type=Path, default=PROJECT_ROOT / "artifacts/backend-datasets"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.version):
        parser.error("Version must be a safe identifier")
    report = prepare_transactions(
        args.input, args.output_root / args.version, protected_ids=protected_original_test_ids()
    )
    print(
        f"Selected {report['selected_count']} / {report['input_count']} transactions; "
        f"dataset ready: {report['dataset_ready']}. No model trained or deployed."
    )
    return 0 if report["dataset_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
