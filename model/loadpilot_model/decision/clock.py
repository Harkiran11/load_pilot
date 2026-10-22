"""Explicit wall/simulation time mapping; never discover or change the external clock."""

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

CLOCK_RULES_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass
class DecisionClock:
    snapshot: dict | None
    now: datetime
    simulated_now: datetime | None = None
    issues: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.snapshot is None:
            return
        age = (self.now - self.snapshot["observed_at"]).total_seconds()
        if age < 0:
            self.issues.append("FUTURE_CLOCK_SNAPSHOT")
        elif age > self.snapshot["max_snapshot_age_seconds"]:
            self.issues.append("STALE_CLOCK_SNAPSHOT")
        if not self.issues:
            try:
                self.simulated_now = self.snapshot["simulated_at"] + timedelta(
                    seconds=age * self.snapshot["speed"]
                )
            except (OverflowError, ValueError):
                self.issues.append("CLOCK_ARITHMETIC_INVALID")

    def expiry(self, stamp):
        """Convert a domain deadline to real UTC; paused future deadlines have no wall limit."""
        if self.snapshot is None:
            return stamp
        if self.simulated_now is None:
            return None
        if stamp <= self.simulated_now:
            return self.now
        speed = self.snapshot["speed"]
        if speed == 0:
            return None
        try:
            return self.now + timedelta(
                seconds=(stamp - self.simulated_now).total_seconds() / speed
            )
        except (OverflowError, ValueError):
            self.issues.append("CLOCK_ARITHMETIC_INVALID")
            return None

    def input_deadline(self, stamp):
        if self.snapshot is None or self.simulated_now is None:
            return None
        speed = self.snapshot["speed"]
        if speed == 0:
            return None
        age = (self.now - stamp).total_seconds() * speed
        if not math.isfinite(age):
            self.issues.append("CLOCK_ARITHMETIC_INVALID")
            return self.now
        if age > self.snapshot["max_simulated_input_age_seconds"]:
            self.issues.append("STALE_SIMULATION_INPUT")
        try:
            return stamp + timedelta(
                seconds=self.snapshot["max_simulated_input_age_seconds"] / speed
            )
        except (OverflowError, ValueError):
            self.issues.append("CLOCK_ARITHMETIC_INVALID")
            return None

    def snapshot_deadline(self):
        if self.snapshot is None:
            return None
        try:
            return self.snapshot["observed_at"] + timedelta(
                seconds=self.snapshot["max_snapshot_age_seconds"]
            )
        except (OverflowError, ValueError):
            self.issues.append("CLOCK_ARITHMETIC_INVALID")
            return self.now

    def describe(self, valid_until=None):
        simulated_until = None
        if self.simulated_now is not None and valid_until is not None:
            try:
                simulated_until = self.simulated_now + timedelta(
                    seconds=(valid_until - self.now).total_seconds() * self.snapshot["speed"]
                )
            except (OverflowError, ValueError):
                pass  # Wall deadline remains authoritative; never invent a simulation timestamp.
        return {
            "domain": "simulation" if self.snapshot is not None else "wall",
            "clock_id": self.snapshot["clock_id"] if self.snapshot else None,
            "revision": self.snapshot["revision"] if self.snapshot else None,
            "speed": self.snapshot["speed"] if self.snapshot else 1.0,
            "evaluated_simulated_at": self.simulated_now,
            "valid_until_simulated_at": simulated_until,
            "rules_sha256": CLOCK_RULES_SHA256,
        }
