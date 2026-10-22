"""Explicit illustrative policy and backend snapshot; no production defaults."""

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def request():
    return {
        "mode": "live",
        "shipment_id": "shipment-example",
        "unit_id": "unit-example",
        "shipment": {
            "order_type": "open_bid",
            "distance_miles": 1000,
            "weight_lbs": 10000,
            "origin_latitude": 33,
            "origin_longitude": -84,
            "destination_latitude": 33,
            "destination_longitude": -97,
            "lane_id": "ATL-DFW",
            "origin_state": "GA",
            "destination_state": "TX",
        },
        "backend_context": {
            "shipment_id": "shipment-example",
            "unit_id": "unit-example",
            "snapshot_version": "snapshot-example-v1",
            "projected_position": {"latitude": 33, "longitude": -84, "computed_at": NOW},
            "loaded_cost_dollars": 1200,
            "deadhead_cost_dollars": 200,
            "deadhead_miles": 50,
            "costs_observed_at": NOW,
            "unit_observed_at": NOW,
            "order_observed_at": NOW,
            "order_expires_at": NOW + timedelta(hours=1),
            "checks": {
                name: True
                for name in (
                    "unit_available",
                    "commitments_clear",
                    "capacity_compatible",
                    "equipment_compatible",
                    "location_verified",
                    "timing_feasible",
                    "order_open",
                )
            },
        },
        "policy": {
            "policy_version": "live-example-v1",
            "currency": "USD",
            "min_profit_dollars": 200,
            "min_margin_percent": 20,
            "max_bid_dollars": 2500,
            "max_deadhead_miles": 100,
            "max_input_age_seconds": {
                name: 300
                for name in ("unit_position", "unit_availability", "costs", "order_status")
            },
            "max_decision_age_seconds": 60,
            "prediction_timeout_seconds": 1,
            "max_reference_deviation_percent": 25,
            "fallback_mode": "review",
            "unseen_category_action": "review",
            "baseline_id": None,
            "baseline_version": None,
            "min_lane_samples": None,
        },
        "control": {"autopilot_enabled": True, "kill_switch_active": False},
    }


def candidate(price=2000, reference=2000):
    return {
        "pricing_source": "model",
        "predicted_bid_dollars": price,
        "predicted_bid_rpm": price / 1000,
        "baseline_reference": {
            "predicted_bid_dollars": reference,
            "predicted_bid_rpm": reference / 1000,
        },
    }
