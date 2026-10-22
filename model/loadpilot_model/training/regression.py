"""Early-stopped freight training with best checkpoints and validation-only selection."""

import argparse
import csv
import hashlib
import json
import os
import platform
from pathlib import Path

import numpy as np
import torch

from loadpilot_model.data.dataset import FreightDataset, create_dataloader
from loadpilot_model.evaluation.regression import evaluate_regressor
from loadpilot_model.models.regressor import RegressorConfig
from loadpilot_model.paths import PROJECT_ROOT
from loadpilot_model.training.early_stopping import EarlyStopping
from loadpilot_model.training.setup import TrainingSetupConfig, build_training_components


def positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def fit_candidate(
    train: FreightDataset,
    validation: FreightDataset,
    setup: TrainingSetupConfig,
    *,
    epochs: int = 300,
    batch_size: int = 64,
    device: str = "cpu",
    patience: int = 25,
    min_delta: float = 1e-4,
    output_dir: Path | None = None,
):
    """Monitor validation Huber only, save each new minimum, and restore best weights."""
    positive_integer(epochs, "epochs")
    positive_integer(batch_size, "batch_size")
    stopper = EarlyStopping(patience=patience, min_delta=min_delta)
    if train.partition != "train" or validation.partition != "validation":
        raise ValueError("Optimization requires train data and selection requires validation data")
    for field in ("source_sha256", "split_manifest_sha256", "preprocessor_sha256", "feature_names"):
        if getattr(train, field) != getattr(validation, field):
            raise ValueError("Training/validation dataset lineage or preprocessing differs")
    if train.metadata[-1].shipped_at >= validation.metadata[0].shipped_at:
        raise ValueError("Training timestamps must precede validation timestamps")
    model_config = RegressorConfig(input_features=len(train.feature_names))
    components = build_training_components(model_config, setup, device=device)
    train_loader = create_dataloader(train, batch_size=batch_size, seed=setup.seed)
    validation_loader = create_dataloader(validation, batch_size=batch_size, seed=setup.seed)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=False)
    history, best_state = [], None
    fields = [
        "epoch",
        "train_huber",
        "validation_huber",
        "validation_mae_rpm",
        "validation_mae_dollars",
        "best_epoch",
        "bad_epochs",
        "checkpoint_updated",
        "stop",
    ]
    log = (
        (output_dir / "history.csv").open("x", newline="", encoding="utf-8") if output_dir else None
    )
    writer = csv.DictWriter(log, fieldnames=fields) if log else None
    if writer:
        writer.writeheader()
        log.flush()
    try:
        for epoch in range(1, epochs + 1):
            components.model.train()
            loss_sum, rows = 0.0, 0
            for features, targets in train_loader:
                features, targets = features.to(device), targets.to(device)
                components.optimizer.zero_grad(set_to_none=True)
                output = components.model(features)
                loss = components.criterion(output, targets)
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite training loss")
                loss.backward()
                for parameter in components.model.parameters():
                    if parameter.grad is not None and not torch.isfinite(parameter.grad).all():
                        raise ValueError("Nonfinite training gradient")
                components.optimizer.step()
                loss_sum += loss.item() * len(targets)
                rows += len(targets)
            if rows != len(train):
                raise ValueError("Training must retain every selected training row")
            metrics, _ = evaluate_regressor(
                components.model, validation_loader, components.criterion
            )
            is_best = stopper.update(epoch, metrics["huber_loss"])
            if is_best:
                best_state = {
                    name: value.detach().cpu().clone()
                    for name, value in components.model.state_dict().items()
                }
                if output_dir is not None:
                    checkpoint = {
                        "model_state_dict": best_state,
                        "optimizer_state_dict": components.optimizer.state_dict(),
                        "model_config": model_config.to_dict(),
                        "setup_config": setup.to_dict(),
                        "epoch": epoch,
                        "candidate_id": output_dir.name,
                        "validation": metrics,
                        "monitor": "validation_huber",
                        "source_sha256": train.source_sha256,
                        "split_manifest_sha256": train.split_manifest_sha256,
                        "preprocessor_sha256": train.preprocessor_sha256,
                        "encoded_feature_order": train.feature_names,
                    }
                    temporary = output_dir / "best.pt.tmp"
                    torch.save(checkpoint, temporary)
                    os.replace(temporary, output_dir / "best.pt")
            row = {
                "epoch": epoch,
                "train_huber": loss_sum / rows,
                "validation_huber": metrics["huber_loss"],
                "validation_mae_rpm": metrics["mae_rpm"],
                "validation_mae_dollars": metrics["mae_dollars"],
                "best_epoch": stopper.best_epoch,
                "bad_epochs": stopper.bad_epochs,
                "checkpoint_updated": is_best,
                "stop": stopper.should_stop,
            }
            history.append(row)
            if writer:
                writer.writerow(row)
                log.flush()
            if output_dir is not None and (epoch == 1 or epoch % 25 == 0 or stopper.should_stop):
                print(
                    f"  epoch {epoch}: train Huber={row['train_huber']:.6f}, "
                    f"validation Huber={row['validation_huber']:.6f}, best={stopper.best_epoch}",
                    flush=True,
                )
            if stopper.should_stop:
                break
    finally:
        if log:
            log.close()
    components.model.load_state_dict(best_state)
    metrics, predicted = evaluate_regressor(
        components.model, validation_loader, components.criterion
    )
    return (
        components.model,
        {
            "model_config": model_config.to_dict(),
            "setup_config": setup.to_dict(),
            "epochs": len(history),
            "max_epochs": epochs,
            "best_epoch": stopper.best_epoch,
            "stop_reason": "patience_exhausted" if stopper.should_stop else "max_epochs",
            "stopped_early": len(history) < epochs,
            "early_stopping": {
                "monitor": "validation_huber",
                "patience": patience,
                "min_delta": min_delta,
            },
            "batch_size": batch_size,
            "last_epoch_train_huber": history[-1]["train_huber"],
            "validation": metrics,
            "history": history,
        },
        predicted,
    )


