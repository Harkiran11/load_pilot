"""Read-only operational probes used by Compose (no bookings or training)."""

import json
import os
import sys
from urllib.request import Request, urlopen


def request(path, data=None):
    body = None if data is None else json.dumps(data).encode()
    with urlopen(Request(path, data=body, headers={"Content-Type": "application/json"}),
                 timeout=5) as response:
        return json.load(response)


def check(service):
    if service == "backend":
        base = "http://127.0.0.1:8000"
        assert request(base + "/health")["status"] == "ok"
        status = request(base + "/api/system/status")
        assert status["sim_time_utc"]
        # With no board configured the service is healthy in offline mode.
        board = os.getenv("EXTERNAL_API_URL", "").strip()
        websocket = os.getenv("BOARD_WEBSOCKET_ENABLED", "true").lower() == "true"
        expected = "connected" if (board and websocket) else "disabled"
        assert status["connection_status"] == expected
        assert request(base + "/api/terminals/status")
        assert request(base + "/api/fleet/units?limit=1")["total"] > 0
    elif service == "model":
        base = "http://127.0.0.1:9000"
        request(base + "/openapi.json")
        result = request(base + "/api/v1/predict-bid", {
            "order_type": "open_bid", "distance_miles": 674.2, "weight_lbs": 35678,
            "origin_latitude": 40.10976, "origin_longitude": -82.66231,
            "destination_latitude": 30.3639, "destination_longitude": -82.07184,
            "lane_id": "CMH-JAX", "origin_state": "OH", "destination_state": "FL",
        })
        assert result["pricing_source"] == "model"
        assert result["model_version"] == os.getenv("MODEL_EXPECTED_VERSION", "freight-rpm-v1")
        assert result["predicted_bid_rpm"] > 0 and result["predicted_bid_dollars"] > 0
    else:
        raise ValueError("Unknown service")


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"Readiness failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
