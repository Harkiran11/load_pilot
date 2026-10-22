# Pricing model

PyTorch pricing service that estimates freight bid rates and dollar amounts,
compares manual bids with historical ranges, and provides autopilot decisions.
It supplies advice; the dispatcher owns reservations and actual bookings.

## Structure

```text
model/
├── loadpilot_model/
│   ├── api/         FastAPI pricing, manual evaluation, and autopilot endpoints
│   ├── data/        Validation, dataset preparation, and chronological splits
│   ├── features/    Shipment feature schema and preprocessing
│   ├── models/      Freight RPM neural-network architecture
│   ├── baselines/   Historical and non-neural pricing references
│   ├── training/    Fitting, tuning, retraining, and synthetic examples
│   ├── inference/   Bundle validation, loading, prediction, and fallback
│   ├── decision/    Bid/skip logic, fleet allocation, and clock checks
│   ├── evaluation/  Pricing metrics, comparisons, and operational audits
│   └── paths.py     Shared project paths
├── tests/           Tests grouped by the same domains
├── integration/     Consumer integration resources
├── artifacts/       Restored model bundles and generated outputs (local)
├── data/            Optional historical/training inputs (local)
├── main.py          Synthetic training demo; the API uses a separate entry point
└── pyproject.toml   Dependencies and extras; uv.lock pins versions
```

## Run the API

Requires **uv** and **Python 3.13** for the saved freight bundle. Run from `model/`.
Restore the complete trusted `artifacts/models/freight-rpm-v1/` bundle and
`artifacts/baselines/historical-range-v1.json` from your artifact backup first.
These local files are ignored by Git in this checkout. No training is needed
when serving an existing bundle.

```sh
uv sync --locked --extra api --python 3.13
uv run --locked --extra api uvicorn loadpilot_model.api.app:app --host 127.0.0.1 --port 9000 --workers 1
```

API docs: **http://127.0.0.1:9000/docs**. Stop with `Ctrl+C`.
Set the backend's `MODEL_API_URL=http://127.0.0.1:9000` to connect it.
The model reads exported environment variables; it does not automatically load
`.env`. If you maintain one, append `--env-file .env` to the Uvicorn command.
Use `LOADPILOT_MODEL_BUNDLE` and `LOADPILOT_HISTORICAL_RANGE_ARTIFACT` for alternate artifact paths.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/predict-bid` | Predict bid RPM and total dollars |
| `POST /api/v1/manual-mode/evaluate` | Compare a manual bid while preserving backend economics |
| `POST /api/v1/autopilot/evaluate` | Evaluate one current order |
| `POST /api/v1/autopilot/analyze-batch` | Analyze orders and fleet assignments together |

Autopilot requires explicit live enablement, its kill switch off, and a shared
backend/model token. Ordinary pricing needs no autopilot activation.
`/openapi.json` checks HTTP availability; a successful `/predict-bid` response
with `pricing_source: model` confirms that the trained model is serving.

## Useful commands

Run these from `model/`:

| Command | Purpose |
| --- | --- |
| `uv run --locked --extra api python -m unittest discover -s tests` | Run model/API tests |
| `uv run --locked ruff check .` | Check Python style and imports |
| `uv run --locked ruff format --check .` | Check formatting |
| `uv run --locked python -m loadpilot_model.data.validation --help` | Show historical CSV validation options |
| `uv run --locked python -m loadpilot_model.training.regression --help` | Show freight training options |
| `uv run --locked python -m loadpilot_model.inference.model_bundle --help` | Show model packaging options |
| `uv run --locked python main.py --device cpu --epochs 2` | Train a small synthetic demo |

The synthetic demo writes `artifacts/model.pt`; it does not replace or deploy
the freight pricing bundle. Training requires separate datasets and preparation.
