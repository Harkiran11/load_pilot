"""Frozen descriptive bid-RPM quartiles fitted exclusively on training records."""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from loadpilot_model.baselines.global_median import load_training_targets
from loadpilot_model.baselines.lane_median import check_min_samples, normalize_lane
from loadpilot_model.evaluation.validation_metrics import assign_band, training_band_cuts
from loadpilot_model.features.schema import FEATURE_COLUMNS, build_features
from loadpilot_model.paths import PROJECT_ROOT


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(value: dict) -> str:
    return digest(json.dumps(value, sort_keys=True, allow_nan=False).encode())


def statistics(values: list[float]) -> dict:
    quartiles = np.quantile(values, [0.25, 0.5, 0.75], method="linear").tolist()
    return dict(
        sample_count=len(values),
        lower_rpm=quartiles[0],
        median_rpm=quartiles[1],
        upper_rpm=quartiles[2],
    )


def fit_from_training(dataset: Path, minimum_samples: int = 20) -> dict:
    check_min_samples(minimum_samples)
    values, _, provenance = load_training_targets(dataset)
    contents = (dataset / "train/features.csv").read_bytes()
    reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig")), strict=True)
    if reader.fieldnames != list(FEATURE_COLUMNS):
        raise ValueError("Unexpected training features")
    rows = list(reader)
    if len(rows) != len(values) or len(values) < minimum_samples:
        raise ValueError("Aligned training rows and sufficient global history are required")
    features = [build_features(row) for row in rows]
    cuts = training_band_cuts([row["distance_miles"] for row in features])
    lanes, bands, lane_bands = defaultdict(list), defaultdict(list), defaultdict(list)
    for row, rpm in zip(features, values):
        lane = normalize_lane(row["lane_id"])
        band = assign_band(row["distance_miles"], cuts)
        lanes[lane].append(rpm)
        bands[band].append(rpm)
        lane_bands[(lane, band)].append(rpm)
    grouped = {}
    for (lane, band), rpms in sorted(lane_bands.items()):
        grouped.setdefault(lane, {})[band] = statistics(rpms)
    provenance["training_files_sha256"]["train/features.csv"] = digest(contents)
    payload = {
        "schema_version": 1,
        "policy_version": "historical-range-v1",
        "minimum_samples": minimum_samples,
        "quantiles": [0.25, 0.75],
        "quantile_method": "linear",
        "distance_cuts": cuts,
        "global": statistics(values),
        "lanes": {key: statistics(rpms) for key, rpms in sorted(lanes.items())},
        "distance_bands": {key: statistics(rpms) for key, rpms in sorted(bands.items())},
        "lane_distance_bands": grouped,
        "selection_order": ["lane_distance_band", "lane", "distance_band", "global"],
        "interpretation": "Descriptive middle 50% of historical submitted bid RPM; "
        "not a calibrated prediction interval or acceptance guarantee",
        **provenance,
    }
    return {**payload, "artifact_id": identity(payload)}


