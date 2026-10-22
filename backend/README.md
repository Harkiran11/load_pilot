# Dispatcher backend

FastAPI service that synchronizes the load board, tracks fleet availability,
evaluates costs and bids, and handles bookings. It exposes REST and WebSocket
APIs; the separate model service supplies pricing advice and decisions.

## Structure

```text
backend/
├── controller/       HTTP routes and WebSocket endpoints, grouped by domain
├── schema/           Database models and request/response contracts
├── service/          Business logic
│   ├── ingestion/   Board orders, historical data, and reconciliation
│   ├── orders/      Load updates and offer expiration
│   ├── fleet/       Unit availability, schedules, and projections
│   ├── terminal/    Terminal capacity and status
│   ├── dispatch/    Economics, eligibility, ranking, and bookings
│   ├── allocation/  Fleet assignment planning and holds
│   ├── pricing/     Model advice, market context, and profit adjustments
│   ├── autopilot/   Automated runs and model integration
│   ├── analytics/   Dashboard summaries and financial views
│   ├── audit/       Activity, prediction records, and monitoring
│   ├── simulation/  Simulator clock synchronization
│   ├── system/      Health, connection state, and cleanup
│   └── shared/      Database access and common helpers
├── migrations/      Alembic schema revisions
├── tests/           Backend regression tests
├── data/            Local SQLite database, optional CSVs, and exports
├── main.py          API entry point
├── startup.py       Startup, background workers, and graceful shutdown
└── pyproject.toml   Dependencies; uv.lock pins installed versions
```

## Run locally

Requires Python **3.13** and **uv**. Run commands from `backend/`.
The local model dependency currently points to `../project-model/model` in
`pyproject.toml`; that directory must exist for dependency installation.

Start the [simulator](../loadpilot/README.md) on port **8087** first.
Start the [model API](../model/README.md) on **9000** when using pricing advice.
Create or update `backend/.env`, preserving any existing settings:

```dotenv
EXTERNAL_API_URL=http://127.0.0.1:8087
MODEL_API_URL=http://127.0.0.1:9000
HISTORY_CSV_PATH=
AUTOPILOT_LIVE_ENABLED=false
AUTOPILOT_KILL_SWITCH=true
```

```sh
uv sync --locked --python 3.13
uv run --locked alembic upgrade head
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

API docs: **http://127.0.0.1:8000/docs**. Stop with `Ctrl+C`.
Keep one worker: the serving process also runs ingestion and autopilot workers.
The default database is `data/dispatcher.db`; set `DISPATCHER_DATABASE_URL`
to override it. An empty history path disables optional CSV import.
Starting the service does not start an autopilot run.

## Useful commands

| Command | Purpose |
| --- | --- |
| `uv run --locked uvicorn main:app --reload --port 8000` | Development with automatic reload |
| `uv run --locked python -m unittest discover -s tests` | Run backend tests |
| `uv run --locked alembic current` | Show the database revision |
| `uv run --locked alembic history` | List available migrations |
| `uv run --locked alembic upgrade head` | Apply migrations, preserving existing rows |
| `curl --fail http://127.0.0.1:8000/health` | Check HTTP liveness |
| `curl --fail http://127.0.0.1:8000/api/system/status` | Check board connection and simulation clock |

For full readiness, also check `/api/terminals/status` and `/api/fleet/units`.
