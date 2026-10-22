"""Run the packaged model on validation inputs and export reviewable plots."""

import argparse
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import torch

from loadpilot_model.data.dataset import FreightDataset
from loadpilot_model.evaluation.plots import (
    plot_model_comparison,
    plot_prediction_diagnostics,
    plot_training_history,
)
from loadpilot_model.inference.model_bundle import load_model_bundle
from loadpilot_model.paths import PROJECT_ROOT


def run_preview(bundle: Path, dataset: Path, output: Path) -> dict:
    archive = output.with_name(output.name + ".zip")
    if output.exists() or archive.exists():
        raise ValueError("Preview output already exists; choose a new --output-dir")
    loaded = load_model_bundle(bundle)
    validation = FreightDataset(dataset, "validation")
    for name in ("source_sha256", "split_manifest_sha256", "preprocessor_sha256"):
        if getattr(validation, name) != loaded.metadata[name]:
            raise ValueError("Preview dataset differs from the packaged training lineage")
    with (dataset / "validation/features.csv").open(newline="") as stream:
        records = list(csv.DictReader(stream))
    features = loaded.preprocessing.transform(records)
    np.testing.assert_array_equal(features, validation.features.numpy())
    training = json.loads((bundle / "training/tuning-results.json").read_text())
    selected = next(
        c
        for c in training["candidates"]
        if c["candidate_id"] == loaded.metadata["selected_candidate"]
    )
    batch_size = selected["batch_size"]
    torch.set_num_threads(1)
    with torch.inference_mode():
        predicted = (
            torch.cat(
                [
                    loaded.model(torch.from_numpy(features[i : i + batch_size]))
                    for i in range(0, len(features), batch_size)
                ]
            )
            .numpy()
            .astype(np.float64)
        )
    target, miles = validation.target_rpm_exact, validation.loaded_miles
    error = predicted - target
    metrics = {
        "row_count": len(predicted),
        "mae_rpm": float(np.abs(error).mean()),
        "mae_dollars": float(np.abs(error * miles).mean()),
    }
    for name in ("mae_rpm", "mae_dollars"):
        if metrics[name] != loaded.metadata["validation_metrics"][name]:
            raise ValueError("Packaged model did not reproduce its recorded validation MAE")
    report = {
        "model_version": loaded.manifest["model_version"],
        "bundle_id": loaded.manifest["bundle_id"],
        "partition": "validation",
        "metrics": metrics,
        "device": "cpu",
        "threads": 1,
        "batch_size": batch_size,
        "release_status": loaded.metadata["release_status"],
        "policy": "No training, test rescoring, API startup or bid submission",
        "historical_charts": "Training histories and baseline comparison reused from package",
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "preview-results.json").write_text(json.dumps(report, indent=2) + "\n")
    with (output / "validation-predictions.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "shipment_id",
                "shipped_at",
                "loaded_miles",
                "target_bid_rpm",
                "predicted_bid_rpm",
                "target_bid_dollars",
                "predicted_bid_dollars",
            ]
        )
        for metadata, distance, actual, prediction in zip(
            validation.metadata, miles, target, predicted
        ):
            writer.writerow(
                [
                    metadata.shipment_id,
                    metadata.shipped_at,
                    distance,
                    actual,
                    prediction,
                    actual * distance,
                    prediction * distance,
                ]
            )
    plot_prediction_diagnostics(target, predicted, miles, loaded.manifest["model_version"], output)
    plot_training_history(training["candidates"], output)
    comparison = json.loads((bundle / "evaluation/comparison.json").read_text())
    plot_model_comparison(comparison, output)
    shutil.make_archive(str(output), "zip", root_dir=output.parent, base_dir=output.name)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", type=Path, default=PROJECT_ROOT / "artifacts/models/freight-rpm-v1"
    )
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/model-preview-v1"
    )
    args = parser.parse_args()
    try:
        report = run_preview(args.bundle, args.dataset, args.output_dir)
    except (OSError, ValueError, RuntimeError, KeyError, AssertionError) as error:
        parser.exit(2, f"Unable to preview packaged model: {error}\n")
    print(json.dumps(report, indent=2))
    print(f"Plots and predictions: {args.output_dir}\nZIP export: {args.output_dir}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
