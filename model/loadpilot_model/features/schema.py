"""Explicit pre-bid feature projection shared by preparation and future inference."""

import math
from collections.abc import Mapping

NUMERIC_FEATURES = (
    "distance_miles",
    "weight_lbs",
    "origin_latitude",
    "origin_longitude",
    "destination_latitude",
    "destination_longitude",
)
CATEGORICAL_FEATURES = ("lane_id", "origin_state", "destination_state")
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET_COLUMN = "target_bid_rpm"
METADATA_COLUMNS = ("shipment_id", "shipped_at")
MISSING_FEATURE_TOKENS = ("", "na", "n/a", "null", "none", "nan")
FEATURE_PROVENANCE = {
    "distance_miles": {"units": "loaded miles", "source": "posted shipment.distance_miles"},
    "weight_lbs": {"units": "pounds", "source": "posted shipment.weight_lbs"},
    **{
        name: {"units": "degrees", "source": f"posted shipment.{name}"}
        for name in NUMERIC_FEATURES
        if name not in {"distance_miles", "weight_lbs"}
    },
    "lane_id": {
        "units": "directional terminal-code pair",
        "source": "historical lane_id; live origin_terminal_code-destination_terminal_code",
    },
    "origin_state": {"units": "state code", "source": "posted shipment.origin_state"},
    "destination_state": {"units": "state code", "source": "posted shipment.destination_state"},
}
EXCLUDED_FIELDS = {
    "shipment_id": "Identifier retained only for audit/join metadata",
    "won": "Post-bid award outcome",
    "margin_estimate": "Outcome-derived estimated margin",
    "bid_rate": "Submitted bid used only to construct the target",
    "target_bid_rpm": "Training label, never an input",
    "shipped_at": "Actual shipment time; metadata for chronological splitting only",
    "season": "Derived from actual shipment time, not a confirmed pre-bid timestamp",
    "offered_rate": "Historical offer unavailable on live open bids",
    "market_rpm": "Derived from historical offered price in this export",
    "all_in_rpm": "Duplicate offered-price-derived RPM",
    "fuel_surcharge_rpm": "Fully missing export helper",
    "booked_lead_hours": "Fully missing; booking-time availability unconfirmed",
    "cost_per_mile": "Pre-bid availability/provenance not yet approved",
    "estimated_cost": "Pre-bid availability/provenance not yet approved",
}


def build_features(record: Mapping[str, object]) -> dict[str, str]:
    """Ignore all fields outside the allowlist, including new or renamed outcomes."""
    features = {}
    for name in FEATURE_COLUMNS:
        raw = record.get(name)
        value = "" if raw is None else str(raw).strip()
        if name == "lane_id" and not value:
            origin = str(record.get("origin_terminal_code") or "").strip().upper()
            destination = str(record.get("destination_terminal_code") or "").strip().upper()
            if (
                origin.lower() not in MISSING_FEATURE_TOKENS
                and destination.lower() not in MISSING_FEATURE_TOKENS
            ):
                value = f"{origin}-{destination}"
        if value.lower() in MISSING_FEATURE_TOKENS:
            raise ValueError(f"Missing required model feature: {name}")
        if name in NUMERIC_FEATURES:
            if isinstance(raw, bool):
                raise ValueError(f"Invalid numeric model feature: {name}")
            try:
                number = float(value)
            except ValueError as error:
                raise ValueError(f"Invalid numeric model feature: {name}") from error
            if not math.isfinite(number):
                raise ValueError(f"Nonfinite model feature: {name}")
            if name in {"distance_miles", "weight_lbs"} and number <= 0:
                raise ValueError(f"Nonpositive model feature: {name}")
            if "latitude" in name and abs(number) > 90:
                raise ValueError(f"Invalid latitude model feature: {name}")
            if "longitude" in name and abs(number) > 180:
                raise ValueError(f"Invalid longitude model feature: {name}")
        features[name] = value.upper() if name in CATEGORICAL_FEATURES else value
    return features


def build_model_inputs(rows: list[dict[str, str]]) -> tuple[list[dict], list[dict], list[dict]]:
    """Separate X, y, and split/audit metadata without reading submitted bids."""
    features, targets, metadata = [], [], []
    for record in rows:
        feature_row = build_features(record)
        try:
            target = float(record[TARGET_COLUMN])
        except (KeyError, ValueError) as error:
            raise ValueError("Missing or invalid target_bid_rpm") from error
        if not math.isfinite(target) or target <= 0:
            raise ValueError("target_bid_rpm must be finite and positive")
        features.append(feature_row)
        targets.append({TARGET_COLUMN: record[TARGET_COLUMN]})
        metadata.append({name: record.get(name, "") for name in METADATA_COLUMNS})
    return features, targets, metadata
