from collections import Counter

from schema.dispatch.dispatch_policy import DispatchRejection, DispatchUnitRejection

# Stable codes remain suitable for filtering; messages/actions can be displayed
# directly by clients. Group aliases that describe the same recovery step.
REASONS = {}


def register(codes, message, action):
    for code in codes.split():
        REASONS[code] = (message, action)


register('order_missing_from_board', 'The order is no longer present on the board.',
         'Refresh the order board and choose a currently published offer.')
register('order_not_open', 'The order is not open for dispatch.',
         'Refresh its lifecycle status; choose an open offer instead of resubmitting.')
register('offer_expired', 'The offer has expired in simulated time.',
         'Refresh the live offers and choose an unexpired order.')
register('offer_expiry_unknown', 'The offer expiration time is unknown.',
         'Sync the order expiration from the board before retrying.')
register('maximum_weight_exceeded', 'The load exceeds the configured weight limit.',
         'Choose a load within the weight limit; correct the source weight if it is inaccurate.')
register('weight_unknown_or_invalid', 'A positive load weight is required.',
         'Sync a valid weight in pounds from the source order before dispatching.')
register('minimum_margin_usd_not_met minimum_margin_pct_not_met',
         'The expected margin is below the configured minimum.',
         'For an open bid, raise the entered rate and evaluate again; otherwise choose a higher-paying offer or lower-cost route.')
register('expected_margin_unknown', 'Expected margin cannot be verified from the available cost and payout inputs.',
         'Resolve the missing cost, payout, or selected-unit location inputs and evaluate again.')
register('entered_bid_required entered_bid_invalid', 'A positive entered bid is required to evaluate this open-bid order.',
         'Enter the proposed bid_rate and request a new evaluation.')
register('offered_rate_unknown_or_invalid zero_payout_percentage_undefined', 'The offered payout is missing, invalid, or zero.',
         'Sync a positive fixed-rate payout from the board or choose another offer.')
register('cost_per_mile_unknown_or_invalid loaded_cost_unknown_or_invalid loaded_distance_unknown_or_invalid', 'Required loaded-cost or cost-per-mile data is missing or invalid.',
         'Sync valid cost inputs; loaded cost requires estimated_cost or distance_miles and cost_per_mile.')
register('maximum_deadhead_exceeded pickup_move_exceeds_limit projected_pickup_move_exceeds_limit',
         'The unit-to-pickup distance exceeds the configured maximum.',
         'Choose an offer closer to the board-selectable unit or wait for a verified closer unit position.')
register('deadhead_distance_unknown deadhead_distance_unknown_or_invalid', 'The empty travel distance cannot be verified.',
         'Sync valid pickup coordinates and the selected unit location or confirmed prior delivery destination.')
register('booking_times_unknown pickup_time_unknown delivery_time_unknown', 'Pickup and delivery timestamps are required.',
         'Sync both booking timestamps from the source order before retrying.')
register('invalid_pickup_delivery_window', 'Delivery must be later than pickup.',
         'Correct the source booking times and sync the order again.')
register('insufficient_pickup_lead_time', 'Pickup is too soon or already past in simulated time.',
         'Choose a later pickup that meets the configured minimum lead time.')
register('cannot_reach_pickup_in_time', 'The unit cannot reach pickup by the scheduled time.',
         'Choose a later pickup or an offer closer to the unit; include travel and the configured pickup buffer.')
register('occupied_window_unknown', 'The full travel, shipment, and turnaround window cannot be verified.',
         'Sync pickup/delivery times and the projected unit position before reserving the unit.')
register('departure_in_past', 'The required deadhead departure is already past in simulated time.',
         'Choose a later pickup or a closer unit so deadhead travel can start before pickup.')
register('pickup_arrival_unknown', 'The pickup arrival time cannot be verified.',
         'Sync the unit availability and projected location before evaluating travel time again.')
register('assignment_window_overlap', 'The booking window overlaps an existing truck assignment.',
         'Review conflicting assignment IDs and intervals; choose a non-overlapping pickup/delivery window.')
register('unit_schedule_unavailable', 'A truck cannot be reserved for the full booking window.',
         'Review conflicting assignment IDs and intervals; choose a non-overlapping pickup/delivery window.')
register('assignment_window_reserved', 'Another dispatch submission already holds this unit/window.',
         'Wait for that action to reconcile, then refresh availability; do not submit a duplicate reservation.')
register('unit_status_unavailable unit_unavailable_at_pickup projected_unit_unavailable',
         'The unit is unavailable or off duty at pickup.',
         'Refresh fleet status and choose an offer with a verified available unit.')
