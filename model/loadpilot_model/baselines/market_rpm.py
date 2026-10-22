"""Use an approved, timestamped pre-bid market quote as a direct RPM baseline."""

import argparse
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loadpilot_model.baselines.lane_median import normalize_lane
from loadpilot_model.paths import PROJECT_ROOT

BASELINE_ID = "prebid_market_rpm"
EXCLUDED_SOURCES = ("historical_csv.market_rpm", "historical_csv.all_in_rpm")


def aware_timestamp(value: str | datetime) -> datetime:
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return stamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class MarketRPMBaseline:
    approved_sources: tuple[str, ...] = ()
    max_age_seconds: float | None = None

    def __post_init__(self):
        if any(
            not isinstance(source, str)
            or not source.strip()
            or source != source.strip()
            or source in EXCLUDED_SOURCES
            for source in self.approved_sources
        ) or len(set(self.approved_sources)) != len(self.approved_sources):
            raise ValueError(
                "Approved sources must be unique independent pre-bid source identifiers"
            )
        age = self.max_age_seconds
        if age is None and not self.approved_sources:
            return
        if (
            isinstance(age, bool)
            or not isinstance(age, (int, float))
            or not math.isfinite(age)
            or age <= 0
        ):
            raise ValueError("A finite positive max_age_seconds policy is required")

    def predict(
        self, quote: Mapping | None, *, decision_at: str | datetime, shipment_id: str, lane_id: str
    ) -> dict:
        """Return quote RPM unchanged, or explicit unavailability; no implicit fallback."""
        result = {
            "baseline_id": BASELINE_ID,
            "status": "unavailable",
            "predicted_bid_rpm": None,
            "units": "USD per loaded mile",
            "reason": None,
        }

        def unavailable(reason):
            return {**result, "reason": reason}

        if not self.approved_sources:
            return unavailable("NO_APPROVED_MARKET_SOURCE")
        if not isinstance(quote, Mapping):
            return unavailable("MISSING_MARKET_QUOTE")
        source = quote.get("source_id")
        if source in EXCLUDED_SOURCES:
            return unavailable("OFFER_DERIVED_REFERENCE")
        if source not in self.approved_sources:
            return unavailable("UNAPPROVED_MARKET_SOURCE")
        if not isinstance(quote.get("quote_id"), str) or not quote["quote_id"].strip():
            return unavailable("MISSING_QUOTE_ID")
        if (
            not isinstance(shipment_id, str)
            or not shipment_id.strip()
            or quote.get("shipment_id") != shipment_id
        ):
            return unavailable("SHIPMENT_MISMATCH")
        try:
            if normalize_lane(quote.get("lane_id")) != normalize_lane(lane_id):
                return unavailable("LANE_MISMATCH")
        except ValueError:
            return unavailable("LANE_MISMATCH")
        if quote.get("currency") != "USD":
            return unavailable("CURRENCY_MISMATCH")
        if quote.get("rate_basis") != "loaded_mile":
            return unavailable("RATE_BASIS_MISMATCH")
        raw = quote.get("market_rpm")
        try:
            rpm = float(raw)
        except (TypeError, ValueError):
            return unavailable("INVALID_MARKET_RPM")
        if isinstance(raw, bool) or not math.isfinite(rpm) or rpm <= 0:
            return unavailable("INVALID_MARKET_RPM")
        try:
            decision = aware_timestamp(decision_at)
            observed = aware_timestamp(quote.get("observed_at"))
            available = aware_timestamp(quote.get("available_at"))
        except (TypeError, ValueError):
            return unavailable("INVALID_MARKET_TIMESTAMP")
        if observed > available:
            return unavailable("INCONSISTENT_MARKET_TIMESTAMPS")
        if available > decision:
            return unavailable("NOT_AVAILABLE_AT_BID")
        if (decision - observed).total_seconds() > self.max_age_seconds:
            return unavailable("STALE_MARKET_QUOTE")
        return {
            **result,
            "status": "available",
            "predicted_bid_rpm": rpm,
            "reason": "VALID_PREBID_MARKET_QUOTE",
            "source_id": source,
            "quote_id": quote["quote_id"],
            "shipment_id": shipment_id,
            "lane_id": normalize_lane(lane_id),
            "observed_at": observed.isoformat(),
            "available_at": available.isoformat(),
            "decision_at": decision.isoformat(),
        }


def load_market_rpm(path: Path) -> MarketRPMBaseline:
    artifact = json.loads(path.read_text())
    if (
        artifact["schema_version"] != 1
        or artifact["baseline_id"] != BASELINE_ID
        or artifact["units"] != "USD per loaded mile"
    ):
        raise ValueError("Unsupported pre-bid market baseline artifact")
    return MarketRPMBaseline(tuple(artifact["approved_sources"]), artifact["max_age_seconds"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/baselines/market-rpm-v1.json"
    )
    args = parser.parse_args()
    try:
        report = json.loads((args.dataset / "split-report.json").read_text())
        artifact = {
            "schema_version": 1,
            "baseline_id": BASELINE_ID,
            "units": "USD per loaded mile",
            "formula": "predicted_bid_rpm = approved_prebid_quote.market_rpm; no fitting",
            "approved_sources": [],
            "max_age_seconds": None,
            "source_sha256": report["source_sha256"],
            "current_dataset_status": "unavailable",
            "reason": "Offer-derived CSV helpers; no independent pre-bid market feed supplied",
            "excluded_sources": EXCLUDED_SOURCES,
            "required_quote_fields": [
                "source_id",
                "quote_id",
                "shipment_id",
                "lane_id",
                "market_rpm",
                "currency",
                "rate_basis",
                "observed_at",
                "available_at",
            ],
            "availability_policy": "observed_at <= available_at <= decision_at; timezone-aware; "
            "observed quote age <= configured max_age_seconds",
            "registration_policy": "Only a trusted adapter with reviewed independent provenance "
            "may register sources and issue observation/availability timestamps",
            "unavailable_policy": "Return no RPM; caller may apply a separately configured "
            "global/lane fallback or REVIEW",
            "heldout_policy": "No validation/test files read or metrics calculated",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Unable to save market baseline contract: {error}\n")
    print(
        "Market baseline implemented; current dataset unavailable: no approved pre-bid market feed."
    )
    print(f"Saved contract to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
