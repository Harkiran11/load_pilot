"""Standalone training-curve artifacts from recorded train/validation histories."""

import os
from pathlib import Path

from loadpilot_model.paths import PROJECT_ROOT


def plot_training_history(results: list[dict], output_dir: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / "artifacts/.matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / "artifacts/.cache"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, len(results), figsize=(6 * len(results), 4), squeeze=False)
    for axis, result in zip(axes[0], results):
        history = result["history"]
        epochs = [row["epoch"] for row in history]
        axis.plot(epochs, [row["train_huber"] for row in history], label="Training (batch average)")
        axis.plot(epochs, [row["validation_huber"] for row in history], label="Validation")
        axis.axvline(
            result["best_epoch"],
            color="black",
            linestyle="--",
            linewidth=1,
            label=f"Best epoch {result['best_epoch']}",
        )
        axis.set_title(
            f"{result['candidate_id']} · learning rate {result['setup_config']['learning_rate']}"
        )
        axis.set_xlabel("Epoch")
        axis.set_ylabel(f"Mean Huber loss (delta {result['setup_config']['huber_delta']})")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8)
    figure.suptitle("Freight RPM training · chronological validation · training selection")
    figure.tight_layout()
    figure.savefig(output_dir / "loss-curves.png", dpi=160)
    figure.savefig(output_dir / "loss-curves.svg")
    plt.close(figure)


def plot_prediction_diagnostics(target_rpm, predicted_rpm, miles, version, output_dir):
    """Export validation price scatter plots and signed prediction errors."""
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / "artifacts/.matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / "artifacts/.cache"))
    import matplotlib
    import numpy as np

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    error = predicted_rpm - target_rpm
    figure, axes = plt.subplots(2, 2, figsize=(11, 8))
    for axis, target, predicted, units in (
        (axes[0, 0], target_rpm, predicted_rpm, "USD / loaded mile"),
        (axes[0, 1], target_rpm * miles, predicted_rpm * miles, "USD"),
    ):
        axis.scatter(target, predicted, alpha=0.65, s=22, color="#397ca8")
        lower, upper = min(target.min(), predicted.min()), max(target.max(), predicted.max())
        padding = (upper - lower) * 0.05 or 0.1
        bounds = (lower - padding, upper + padding)
        axis.plot(bounds, bounds, color="black", linestyle="--", label="Perfect agreement")
        axis.set_xlim(bounds)
        axis.set_ylim(bounds)
        axis.set_xlabel(f"Historical submitted bid ({units})")
        axis.set_ylabel(f"Predicted bid ({units})")
        axis.legend(fontsize=8)
    axes[1, 0].hist(error, bins=20, color="#397ca8", edgecolor="white")
    axes[1, 0].axvline(0, color="black", linestyle="--")
    axes[1, 0].set_xlabel("Predicted minus historical RPM (USD / loaded mile)")
    axes[1, 0].set_ylabel("Shipment count")
    axes[1, 1].scatter(miles, error * miles, alpha=0.65, s=22, color="#397ca8")
    axes[1, 1].axhline(0, color="black", linestyle="--")
    axes[1, 1].set_xlabel("Loaded distance (miles)")
    axes[1, 1].set_ylabel("Predicted minus historical bid (USD)")
    for axis in axes.flat:
        axis.grid(alpha=0.2)
        axis.set_axisbelow(True)
    figure.suptitle(
        f"{version} · {len(target_rpm)} validation shipments\n"
        f"MAE {np.abs(error).mean():.5f} RPM · ${np.abs(error * miles).mean():.2f}"
    )
    figure.text(
        0.5,
        0.015,
        "Historical price prediction · advisory only · release unverified",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 0.95))
    for extension in ("png", "svg", "pdf"):
        figure.savefig(output_dir / f"prediction-diagnostics.{extension}", dpi=180)
    plt.close(figure)


def plot_model_comparison(report: dict, output_dir: Path) -> None:
    """Plot saved aggregate errors without training or inference."""
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / "artifacts/.matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / "artifacts/.cache"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(11, 7))
    methods = ("global_median", "lane_median", "pytorch")
    labels = ("Global median", "Lane + global", "PyTorch")
    for row, partition in enumerate(("validation", "test")):
        for column, metric in enumerate(("mae_rpm", "mae_dollars")):
            axis = axes[row, column]
            values = [report["partitions"][partition]["overall"][m][metric] for m in methods]
            bars = axis.bar(labels, values, color=("#8c99a8", "#dd9844", "#397ca8"))
            axis.bar_label(bars, fmt="%.3f" if column == 0 else "$%.2f", padding=4)
            axis.set_ylim(0, max(values) * 1.2)
            axis.set_ylabel("MAE (USD / loaded mile)" if column == 0 else "MAE (USD)")
            axis.set_title(f"{partition.capitalize()} · {metric.removeprefix('mae_').upper()}")
            axis.grid(axis="y", alpha=0.2)
            axis.set_axisbelow(True)
    figure.suptitle("Historical submitted-bid error · frozen model and training-only baselines")
    figure.text(
        0.5,
        0.015,
        "Lower is better · market quote unavailable · lane gates unverified",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.04, 1, 0.96))
    figure.savefig(output_dir / "baseline-comparison.png", dpi=160)
    figure.savefig(output_dir / "baseline-comparison.svg")
    plt.close(figure)