register('unit_available_after_pickup unit_availability_unknown unit_available_after_departure', 'The unit availability is unknown or later than pickup.',
         'Sync availability or choose pickup after the unit becomes available.')
register('unit_not_board_selectable future_terminal_not_yet_board_selectable',
         'The requested unit cannot currently be selected by the board at this pickup hub.',
         'Use the current board-style candidate or wait for re-homing to appear in a fresh fleet snapshot.')
register('unit_missing_from_board', 'No present board unit can be verified for this offer.',
         'Sync the current fleet roster and reevaluate an offer with available capacity.')
register('unit_snapshot_stale_or_unknown terminal_snapshot_stale', 'The fleet snapshot is too old or its sync time is unknown.',
         'Complete a fresh fleet snapshot sync before retrying; a preview does not refresh the board.')
register('no_terminal_capacity_for_window no_available_units_at_pickup_terminal',
         'No verified pickup-terminal unit is available for the requested booking window.',
         'Review roster availability, active assignments, and pending holds; choose another window or pickup hub.')
register('no_feasible_units', 'Pickup-terminal trucks are blocked by different constraints.',
         'Review the per-truck rejection reasons, then choose a suitable truck, pickup hub, or booking window.')
register('terminal_capacity_unknown', 'The pickup terminal capacity has not been verified.',
         'Sync the terminal roster and capacity counts before dispatching.')
register('terminal_capacity_discrepancy', 'Board capacity counts or terminal membership disagree with the local roster.',
         'Run a fresh fleet reconciliation and resolve the reported counts or hub membership before retrying.')
register('unknown_origin_terminal_code pickup_terminal_unresolved', 'The pickup terminal cannot be resolved from the current fleet hubs.',
         'Correct the origin hub code or sync the missing terminal; off-pin origins require valid coordinates.')
register('pickup_coordinates_unknown_or_invalid', 'Pickup coordinates are missing or invalid.',
         'Sync latitude between -90 and 90 and longitude between -180 and 180 from the source order.')
register('available_unit_coordinates_unknown_or_invalid unit_coordinates_unknown_or_invalid projected_location_unknown',
         'A potential board-selected unit has no verified position.',
         'Sync valid unit coordinates and confirmed delivery destinations before retrying.')
register('fleet_projection_time_unknown projection_before_fleet_snapshot',
         'The fleet snapshot cannot support a projection at the requested pickup time.',
         'Sync the fleet and simulation clock together, then choose pickup at or after the snapshot time.')
register('projected_location_unavailable projected_pickup_terminal_mismatch prior_assignment_destination_unverified booked_destination_unknown projected_destination_unknown unresolved_submission_location',
         'The unit projected location or pickup hub cannot be verified.',
         'Reconcile earlier submissions and confirmed delivery destinations, then refresh the fleet projection.')


def rejection_detail(code, check, context):
    message, action = REASONS.get(code, (
        'The dispatcher could not verify this local eligibility check.',
        'Refresh order and fleet data and review the reported check and context before retrying.',
    ))
    return DispatchRejection(code=code, check=check, message=message,
                             suggested_action=action, context=context)


def unit_rejection(route, common, max_deadhead, miles):
    """Explain the truck's blocker before reporting consequences of that blocker."""
    reasons = set(route.rejection_reasons)
    if 'unit_status_unavailable' in reasons:
        code, check = 'no_available_units_at_pickup_terminal', 'capacity_passed'
    elif route.conflicting_assignments or 'assignment_window_overlap' in reasons:
        code, check = 'assignment_window_overlap', 'schedule_passed'
    elif route.conflicting_reservations or 'assignment_window_reserved' in reasons:
        code, check = 'assignment_window_reserved', 'schedule_passed'
    elif not route.available_at_pickup or 'unit_availability_unknown' in reasons:
        code, check = 'no_available_units_at_pickup_terminal', 'capacity_passed'
    else:
        # Unknown positions do not prove excessive travel. Prefer the specific
        # projection failure over derivative distance/arrival/profit failures.
        location_codes = (
            'prior_assignment_destination_unverified', 'unresolved_submission_location',
            'booked_destination_unknown', 'projected_destination_unknown',
            'fleet_projection_time_unknown', 'projection_before_fleet_snapshot',
            'projected_pickup_terminal_mismatch', 'unit_coordinates_unknown_or_invalid',
            'projected_location_unknown', 'projected_unit_unavailable',
        )
        code = next((item for item in location_codes if item in reasons), None)
        check = 'location_passed'
        if code is None and reasons & {'pickup_move_exceeds_limit', 'projected_pickup_move_exceeds_limit'}:
            code, check = 'pickup_move_exceeds_limit', 'deadhead_passed'
        # The legacy unit_available_after_pickup code also covers availability
        # after *departure*. available_at_pickup above distinguishes those cases.
        if code is None and reasons & {'unit_available_after_departure', 'unit_available_after_pickup'}:
            code, check = 'cannot_reach_pickup_in_time', 'timing_passed'
        if code is None:
            code, check = 'unit_schedule_unavailable', 'schedule_passed'
    return rejection_detail(code, check, common | {
        'selection_scope': 'unit',
        'unit_id': route.unit_id, 'board_unit_id': route.board_unit_id,
        'unit_number': route.unit_number, 'unit_rejection_reasons': route.rejection_reasons,
        'deadhead_miles': miles, 'max_deadhead_miles': max_deadhead,
        'available_at_pickup': route.available_at_pickup,
        'conflicting_assignments': [item.model_dump(mode='json') for item in route.conflicting_assignments],
        'conflicting_reservations': [item.model_dump(mode='json') for item in route.conflicting_reservations],
    })


