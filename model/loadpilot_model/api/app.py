"""Optional project-local ASGI entry point; embedding the router remains supported."""

from fastapi import FastAPI

from loadpilot_model.api.pricing import create_pricing_router
from loadpilot_model.paths import PROJECT_ROOT

app = FastAPI(title="LoadPilot Pricing API", version="v1")
app.include_router(
    create_pricing_router(
        PROJECT_ROOT / "artifacts/models/freight-rpm-v1",
        range_path=PROJECT_ROOT / "artifacts/baselines/historical-range-v1.json",
    )
)
