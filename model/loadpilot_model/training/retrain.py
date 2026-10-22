"""Manually triggered, versioned research retraining; never deploys or scores a test set."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loadpilot_model.paths import PROJECT_ROOT

DATA_FILES = ("split-report.json", "preprocessor.pkl") + tuple(
    f"{partition}/{filename}"
    for partition in ("train", "validation")
    for filename in ("features.csv", "targets.csv", "metadata.csv", "arrays.npz")
)


def create_candidate(
    dataset: Path,
    output: Path,
    version: str,
    protected_manifest: Path,
    protected_sha256: str,
    *,
    execute: bool = False,
    epochs: int = 300,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
        raise ValueError("Candidate version must be a safe identifier")
    if isinstance(epochs, bool) or not isinstance(epochs, int) or epochs <= 0:
        raise ValueError("epochs must be a positive integer")
    protected_bytes = protected_manifest.read_bytes()
    if hashlib.sha256(protected_bytes).hexdigest() != protected_sha256:
        raise ValueError("Protected original split manifest checksum mismatch")
    protected_ids = set(json.loads(protected_bytes)["partitions"]["test"]["shipment_ids"])
    # Capture exact train/validation bytes, not a mutable path or future test features/labels.
    captured = {name: (dataset / name).read_bytes() for name in DATA_FILES}
    report = json.loads(captured["split-report.json"])
    train = report["partitions"]["train"]
    validation = report["partitions"]["validation"]
    training_ids, validation_ids = set(train["shipment_ids"]), set(validation["shipment_ids"])
    if protected_ids & (training_ids | validation_ids):
        raise ValueError("Original final-test shipments cannot enter candidate training/validation")
    if training_ids & validation_ids or not train["date_max"] < validation["date_min"]:
        raise ValueError("Candidate train/validation must be distinct and chronological")
    candidate_test = report["partitions"].get("test")
    if candidate_test is not None and (
        set(candidate_test["shipment_ids"]) & (training_ids | validation_ids)
        or not validation["date_max"] < candidate_test["date_min"]
    ):
        raise ValueError("Candidate test metadata must remain disjoint and chronological")
    hashes = {name: hashlib.sha256(contents).hexdigest() for name, contents in captured.items()}
    dataset_version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    target = output / version
    target.mkdir(parents=True, exist_ok=False)
    snapshot = target / "dataset"
    for name, contents in captured.items():
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    plan = {
        "schema_version": 1,
        "candidate_version": version,
        "dataset_version": dataset_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files_sha256": hashes,
        "source_sha256": report["source_sha256"],
        "dataset_origin": report.get("dataset_origin", "historical_csv"),
        "synthetic": report.get("synthetic", False),
        "timestamp_source": report.get("timestamp_source", "shipped_at"),
        "label_policy": report.get("label_policy"),
        "protected_original_split_sha256": protected_sha256,
        "protected_original_test_count": len(protected_ids),
        "epochs": epochs,
        "execution_requested": execute,
        "status": "planned",
        "test_scored": False,
        "deployed": False,
        "limitations": [
            "Research candidate only; validation selection does not grant release approval.",
            "Source provenance and new independent evaluation windows need human review.",
            "No automatic backend ingestion, scheduling or production bundle replacement.",
        ],
    }
    plan_path = target / "candidate.json"

    def save():
        plan_path.write_text(json.dumps(plan, indent=2, allow_nan=False) + "\n")

    save()
    if execute:
        try:
            from loadpilot_model.training.regression import run_tuning

            result = run_tuning(snapshot, target / "training", epochs=epochs)
            plan.update(
                status="validation_selected",
                selected_weights_sha256=result["selected_weights_sha256"],
                selected_candidate=result["selected_candidate"],
            )
        except Exception:
            plan["status"] = "failed"
            save()
            raise
        save()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts/retraining")
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--execute", action="store_true", help="Train; otherwise only save a plan")
    args = parser.parse_args()
    # Pin original test IDs to the frozen deployed model's split identity.
    bundle = PROJECT_ROOT / "artifacts/models/freight-rpm-v1"
    manifest = json.loads((bundle / "manifest.json").read_text())
    metadata = (bundle / "metadata.json").read_bytes()
    if hashlib.sha256(metadata).hexdigest() != manifest["files_sha256"]["metadata.json"]:
        parser.error("Frozen model metadata checksum mismatch")
    plan = create_candidate(
        args.dataset,
        args.output_root,
        args.version,
        PROJECT_ROOT / "artifacts/dataset/split-report.json",
        json.loads(metadata)["split_manifest_sha256"],
        execute=args.execute,
        epochs=args.epochs,
    )
    print(f"Candidate {args.version}: {plan['status']}; dataset {plan['dataset_version']}.")


if __name__ == "__main__":
    main()
