import os
from datetime import timedelta
from decimal import Context, Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext

from dotenv import dotenv_values
from pydantic import ValidationError

from schema.dispatch.occupancy import OccupancyPolicy, OccupiedWindow
from service.shared.database import PROJECT_ROOT
from service.shared.errors import ProductError
from service.orders.expiration import utc

CONTEXT = Context(prec=50, rounding=ROUND_HALF_UP)
MICROSECONDS_PER_HOUR = Decimal('3600000000')
MICROSECONDS_PER_MINUTE = Decimal('60000000')


def occupancy_policy():
    config = dotenv_values(PROJECT_ROOT / '.env')
    try:
        return OccupancyPolicy(**{field: os.environ.get(key, config.get(key, default)) for field, key, default in (
            ('assumed_travel_speed_mph', 'ASSUMED_TRAVEL_SPEED_MPH', '45'),
            ('turnaround_minutes', 'TURNAROUND_MINUTES', '30'),
            ('pickup_buffer_minutes', 'PICKUP_BUFFER_MINUTES', '0'),
        )})
    except ValidationError as exc:
        raise ProductError(503, 'ASSUMED_TRAVEL_SPEED_MPH must be 1–90 and TURNAROUND_MINUTES/PICKUP_BUFFER_MINUTES must be 0–1440, all finite') from exc


def hours_between(start, end):
    if start is None or end is None or utc(end) <= utc(start):
        return None
    delta = utc(end) - utc(start)
    with localcontext(CONTEXT):
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
        return seconds / 3600


def minutes_delta(minutes):
    # Round outwards: timestamps must never under-reserve fractional travel time.
    with localcontext(CONTEXT):
        return timedelta(microseconds=int((minutes * MICROSECONDS_PER_MINUTE).to_integral_value(rounding=ROUND_CEILING)))


def occupied_window(shipment, miles, policy):
    duration = hours_between(getattr(shipment, 'pickup_at', None), getattr(shipment, 'delivery_at', None))
    if duration is None or not isinstance(miles, Decimal) or not miles.is_finite() or miles < 0:
        return None
    with localcontext(CONTEXT):
        travel = miles / policy.assumed_travel_speed_mph
        turnaround = policy.turnaround_minutes / 60
        buffer = policy.pickup_buffer_minutes / 60
        departure_microseconds = (miles * MICROSECONDS_PER_HOUR / policy.assumed_travel_speed_mph
                                  + policy.pickup_buffer_minutes * MICROSECONDS_PER_MINUTE)
        departure_delta = timedelta(microseconds=int(departure_microseconds.to_integral_value(rounding=ROUND_CEILING)))
        return OccupiedWindow(
            occupied_from=utc(shipment.pickup_at) - departure_delta,
            occupied_until=utc(shipment.delivery_at) + minutes_delta(policy.turnaround_minutes),
            pickup_buffer_hours=buffer, deadhead_travel_hours=travel, shipment_duration_hours=duration,
            turnaround_hours=turnaround, occupied_hours=travel + duration + turnaround,
        )
