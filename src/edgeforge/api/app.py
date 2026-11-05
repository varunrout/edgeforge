"""FastAPI app (Phase 8): events, markets by lifecycle state, SGA pricing, model health."""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from edgeforge.api import service
from edgeforge.api.bundle import DEFAULT_DIR, Bundle, load_bundle
from edgeforge.sga.engine import SGAError


class SgaRequest(BaseModel):
    event_id: int
    state: str = "lineups"
    legs: list[dict[str, Any]] = Field(min_length=2, max_length=8)
    overround: float = 0.10


def create_app(bundle_dir: Path | None = None, metrics_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="EdgeForge pricing API",
        version="0.1.0",
        description=service.DISCLAIMER,
    )
    app.state.bundle_dir = bundle_dir or Path(os.environ.get("EDGEFORGE_BUNDLE", str(DEFAULT_DIR)))
    app.state.bundle = None

    def bundle() -> Bundle:
        if app.state.bundle is None:
            try:
                app.state.bundle = load_bundle(app.state.bundle_dir)
            except FileNotFoundError as err:
                raise HTTPException(status_code=503, detail=str(err)) from err
        b: Bundle = app.state.bundle
        return b

    def guard(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except service.ServiceError as err:
            code = 404 if str(err).startswith("unknown event") else 400
            raise HTTPException(status_code=code, detail=str(err)) from err
        except (SGAError, ValidationError) as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/events")
    def events() -> dict[str, Any]:
        b = bundle()
        return {
            "label": b.meta["label"],
            "events": service.list_events(b),
            "disclaimer": service.DISCLAIMER,
        }

    @app.get("/events/{event_id}/markets")
    def markets(
        event_id: int,
        state: str = Query("opening", description="opening | lineups | inplay"),
        minute: float | None = Query(None, ge=0, description="elapsed minute (inplay only)"),
        overround: float = Query(0.10, ge=0, le=0.20),
        method: str = Query("proportional", description="margin on team markets"),
        prop_method: str = Query("odds_ratio", description="margin on player props"),
        settlement: str = Query("start", description="start | appear"),
    ) -> dict[str, Any]:
        b = bundle()
        out: dict[str, Any] = {
            "team": guard(service.team_markets, b, event_id, state, minute, overround, method)
        }
        if state in ("opening", "lineups"):
            out["props"] = guard(
                service.props, b, event_id, state, overround, prop_method, settlement
            )
        return out

    @app.get("/events/{event_id}/path")
    def path(event_id: int, step_min: int = Query(5, ge=1, le=15)) -> dict[str, Any]:
        return guard(service.team_path, bundle(), event_id, step_min)  # type: ignore[no-any-return]

    @app.get("/events/{event_id}/shock")
    def shock(event_id: int, settlement: str = Query("start")) -> dict[str, Any]:
        rows = guard(service.lineup_shock, bundle(), event_id, settlement)
        return {
            "label": "UPPER BOUND: lineups assumed known with certainty at T-60 minutes",
            "settlement": settlement,
            "rows": rows,
        }

    @app.post("/sga/price")
    def sga_price(req: SgaRequest) -> dict[str, Any]:
        return guard(service.price_sga, bundle(), req.event_id, req.state, req.legs, req.overround)  # type: ignore[no-any-return]

    @app.get("/model-health")
    def model_health() -> dict[str, Any]:
        return service.model_health(metrics_dir)

    return app


def app_factory() -> FastAPI:
    return create_app()
