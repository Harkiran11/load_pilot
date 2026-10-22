"""Compare validation RPM references with actual historical total-dollar bids."""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from loadpilot_model.baselines.global_median import load_global_median
from loadpilot_model.baselines.lane_median import load_lane_median
from loadpilot_model.features.schema import (
    FEATURE_COLUMNS,
    METADATA_COLUMNS,
    TARGET_COLUMN,
    build_features,
)
from loadpilot_model.paths import PROJECT_ROOT


def positive_decimal(value, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and positive")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be numeric") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def rpm_to_bid_dollars(predicted_rpm, loaded_miles) -> Decimal:
    """Multiply by loaded miles only; preserve precision before display rounding."""
    return positive_decimal(predicted_rpm, "predicted_rpm") * positive_decimal(
        loaded_miles, "loaded_miles"
    )


def compare_prediction(predicted_rpm, loaded_miles, historical_bid) -> dict:
    bid = positive_decimal(historical_bid, "historical_bid")
    predicted = rpm_to_bid_dollars(predicted_rpm, loaded_miles)
    rounded = predicted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    error = predicted - bid
    return {
        "predicted_bid_dollars": str(predicted),
        "predicted_bid_dollars_rounded": str(rounded),
        "signed_error_dollars": str(error),
        "absolute_error_dollars": str(abs(error)),
        "rounded_signed_error_dollars": str(rounded - bid),
    }


def read_table(path: Path, fields) -> tuple[list[dict], bytes]:
    contents = path.read_bytes()
    reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != list(fields):
        raise ValueError(f"Unexpected columns in {path.name}")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed rows in {path.name}")
    return rows, contents


def load_validation_records(dataset: Path, source: Path) -> tuple[list[dict], dict]:
    return load_partition_records(dataset, source, "validation")


def load_partition_records(
    dataset: Path, source: Path, partition_name: str
) -> tuple[list[dict], dict]:
    """Align historical bids and saved rows without model inference or fitting."""
    if partition_name not in {"validation", "test"}:
        raise ValueError("Comparison requires validation or final test records")
    report_bytes = (dataset / "split-report.json").read_bytes()
    report = json.loads(report_bytes)
    partition = report["partitions"][partition_name]
    tables, hashes = {}, {}
    for name, fields in (
        ("features", FEATURE_COLUMNS),
        ("targets", (TARGET_COLUMN,)),
        ("metadata", METADATA_COLUMNS),
    ):
        tables[name], contents = read_table(dataset / partition_name / f"{name}.csv", fields)
        hashes[f"{partition_name}/{name}.csv"] = hashlib.sha256(contents).hexdigest()
    count = partition["row_count"]
    if count < 1 or any(len(rows) != count for rows in tables.values()):
        raise ValueError("Validation features, targets, and metadata must align with the manifest")
    ids = [row["shipment_id"] for row in tables["metadata"]]
    if len(set(ids)) != count or ids != partition["shipment_ids"]:
        raise ValueError("Validation IDs do not match the ordered saved partition")
    stamps = [
        datetime.strptime(row["shipped_at"], "%Y-%m-%d %H:%M:%S") for row in tables["metadata"]
    ]
    if (
        stamps != sorted(stamps)
        or stamps[0].isoformat(sep=" ") != partition["date_min"]
        or stamps[-1].isoformat(sep=" ") != partition["date_max"]
    ):
        raise ValueError("Validation dates do not match the saved partition")
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != report["source_sha256"]:
        raise ValueError("Historical source does not match the frozen dataset")
    wanted, originals = set(ids), {}
    for row in csv.DictReader(
        io.StringIO(source_bytes.decode("utf-8-sig"), newline=""), strict=True
    ):
        shipment_id = row.get("shipment_id")
        if shipment_id in wanted:
            if shipment_id in originals:
                raise ValueError("Duplicate validation shipment in historical source")
            originals[shipment_id] = row
    records = []
    for features, targets, metadata in zip(
        tables["features"], tables["targets"], tables["metadata"]
    ):
        raw = originals.get(metadata["shipment_id"])
        if raw is None or raw["order_type"].strip().lower() != "open_bid":
            raise ValueError("Validation shipment missing from historical open bids")
        if (
            build_features(raw) != build_features(features)
            or raw["shipped_at"] != metadata["shipped_at"]
        ):
            raise ValueError("Validation feature/metadata alignment differs from original shipment")
        bid = positive_decimal(raw["bid_rate"], "historical_bid")
        miles = positive_decimal(features["distance_miles"], "loaded_miles")
        label = positive_decimal(targets[TARGET_COLUMN], "target_bid_rpm")
        if not math.isclose(float(label), float(bid / miles), rel_tol=1e-12):
            raise ValueError("Validation target differs from actual submitted bid per loaded mile")
        records.append(
            {
                **features,
                **metadata,
                "historical_bid_dollars": str(bid),
                "historical_bid_rpm": str(bid / miles),
            }
        )
    return records, {
        "partition": partition_name,
        "row_count": count,
        "source_sha256": report["source_sha256"],
        "dataset_manifest_sha256": hashlib.sha256(report_bytes).hexdigest(),
        f"{partition_name}_files_sha256": hashes,
    }