def set_primary_rejection(result, routing, common):
    """Select a causal headline; retain all failed checks for auditing."""
    distances = {item.projection.unit_id: item.projected_deadhead_miles for item in routing.chaining_units}
    for route in routing.units:
        if not route.eligible:
            miles = distances.get(route.unit_id, route.distance_miles)
            result.unit_rejections.append(DispatchUnitRejection(
                unit_id=route.unit_id, board_unit_id=route.board_unit_id, unit_number=route.unit_number,
                primary_rejection=unit_rejection(route, common, result.policy.max_deadhead_miles, miles),
                rejection_reasons=route.rejection_reasons, distance_miles=miles,
                available_at_pickup=route.available_at_pickup,
                conflicting_assignments=route.conflicting_assignments,
                conflicting_reservations=route.conflicting_reservations,
            ))
    details = {item.code: item for item in result.rejection_details}
    # Offer/input and snapshot failures are independent of truck selection.
    priority = (
        'order_missing_from_board', 'order_not_open', 'offer_expired', 'offer_expiry_unknown',
        'maximum_weight_exceeded', 'weight_unknown_or_invalid',
        'unknown_origin_terminal_code', 'pickup_terminal_unresolved',
        'pickup_coordinates_unknown_or_invalid', 'pickup_time_unknown', 'delivery_time_unknown',
        'booking_times_unknown', 'invalid_pickup_delivery_window', 'insufficient_pickup_lead_time',
        'terminal_capacity_unknown', 'terminal_snapshot_stale', 'unit_snapshot_stale_or_unknown',
    )
    primary = next((details[code] for code in priority if code in details), None)
    if primary is None and result.rejection_details:
        selected = next((item for item in routing.units if item.unit_id == result.candidate_unit_id), None)
        if result.candidate_unit_id is not None and result.candidate_unit_id != routing.routing_candidate_unit_id:
            primary = details.get('unit_not_board_selectable')
        elif selected is not None and not selected.eligible:
            primary = next(item.primary_rejection for item in result.unit_rejections if item.unit_id == selected.unit_id)
            primary = primary.model_copy(update={'context': primary.context | {'selection_scope': 'board_selected_unit'}})
        elif result.candidate_unit_id is None:
            blocked = result.unit_rejections
            if not routing.units:
                primary = details.get('no_available_units_at_pickup_terminal')
                if primary is not None:
                    primary = primary.model_copy(update={'context': primary.context | {
                        'selection_scope': 'pickup_terminal', 'units_considered': 0,
                        'unit_rejection_counts': {}, 'blocked_unit_ids': [],
                    }})
            elif len(blocked) == len(routing.units):
                counts = Counter(item.primary_rejection.code for item in blocked)
                checks = {item.primary_rejection.check for item in blocked}
                if len(counts) == 1:
                    first = blocked[0].primary_rejection
                    code, check = first.code, first.check
                elif checks == {'schedule_passed'}:
                    code, check = 'unit_schedule_unavailable', 'schedule_passed'
                else:
                    code, check = 'no_feasible_units', 'capacity_passed'
                primary = rejection_detail(code, check, common | {
                    'selection_scope': 'pickup_terminal', 'units_considered': len(routing.units),
                    'unit_rejection_counts': dict(sorted(counts.items())),
                    'blocked_unit_ids': [item.unit_id for item in blocked],
                })
            # Even one unknown current position prevents predicting the board's
            # choice; another locally feasible truck does not fix that ambiguity.
            if primary is None:
                primary = details.get('available_unit_coordinates_unknown_or_invalid')
        if primary is None:
            fallback = (
                'assignment_window_overlap', 'assignment_window_reserved',
                'unit_not_board_selectable', 'maximum_deadhead_exceeded', 'pickup_move_exceeds_limit',
                'cannot_reach_pickup_in_time', 'departure_in_past',
                'terminal_capacity_discrepancy',
                'minimum_margin_usd_not_met', 'minimum_margin_pct_not_met',
                'entered_bid_required', 'entered_bid_invalid', 'expected_margin_unknown',
            )
            primary = next((details[code] for code in fallback if code in details), None)
        if primary is None:
            primary = min(result.rejection_details, key=lambda item: (item.check, item.code))
    if primary is not None:
        result.primary_rejection = primary
        result.checks[primary.check] = False
        result.rejection_reasons = [primary.code, *[code for code in result.rejection_reasons if code != primary.code]]
        result.rejection_details = [primary, *[item for item in result.rejection_details if item.code != primary.code]]