def select_candidate(results: list[dict]) -> dict:
    if not results:
        raise ValueError("At least one candidate result is required")
    for result in results:
        if not all(
            np.isfinite(result["validation"][name]) and result["validation"][name] >= 0
            for name in ("mae_rpm", "mae_dollars")
        ):
            raise ValueError("Selection requires finite nonnegative validation MAE")
    return min(
        results,
        key=lambda result: (
            result["validation"]["mae_rpm"],
            result["validation"]["mae_dollars"],
            result["candidate_id"],
        ),
    )


def run_tuning(
    dataset_dir: Path,
    output_dir: Path,
    *,
    learning_rates=(0.003, 0.001),
    epochs: int = 300,
    batch_size: int = 64,
    seed: int = 42,
    threads: int = 1,
    device: str = "cpu",
    patience: int = 25,
    min_delta: float = 1e-4,
) -> dict:
    positive_integer(epochs, "epochs")
    positive_integer(batch_size, "batch_size")
    positive_integer(threads, "threads")
    EarlyStopping(patience=patience, min_delta=min_delta)
    if not learning_rates or len(set(learning_rates)) != len(learning_rates):
        raise ValueError("Specify at least one distinct candidate learning rate")
    settings = [TrainingSetupConfig(seed=seed, learning_rate=rate) for rate in learning_rates]
    if output_dir.exists():
        raise ValueError("Training output exists; use a new versioned output directory")
    torch.set_num_threads(threads)
    train = FreightDataset(dataset_dir, "train")
    validation = FreightDataset(dataset_dir, "validation")
    plan = {
        "epochs_per_candidate": epochs,
        "batch_size": batch_size,
        "device": device,
        "threads": threads,
        "candidates": [config.to_dict() for config in settings],
        "selection_rule": "Validation RPM MAE of each candidate's lowest-Huber checkpoint, "
        "then dollar MAE, then candidate ID",
        "early_stopping": {
            "monitor": "validation_huber",
            "patience": patience,
            "min_delta": min_delta,
            "checkpoint_rule": "Save every strict validation Huber minimum; "
            "patience resets only after improvement greater than min_delta",
        },
        "training_rows": len(train),
        "validation_rows": len(validation),
        "source_sha256": train.source_sha256,
        "split_manifest_sha256": train.split_manifest_sha256,
        "preprocessor_sha256": train.preprocessor_sha256,
        "train_array_sha256": train.array_sha256,
        "validation_array_sha256": validation.array_sha256,
        "encoded_feature_order": train.feature_names,
        "versions": {
            "torch": torch.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
        },
        "test_policy": "No test files loaded, no final test evaluation",
        "checkpoint_policy": "Restore minimum validation Huber weights; test reserved",
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "run-plan.json").write_text(json.dumps(plan, indent=2, allow_nan=False) + "\n")
    results, predictions = [], {}
    for index, config in enumerate(settings, 1):
        candidate_id = f"candidate-{index:02}"
        print(f"Training {candidate_id}: lr={config.learning_rate}, epochs={epochs}", flush=True)
        _, result, predicted = fit_candidate(
            train,
            validation,
            config,
            epochs=epochs,
            batch_size=batch_size,
            device=device,
            patience=patience,
            min_delta=min_delta,
            output_dir=output_dir / candidate_id,
        )
        result["candidate_id"] = candidate_id
        results.append(result)
        predictions[candidate_id] = predicted
        print(
            f"{candidate_id}: validation RPM MAE={result['validation']['mae_rpm']:.6f}, "
            f"USD MAE={result['validation']['mae_dollars']:.2f}",
            flush=True,
        )
    selected = select_candidate(results)
    selected_id = selected["candidate_id"]
    artifact = output_dir / "selected-best.pt"
    artifact.write_bytes((output_dir / selected_id / "best.pt").read_bytes())
    report = {
        "plan": plan,
        "candidates": results,
        "selected_candidate": selected_id,
        "selected_best_epoch": selected["best_epoch"],
        "selection_partition": "validation",
        "fit_partition": "train",
        "selected_weights_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "status": "validation-selected research model; not approved for live pricing",
    }
    (output_dir / "tuning-results.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    from loadpilot_model.evaluation.plots import plot_training_history

    plot_training_history(results, output_dir)
    with (output_dir / "validation-predictions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["shipment_id", "shipped_at", "loaded_miles", "target_bid_rpm", "predicted_bid_rpm"]
        )
        for metadata, miles, target, prediction in zip(
            validation.metadata,
            validation.loaded_miles,
            validation.target_rpm_exact,
            predictions[selected_id],
        ):
            writer.writerow([metadata.shipment_id, metadata.shipped_at, miles, target, prediction])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/training/freight-earlystop-v1"
    )
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--device", default="cpu", choices=("cpu", "mps", "cuda"))
    parser.add_argument("--learning-rates", type=float, nargs="+", default=[0.003, 0.001])
    args = parser.parse_args()
    try:
        report = run_tuning(
            args.dataset,
            args.output_dir,
            learning_rates=args.learning_rates,
            epochs=args.epochs,
            batch_size=args.batch_size,
            seed=args.seed,
            threads=args.threads,
            device=args.device,
            patience=args.patience,
            min_delta=args.min_delta,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(2, f"Unable to train freight candidates: {error}\n")
    print(
        f"Selected {report['selected_candidate']}; outputs: {args.output_dir}; "
        "test remains reserved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
