"""Package/load frozen freight weights and preprocessing under one model version."""

import argparse
import json
import pickle
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import torch

from loadpilot_model.inference.bundle import (
    InferencePreprocessor,
    digest,
    load_inference_bundle,
    runtime_versions,
    save_inference_bundle,
)
from loadpilot_model.models.regressor import FreightRPMRegressor, RegressorConfig
from loadpilot_model.paths import PROJECT_ROOT

MODEL_CODE = "loadpilot_model/models/regressor.py"


def sha256(path: Path) -> str:
    return digest(path.read_bytes())


def check_version(version: str) -> None:
    if not isinstance(version, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version
    ):
        raise ValueError("Model version must be 1-64 letters, digits, dots, underscores or hyphens")


def bundle_identity(manifest: dict) -> str:
    payload = {key: value for key, value in manifest.items() if key != "bundle_id"}
    return digest(json.dumps(payload, sort_keys=True, allow_nan=False).encode())


@dataclass(frozen=True)
class LoadedFreightModel:
    model: FreightRPMRegressor
    preprocessing: InferencePreprocessor
    metadata: dict
    manifest: dict


def load_model_bundle(path: Path) -> LoadedFreightModel:
    """Load a locally trusted bundle; fail on incompatible or changed artifacts."""
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["schema_version"] != 1 or manifest["bundle_id"] != bundle_identity(manifest):
        raise ValueError("Invalid model bundle manifest/identity")
    check_version(manifest["model_version"])
    # PyTorch's Linux wheels append a platform build tag (e.g. +cu130 or +cpu).
    # We load weights on CPU; require the same release across platforms while
    # retaining exact checks for every other dependency and all artifact hashes.
    expected_runtime = dict(manifest["runtime_versions"])
    if "torch" in expected_runtime:
        expected_runtime["torch"] = expected_runtime["torch"].partition("+")[0]
    actual_runtime = {**runtime_versions(), "torch": str(torch.__version__).partition("+")[0]}
    if expected_runtime != actual_runtime:
        raise ValueError("Model bundle dependency versions differ; use the locked environment")
    if manifest["model_code_sha256"] != sha256(PROJECT_ROOT / MODEL_CODE):
        raise ValueError("Model implementation differs from the saved bundle")
    expected_files = manifest["files_sha256"]
    required = {
        "selected-checkpoint.pt",
        "model-config.json",
        "metadata.json",
        "preprocessing/manifest.json",
        "preprocessing/preprocessor.pkl",
        "preprocessing/feature-schema.json",
        "training/run-plan.json",
        "training/tuning-results.json",
        "evaluation/comparison.json",
        "evaluation/final-test-plan.json",
        "evaluation/test-results.json",
        "baselines/fallback-policy-v1.json",
        "pyproject.toml",
        "uv.lock",
    }
    if not required.issubset(expected_files):
        raise ValueError("Model bundle is missing required artifact entries")
    for name, expected in expected_files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name == "manifest.json":
            raise ValueError("Invalid artifact path in model manifest")
        if sha256(path / relative) != expected:
            raise ValueError(f"Model artifact checksum mismatch: {name}")
    metadata = json.loads((path / "metadata.json").read_text())
    if (
        metadata["model_version"] != manifest["model_version"]
        or metadata["release_status"] != "unverified"
        or metadata["live_submission_enabled"] is not False
    ):
        raise ValueError("Invalid model version or unsupported release state")
    config = RegressorConfig.from_dict(json.loads((path / "model-config.json").read_text()))
    preprocessing = load_inference_bundle(path / "preprocessing")
    checkpoint = torch.load(path / "selected-checkpoint.pt", map_location="cpu", weights_only=True)
    if (
        RegressorConfig.from_dict(checkpoint["model_config"]) != config
        or checkpoint["epoch"] != metadata["selected_epoch"]
        or checkpoint["candidate_id"] != metadata["selected_candidate"]
        or list(checkpoint["encoded_feature_order"]) != metadata["encoded_feature_order"]
        or metadata["encoded_feature_order"] != preprocessing.schema["encoded_feature_order"]
        or metadata["raw_feature_order"] != preprocessing.schema["raw_feature_order"]
        or RegressorConfig.from_dict(metadata["model_config"]) != config
        or config.input_features != len(metadata["encoded_feature_order"])
        or checkpoint["preprocessor_sha256"] != expected_files["preprocessing/preprocessor.pkl"]
        or metadata["preprocessor_sha256"] != checkpoint["preprocessor_sha256"]
        or checkpoint["source_sha256"] != preprocessing.schema["source_sha256"]
        or metadata["source_sha256"] != checkpoint["source_sha256"]
        or checkpoint["split_manifest_sha256"] != metadata["split_manifest_sha256"]
        or metadata["checkpoint_sha256"] != expected_files["selected-checkpoint.pt"]
        or metadata["validation_metrics"] != checkpoint["validation"]
        or metadata["training_setup"] != checkpoint["setup_config"]
    ):
        raise ValueError("Weights, features, preprocessing or metadata are misaligned")
    model = FreightRPMRegressor(config)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    model.eval()
    for parameter in model.parameters():
        if not torch.isfinite(parameter).all():
            raise ValueError("Model bundle contains nonfinite weights")
        parameter.requires_grad_(False)
    return LoadedFreightModel(model, preprocessing, metadata, manifest)


