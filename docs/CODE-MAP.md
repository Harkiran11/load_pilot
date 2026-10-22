# Code map

A file-by-file guide to the repository: what each area is for, which file does
what, and how data flows from the board to the screen.

- **Layers:** `controller` (HTTP endpoints) → `service` (the actual logic) →
  `schema` (the data shapes) → `database` (our own tables).
- **Rule of thumb:** a file in `service/` does one job. If a name starts with
  `dashboard`, `orders`, `fleet`, `dispatch`, `pricing`, `allocation`,
  `autopilot`, `audit`, `ingestion`, `simulation` or `analytics`, you can guess
  its area from the folder alone.

---

## 1. The whole picture in one flow

```
  board (external)                     our backend                     our frontend
  ─────────────────                    ────────────                    ────────────
  /orders, /fleet, /terminals
  /simulation/clock  ──WebSocket──▶  ingestion/                       api/overview.ts
  /orders/ws                          order_stream.py                 api/orders.ts
                                      ingest.py                       api/shipments.ts
                                        │                             api/terminals.ts
                                        ▼                             api/activity.ts
                                      our SQLite tables              api/fleet.ts
                                      shipments, fleet_units,          api/analytics.ts
                                      assignments, evaluations,        api/bids.ts
                                      autopilot_runs, activity         api/autopilot.ts
                                        │
                                        ▼
                                      dispatch/  (may we book it?)
                                      pricing/   (does it make money?)
                                      allocation/(which trucks for which loads?)
                                      autopilot/ (should a machine book it?)
                                        │
                                        ▼
                                      controller/  →  JSON over HTTP
                                                                   →  pages/*.tsx
```

---

## 2. Backend — the folders

| Folder | What it owns |
|---|---|
| `backend/controller/` | The HTTP and WebSocket surface. Thin: parse the request, call one service function, return the answer. No business logic here. |
| `backend/service/` | All the real logic. This is where you look to answer "how does it work?". |
| `backend/schema/` | The shape of every table and every API payload. |
| `backend/migrations/` | The Alembic migrations that build the database, one version per change. |
| `backend/tests/` | 34 tests. They check the dispatch rules, the scheduler, the database behaviour, and the service wiring. |
| `backend/requirements/` | `database-schema.md` — the human-readable schema reference. |

---

## 3. Backend services, folder by folder

### `service/ingestion/` — "always know the truth"

| File | What it does |
|---|---|
| `order_stream.py` | Listens to the board's WebSocket. Turns each event into a local write, and reconnects with a full re-sync when the socket drops. |
| `ingest.py` | The one place that writes board data into our tables: orders, trucks, terminals, the clock, and the reconciliation that follows. |
| `periodic_sync.py` | A slow safety net — a full REST reconciliation every 30 seconds in case a WebSocket message was missed. |
| `reconciliation.py` | Re-checks specific orders we care about after an action. |
| `board_reset.py` | Detects when the board wipes and restarts its world, and retires the old generation so we never mix two boards' data. |
| `history_import.py` | Downloads the board's historical dataset once and imports it atomically. Only re-reads it when the bytes change. |

### `service/orders/` — the live picture

| File | What it does |
|---|---|
| `load_stream.py` | Builds the live order feed. The `live` mode is the bounded set (open, pending, running, recent deliveries); `loads=false` gives a clock-only stream for screens that don't need orders. |
| `expiration.py` | Expiry, derived from the *simulation* clock. Also the background monitor that marks offers expired. |

### `service/fleet/` — "where will each truck be?"

| File | What it does |
|---|---|
| `unit_projection.py` | Projects every truck's position from its confirmed bookings. This is how a truck "re-homes" to the delivery hub, and how we work out its empty travel to the next pickup. |
| `fleet_schedule.py` | The schedule/timeline view for the fleet page. |
| `fleet.py` | Reconciles the board's roster and capacity against ours, and reports where the two disagree. |

### `service/dispatch/` — "may we book this, and is it worth it?"

