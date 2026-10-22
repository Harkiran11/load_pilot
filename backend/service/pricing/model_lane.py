import re

from sqlalchemy import select

from schema.pricing.price_analysis import ModelLaneResolution, ModelShipmentFeatures
from schema.terminal.terminal import Terminal
from service.shared.geography import nearest_terminal

MISSING_CODES = {'', 'NA', 'N/A', 'NULL', 'NONE', 'NAN', 'UNKNOWN', 'UNDEFINED'}


def model_shipment_features(session, shipment):
    fields = {name: getattr(shipment, name) for name in (
        'distance_miles', 'weight_lbs', 'origin_latitude', 'origin_longitude',
        'destination_latitude', 'destination_longitude', 'origin_state', 'destination_state')}
    for name in ('distance_miles', 'weight_lbs', 'origin_latitude', 'origin_longitude',
                 'destination_latitude', 'destination_longitude'):
        fields[name] = float(fields[name]) if fields[name] is not None else None
    lane = resolve_model_lane(session, shipment)
    return ModelShipmentFeatures(**fields, lane_id=lane.lane_id).model_dump(mode='json'), lane


def terminal_code(value):
    code = (value or '').strip().upper()
    if code in MISSING_CODES or not re.fullmatch(r'[A-Z0-9_]+', code):
        return None
    return code


def resolve_model_lane(session, shipment, override=None):
    """Resolve model features without changing board-owned shipment geography."""
    if override is not None:
        return ModelLaneResolution(lane_id=override, source='request')
    codes, sources, warnings = {}, {}, []
    terminals = None
    for side in ('origin', 'destination'):
        code = terminal_code(getattr(shipment, f'{side}_terminal_code'))
        source = 'board_terminal_code' if code else 'missing'
        if code is None:
            terminal_id = getattr(shipment, f'{side}_terminal_id')
            linked = session.get(Terminal, terminal_id) if terminal_id else None
            code = terminal_code(linked.code) if linked else None
            if code:
                source = 'linked_terminal'
        if code is None:
            if terminals is None:
                terminals = [row for row in session.scalars(select(Terminal)) if terminal_code(row.code)]
            nearest = nearest_terminal(terminals, getattr(shipment, f'{side}_latitude'),
                                       getattr(shipment, f'{side}_longitude'))
            if nearest:
                code, source = terminal_code(nearest.code), 'nearest_terminal'
                warnings.append(f'Model lane {side} uses nearest terminal {code}; this is a geographic proxy, not a board terminal assignment.')
        codes[side], sources[side] = code, source
    lane = f"{codes['origin']}-{codes['destination']}" if all(codes.values()) else None
    source = ('missing' if lane is None else 'nearest_terminal_codes' if 'nearest_terminal' in sources.values()
              else 'linked_terminal_codes' if 'linked_terminal' in sources.values() else 'board_terminal_codes')
    return ModelLaneResolution(lane_id=lane, source=source, origin_terminal_code=codes['origin'],
                               destination_terminal_code=codes['destination'], terminal_sources=sources, warnings=warnings)
