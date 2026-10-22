"""Forward-only freight scoring with an explicit opt-in for final test evaluation."""

import numpy as np
import torch
from torch.utils.data import DataLoader, SequentialSampler


def evaluate_regressor(
    model, loader: DataLoader, criterion, *, allow_test: bool = False
) -> tuple[dict, np.ndarray]:
    """Score frozen weights; test requires explicit final-evaluation authorization."""
    dataset = loader.dataset
    if dataset.partition not in {"train", "validation"} and not (
        dataset.partition == "test" and allow_test is True
    ):
        raise ValueError("This scorer does not authorize reserved test evaluation")
    if not isinstance(loader.sampler, SequentialSampler):
        raise ValueError("Scoring requires saved row order, not shuffled batches")
    device = next(model.parameters()).device
    previous_mode = model.training
    predictions, loss_sum = [], 0.0
    try:
        model.eval()
        with torch.inference_mode():
            for features, targets in loader:
                output = model(features.to(device))
                loss = criterion(output, targets.to(device))
                predictions.append(output.cpu().numpy().copy())
                loss_sum += loss.item() * len(targets)
    finally:
        model.train(previous_mode)
    predicted = np.concatenate(predictions).astype(np.float64)
    if (
        len(predicted) != len(dataset)
        or not np.isfinite(predicted).all()
        or not (predicted > 0).all()
    ):
        raise ValueError("Scoring must cover every row with a finite positive prediction")
    errors = predicted - dataset.target_rpm_exact
    return {
        "row_count": len(dataset),
        "huber_loss": loss_sum / len(dataset),
        "mae_rpm": float(np.abs(errors).mean()),
        "mae_dollars": float(np.abs(errors * dataset.loaded_miles).mean()),
    }, predicted
