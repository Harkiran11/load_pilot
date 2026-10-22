"""Fit a global median bid-RPM reference using saved training labels only."""

import argparse
import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

from loadpilot_model.features.schema import METADATA_COLUMNS, TARGET_COLUMN
from loadpilot_model.paths import PROJECT_ROOT

BASELINE_ID = "global_median_bid_rpm"


@dataclass(frozen=True)
class GlobalMedianBaseline:
    median_bid_rpm: float
    training_row_count: int

    def __post_init__(self):
        if (
            isinstance(self.median_bid_rpm, bool)
            or not isinstance(self.median_bid_rpm, (int, float))
            or not math.isfinite(self.median_bid_rpm)
            or self.median_bid_rpm <= 0
        ):
            raise ValueError("Median bid RPM must be finite and positive")
        if (
            isinstance(self.training_row_count, bool)
            or not isinstance(self.training_row_count, int)
            or self.training_row_count < 1
        ):
            raise ValueError("Training row count must be a positive integer")

    def predict_rpm(self) -> float:
        """Return the same training-derived reference for any supported shipment."""
        return self.median_bid_rpm


def load_training_targets(dataset: Path) -> tuple[list[float], list[dict], dict]:
    """Read the split manifest and train labels/metadata; never read held-out files."""
    manifest_bytes = (dataset / "split-report.json").read_bytes()
    report = json.loads(manifest_bytes)
    if report["fit_partition"] != "train" or report["target_column"] != TARGET_COLUMN:
        raise ValueError("Expected a training-only pricing dataset manifest")
    train = report["partitions"]["train"]
    contents = {
        name: (dataset / "train" / f"{name}.csv").read_bytes() for name in ("targets", "metadata")
    }
    tables = {}
    for name, header in (("targets", [TARGET_COLUMN]), ("metadata", list(METADATA_COLUMNS))):
        reader = csv.DictReader(
            io.StringIO(contents[name].decode("utf-8-sig"), newline=""), strict=True
        )
        if reader.fieldnames != header:
            raise ValueError(f"Unexpected training {name} columns")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"Malformed training {name} rows")
        tables[name] = rows
    count = len(tables["targets"])
    if (
        count == 0
        or count != len(tables["metadata"])
        or count != train["row_count"]
        or count != report["fit_row_count"]
    ):
        raise ValueError(
            "Training labels/metadata and manifest row counts must agree and be nonzero"
        )
    ids = [row["shipment_id"] for row in tables["metadata"]]
    if not all(ids) or len(set(ids)) != count or ids != train["shipment_ids"]:
        raise ValueError("Training shipment IDs must match the saved ordered partition")
    stamps = [
        datetime.strptime(row["shipped_at"], "%Y-%m-%d %H:%M:%S") for row in tables["metadata"]
    ]
    if (
        stamps != sorted(stamps)
        or stamps[0].isoformat(sep=" ") != train["date_min"]
        or stamps[-1].isoformat(sep=" ") != train["date_max"]
    ):
        raise ValueError("Training dates do not match the chronological manifest")
    values = [float(row[TARGET_COLUMN]) for row in tables["targets"]]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Training target RPM values must be finite and positive")
    provenance = {
        "fit_partition": "train",
        "training_row_count": count,
        "units": "USD per loaded mile",
        "target_column": TARGET_COLUMN,
        "source_sha256": report["source_sha256"],
        "training_date_range": {"min": train["date_min"], "max": train["date_max"]},
        "dataset_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "training_files_sha256": {
            f"train/{name}.csv": hashlib.sha256(value).hexdigest()
            for name, value in contents.items()
        },
        "validation_test_policy": "No validation/test files read, no held-out metrics calculated",
        "interpretation": "Historical submitted-bid reference; no profit or acceptance guarantee",
    }
    return values, tables["metadata"], provenance


def fit_from_training(dataset: Path) -> dict:
    values, _, provenance = load_training_targets(dataset)
    baseline = GlobalMedianBaseline(float(median(values)), len(values))
    return {
        "schema_version": 1,
        "baseline_id": BASELINE_ID,
        "median_bid_rpm": baseline.median_bid_rpm,
        "statistic": "Unweighted median of per-shipment training target_bid_rpm; "
        "for even counts, mean of the two middle sorted values",
        **provenance,
    }


def load_global_median(path: Path) -> GlobalMedianBaseline:
    artifact = json.loads(path.read_text())
    if (
        artifact["schema_version"] != 1
        or artifact["baseline_id"] != BASELINE_ID
        or artifact["fit_partition"] != "train"
        or artifact["units"] != "USD per loaded mile"
    ):
        raise ValueError("Unsupported global median baseline artifact")
    return GlobalMedianBaseline(artifact["median_bid_rpm"], artifact["training_row_count"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/baselines/global-median-v1.json"
    )
    args = parser.parse_args()
    try:
        artifact = fit_from_training(args.dataset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.exit(2, f"Unable to fit global median baseline: {error}\n")
    print(
        f"Global median: {artifact['median_bid_rpm']:.6f} USD/mile "
        f"from {artifact['training_row_count']} training records"
    )
    print(f"Saved baseline to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
