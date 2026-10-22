"""One final chronological test evaluation of a validation-selected checkpoint."""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from loadpilot_model.data.dataset import FreightDataset, create_dataloader
from loadpilot_model.evaluation.regression import evaluate_regressor
from loadpilot_model.models.regressor import FreightRPMRegressor, RegressorConfig
from loadpilot_model.paths import PROJECT_ROOT
from loadpilot_model.training.setup import RPMHuberLoss


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_final_test(dataset_dir: Path, run_dir: Path, baseline_policy: Path) -> dict:
    """Freeze the plan before test access; reject reuse of this training run."""
    marker = run_dir / "final-test-plan.json"
    if marker.exists():
        raise ValueError("Final test already reserved/evaluated for this run; reuse saved results")
    results = json.loads((run_dir / "tuning-results.json").read_text())
    weights_path = run_dir / "selected-best.pt"
    weights_hash = sha256(weights_path)
    if weights_hash != results["selected_weights_sha256"]:
        raise ValueError("Selected checkpoint checksum mismatch")
    if results["selection_partition"] != "validation" or results["fit_partition"] != "train":
        raise ValueError("Final model must be selected on validation and fitted on train")
    selected = next(
        row for row in results["candidates"] if row["candidate_id"] == results["selected_candidate"]
    )
    checkpoint = torch.load(weights_path, map_location="cpu", weights_only=True)
    if (
        checkpoint["epoch"] != selected["best_epoch"]
        or checkpoint["candidate_id"] != selected["candidate_id"]
        or checkpoint["validation"] != selected["validation"]
    ):
        raise ValueError("Checkpoint differs from validation-selected candidate")
    manifest = json.loads((dataset_dir / "split-report.json").read_text())
    expected = {
        "source_sha256": manifest["source_sha256"],
        "split_manifest_sha256": sha256(dataset_dir / "split-report.json"),
        "preprocessor_sha256": sha256(dataset_dir / "preprocessor.pkl"),
    }
    for name, value in expected.items():
        if checkpoint[name] != value or results["plan"][name] != value:
            raise ValueError("Checkpoint/dataset lineage mismatch")
    if list(checkpoint["encoded_feature_order"]) != manifest["transformed_feature_names"]:
        raise ValueError("Checkpoint feature order mismatch")
    partitions = manifest["partitions"]
    if not partitions["train"]["date_max"] < partitions["validation"]["date_min"]:
        raise ValueError("Training must precede validation")
    if not partitions["validation"]["date_max"] < partitions["test"]["date_min"]:
        raise ValueError("Validation must precede test")
    policy = json.loads(baseline_policy.read_text())
    if (
        policy["source_sha256"] != expected["source_sha256"]
        or policy["dataset_manifest_sha256"] != expected["split_manifest_sha256"]
        or policy["selection_partition"] != "validation"
        or policy["fit_partition"] != "train"
    ):
        raise ValueError("Baseline policy must share training/validation provenance")
    baseline = next(
        row
        for row in policy["validation_metrics"]
        if row["baseline"] == policy["selected_baseline"]
    )
    if baseline["coverage"] != 1 or not all(
        np.isfinite(baseline[key]) and baseline[key] > 0 for key in ("mae_rpm", "mae_dollars")
    ):
        raise ValueError("Release gates require valid full-coverage validation baseline metrics")
    config = RegressorConfig.from_dict(checkpoint["model_config"])
    model = FreightRPMRegressor(config)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    criterion = RPMHuberLoss(delta=checkpoint["setup_config"]["huber_delta"])
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    gates = {
        "minimum_coverage": 1.0,
        "maximum_mae_rpm": baseline["mae_rpm"] * 0.95,
        "maximum_mae_dollars": baseline["mae_dollars"] * 0.95,
        "minimum_improvement_vs_test_baseline_percent": 5.0,
        "maximum_slice_regression_percent": 10.0,
        "minimum_slice_samples": 10,
        "slice_dimensions": ["lane", "distance_band", "season", "weight_band"],
        "sparse_slice_policy": "Report sparse/unseen slices as unverified; no release claim",
        "zero_baseline_error_policy": "Require zero model error; percentage undefined",
        "rationale": "Provisional engineering gates: 5% useful improvement, "
        "full coverage, no >10% regression in sufficiently populated slices; "
        "absolute ceilings use validation baseline only",
    }
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint_sha256": weights_hash,
        "tuning_results_sha256": sha256(run_dir / "tuning-results.json"),
        "baseline_policy_sha256": sha256(baseline_policy),
        **expected,
        "selected_candidate": selected["candidate_id"],
        "selected_epoch": checkpoint["epoch"],
        "partition": "test",
        "batch_size": selected["batch_size"],
        "device": "cpu",
        "threads": 1,
        "gates": gates,
        "policy": "One forward-only pass; no refitting, retuning, or live promotion. "
        "Marker retained on failure; inspect failure before any explicit recovery.",
        "versions": {"torch": str(torch.__version__), "numpy": np.__version__},
    }
    # Exclusive creation is the guard: persisted before opening any test records.
    with marker.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(plan, indent=2, allow_nan=False) + "\n")
    dataset = FreightDataset(dataset_dir, "test", allow_test=True)
    metrics, predicted = evaluate_regressor(
        model,
        create_dataloader(dataset, batch_size=selected["batch_size"]),
        criterion,
        allow_test=True,
    )
    errors = predicted - dataset.target_rpm_exact
    metrics.update(
        {
            "coverage": len(predicted) / len(dataset),
            "mean_signed_error_rpm": float(errors.mean()),
            "mean_signed_error_dollars": float((errors * dataset.loaded_miles).mean()),
        }
    )
    predictions_path = run_dir / "test-predictions.csv"
    with predictions_path.open("x", newline="", encoding="utf-8") as stream:
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
                "error_rpm",
                "error_dollars",
            ]
        )
        for metadata, miles, target, prediction in zip(
            dataset.metadata, dataset.loaded_miles, dataset.target_rpm_exact, predicted
        ):
            writer.writerow(
                [
                    metadata.shipment_id,
                    metadata.shipped_at,
                    miles,
                    target,
                    prediction,
                    target * miles,
                    prediction * miles,
                    prediction - target,
                    (prediction - target) * miles,
                ]
            )
    report = {
        "plan_sha256": sha256(marker),
        "checkpoint_sha256": weights_hash,
        "test_arrays_sha256": dataset.array_sha256,
        "predictions_sha256": sha256(predictions_path),
        "test_date_min": dataset.metadata[0].shipped_at,
        "test_date_max": dataset.metadata[-1].shipped_at,
        "metrics": metrics,
        "absolute_gate_results": {
            "coverage": metrics["coverage"] >= gates["minimum_coverage"],
            "rpm_mae": metrics["mae_rpm"] <= gates["maximum_mae_rpm"],
            "dollar_mae": metrics["mae_dollars"] <= gates["maximum_mae_dollars"],
        },
        "baseline_and_slice_gate_status": "pending section 4.8",
        "release_status": "research only; no live approval",
    }
    with (run_dir / "test-results.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--run-dir", type=Path, default=PROJECT_ROOT / "artifacts/training/freight-earlystop-v1"
    )
    parser.add_argument(
        "--baseline-policy",
        type=Path,
        default=PROJECT_ROOT / "artifacts/baselines/fallback-policy-v1.json",
    )
    args = parser.parse_args()
    try:
        report = evaluate_final_test(args.dataset, args.run_dir, args.baseline_policy)
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as error:
        parser.exit(2, f"Unable to evaluate final test: {error}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
