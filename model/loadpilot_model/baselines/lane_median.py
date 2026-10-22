"""Training-only lane median RPM with a global median for sparse/unseen lanes."""

import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from loadpilot_model.baselines.global_median import GlobalMedianBaseline, load_training_targets
from loadpilot_model.features.schema import FEATURE_COLUMNS, MISSING_FEATURE_TOKENS, build_features
from loadpilot_model.paths import PROJECT_ROOT

BASELINE_ID = "lane_median_bid_rpm"


def check_min_samples(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("min_lane_samples must be a positive integer")


def normalize_lane(value: str) -> str:
    if not isinstance(value, str) or value.strip().lower() in MISSING_FEATURE_TOKENS:
        raise ValueError("A nonmissing directional lane ID is required")
    return value.strip().upper()


@dataclass(frozen=True)
class LaneMedianBaseline:
    min_lane_samples: int
    global_baseline: GlobalMedianBaseline
    lanes: dict

    def __post_init__(self):
        check_min_samples(self.min_lane_samples)
        total = 0
        for name, entry in self.lanes.items():
            if name != normalize_lane(name):
                raise ValueError("Stored lane IDs must be normalized")
            count = entry["training_row_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("Lane training counts must be positive integers")
            if count >= self.min_lane_samples:
                GlobalMedianBaseline(entry["median_bid_rpm"], count)
            elif entry["median_bid_rpm"] is not None:
                raise ValueError("Sparse lane must use the global fallback, not its own median")
            total += count
        if total != self.global_baseline.training_row_count:
            raise ValueError("Lane counts must sum to the global training population")

    def predict(self, lane_id: str) -> dict:
        lane = normalize_lane(lane_id)
        entry = self.lanes.get(lane)
        count = entry["training_row_count"] if entry else 0
        use_lane = count >= self.min_lane_samples
        return {
            "lane_id": lane,
            "predicted_bid_rpm": entry["median_bid_rpm"]
            if use_lane
            else self.global_baseline.predict_rpm(),
            "source": "lane_median" if use_lane else "global_median",
            "reason": "SUFFICIENT_TRAINING_SAMPLES"
            if use_lane
            else ("SPARSE_LANE" if entry else "UNSEEN_LANE"),
            "lane_training_row_count": count,
            "min_lane_samples": self.min_lane_samples,
        }


def fit_from_training(dataset: Path, min_lane_samples: int = 5) -> dict:
    check_min_samples(min_lane_samples)
    values, _, provenance = load_training_targets(dataset)
    contents = (dataset / "train/features.csv").read_bytes()
    reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != list(FEATURE_COLUMNS):
        raise ValueError("Unexpected training feature columns")
    rows = list(reader)
    if len(rows) != len(values) or any(
        None in row or any(v is None for v in row.values()) for row in rows
    ):
        raise ValueError("Training features must align with every target record")
    groups = defaultdict(list)
    for record, value in zip(rows, values):
        lane = normalize_lane(build_features(record)["lane_id"])
        groups[lane].append(value)
    lanes = {
        lane: {
            "training_row_count": len(targets),
            "median_bid_rpm": float(median(targets)) if len(targets) >= min_lane_samples else None,
        }
        for lane, targets in sorted(groups.items())
    }
    global_baseline = GlobalMedianBaseline(float(median(values)), len(values))
    LaneMedianBaseline(min_lane_samples, global_baseline, lanes)
    provenance["training_files_sha256"]["train/features.csv"] = hashlib.sha256(contents).hexdigest()
    eligible = sum(entry["training_row_count"] >= min_lane_samples for entry in lanes.values())
    return {
        "schema_version": 1,
        "baseline_id": BASELINE_ID,
        "min_lane_samples": min_lane_samples,
        "threshold_policy": "Inclusive configured minimum training count; no held-out fitting",
        "lane_normalization": "Strip whitespace, uppercase, preserve origin-destination direction",
        "statistic": "Unweighted median of training target_bid_rpm within each eligible lane",
        "global_fallback": {
            "baseline_id": "global_median_bid_rpm",
            "median_bid_rpm": global_baseline.median_bid_rpm,
            "training_row_count": global_baseline.training_row_count,
        },
        "lanes": lanes,
        "seen_lane_count": len(lanes),
        "eligible_lane_count": eligible,
        "sparse_lane_count": len(lanes) - eligible,
        "training_rows_with_lane_reference": sum(
            entry["training_row_count"]
            for entry in lanes.values()
            if entry["training_row_count"] >= min_lane_samples
        ),
        **provenance,
    }


def load_lane_median(path: Path) -> LaneMedianBaseline:
    artifact = json.loads(path.read_text())
    if (
        artifact["schema_version"] != 1
        or artifact["baseline_id"] != BASELINE_ID
        or artifact["fit_partition"] != "train"
        or artifact["units"] != "USD per loaded mile"
    ):
        raise ValueError("Unsupported lane median baseline artifact")
    fallback = artifact["global_fallback"]
    if fallback["training_row_count"] != artifact["training_row_count"]:
        raise ValueError("Fallback training population differs from lane population")
    return LaneMedianBaseline(
        artifact["min_lane_samples"],
        GlobalMedianBaseline(fallback["median_bid_rpm"], fallback["training_row_count"]),
        artifact["lanes"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument("--min-lane-samples", type=int, default=5)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/baselines/lane-median-v1.json"
    )
    args = parser.parse_args()
    try:
        artifact = fit_from_training(args.dataset, args.min_lane_samples)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.exit(2, f"Unable to fit lane median baseline: {error}\n")
    print(
        f"Lane medians: {artifact['eligible_lane_count']} eligible, "
        f"{artifact['sparse_lane_count']} sparse "
        f"of {artifact['seen_lane_count']} training lanes; minimum {args.min_lane_samples} samples"
    )
    print(f"Global fallback: {artifact['global_fallback']['median_bid_rpm']:.6f} USD/mile")
    print(f"Saved baseline to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
