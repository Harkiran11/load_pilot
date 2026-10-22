"""Validated Huber/AdamW setup and explicit reproducible initialization."""

import math
import os
import random
from dataclasses import asdict, dataclass

import numpy as np
import torch
from torch import nn

from loadpilot_model.models.regressor import FreightRPMRegressor, RegressorConfig


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in [0, 2**32)")


@dataclass(frozen=True)
class TrainingSetupConfig:
    seed: int = 42
    learning_rate: float = 0.003
    weight_decay: float = 0.01
    huber_delta: float = 0.5
    deterministic: bool = True

    def __post_init__(self):
        _check_seed(self.seed)
        if not isinstance(self.deterministic, bool):
            raise ValueError("deterministic must be boolean")
        for name in ("learning_rate", "weight_decay", "huber_delta"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or name != "weight_decay"
                and value == 0
            ):
                raise ValueError(
                    f"{name} must be finite and "
                    + ("nonnegative" if name == "weight_decay" else "positive")
                )

    def to_dict(self) -> dict:
        return asdict(self)


def set_random_seeds(seed: int = 42, *, deterministic: bool = True) -> None:
    """Seed Python, NumPy, and PyTorch before model creation or batch sampling."""
    _check_seed(seed)
    if not isinstance(deterministic, bool):
        raise ValueError("deterministic must be boolean")
    # Set before CUDA execution; preserve an explicitly configured supported workspace.
    if deterministic:
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(deterministic)
    torch.backends.cudnn.deterministic = deterministic
    torch.backends.cudnn.benchmark = not deterministic


class RPMHuberLoss(nn.HuberLoss):
    """Huber mean on matching scalar-per-row RPM tensors; prohibit broadcasting."""

    def __init__(self, delta: float = 0.5):
        TrainingSetupConfig(huber_delta=delta)
        super().__init__(delta=delta, reduction="mean")

    def forward(self, predictions: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        if not isinstance(predictions, torch.Tensor) or not isinstance(targets, torch.Tensor):
            raise TypeError("Predictions and targets must be tensors")
        if predictions.ndim != 1 or predictions.shape != targets.shape or predictions.numel() == 0:
            raise ValueError(
                "Huber loss requires matching nonempty (batch,) predictions and targets"
            )
        if not torch.is_floating_point(predictions) or predictions.dtype != targets.dtype:
            raise ValueError("Predictions and targets must have matching floating-point dtype")
        if predictions.device != targets.device:
            raise ValueError("Predictions and targets must be on the same device")
        if (
            not torch.isfinite(predictions).all()
            or not torch.isfinite(targets).all()
            or not (targets > 0).all()
        ):
            raise ValueError("Predictions must be finite and RPM targets finite and positive")
        return super().forward(predictions, targets)


@dataclass
class TrainingComponents:
    model: FreightRPMRegressor
    criterion: RPMHuberLoss
    optimizer: torch.optim.AdamW
    config: TrainingSetupConfig


def build_training_components(
    model_config: RegressorConfig,
    setup_config: TrainingSetupConfig | None = None,
    *,
    device: str | torch.device = "cpu",
) -> TrainingComponents:
    """Initialize seeded components only; never load records, fit preprocessing, or train."""
    config = setup_config if setup_config is not None else TrainingSetupConfig()
    set_random_seeds(config.seed, deterministic=config.deterministic)
    model = FreightRPMRegressor(model_config).to(device)
    criterion = RPMHuberLoss(delta=config.huber_delta)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    return TrainingComponents(model, criterion, optimizer, config)