def export_model_bundle(
    dataset: Path,
    run: Path,
    comparison_dir: Path,
    baselines: Path,
    output_root: Path,
    version: str,
) -> dict:
    """Build privately, verify without predictions, then publish the local directory."""
    check_version(version)
    destination = output_root / version
    if destination.exists():
        raise ValueError("Model version already exists; existing bundles are preserved")
    comparison = json.loads((comparison_dir / "comparison.json").read_text())
    if comparison["pricing_gate_status"] != "unverified":
        raise ValueError("This package preserves the current unverified research release state")
    for name, expected in comparison["artifact_sha256"].items():
        if sha256(Path(name)) != expected:
            raise ValueError("Comparison provenance artifact changed")
    for name, expected in comparison["baseline_files_sha256"].items():
        if sha256(baselines / name) != expected:
            raise ValueError("Baseline artifact changed")
    for path, field in (
        (run / "validation-predictions.csv", "validation_predictions_sha256"),
        (run / "test-results.json", "test_results_sha256"),
    ):
        if sha256(path) != comparison[field]:
            raise ValueError("Comparison prediction/evaluation artifact changed")
    tuning = json.loads((run / "tuning-results.json").read_text())
    checkpoint_path = run / "selected-best.pt"
    if sha256(checkpoint_path) != tuning["selected_weights_sha256"]:
        raise ValueError("Selected checkpoint checksum mismatch")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    selected = next(
        c for c in tuning["candidates"] if c["candidate_id"] == tuning["selected_candidate"]
    )
    split = json.loads((dataset / "split-report.json").read_text())
    original_preprocessor = (dataset / "preprocessor.pkl").read_bytes()
    for name, expected in {
        "source_sha256": split["source_sha256"],
        "split_manifest_sha256": sha256(dataset / "split-report.json"),
        "preprocessor_sha256": digest(original_preprocessor),
    }.items():
        if checkpoint[name] != expected or tuning["plan"][name] != expected:
            raise ValueError("Model/dataset provenance mismatch")
    if (
        checkpoint["epoch"] != selected["best_epoch"]
        or checkpoint["candidate_id"] != selected["candidate_id"]
        or checkpoint["validation"] != selected["validation"]
    ):
        raise ValueError("Checkpoint differs from selected training result")
    metadata = {
        "model_version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": "Historical open-bid market-reference RPM; not profit or acceptance",
        "output_units": "USD per loaded mile",
        "total_bid_formula": "predicted_bid_rpm * distance_miles",
        "model_config": checkpoint["model_config"],
        "raw_feature_order": split["feature_columns"],
        "encoded_feature_order": list(checkpoint["encoded_feature_order"]),
        "source_sha256": split["source_sha256"],
        "split_manifest_sha256": checkpoint["split_manifest_sha256"],
        "preprocessor_sha256": checkpoint["preprocessor_sha256"],
        "checkpoint_sha256": sha256(checkpoint_path),
        "training_rows": tuning["plan"]["training_rows"],
        "validation_rows": tuning["plan"]["validation_rows"],
        "partition_dates": {
            name: {key: partition[key] for key in ("date_min", "date_max")}
            for name, partition in split["partitions"].items()
        },
        "selected_candidate": selected["candidate_id"],
        "selected_epoch": checkpoint["epoch"],
        "training_setup": checkpoint["setup_config"],
        "training_epochs_executed": selected["epochs"],
        "early_stopping": selected["early_stopping"],
        "validation_metrics": selected["validation"],
        "test_metrics": comparison["partitions"]["test"]["overall"]["pytorch"],
        "release_status": "unverified",
        "live_submission_enabled": False,
        "operational_policy": comparison["operational_policy"],
        "unverified_test_slice_count": comparison["unverified_test_slice_count"],
        "limitations": [
            "All individual test lanes are sparse; winter/spring absent",
            "Short-haul regression; independent pre-bid market quotes unavailable",
            "No claim of realized profit, guaranteed acceptance or operational readiness",
            "Test consumed; future selection requires a fresh holdout",
        ],
    }
    copies = {
        "selected-checkpoint.pt": checkpoint_path,
        "training/run-plan.json": run / "run-plan.json",
        "training/tuning-results.json": run / "tuning-results.json",
        "training/history.csv": run / selected["candidate_id"] / "history.csv",
        "evaluation/final-test-plan.json": run / "final-test-plan.json",
        "evaluation/test-results.json": run / "test-results.json",
        "evaluation/comparison.json": comparison_dir / "comparison.json",
        "evaluation/overall.csv": comparison_dir / "overall.csv",
        "evaluation/slices.csv": comparison_dir / "slices.csv",
        "pyproject.toml": PROJECT_ROOT / "pyproject.toml",
        "uv.lock": PROJECT_ROOT / "uv.lock",
        **{
            f"baselines/{name}": baselines / name
            for name in (
                "fallback-policy-v1.json",
                "global-median-v1.json",
                "lane-median-v1.json",
                "market-rpm-v1.json",
            )
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{version}-", dir=output_root) as temporary:
        staged = Path(temporary) / version
        staged.mkdir()
        for name, source in copies.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        save_inference_bundle(
            pickle.loads(original_preprocessor),
            split,
            staged / "preprocessing",
            preprocessor_bytes=original_preprocessor,
        )
        for name, value in (
            ("metadata.json", metadata),
            ("model-config.json", checkpoint["model_config"]),
        ):
            (staged / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        manifest = {
            "schema_version": 1,
            "model_version": version,
            "files_sha256": {
                str(p.relative_to(staged)): sha256(p)
                for p in sorted(staged.rglob("*"))
                if p.is_file()
            },
            "runtime_versions": {**runtime_versions(), "torch": str(torch.__version__)},
            "model_code_sha256": sha256(PROJECT_ROOT / MODEL_CODE),
        }
        manifest["bundle_id"] = bundle_identity(manifest)
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        load_model_bundle(staged)
        staged.rename(destination)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--run-dir", type=Path, default=PROJECT_ROOT / "artifacts/training/freight-earlystop-v1"
    )
    parser.add_argument(
        "--comparison-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts/evaluation/model-comparison-v1",
    )
    parser.add_argument("--baselines", type=Path, default=PROJECT_ROOT / "artifacts/baselines")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts/models")
    parser.add_argument("--model-version", default="freight-rpm-v1")
    args = parser.parse_args()
    try:
        manifest = export_model_bundle(
            args.dataset,
            args.run_dir,
            args.comparison_dir,
            args.baselines,
            args.output_root,
            args.model_version,
        )
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as error:
        parser.exit(2, f"Unable to package model: {error}\n")
    print(
        f"Saved {manifest['model_version']} to {args.output_root / args.model_version}; "
        "release remains unverified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