| File | What it does |
|---|---|
| `dispatch_policy.py` | **The eight checks.** The single most important file in the project. Also the margin floor, and the `evaluations` rows that record every outcome. |
| `economics.py` | The money math. Loaded cost, deadhead cost, total cost, profit, margin, and the minimum profitable bid. Decimal-safe; no floating-point money. |
| `pickup_routing.py` | Which trucks could serve this pickup: at the right terminal, free at the right time, close enough, and reachable in time. |
| `chaining.py` | Which trucks could chain onto this load — a truck that will be nearby *after* its previous delivery. This is the empty-mile optimisation. |
| `occupancy.py` | The occupancy policy: how long a truck is held after a delivery (turnaround) and other timing rules. |
| `scheduling.py` | The SQL that finds conflicting assignments — the double-booking check. |
| `assignment_occupancy.py` | Builds the **occupied window** for a booking: from before the deadhead drive until after the turnaround. Two bookings conflict when these overlap. |
| `reservations.py` | Holds a truck for a booking that is in flight, and releases it when the outcome is known. |
| `actions.py` | Accept, decline and bid. Reserve first, commit, then call the board. |
| `ranking.py` | Scores candidate trucks so the list can be ordered by profit, empty miles, continuity and utilisation. |
| `dispatch_feedback.py` | Turns rejections into readable reasons for the dispatcher. |

### `service/pricing/` — "what should we charge?"

| File | What it does |
|---|---|
| `price_analysis.py` | The price-analysis endpoint's logic: deterministic economics plus the model's advice, side by side. |
| `market_context.py` | The historical range for a lane — median payout, rate per mile, sample count, historical win fraction. |
| `profit_adjustment.py` | Planning estimates: opportunity cost, the value of a chaining move, and how fresh the inputs are. Explicitly never used to satisfy the cash-margin check. |
| `bid_scenario.py` | "What if I bid this?" — compares candidate bids against the model, the history and the floor. |
| `model_lane.py` | Resolves which model lane an order belongs to. |

### `service/allocation/` — "which trucks for which loads, together?"

| File | What it does |
|---|---|
| `fleet_allocation.py` | The planner. Copies the operational tables into an isolated in-memory database, then searches for a good set of order-and-truck combinations under node, time and candidate limits. |
| `allocation_dispatch.py` | Drives the planner from the autopilot: claim a plan, price it, ask the model, reserve it, submit it. |
| `allocation_holds.py` | Reservation bookkeeping for the planner, and releasing plans that are no longer valid. |

### `service/autopilot/` — "should a machine book this?"

| File | What it does |
|---|---|
| `autopilot.py` | The worker loop and the run lifecycle: start, stop, limits, and the per-attempt flow. |
| `autopilot_guards.py` | **The brakes.** Every guardrail that can stop a submission, and the re-checks that happen at the moment of submitting. |
| `model_autopilot.py` | Prepares and calls the model for a single order, and interprets the answer. |
| `model_batch.py` | The same for a batch of orders and trucks. |
| `order_collection.py` | Collects the orders a run should consider. |
| `autopilot_stream.py` | The live run feed for the UI. |

### `service/audit/` — "prove what we did"

| File | What it does |
|---|---|
| `activity.py` | The human-readable event log behind the Activity page. |
| `live_audit.py` | The durable, queryable record of every model observation and every submission. |
| `prediction_capture.py` | Captures each price analysis: the request, the response, and the operational snapshot at that moment. |
| `prediction_monitoring.py` | Compares those live observations against the frozen model bundle. |
| `audit_source.py` | Marks evidence as live or synthetic, so the two are never confused. |

### `service/analytics/` — "what did we learn?"

| File | What it does |
|---|---|
| `dashboard.py` | The overview, the order list and the terminal list. |
| `order_analytics.py` | Compact projections of the order history, plus the paged, filtered, sorted bid log. |
| `analytics_summary.py` | Aggregates the analytics page's figures in the database for the selected date range. |
| `financials.py` | Reported actuals on shipments, and observed cohorts for comparison. |
| `recommendation_comparison.py` | Compares two candidate recommendations. |

### `service/simulation/` and `service/system/`

