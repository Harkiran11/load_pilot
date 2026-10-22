"""Create chronological pricing partitions with training-only preprocessing."""

import argparse
import csv
import hashlib
import json
import math
import pickle
from datetime import date, datetime
from pathlib import Path

import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from loadpilot_model.data.preparation import load_open_bid_rows
from loadpilot_model.features.preprocessing import feature_frame, transform_records
from loadpilot_model.features.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    METADATA_COLUMNS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    build_model_inputs,
)
from loadpilot_model.inference.bundle import save_inference_bundle
from loadpilot_model.paths import PROJECT_ROOT


def build_partitions(rows: list[dict], train_fraction=0.70, validation_fraction=0.15):
    if not (
        math.isfinite(train_fraction)
        and math.isfinite(validation_fraction)
        and 0 < train_fraction < 1
        and 0 < validation_fraction < 1
        and train_fraction + validation_fraction < 1
    ):
        raise ValueError("Fractions must be positive and leave a nonempty test fraction")
    if len(rows) < 3:
        raise ValueError("At least three records and distinct timestamps are required")
    ids = [row.get("shipment_id", "") for row in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Unique nonempty shipment IDs are required")
    ordered = sorted(
        rows,
        key=lambda row: (
            datetime.strptime(row["shipped_at"], "%Y-%m-%d %H:%M:%S"),
            row["shipment_id"],
        ),
    )
    timestamps = [datetime.strptime(row["shipped_at"], "%Y-%m-%d %H:%M:%S") for row in ordered]
    boundaries = [
        index for index in range(1, len(rows)) if timestamps[index - 1] < timestamps[index]
    ]
    if len(boundaries) < 2:
        raise ValueError("At least three distinct timestamps are required for disjoint splits")
    # Select the nearest legal timestamp-group boundaries; earlier wins a tie.
    first = min(
        boundaries[:-1], key=lambda index: (abs(index - int(len(rows) * train_fraction)), index)
    )
    second = min(
        (index for index in boundaries if index > first),
        key=lambda index: (
            abs(index - int(len(rows) * (train_fraction + validation_fraction))),
            index,
        ),
    )
    splits = {}
    for name, partition in zip(
        ("train", "validation", "test"),
        (ordered[:first], ordered[first:second], ordered[second:]),
    ):
        features, targets, metadata = build_model_inputs(partition)
        splits[name] = {"features": features, "targets": targets, "metadata": metadata}

    processor = ColumnTransformer(
        [
            ("numeric", StandardScaler(), list(NUMERIC_FEATURES)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float32),
                list(CATEGORICAL_FEATURES),
            ),
        ],
        remainder="drop",
    )
    # This is the only fit call. No validation/test features or labels are supplied.
    processor.fit(feature_frame(splits["train"]["features"]))
    for partition in splits.values():
        partition["X"] = transform_records(processor, partition["features"])
        partition["y"] = np.array(
            [float(row[TARGET_COLUMN]) for row in partition["targets"]], dtype=np.float32
        )
        if not np.isfinite(partition["y"]).all() or not (partition["y"] > 0).all():
            raise ValueError("Targets cannot be represented as finite positive float32 values")
    report = {
        "requested_fractions": {
            "train": train_fraction,
            "validation": validation_fraction,
            "test": 1 - train_fraction - validation_fraction,
        },
        "split_policy": "Sort shipped_at ascending, then shipment_id; keep timestamp groups intact",
        "timezone": "Source timestamps are naive; ordering preserves their source clock",
        "fit_partition": "train",
        "fit_row_count": len(splits["train"]["features"]),
        "feature_columns": FEATURE_COLUMNS,
        "target_column": TARGET_COLUMN,
        "transformed_feature_names": processor.get_feature_names_out().tolist(),
        "unknown_categories": "All-zero one-hot block for that categorical feature; no refitting",
        "target_scaling": "None; y remains USD per loaded mile",
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "test_policy": "One final evaluation only; never use for fitting, tuning, or selection",
        "partitions": {
            name: {
                "row_count": len(partition["metadata"]),
                "fraction": len(partition["metadata"]) / len(rows),
                "date_min": partition["metadata"][0]["shipped_at"],
                "date_max": partition["metadata"][-1]["shipped_at"],
                "shipment_ids": [row["shipment_id"] for row in partition["metadata"]],
            }
            for name, partition in splits.items()
        },
    }
    return splits, processor, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.exit(
            2, "Output directory already exists; choose a new --output-dir to preserve partitions\n"
        )
    try:
        rows, selection = load_open_bid_rows(args.data, args.as_of)
        splits, processor, report = build_partitions(
            rows, args.train_fraction, args.validation_fraction
        )
    except (ValueError, KeyError, OSError, UnicodeError, csv.Error) as error:
        parser.exit(2, f"Unable to partition historical data: {error}\n")
    report.update(
        source=selection["source"],
        source_sha256=selection["sha256"],
        as_of_date=selection["as_of_date"],
        selection=selection,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, partition in splits.items():
        folder = args.output_dir / name
        folder.mkdir()
        for filename, fields in (
            ("features", FEATURE_COLUMNS),
            ("targets", (TARGET_COLUMN,)),
            ("metadata", METADATA_COLUMNS),
        ):
            with (folder / f"{filename}.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(partition[filename])
        np.savez_compressed(folder / "arrays.npz", X=partition["X"], y=partition["y"])
    artifact = args.output_dir / "preprocessor.pkl"
    with artifact.open("wb") as stream:
        pickle.dump(processor, stream)
    report["preprocessor_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (args.output_dir / "split-report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    save_inference_bundle(processor, report, args.output_dir / "inference")
    for name, partition in splits.items():
        print(f"{name}: {len(partition['y'])} records, X shape {partition['X'].shape}")
    print(f"Saved training-fitted preprocessor and split manifest to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
