"""Validation MAE overall and by lane, training-derived bands, and retrospective season."""

import argparse
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

from loadpilot_model.evaluation.bid_comparison import (
    compare_prediction,
    load_validation_records,
    positive_decimal,
    read_table,
)
from loadpilot_model.features.schema import FEATURE_COLUMNS
from loadpilot_model.paths import PROJECT_ROOT

BASELINES = ("global_median", "lane_median", "market_rpm")
SEASONS = ("winter", "spring", "summer", "fall")


def retrospective_season(shipped_at: str) -> str:
    month = datetime.strptime(shipped_at, "%Y-%m-%d %H:%M:%S").month
    return SEASONS[(month % 12) // 3]


def training_band_cuts(values: list[str]) -> list[float]:
    if not values:
        raise ValueError("Band boundaries require nonempty training data")
    numbers = [float(positive_decimal(value, "training band value")) for value in values]
    if not np.isfinite(numbers).all():
        raise ValueError("Training band values must be representable as finite numbers")
    return sorted(set(np.quantile(numbers, [0.25, 0.5, 0.75], method="linear").tolist()))


def band_labels(cuts: list[float]) -> list[str]:
    edges = [0.0, *cuts]
    return [f"[{lower}, {upper})" for lower, upper in zip(edges, cuts)] + [
        f"[{edges[-1]}, infinity)"
    ]


def assign_band(value: str, cuts: list[float]) -> str:
    number = positive_decimal(value, "slice value")
    index = sum(number >= Decimal(str(cut)) for cut in cuts)
    return band_labels(cuts)[index]


def summarize(rows: list[dict], minimum: int = 5) -> dict:
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("Minimum slice samples must be a positive integer")
    if any(row["status"] not in {"available", "unavailable"} for row in rows):
        raise ValueError("Unknown comparison availability")
    available = [row for row in rows if row["status"] == "available"]
    n = len(available)
    rpm_errors = [Decimal(row["signed_error_rpm"]) for row in available]
    dollar_errors = [Decimal(row["signed_error_dollars"]) for row in available]
    if any(not value.is_finite() for value in rpm_errors + dollar_errors):
        raise ValueError("Cannot score nonfinite errors")
    return {
        "eligible_count": len(rows),
        "prediction_count": n,
        "unavailable_count": len(rows) - n,
        "coverage": n / len(rows) if rows else None,
        "status": "scored" if n else ("unavailable" if rows else "no_eligible_records"),
        "sparse": 0 < n < minimum,
        "minimum_slice_samples": minimum,
        "mae_rpm": float(sum(map(abs, rpm_errors), Decimal(0)) / n) if n else None,
        "mae_dollars": float(sum(map(abs, dollar_errors), Decimal(0)) / n) if n else None,
        "mean_signed_error_rpm": float(sum(rpm_errors, Decimal(0)) / n) if n else None,
        "mean_signed_error_dollars": float(sum(dollar_errors, Decimal(0)) / n) if n else None,
    }


def evaluate_validation(dataset: Path, source: Path, comparison_dir: Path, minimum: int = 5):
    # Validate the original bid, features, and partition lineage before scoring.
    records, provenance = load_validation_records(dataset, source)
    context = {row["shipment_id"]: row for row in records}
    manifest = json.loads((dataset / "split-report.json").read_text())
    train, training_bytes = read_table(dataset / "train/features.csv", FEATURE_COLUMNS)
    if len(train) != manifest["partitions"]["train"]["row_count"] or not train:
        raise ValueError("Training band data does not match the saved training count")
    cuts = {
        "distance_band": training_band_cuts([row["distance_miles"] for row in train]),
        "weight_band": training_band_cuts([row["weight_lbs"] for row in train]),
    }
    report_bytes = (comparison_dir / "report.json").read_bytes()
    comparison_report = json.loads(report_bytes)
    for field in (
        "partition",
        "row_count",
        "source_sha256",
        "dataset_manifest_sha256",
        "validation_files_sha256",
    ):
        if comparison_report[field] != provenance[field]:
            raise ValueError("Comparison artifact lineage differs from validation data")
    contents = (comparison_dir / "comparisons.csv").read_bytes()

    reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig"), newline=""), strict=True)
    rows = list(reader)
    grouped, seen = defaultdict(list), set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed comparison record")
        key = (row["baseline"], row["shipment_id"])
        if row["baseline"] not in BASELINES or row["shipment_id"] not in context or key in seen:
            raise ValueError("Unknown or duplicate baseline/shipment comparison")
        seen.add(key)
        record = context[row["shipment_id"]]
        if (
            row["lane_id"] != record["lane_id"]
            or row["shipped_at"] != record["shipped_at"]
            or Decimal(row["loaded_miles"]) != Decimal(record["distance_miles"])
            or Decimal(row["historical_bid_dollars"]) != Decimal(record["historical_bid_dollars"])
            or Decimal(row["historical_bid_rpm"]) != Decimal(record["historical_bid_rpm"])
        ):
            raise ValueError(
                "Comparison shipment context differs from historical validation record"
            )
        if row["status"] == "available":
            expected = compare_prediction(
                row["predicted_bid_rpm"], record["distance_miles"], record["historical_bid_dollars"]
            )
            for name, value in expected.items():
                if Decimal(row[name]) != Decimal(value):
                    raise ValueError("Stored dollar comparison error is inconsistent")
            error = Decimal(row["predicted_bid_rpm"]) - Decimal(record["historical_bid_rpm"])
            if Decimal(row["signed_error_rpm"]) != error:
                raise ValueError("Stored RPM comparison error is inconsistent")
        elif row["status"] == "unavailable":
            if any(
                row[name]
                for name in (
                    "predicted_bid_rpm",
                    "predicted_bid_dollars",
                    "signed_error_rpm",
                    "signed_error_dollars",
                    "absolute_error_dollars",
                )
            ):
                raise ValueError(
                    "Unavailable predictions must not contain fabricated values/errors"
                )
        else:
            raise ValueError("Unknown comparison availability")
        grouped[row["baseline"]].append(row)
    if len(seen) != len(records) * len(BASELINES):
        raise ValueError(
            "Each baseline must cover every validation shipment, including unavailable rows"
        )
    for status, field in (("available", "available_counts"), ("unavailable", "unavailable_counts")):
        actual = dict(Counter(row["baseline"] for row in rows if row["status"] == status))
        if actual != comparison_report[field]:
            raise ValueError("Comparison coverage counts differ from the saved report")
    overall, slices = [], []
    labels = {
        "lane": sorted({r["lane_id"] for r in records}),
        "distance_band": band_labels(cuts["distance_band"]),
        "season": list(SEASONS),
        "weight_band": band_labels(cuts["weight_band"]),
    }
    for baseline in BASELINES:
        baseline_rows = grouped[baseline]
        overall.append({"baseline": baseline, **summarize(baseline_rows, minimum)})
        for dimension, possible in labels.items():
            bins = defaultdict(list)
            for row in baseline_rows:
                record = context[row["shipment_id"]]
                label = (
                    record["lane_id"]
                    if dimension == "lane"
                    else retrospective_season(record["shipped_at"])
                    if dimension == "season"
                    else assign_band(
                        record["distance_miles"]
                        if dimension == "distance_band"
                        else record["weight_lbs"],
                        cuts[dimension],
                    )
                )
                bins[label].append(row)
            for label in possible:
                slices.append(
                    {
                        "baseline": baseline,
                        "dimension": dimension,
                        "slice": label,
                        **summarize(bins[label], minimum),
                    }
                )
    report = {
        **provenance,
        "overall": overall,
        "slices": slices,
        "band_policy": {
            "fit_partition": "train",
            "method": "Linear 25/50/75 percentiles; duplicate cut points collapsed",
            "cuts": cuts,
            "intervals": "Lower inclusive, upper exclusive; last band unbounded",
            "training_feature_sha256": hashlib.sha256(training_bytes).hexdigest(),
        },
        "season_policy": "Actual shipped_at season in source clock; evaluation only",
        "sparse_policy": "Report all counts; flag 0 < predictions < minimum; "
        "sparse-slice rankings are not reliable",
        "comparison_csv_sha256": hashlib.sha256(contents).hexdigest(),
        "comparison_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "baseline_files_sha256": comparison_report["baseline_files_sha256"],
        "mae_policy": "Unweighted MAE over available predictions; unrounded USD totals",
        "test_policy": "No test files read or scored; no baseline fitting or threshold tuning",
        "interpretation": "Historical price error, not realized profit or award improvement",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--source", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv"
    )
    parser.add_argument(
        "--comparisons", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/validation-bids-v1"
    )
    parser.add_argument("--min-slice-samples", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts/evaluation/validation-metrics-v1",
    )
    args = parser.parse_args()
    try:
        report = evaluate_validation(
            args.dataset, args.source, args.comparisons, args.min_slice_samples
        )
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "metrics.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        for name in ("overall", "slices"):
            with (args.output_dir / f"{name}.csv").open(
                "w", newline="", encoding="utf-8"
            ) as stream:
                writer = csv.DictWriter(stream, fieldnames=list(report[name][0]))
                writer.writeheader()
                writer.writerows(report[name])
    except (OSError, ValueError, KeyError, csv.Error, InvalidOperation) as error:
        parser.exit(2, f"Unable to evaluate validation metrics: {error}\n")
    for row in report["overall"]:
        print(
            f"{row['baseline']}: n={row['prediction_count']}/{row['eligible_count']}, "
            f"MAE RPM={row['mae_rpm']}, MAE USD={row['mae_dollars']}"
        )
    print(f"Saved overall and slice metrics to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