| File | What it does |
|---|---|
| `simulation/simulation_time.py` | The one place that converts between wall time and simulated time. Every expiry and occupancy comparison goes through it. |
| `simulation/simulation_clock_sync.py` | Keeps the local clock in step with the board's. |
| `system/history_cleanup.py` | Retention: deletes the oldest activity, shipment or bid history on request. |
| `system/record_browser.py` | Read-only access to the tables we own, for the "Our Records" page. A fixed list of tables and columns, long values truncated. |
| `system/system_state.py` | The board connection state shown in the header. |
| `system/health_service.py` | The readiness check the container uses. |

### `service/shared/`

| File | What it does |
|---|---|
| `database.py` | The engine, the SQLite settings (WAL, busy timeout), the background checkpoint, and the slow-transaction log. |
| `geography.py` | Distance calculations and "nearest hub". |
| `errors.py` | `ProductError` — the one exception type that becomes a clean API response. |

---

## 4. Backend controllers — the API surface

| File | Endpoints |
|---|---|
| `orders/load_controller.py` | The load list, the WebSocket feed, routing, cost preview, evaluate, accept, decline, bid, price analysis, market context, bid scenarios. |
| `orders/bid_controller.py` | The bid list, the bid log, and history cleanup. |
| `orders/shipment_controller.py` | Shipments and their reported financials. |
| `orders/order_controller.py` | One order's expiration. |
| `fleet/fleet_controller.py` | Capacity, schedules, units, projections. |
| `terminal/terminal_controller.py` | Terminal status, the status feed, the roster. |
| `autopilot/autopilot_controller.py` | Start, stop, list and inspect runs, attempts and allocations. |
| `analytics/analytics_controller.py` | The analytics summary, the compact order history, and the paged bid log. |
| `analytics/overview_controller.py` | The dashboard's single-shot overview. |
| `audit/activity_controller.py` | The activity feed and its cleanup. |
| `audit/live_audit_controller.py` | The raw model-observation and submission records. |
| `audit/monitoring_controller.py` | The model monitoring report. |
| `pricing/price_analysis_controller.py` | The price analysis endpoint. |
| `system/system_controller.py` | System status, policy, recommendation comparison, and the "Our Records" table browser. |
| `system/health_controller.py` | The health check. |
| `allocation/allocation_controller.py` | A planner preview without booking anything. |

---

## 5. Frontend — how a page is built

Every page follows the same three-part pattern:

```
api/<area>.ts        subscribe to the server; keep state
utils/<area>.ts      pure calculations (no side effects — easy to test)
pages/<Area>Page.tsx the screen
components/*.tsx     the reusable pieces
```

| Frontend folder | What it holds |
|---|---|
| `api/` | One file per backend area. Each exports a `subscribe…` function that opens a WebSocket or polls REST and pushes state to the page. |
| `utils/` | Pure functions: money formatting, margin math, sorting, filtering, date ranges, the trip-flow geometry. No network, no React — so they are trivially correct. |
| `components/` | The reusable pieces: the load card, the detail panel, the profit-margin analyzer, the price-analysis chart and metrics, the autopilot panel and settings, the maps, the timeline. |
| `hooks/` | `use-autopilot` (run lifecycle and polling) and `use-run-profit-stats`. |
| `context/page-status.ts` | Turns each page's connection state into the header banner. |
| `config/env.ts` | Where the API base URL comes from. |
| `layouts/AppLayout.tsx` | The shell: sidebar, header, live clock, connection status. |

### The three files that matter most on the frontend

| File | Why it matters |
|---|---|
| `components/ProfitMarginAnalyzer.tsx` | The accept/decline/bid screen. Computes the proposed bid from the margin the dispatcher types, and shows cost, profit and margin before anything is committed. |
| `components/PriceAnalysisPanel.tsx` | The model's advice next to our own economics. It debounces the request so dragging the margin slider doesn't fire a request per pixel, and it re-checks that the answer belongs to the order and the bid. |
| `hooks/use-autopilot.ts` | The autopilot's whole lifecycle from the browser: start, poll, recover from an uncertain start, stop. It keeps a "pending start" in `sessionStorage` so a page refresh can't accidentally start a second run. |

---

## 6. The model service (`model/`)