def add_feedback(result, shipment, routing, capacity, unit, reason_checks):
    common = {
        'external_id': shipment.external_id, 'unit_id': result.candidate_unit_id,
        'pickup_terminal_code': routing.pickup_terminal_code,
    }
    contexts = {
        'offer_open_unexpired': {'status': shipment.status, 'expires_at': shipment.expires_at,
                                'simulated_now': result.evaluated_at_sim},
        'weight_passed': {'weight_lbs': shipment.weight_lbs, 'max_weight_lbs': result.policy.max_weight_lbs},
        'pricing_passed': {'expected_margin_usd': result.economics.gross_profit,
                           'expected_margin_pct': result.margin_pct_for_policy,
                           'min_expected_margin_usd': result.policy.min_expected_margin_usd,
                           'min_expected_margin_pct': result.policy.min_expected_margin_pct,
                           'missing_inputs': result.economics.missing_reasons},
        'deadhead_passed': {'deadhead_miles': result.deadhead_miles,
                            'max_deadhead_miles': result.policy.max_deadhead_miles},
        'timing_passed': {'pickup_at': shipment.pickup_at, 'delivery_at': shipment.delivery_at,
                          'earliest_pickup_at': result.earliest_pickup_at,
                          'occupied_window': result.occupied_window.model_dump(mode='json') if result.occupied_window else None,
                          'simulated_now': result.evaluated_at_sim,
                          'min_pickup_lead_minutes': result.policy.min_pickup_lead_minutes,
                          'pickup_buffer_minutes': result.policy.pickup_buffer_minutes},
        'location_passed': {'projected_terminal_code': result.projection.terminal_code if result.projection else None,
                            'unit_last_synced_at': unit.last_synced_at if unit else None,
                            'max_snapshot_age_seconds': result.policy.max_fleet_snapshot_age_seconds,
                            'unverified_assignment_ids': result.projection.unverified_assignment_ids if result.projection else [],
                            'uncertain_action_ids': result.projection.uncertain_action_ids if result.projection else []},
        'schedule_passed': {'pickup_at': shipment.pickup_at, 'delivery_at': shipment.delivery_at,
                            'conflicting_assignments': [], 'conflicting_reservations': [],
                            'unit_status': unit.status if unit else None,
                            'unit_available_at': unit.available_at if unit else None},
        'capacity_passed': {'capacity_units_for_window': result.capacity_units_for_window,
                            'snapshot_checked_at': capacity.checked_at if capacity else None,
                            'max_snapshot_age_seconds': result.policy.max_fleet_snapshot_age_seconds,
                            'board_total_units': capacity.board_total_units if capacity else None,
                            'board_available_units': capacity.board_available_units if capacity else None,
                            'board_assigned_units': capacity.board_assigned_units if capacity else None},
    }
    selected = next((item for item in routing.units if item.unit_id == result.candidate_unit_id), None)
    if selected:
        for field in ('conflicting_assignments', 'conflicting_reservations'):
            contexts['schedule_passed'][field] = [item.model_dump(mode='json') for item in getattr(selected, field)]
    for code, check in reason_checks.items():
        result.rejection_details.append(rejection_detail(code, check, common | contexts[check]))
    set_primary_rejection(result, routing, common)
    for warning in result.capacity_warnings:
        warning.update({
            'message': 'Fleet reconciliation reported a capacity or roster discrepancy.',
            'suggested_action': 'Refresh the fleet snapshot and review the reported roster and count differences.',
            'blocking': warning.get('kind') in ('roster_terminal_mismatch', 'terminal_missing_from_snapshot', 'preflight_capacity_discrepancy'),
            'pickup_terminal_code': routing.pickup_terminal_code,
        })
    for detail in result.rejection_details:
        if detail.check == 'capacity_passed':
            result.capacity_warnings.append({
                'kind': detail.code, 'message': detail.message, 'suggested_action': detail.suggested_action,
                'blocking': True, 'context': detail.context,
            })
