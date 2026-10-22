# Feature coverage

What is implemented, where it lives, and how to verify it. The engineering
reasoning behind these choices is in [ENGINEERING.md](ENGINEERING.md).

## At a glance

| Area | Implementation | Where |
|---|---|---|
| Dispatch ledger | Alembic-managed SQLAlchemy schema with 20 tables | `backend/schema/`, `backend/migrations/` |
| Live ingestion | Startup snapshot, WebSocket events, 30 s reconciliation, clock sync | `backend/service/ingestion/` |
| Scheduling | Occupied-window locks, projected positions, chaining, fleet allocation | `backend/service/dispatch/`, `backend/service/allocation/` |
| Dispatch UI | React 19 SPA with live queues, analysis, fleet, terminals, records | `frontend/src/` |
| Business rules | Eight fail-closed checks before any submission | `backend/service/dispatch/dispatch_policy.py` |
| Pricing model | PyTorch RPM model, baselines, historical range, monitoring | `model/loadpilot_model/`, `model/artifacts/` |
| Autopilot | Guarded worker with two independent live gates and hard limits | `backend/service/autopilot/` |
| Audit | Evaluations, reservations, submissions, outcomes, activity log | `backend/service/audit/` |
| Operations | Four-container Compose stack, health checks, read-only DB tools | `compose.yaml`, `scripts/db.sh` |

## Dispatch ledger

The dispatcher keeps its own database. The board is an external system; the
ledger is the product's memory.

- **Orders:** `shipments` keyed by the board's `external_id`, with status,
  pickup/delivery windows, weight, rate, cost, and board timestamps.
- **Fleet and terminals:** `fleet_units`, `fleet_capacity`, `terminals`.
- **Scheduling:** `assignments` stores both the busy window (pickup → delivery)
  and the occupied window (deadhead departure → delivery + turnaround), plus
  `assignment_reservations` and `fleet_allocation_reservations` for holds.
- **Decisions and audit:** `evaluations`, `dispatch_actions`, `autopilot_runs`,
  `autopilot_attempts`, `activity`, live audits, and prediction observations.
- **Analytics:** `historical_shipments`, `observed_cohorts`, `observed_comparisons`.

Time-window state is explicit. A booking records the interval the truck is
actually unavailable, not just the loaded trip. Conflicting writes run inside a
`BEGIN IMMEDIATE` transaction, so the overlap check and the insert commit
together.

The database is inspectable two ways:

- **Our Records** in the UI lists every table with live row counts and a paged
  row browser.
- `./scripts/db.sh tables|query|snapshot|open` runs read-only queries or takes an
  online-backup snapshot for a desktop SQLite browser.

Migrations, foreign keys, uniqueness constraints, and indexes are tested against
both the SQLAlchemy models and `backend/requirements/database-schema.md`.

## Live ingestion

- REST snapshot at startup for terminals, fleet, orders, and the simulation clock.
- WebSocket subscription for new orders and lifecycle changes.
- Periodic REST reconciliation (default 30 s) as the authoritative fallback.
- Clock sync every second; expiry monitor every second in simulated time.
- Idempotent upserts on the board's `external_id`; duplicate events are safe.
- Board retirements and missing orders are tracked (`board_missing_at`,
  `board_retired_at`) so stale offers cannot be booked.
- Reconnect and restart recovery re-reads the board. Unknown submission outcomes
  keep their reservations until reconciliation.

## Scheduling and empty miles

- **Double-booking prevention:** the occupied window includes deadhead travel,
  pickup buffer, the loaded trip, and turnaround. Overlaps are checked against
  active assignments, retained tails, pending reservations, and allocation holds
  with a half-open predicate (`existing.start < candidate.end AND existing.end >
  candidate.start`), so touching endpoints are allowed.
- **Board-selected unit:** the dispatcher predicts the board's nearest free unit
  at the origin hub and rejects attempts to use a different truck.
- **Capacity follows freight:** after delivery the unit is re-homed to the
  delivery hub, and future loads are evaluated against its projected position.
- **Empty miles:** deadhead is computed from where the truck will be at pickup,
  not where it is now. The default cap is 100 miles.
- **Chaining:** `project_units()` combines active assignments and retained locks
  to find the next feasible load for each truck.
- **Fleet allocation:** bounded branch-and-bound over candidate loads (12
  candidates, 2,000 nodes / 2 CPU seconds) plus exact batch matching with
  successive shortest augmenting paths.

## Dispatch UI

| Page | Capability |
|---|---|
| **Dashboard** | Live KPIs, terminal capacity map, animated trip-flow map, connection and clock status, active-trip progress, recent activity |
| **Load Board** | Live fixed-rate, open-bid, and all queues; search and filters; manual and autopilot modes |
| **Load detail** | Cost, deadhead, weight, windows, profit, margin, model advisory, historical baseline, bid floor, and dispatch checks |
| **Terminals** | Per-terminal utilization, fleet counts, capacity warnings, and map |
| **Fleet & Schedule** | Units, availability, projected positions, retained windows, schedule timeline |
| **Bid Logs** | Submitted bids, outcomes, and cleanup |
| **Shipments** | Bookings, lifecycle state, financials, and simulation-clock progress |
| **Analytics** | Profit, lanes, win rate, model comparisons, and observed cohorts |
| **Activity** | Human-readable audit log of ingestion, actions, model, and autopilot events |
| **Our Records** | Every dispatcher table with row counts and a browsable grid |
| **Settings** | Simulation speed, policy values, and integration status |