| File | What it does |
|---|---|
| `loadpilot_model/api/app.py` | The FastAPI app the backend calls. |
| `loadpilot_model/api/manual.py` | Manual price analysis for a dispatcher-typed bid. |
| `loadpilot_model/api/autopilot.py` | Single-order decisions with full context. |
| `loadpilot_model/api/batch.py` | Many orders and many trucks together. |
| `loadpilot_model/api/live.py` | The live-mode wrapper: it re-checks that a decision still matches the order it was made for. |
| `loadpilot_model/decision/engine.py` | The rules a decision must pass before it can be BID. |
| `loadpilot_model/decision/clock.py` | Converts a simulated deadline into a real one, so a decision cannot outlive its order. |
| `loadpilot_model/decision/batch.py` | The matching algorithm: successive shortest augmenting paths on a residual flow graph. |
| `loadpilot_model/features/schema.py` | The feature list and the preprocessing contract. |
| `loadpilot_model/features/preprocessing.py` | The scaler and the encoders, saved with the model. |
| `model/artifacts/models/freight-rpm-v1/` | The frozen bundle: weights, preprocessing, baselines, evaluation results, and a manifest with hashes. |

---

## 7. Where to look for a specific question

| Question | Start here |
|---|---|
| "Why was this load refused?" | `service/dispatch/dispatch_policy.py` → `evaluations` table → the Activity page |
| "How is profit calculated?" | `service/pricing/economics.py` and `service/dispatch/dispatch_policy.py` |
| "How is double-booking prevented?" | `service/dispatch/scheduling.py` + `service/dispatch/assignment_occupancy.py` + `service/dispatch/actions.py` |
| "How does chaining reduce empty miles?" | `service/dispatch/chaining.py` + `service/fleet/unit_projection.py` |
| "What stops the autopilot?" | `service/autopilot/autopilot_guards.py` |
| "How is the minimum bid worked out?" | `service/pricing/economics.py` → `minimum_profitable_bid` |
| "Where does the historical range come from?" | `service/pricing/market_context.py` + `service/ingestion/history_import.py` |
| "How does the board get called?" | `service/dispatch/actions.py` |
| "What does the database look like?" | `backend/requirements/database-schema.md` + `backend/schema/` |
| "How is the live feed kept current?" | `service/ingestion/order_stream.py` + `service/ingestion/periodic_sync.py` |
| "Why is the analytics page fast?" | `service/analytics/analytics_summary.py` |
| "Why is the bid log page fast?" | `service/analytics/order_analytics.py` |
| "Where do I see the raw tables we keep?" | `service/system/record_browser.py` and the **Our Records** page |
| "How do I query the database from my machine?" | `./scripts/db.sh` — read-only queries, or an online-backup snapshot |

---

## 8. Configuration that changes behaviour

All of these live in `.env` (local) and `.env.example` (committed defaults).

| Setting | What it controls |
|---|---|
| `TURNAROUND_MINUTES` | How long a truck is held after a delivery before it can take another load. |
| `MIN_EXPECTED_MARGIN_PCT` | The margin floor a booking must clear. |
| `MAX_DEADHEAD_MILES` | The furthest a truck will drive empty for a pickup. |
| `MAX_WEIGHT_LBS` | The weight limit. The board enforces it too; we refuse first. |
| `HISTORICAL_MIN_SAMPLES` | How many historical rows a lane needs before we quote its range. |
| `AUTOPILOT_LIVE_ENABLED`, `AUTOPILOT_KILL_SWITCH`, `MODEL_AUTOPILOT_LIVE_TOKEN` | The three things that must all agree before the autopilot can trade live. |
| `MODEL_MONITORING_BUNDLE` | The frozen bundle the monitoring report compares live observations against. |
| `HISTORY_CSV_PATH` | Where the historical dataset is cached. Fetched from the board automatically if missing. |
| `SIMULATION_SPEED` | How fast the board runs. Every expiry is judged in simulated time, so this changes what "expired" means. |

---

*For the design reasoning and measured numbers see
[ENGINEERING.md](ENGINEERING.md). For capability-by-capability notes see
[FEATURES.md](FEATURES.md).*