def build_comparisons(dataset: Path, source: Path, baselines: Path) -> tuple[list[dict], dict]:
    records, provenance = load_validation_records(dataset, source)
    hashes = {}
    filenames = {
        "global_median": "global-median-v1.json",
        "lane_median": "lane-median-v1.json",
        "market_rpm": "market-rpm-v1.json",
    }
    for name, filename in filenames.items():
        contents = (baselines / filename).read_bytes()
        artifact = json.loads(contents)
        if artifact["source_sha256"] != provenance["source_sha256"]:
            raise ValueError("Baseline source differs from evaluation dataset")
        if name != "market_rpm" and (
            artifact["fit_partition"] != "train"
            or artifact["dataset_manifest_sha256"] != provenance["dataset_manifest_sha256"]
        ):
            raise ValueError("Baseline was not fitted to this dataset's training partition")
        hashes[filename] = hashlib.sha256(contents).hexdigest()
    global_baseline = load_global_median(baselines / filenames["global_median"])
    lane_baseline = load_lane_median(baselines / filenames["lane_median"])
    comparisons = []
    for record in records:
        lane_result = lane_baseline.predict(record["lane_id"])
        predictions = {
            "global_median": (
                global_baseline.predict_rpm(),
                "global_median",
                "GLOBAL_TRAINING_REFERENCE",
            ),
            "lane_median": (
                lane_result["predicted_bid_rpm"],
                lane_result["source"],
                lane_result["reason"],
            ),
            "market_rpm": (None, "market_rpm", "NO_HISTORICAL_PREBID_MARKET_QUOTE"),
        }
        for name, (rpm, reference_source, reason) in predictions.items():
            row = {
                "shipment_id": record["shipment_id"],
                "shipped_at": record["shipped_at"],
                "lane_id": record["lane_id"],
                "loaded_miles": record["distance_miles"],
                "historical_bid_dollars": record["historical_bid_dollars"],
                "historical_bid_rpm": record["historical_bid_rpm"],
                "baseline": name,
                "reference_source": reference_source,
                "reason": reason,
                "status": "available" if rpm is not None else "unavailable",
                "predicted_bid_rpm": "",
                "predicted_bid_dollars": "",
                "predicted_bid_dollars_rounded": "",
                "signed_error_rpm": "",
                "signed_error_dollars": "",
                "absolute_error_dollars": "",
                "rounded_signed_error_dollars": "",
            }
            if rpm is not None:
                row.update(
                    compare_prediction(
                        rpm, record["distance_miles"], record["historical_bid_dollars"]
                    )
                )
                row["predicted_bid_rpm"] = str(rpm)
                row["signed_error_rpm"] = str(
                    Decimal(str(rpm)) - Decimal(record["historical_bid_rpm"])
                )
            comparisons.append(row)
    report = {
        **provenance,
        "baseline_files_sha256": hashes,
        "comparison_rows": len(comparisons),
        "units": "USD and USD per loaded mile",
        "conversion": "predicted_bid_rpm * loaded_miles; deadhead excluded from denominator",
        "rounding": "Unrounded prediction used for errors; separate cents value uses ROUND_HALF_UP",
        "signed_error": "predicted minus historical; positive means above the submitted bid",
        "available_counts": dict(
            Counter(row["baseline"] for row in comparisons if row["status"] == "available")
        ),
        "unavailable_counts": dict(
            Counter(row["baseline"] for row in comparisons if row["status"] == "unavailable")
        ),
        "lane_reference_sources": dict(
            Counter(
                row["reference_source"] for row in comparisons if row["baseline"] == "lane_median"
            )
        ),
        "test_policy": "No test partition files read; no test evaluation or baseline refitting",
        "scope": "Row-level validation price comparisons; aggregate/slice MAE is a separate task",
    }
    return comparisons, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--source", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv"
    )
    parser.add_argument("--baselines", type=Path, default=PROJECT_ROOT / "artifacts/baselines")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/validation-bids-v1"
    )
    args = parser.parse_args()
    try:
        rows, report = build_comparisons(args.dataset, args.source, args.baselines)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        with (args.output_dir / "comparisons.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (args.output_dir / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
    except (OSError, ValueError, KeyError, csv.Error, InvalidOperation) as error:
        parser.exit(2, f"Unable to compare bid prices: {error}\n")
    print(
        f"Compared {report['row_count']} validation shipments across global/lane references; "
        "market references unavailable without historical pre-bid quotes."
    )
    print(f"Saved comparisons to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
