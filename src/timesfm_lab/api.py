from __future__ import annotations

import importlib.util
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import DataError, now
from .panel import audit
from .profiles import core_assets
from .store import Store


class JobRequest(BaseModel):
    interval: Literal["1d", "5m"] = "1d"
    kind: Literal["forecast", "backtest", "compare"] = "forecast"
    model: Literal["timesfm", "naive"] = "timesfm"
    experiment: Literal["stock_only", "macro_control", "stock_covariates", "joint"] = "stock_covariates"
    context: int = Field(default=128, ge=32, le=1024)
    horizon: int = Field(default=5, ge=1, le=64)
    folds: int = Field(default=6, ge=1, le=100)
    allow_retrospective: bool = False
    profile: Literal["core", "full"] = "core"
    latest_complete: bool = False


def create_app(store: Store) -> FastAPI:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timesfm-research")
    jobs, lock = {}, threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True)

    app = FastAPI(title="TimesFM Multi-Asset Research", version="0.1.0", lifespan=lifespan)
    static = files("timesfm_lab").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # A local web page must not be able to trigger expensive jobs cross-origin.
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Cross-origin job requests are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path not in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.get("/")
    def home():
        return FileResponse(str(static.joinpath("index.html")))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "governance": "RESEARCH_ONLY", "promotion_allowed": False}

    @app.get("/api/overview")
    def overview(interval: Literal["1d", "5m"] = "1d"):
        selected_store = store.at_interval(interval)
        coverage = audit(selected_store)
        manifest = Path(".cache/huggingface/timesfm-model.json")
        coverage["model"] = {"installed": importlib.util.find_spec("timesfm3") is not None,
                             "downloaded": manifest.exists(), "checkpoint": json.loads(manifest.read_text()) if manifest.exists() else None}
        coverage["profiles"] = {"core": core_assets(store.assets()), "full": list(store.assets())}
        coverage["data_dir"] = str(store.root.resolve())
        return coverage

    @app.get("/api/series")
    def series(asset_id: str, limit: int = Query(180, ge=1, le=2000), interval: Literal["1d", "5m"] = "1d"):
        asset = store.assets().get(asset_id)
        if asset is None:
            raise HTTPException(404, "Unknown asset")
        return {"asset": asset.__dict__, "interval": interval, "bars": store.at_interval(interval).bars(asset_id, latest=True)[-limit:]}

    @app.get("/api/runs")
    def runs(interval: Literal["1d", "5m"] = "1d"):
        return [{k: v for k, v in run.items() if k not in {"records", "forecasts", "inputs", "ranking", "source_sha256"}} for run in store.at_interval(interval).runs()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        result = store.run(run_id)
        if result is None:
            raise HTTPException(404, "Run not found")
        return result

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        with lock:
            result = jobs.get(job_id)
            if result is None:
                raise HTTPException(404, "Job not found")
            return dict(result)

    def execute(job_id: str, request: JobRequest):
        with lock:
            jobs[job_id]["status"] = "RUNNING"
        try:
            from .models import NaiveModel, TimesFMModel
            from .research import compare, run_forecast, walk_forward
            selected_store = store.at_interval(request.interval)
            model = TimesFMModel() if request.model == "timesfm" else NaiveModel()
            options = {"context": request.context, "horizon": request.horizon,
                       "allow_retrospective": request.allow_retrospective,
                       "asset_ids": core_assets(store.assets()) if request.profile == "core" else list(store.assets())}
            if request.kind == "forecast":
                result = run_forecast(selected_store, model, request.experiment, latest_complete=request.latest_complete, **options)
            elif request.kind == "backtest":
                result = walk_forward(selected_store, model, request.experiment, folds=request.folds, **options)
            else:
                result = compare(selected_store, model, folds=request.folds, **options)
            with lock:
                jobs[job_id].update(status="BLOCKED" if result.get("status") == "BLOCKED" else "COMPLETE", run_id=result["run_id"], finished_at=now())
        except Exception as exc:
            with lock:
                jobs[job_id].update(status="ERROR", error=f"{type(exc).__name__}: {exc}", finished_at=now())

    @app.post("/api/jobs", status_code=202)
    def submit(request: JobRequest):
        if request.horizon >= request.context:
            raise HTTPException(422, "Horizon must be shorter than context")
        if request.model == "naive" and (request.kind == "compare" or request.experiment not in {"stock_only", "joint"}):
            raise HTTPException(422, "Naive baseline supports stock_only or joint only")
        # Validate data before loading a large checkpoint or reserving the worker.
        try:
            from .panel import align
            from .research import prepare
            selected = core_assets(store.assets()) if request.profile == "core" else list(store.assets())
            panel = align(store.at_interval(request.interval), selected, allow_retrospective=request.allow_retrospective)
            if request.kind == "forecast" and not request.latest_complete:
                prepare(panel, store.assets(), len(panel.values) - 1, request.context, request.experiment)
            elif not panel.values.notna().any().any():
                raise DataError("No permitted observations. Review retrospective research mode or import verified data.")
        except DataError as exc:
            raise HTTPException(422, str(exc)) from exc
        with lock:
            if any(j["status"] in {"QUEUED", "RUNNING"} for j in jobs.values()):
                raise HTTPException(409, "A research job is already active")
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"job_id": job_id, "status": "QUEUED", "created_at": now(), "request": request.model_dump()}
            while len(jobs) > 100:
                del jobs[next(iter(jobs))]
        executor.submit(execute, job_id, request)
        return {"job_id": job_id, "status": "QUEUED"}

    return app