class HistoricalPricingRange:
    """Load once, verify frozen training lineage, and select a supported peer group."""

    def __init__(self, path: Path, bundle: Path):
        contents = path.read_bytes()
        artifact = json.loads(contents)
        payload = {key: value for key, value in artifact.items() if key != "artifact_id"}
        if identity(payload) != artifact["artifact_id"]:
            raise ValueError("Historical range checksum mismatch")
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest_payload = {key: value for key, value in manifest.items() if key != "bundle_id"}
        if manifest["schema_version"] != 1 or manifest["bundle_id"] != identity(manifest_payload):
            raise ValueError("Invalid model manifest identity")

        def verified(name):
            content = (bundle / name).read_bytes()
            if digest(content) != manifest["files_sha256"][name]:
                raise ValueError("Range lineage artifact checksum mismatch")
            return json.loads(content)

        metadata = verified("metadata.json")
        baseline = verified("baselines/lane-median-v1.json")
        if (
            artifact["schema_version"] != 1
            or artifact["policy_version"] != "historical-range-v1"
            or artifact["fit_partition"] != "train"
            or artifact["units"] != "USD per loaded mile"
            or artifact["quantiles"] != [0.25, 0.75]
            or artifact["quantile_method"] != "linear"
            or artifact["selection_order"]
            != ["lane_distance_band", "lane", "distance_band", "global"]
            or artifact["source_sha256"] != metadata["source_sha256"]
            or artifact["dataset_manifest_sha256"] != metadata["split_manifest_sha256"]
            or artifact["training_files_sha256"] != baseline["training_files_sha256"]
            or artifact["training_row_count"] != baseline["training_row_count"]
        ):
            raise ValueError("Historical range differs from model training lineage/contract")
        check_min_samples(artifact["minimum_samples"])
        cuts = artifact["distance_cuts"]
        if (
            not cuts
            or any(not math.isfinite(v) or v <= 0 for v in cuts)
            or cuts != sorted(set(cuts))
        ):
            raise ValueError("Invalid historical distance bands")
        groups = [
            artifact["global"],
            *artifact["lanes"].values(),
            *artifact["distance_bands"].values(),
        ]
        groups.extend(
            group for bands in artifact["lane_distance_bands"].values() for group in bands.values()
        )
        for group in groups:
            count = group["sample_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("Invalid history count")
            prices = [group[key] for key in ("lower_rpm", "median_rpm", "upper_rpm")]
            if any(not math.isfinite(v) or v <= 0 for v in prices) or prices != sorted(prices):
                raise ValueError("Invalid historical quartiles")
        total = artifact["global"]["sample_count"]
        if (
            total != artifact["training_row_count"]
            or total < artifact["minimum_samples"]
            or sum(g["sample_count"] for g in artifact["lanes"].values()) != total
            or sum(g["sample_count"] for g in artifact["distance_bands"].values()) != total
            or sum(
                g["sample_count"]
                for bands in artifact["lane_distance_bands"].values()
                for g in bands.values()
            )
            != total
        ):
            raise ValueError("Inconsistent history sample counts")
        self.artifact, self.sha256 = artifact, digest(contents)

    def describe(self, lane_id: str, miles: float) -> dict:
        if not math.isfinite(miles) or miles <= 0:
            raise ValueError("Positive finite loaded miles required")
        artifact = self.artifact
        lane = normalize_lane(lane_id)
        band = assign_band(str(miles), artifact["distance_cuts"])
        candidates = (
            ("lane_distance_band", artifact["lane_distance_bands"].get(lane, {}).get(band)),
            ("lane", artifact["lanes"].get(lane)),
            ("distance_band", artifact["distance_bands"].get(band)),
            ("global", artifact["global"]),
        )
        for source, group in candidates:
            if group and group["sample_count"] >= artifact["minimum_samples"]:
                lower, upper = group["lower_rpm"] * miles, group["upper_rpm"] * miles
                if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0:
                    raise ValueError("Historical dollar range cannot be represented")
                return {
                    "policy_version": artifact["policy_version"],
                    "artifact_sha256": self.sha256,
                    "source": source,
                    "sample_count": group["sample_count"],
                    "minimum_samples": artifact["minimum_samples"],
                    "distance_band": band,
                    "lower_quantile": 0.25,
                    "upper_quantile": 0.75,
                    "lower_rpm": group["lower_rpm"],
                    "upper_rpm": group["upper_rpm"],
                    "lower_bid_dollars": lower,
                    "upper_bid_dollars": upper,
                    "training_date_min": artifact["training_date_range"]["min"],
                    "training_date_max": artifact["training_date_range"]["max"],
                }
        raise ValueError("Insufficient historical support")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/baselines/historical-range-v1.json"
    )
    args = parser.parse_args()
    artifact = fit_from_training(args.dataset, args.minimum_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    print(
        f"Saved historical ranges from {artifact['training_row_count']} training rows: "
        f"{args.output}"
    )


if __name__ == "__main__":
    main()
