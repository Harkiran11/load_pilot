# Database schema

Canonical data dictionary for the dispatcher database. Alembic migrations and the
SQLAlchemy models are verified against this guide by `tests/test_database.py`
(`test_migration_matches_guide_and_models`).

Tags: `UK` single-column unique key, `FK` foreign key.

    AUTOPILOT_RUNS {
    UUID run_id
    TEXT status UK
    TEXT mode
    JSON settings
    DATETIME started_at_sim
    DATETIME deadline_at_sim
    DATETIME stopped_at_sim
    DATETIME last_attempt_at_sim
    DATETIME last_clock_sim_at
    DATETIME last_clock_synced_at_wall
    DATETIME started_at_wall
    DATETIME deadline_at_wall
    DATETIME stopped_at_wall
    TEXT stop_reason
    DATETIME last_attempt_at_wall
    INTEGER orders_reserved
    NUMERIC estimated_loss_usd
    NUMERIC pending_estimated_loss_usd
    INTEGER consecutive_errors
    }

    HISTORICAL_SHIPMENTS {
    UUID historical_shipment_id
    TEXT source_record_key UK
    TEXT lane_id
    TEXT origin_state
    TEXT destination_state
    NUMERIC origin_latitude
    NUMERIC origin_longitude
    NUMERIC destination_latitude
    NUMERIC destination_longitude
    TEXT order_type
    NUMERIC distance_miles
    INTEGER weight_lbs
    NUMERIC offered_rate
    NUMERIC bid_rate
    BOOLEAN won
    NUMERIC cost_per_mile
    NUMERIC estimated_cost
    NUMERIC margin_estimate
    DATETIME shipped_at
    }

    OBSERVED_COHORTS {
    UUID cohort_id
    TEXT name
    TEXT strategy
    DATETIME period_start_sim
    DATETIME period_end_sim
    TEXT fleet_context
    NUMERIC available_unit_hours
    TEXT exposure_evidence
    JSON policy_snapshot
    DATETIME created_at_wall
    }

    PREDICTION_OBSERVATIONS {
    UUID observation_id
    UUID request_id
    TEXT external_id
    BOOLEAN synthetic
    DATETIME started_at_wall
    DATETIME finished_at_wall
    JSON request
    JSON context
    JSON result
    TEXT response_body
    INTEGER http_status
    TEXT error_code
    JSON executions
    }

    SIMULATION_CLOCK {
    INTEGER clock_id
    DATETIME wall_time_utc
    DATETIME sim_time_utc
    NUMERIC simulation_speed
    TEXT note
    DATETIME last_synced_at
    }

    TERMINALS {
    UUID terminal_id
    BIGINTEGER board_terminal_id UK
    TEXT code UK
    TEXT name
    TEXT city
    TEXT state
    NUMERIC latitude
    NUMERIC longitude
    INTEGER seeded_unit_capacity
    DATETIME last_synced_at
    }

    FLEET_ALLOCATION_PLANS {
    DATETIME expires_at_sim
    UUID plan_id
    UUID run_id UK FK
    TEXT status
    TEXT snapshot_fingerprint
    JSON result
    DATETIME created_at_wall
    DATETIME expires_at_wall
    DATETIME finished_at_wall
    }

    FLEET_CAPACITY {
    UUID terminal_id FK
    INTEGER board_total_units
    INTEGER board_available_units
    INTEGER board_assigned_units
    INTEGER local_total_units
    INTEGER local_available_units
    INTEGER local_assigned_units
    JSON discrepancies
    DATETIME as_of_sim_time
    DATETIME checked_at
    }

    FLEET_UNITS {
    UUID unit_id
    BIGINTEGER board_unit_id UK
    UUID home_terminal_id FK
    TEXT unit_number UK
    TEXT employee_code UK
    TEXT first_name
    TEXT last_name
    TEXT status
    NUMERIC current_latitude
    NUMERIC current_longitude
    DATETIME available_at
    DATETIME last_synced_at
    DATETIME board_missing_at
    }

    OBSERVED_COMPARISONS {
    UUID comparison_id
    UUID baseline_id FK
    UUID candidate_id FK
    TEXT status
    TEXT comparability_notes
    JSON metrics
    TEXT evidence_fingerprint
    DATETIME created_at_wall
    DATETIME reviewed_at_wall
    TEXT reviewer
    TEXT review_notes
    }

    SHIPMENTS {
    JSON financial_outcome
    UUID observed_cohort_id FK
    DATETIME shipment_history_deleted_at
    DATETIME bid_history_deleted_at
    DATETIME board_retired_at
    UUID shipment_id
    TEXT external_id UK
    BIGINTEGER board_order_id
    TEXT order_type
    TEXT status
    TEXT origin_terminal_code
    TEXT destination_terminal_code
    UUID origin_terminal_id FK
    UUID destination_terminal_id FK
    TEXT origin_city
    TEXT origin_state
    NUMERIC origin_latitude
    NUMERIC origin_longitude
    TEXT destination_city
    TEXT destination_state
    NUMERIC destination_latitude
    NUMERIC destination_longitude
    NUMERIC distance_miles
    INTEGER weight_lbs
    NUMERIC offered_rate
    NUMERIC cost_per_mile
    NUMERIC estimated_cost
    NUMERIC estimated_deadhead_miles
    NUMERIC estimated_deadhead_cost
    NUMERIC estimated_total_cost
    DATETIME pickup_at
    DATETIME delivery_at
    DATETIME expires_at
    DATETIME board_created_at
    DATETIME bid_decision_at
    NUMERIC winning_bid_rate
    BIGINTEGER board_assigned_unit_id
    TEXT assigned_terminal_code
    DATETIME last_synced_at
    DATETIME board_missing_at
    }

    ASSIGNMENTS {
    DATETIME lock_retired_at
    UUID assignment_id
    UUID shipment_id UK FK
    UUID unit_id FK
    UUID origin_terminal_id FK
    UUID destination_terminal_id FK
    DATETIME busy_from
    DATETIME busy_until
    DATETIME occupied_from
    DATETIME occupied_until
    TEXT assignment_status
    DATETIME confirmed_at
    DATETIME completed_at
    DATETIME cancelled_at
    }

    EVALUATIONS {
    UUID evaluation_id
    UUID shipment_id FK
    UUID candidate_unit_id FK
    NUMERIC payout
    NUMERIC loaded_cost
    NUMERIC deadhead_miles
    NUMERIC deadhead_cost
    NUMERIC total_cost
    NUMERIC profit_usd
    NUMERIC margin_pct
    NUMERIC suggested_bid
    TEXT cost_source
    BOOLEAN pricing_passed
    BOOLEAN capacity_passed
    BOOLEAN schedule_passed
    BOOLEAN weight_passed
    BOOLEAN location_passed
    BOOLEAN offer_open_unexpired
    BOOLEAN eligible
    JSON rejection_reasons
    JSON policy_snapshot
    DATETIME evaluated_at_sim
    DATETIME evaluated_at_wall
    }

    LIVE_BATCH_AUDITS {
    UUID audit_id
    UUID plan_id FK
    UUID run_id FK
    BOOLEAN synthetic
    JSON request
    JSON operational_snapshot
    DATETIME requested_at_wall
    DATETIME received_at_wall
    JSON response
    TEXT response_body
    INTEGER http_status
    TEXT error_code
    }

    DISPATCH_ACTIONS {
    UUID action_id
    UUID shipment_id UK FK
    UUID evaluation_id FK
    TEXT action_type
    NUMERIC bid_rate
    TEXT carrier_note
    TEXT request_status
    INTEGER board_http_status
    TEXT board_message
    DATETIME requested_at
    DATETIME resolved_at
    }

    ACTIVITY {
    UUID event_id
    UUID shipment_id FK
    UUID action_id FK
    TEXT event_type
    TEXT message
    JSON details
    DATETIME occurred_at_wall
    DATETIME occurred_at_sim
    }

    ASSIGNMENT_RESERVATIONS {
    UUID action_id FK
    UUID unit_id FK
    DATETIME busy_from
    DATETIME busy_until
    DATETIME created_at
    }

    AUTOPILOT_ATTEMPTS {
    UUID attempt_id
    UUID run_id UK FK
    UUID shipment_id FK
    TEXT external_id
    TEXT order_type
    TEXT status
    TEXT decision
    UUID action_id UK FK
    UUID candidate_unit_id FK
    NUMERIC bid_rate
    JSON model_snapshot
    JSON evaluation_snapshot
    JSON reason_codes
    TEXT message
    DATETIME created_at_wall
    DATETIME decided_at_wall
    DATETIME finished_at_wall
    UUID allocation_plan_id FK
    }

    FLEET_ALLOCATION_RESERVATIONS {
    DATETIME expires_at_sim
    UUID reservation_id
    UUID plan_id FK
    UUID shipment_id FK
    UUID unit_id FK
    UUID action_id UK FK
    TEXT status
    DATETIME busy_from
    DATETIME busy_until
    DATETIME created_at_wall
    DATETIME expires_at_wall
    }

    LIVE_SUBMISSION_AUDITS {
    UUID submission_id
    UUID batch_audit_id FK
    UUID attempt_id FK
    UUID action_id UK FK
    TEXT decision_id
    TEXT external_id
    UUID unit_id FK
    TEXT idempotency_key UK
    NUMERIC amount_usd
    DATETIME attempted_at_wall
    DATETIME submitted_at_wall
    DATETIME submitted_at_sim
    DATETIME revalidated_at_wall
    BOOLEAN authorization_passed
    BOOLEAN operational_checks_passed
    BOOLEAN atomic_reservation_acquired
    BOOLEAN rate_limit_passed
    TEXT status
    JSON result
    }

