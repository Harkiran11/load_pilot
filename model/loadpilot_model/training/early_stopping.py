"""Validation-loss patience and exact best-epoch tracking."""

import math
from dataclasses import dataclass, field


@dataclass
class EarlyStopping:
    patience: int = 25
    min_delta: float = 1e-4
    best_loss: float = field(default=math.inf, init=False)
    best_epoch: int | None = field(default=None, init=False)
    bad_epochs: int = field(default=0, init=False)
    last_epoch: int = field(default=0, init=False)
    _patience_loss: float = field(default=math.inf, init=False, repr=False)

    def __post_init__(self):
        if (
            isinstance(self.patience, bool)
            or not isinstance(self.patience, int)
            or self.patience < 1
        ):
            raise ValueError("patience must be a positive integer")
        if (
            isinstance(self.min_delta, bool)
            or not isinstance(self.min_delta, (int, float))
            or not math.isfinite(self.min_delta)
            or self.min_delta < 0
        ):
            raise ValueError("min_delta must be finite and nonnegative")

    @property
    def should_stop(self) -> bool:
        return self.bad_epochs >= self.patience

    def update(self, epoch: int, validation_loss: float) -> bool:
        """Return true for any new minimum; patience resets only for significant improvement."""
        if self.should_stop:
            raise ValueError("Early stopping has already triggered")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != self.last_epoch + 1:
            raise ValueError("Epochs must be sequential positive integers starting at one")
        if (
            isinstance(validation_loss, bool)
            or not isinstance(validation_loss, (int, float))
            or not math.isfinite(validation_loss)
            or validation_loss < 0
        ):
            raise ValueError("Validation loss must be finite and nonnegative")
        self.last_epoch = epoch
        is_best = validation_loss < self.best_loss
        if is_best:
            self.best_loss, self.best_epoch = validation_loss, epoch
        if validation_loss < self._patience_loss - self.min_delta:
            self._patience_loss, self.bad_epochs = validation_loss, 0
        else:
            self.bad_epochs += 1
        return is_best
