import math
from decimal import Decimal


def valid_coordinates(latitude, longitude) -> bool:
    if latitude is None or longitude is None:
        return False
    lat, lon = float(latitude), float(longitude)
    return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


def nearest_terminal(terminals, latitude, longitude):
    """Match the board's squared-coordinate nearest-hub rule, with stable ties."""
    if not valid_coordinates(latitude, longitude):
        return None
    candidates = [row for row in terminals if valid_coordinates(row.latitude, row.longitude)]
    return min(candidates, key=lambda row: (
        (float(row.latitude) - float(latitude)) ** 2
        + (float(row.longitude) - float(longitude)) ** 2,
        row.board_terminal_id,
    )) if candidates else None


def pickup_distance_miles(latitude, longitude, pickup_latitude, pickup_longitude) -> Decimal:
    """Unrounded Haversine miles; compare before rounding for display."""
    lat1, lat2 = math.radians(float(latitude)), math.radians(float(pickup_latitude))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(float(pickup_longitude) - float(longitude))
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return Decimal(str(2 * 3958.8 * math.asin(math.sqrt(min(1.0, max(0.0, value))))))
