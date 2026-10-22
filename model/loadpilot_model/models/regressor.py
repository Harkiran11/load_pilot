"""Small tabular MLP producing one positive bid RPM per shipment."""

import math
from dataclasses import asdict, dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class RegressorConfig:
    input_features: int
    hidden_sizes: tuple[int, ...] = (32, 16)
    min_rpm: float = 1e-4

    def __post_init__(self):
        if (
            isinstance(self.input_features, bool)
            or not isinstance(self.input_features, int)
            or self.input_features < 1
        ):
            raise ValueError("input_features must be a positive integer")
        if (
            not isinstance(self.hidden_sizes, tuple)
            or not self.hidden_sizes
            or any(
                isinstance(width, bool) or not isinstance(width, int) or width < 1
                for width in self.hidden_sizes
            )
        ):
            raise ValueError("hidden_sizes must be a nonempty tuple of positive integers")
        if (
            isinstance(self.min_rpm, bool)
            or not isinstance(self.min_rpm, (int, float))
            or not math.isfinite(self.min_rpm)
            or self.min_rpm <= 0
        ):
            raise ValueError("min_rpm must be finite and positive")
        floor = torch.tensor(self.min_rpm, dtype=torch.float32)
        if not torch.isfinite(floor) or floor <= 0:
            raise ValueError("min_rpm must remain finite and positive in float32")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, config: dict) -> "RegressorConfig":
        fields = dict(config)
        if "hidden_sizes" in fields:
            fields["hidden_sizes"] = tuple(fields["hidden_sizes"])
        return cls(**fields)


class FreightRPMRegressor(nn.Module):
    """Consume (batch, encoded features) and return (batch,) USD per loaded mile."""

    def __init__(self, config: RegressorConfig):
        super().__init__()
        self.config = config
        layers = []
        previous = config.input_features
        for width in config.hidden_sizes:
            layers.extend([nn.Linear(previous, width, dtype=torch.float32), nn.ReLU()])
            previous = width
        layers.extend([nn.Linear(previous, 1, dtype=torch.float32), nn.Softplus()])
        self.network = nn.Sequential(*layers)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        if not isinstance(features, torch.Tensor):
            raise TypeError("features must be a torch.Tensor")
        if features.ndim != 2 or features.shape[1] != self.config.input_features:
            raise ValueError(f"Expected features with shape (batch, {self.config.input_features})")
        if features.dtype != next(self.parameters()).dtype:
            raise ValueError(
                "Feature dtype must match the model parameter dtype (float32 by default)"
            )
        if not torch.isfinite(features).all():
            raise ValueError("Model inputs must be finite")
        predictions = self.network(features).squeeze(-1) + self.config.min_rpm
        if not torch.isfinite(predictions).all():
            raise ValueError("Model produced nonfinite RPM predictions")
        return predictions