The layout is responsive and keyboard-accessible. Unsafe actions are disabled
when prerequisites fail, and board errors such as `409` are surfaced with the
reason instead of being treated as success.

## Constraint enforcement

Eight checks run before a booking. All are fail-closed: a missing or stale input
is a rejection, not a pass.

| Check | Rejects when |
|---|---|
| `offer_open_unexpired` | Order is missing, not open, expired, or has no known expiry |
| `pricing_passed` | Cost or margin is unknown, below the profit floor, below margin %, or the bid is below the floor |
| `deadhead_passed` | Deadhead is unknown or above the cap |
| `weight_passed` | Weight is missing/invalid or above 44,000 lb |
| `timing_passed` | Window is invalid, pickup lead is insufficient, the truck cannot arrive in time, or departure is in the past |
| `location_passed` | Terminal is unresolved, the unit is not board-selectable, position is stale, or the projected location does not match |
| `schedule_passed` | Unit is unavailable, availability ends after pickup, or an assignment/reservation overlaps |
| `capacity_passed` | No eligible unit, or terminal inventory is missing, stale, or inconsistent |

A reservation is committed atomically before submission. If the board's response
is unknown, the hold remains until reconciliation. Manual actions, model
approval, the planner, and autopilot all converge on the same dispatch path.

## Pricing model

- PyTorch regression predicting historical open-bid market rate per loaded mile.
  Suggested bid = predicted RPM × distance.
- 756 train / 162 validation / 163 test chronological rows; 114 encoded features
  covering distance, weight, coordinates, lane, and states.
- Saved baselines: global median, lane median, historical percentile range,
  fallback policy, and minimum sample rules.
- Test MAE **$297.35/load** ($0.2748/mile), 100% coverage, about **36% better than
  the global median** and **19% better than the lane median**.
- The model advises; deterministic cost and margin rules decide. It cannot bypass
  the profit floor or a dispatch check.
- The Load Board shows model price, historical range, bid floor, and the user's
  bid side by side, with warnings and the model version.
- **Model monitoring:** the frozen bundle is mounted read-only into the backend.
  `GET /api/analytics/model-monitoring/report` compares live observations against
  it: record count, pricing sources, versions, error codes, missing/unseen
  features, and numeric drift.

Honest limits: the bundle is marked unverified, history is sparse, and open-bid
awards in the demo board are independent of bid amount. Win rate is not optimized.

## Autopilot

- Background worker with live run state, a simulated-time deadline, and recorded
  stop reasons.
- **Two independent live gates:** the backend and the model service each require
  an enable flag, a kill switch in the off position, and a shared token of at
  least 32 characters. Live is off by default.
- **Operational guardrails:** board-selected unit, occupied-window reservations,
  all eight checks, minimum profit/margin, deadhead cap, bid cap, order limit,
  loss budget including pending exposure, action spacing, and a consecutive-error
  stop.
- **Model decision:** `BID`, `REVIEW`, or `SKIP`. `REVIEW` and `SKIP` are never
  converted into a booking. Approval is revalidated at reservation and
  immediately before sending.
- **No automatic declines:** it accepts and bids only; dispatchers retain decline
  control.
- **Audit:** every attempt stores the model request/response, checks, economics,
  reason codes, plan/run IDs, and final status.

## Operations

- Four containers: `frontend` (nginx), `backend`, `model`, and an optional board.
- Health checks for the model, backend, and frontend.
- SQLite in WAL mode with `PASSIVE` checkpoints and slow-transaction logging.
- Online backups through SQLite's backup API; never copy a live WAL file.
- Read-only record browser with a whitelist of 20 tables and a clean `404` for
  unknown names.
- The demo database is seeded on first start and mounted separately from the
  image.

## Verification

```sh
make test    # 34 backend tests + 212 model tests
make lint    # ESLint + tsc -b
```

Manual checks:

1. Load Board → open bid → confirm model advisory, historical range, bid floor,
   warnings, and chart.
2. Autopilot → set limits → run → confirm attempts, stop reason, and decision
   details.
3. Terminals and Fleet → confirm utilization, schedules, and capacity warnings.
4. Activity and Shipments → confirm the audit trail and lifecycle outcomes.
5. Our Records → browse a table and confirm the row counts match the database.

## Scope

- The dispatcher database belongs to LoadPilot; the board is external and can
  still reject a submission.
- Historical CSV import is optional. The trained model, baselines, and historical
  range are bundled artifacts.
- The demo is a local, single-writer deployment, not a production
  compliance/payments system. The enterprise scaling path is in the engineering
  report.
