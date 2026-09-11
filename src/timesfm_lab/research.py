from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .domain import DataError, now, utc
from .models import QUANTILES, NaiveModel
from .panel import Panel, align, features
from .store import Store

EXPERIMENTS = ("stock_only", "macro_control", "stock_covariates", "joint")


@dataclass
class Inputs:
    targets: list[str]
    covariates: list[str]
    x: np.ndarray
    cov: np.ndarray | None
    fingerprint: str


def prepare(panel: Panel, assets: dict, origin: int, context: int, experiment: str,
            technical: bool = False) -> Inputs:
    if experiment not in EXPERIMENTS:
        raise DataError(f"Unknown experiment: {experiment}")
    if context < 32 or origin < context - 1 or origin >= len(panel.values):
        raise DataError("At least 32 complete context sessions are required")
    targets = [a for a in panel.values if experiment == "joint" or assets[a].asset_class == "equity"]
    if not targets:
        raise DataError("No equity targets in the selected universe")
    covariates = [a for a in panel.values if a not in targets and experiment in {"macro_control", "stock_covariates"}
                  and (experiment != "macro_control" or assets[a].asset_class != "commodity")]
    if experiment in {"macro_control", "stock_covariates"} and not covariates:
        raise DataError("This experiment requires cross-asset covariates")
    start = origin + 1 - context
    window = panel.values.iloc[start:origin + 1]
    used = targets + covariates
    for asset_id in used:
        bars = panel.observations[asset_id][start:origin + 1]
        if not np.isfinite(window[asset_id]).all():
            raise DataError(f"{asset_id}: missing or stale observations in context; no interpolation permitted")
        if (window[asset_id] <= 0).any():
            raise DataError(f"{asset_id}: non-positive prices require a dedicated return contract")
        if any(b["corporate_action"] == "split" or b["roll_event"] == "roll" for b in bars[1:]):
            raise DataError(f"{asset_id}: context crosses a split or contract roll")
        if panel.mode == "POINT_IN_TIME_RESEARCH":
            if assets[asset_id].asset_class == "equity" and any(b["corporate_action"] == "unknown" for b in bars):
                raise DataError(f"{asset_id}: corporate actions unverified")
            if assets[asset_id].series_kind == "continuous_future" and any(b["roll_event"] == "unknown" or not b["contract_id"] for b in bars):
                raise DataError(f"{asset_id}: contract identity or rolls unverified")
    x = window[targets].to_numpy(dtype=float).T
    cov = window[covariates].to_numpy(dtype=float).T if covariates else None
    if technical:
        # Compute from a context ending at the origin, never from future rows.
        sliced = Panel(panel.values.iloc[:origin + 1], panel.ages.iloc[:origin + 1],
                       {a: b[:origin + 1] for a, b in panel.observations.items()},
                       panel.cutoffs[:origin + 1], panel.mode, panel.source_hashes, panel.interval)
        table = features(sliced)
        technical_channels = []
        for asset_id in targets:
            for key in ("return_1", "momentum_5", "volatility_20", "rsi_14"):
                channel = table[table.asset_id == asset_id][key].to_numpy()[-context:]
                if not np.isfinite(channel).all():
                    raise DataError(f"{asset_id}: technical feature {key} needs complete warm-up history")
                technical_channels.append(channel)
                covariates.append(f"{asset_id}:{key}")
        cov = np.vstack(([cov] if cov is not None else []) + technical_channels)
    fingerprint = hashlib.sha256()
    fingerprint.update(json.dumps({"targets": targets, "covariates": covariates, "cutoff": panel.cutoffs[origin], "interval": panel.interval}).encode())
    fingerprint.update(x.tobytes())
    if cov is not None:
        fingerprint.update(cov.tobytes())
    return Inputs(targets, covariates, x, cov, fingerprint.hexdigest())


def forecast_payload(panel: Panel, assets: dict, model, experiment: str, context: int, horizon: int,
                     origin: int | None = None, technical: bool = False) -> dict:
    if not 1 <= horizon <= 64 or horizon >= context:
        raise DataError("Horizon must be between 1 and 64 and shorter than context")
    origin = len(panel.values) - 1 if origin is None else origin
    inputs = prepare(panel, assets, origin, context, experiment, technical)
    prediction = model.predict(inputs.x, horizon, inputs.cov)
    prediction.validate(len(inputs.targets), horizon)
    records = []
    for i, asset_id in enumerate(inputs.targets):
        last = float(inputs.x[i, -1])
        for step in range(horizon):
            point = float(prediction.point[i, step])
            q = prediction.quantiles[i, step]
            records.append({
                "asset_id": asset_id, "asset_class": assets[asset_id].asset_class,
                "currency": assets[asset_id].currency, "unit": assets[asset_id].unit,
                "step": step + 1, "base_price": last, "price": point, "return": point / last - 1,
                "direction": "UP" if point > last else "DOWN" if point < last else "FLAT",
                "quantiles": {f"{level:.1f}": float(value) for level, value in zip(QUANTILES, q)},
                "interval_signal": "ABOVE_CURRENT" if q[0] > last else "BELOW_CURRENT" if q[-1] < last else "STRADDLES_CURRENT",
            })
    used_hashes = sorted({bar["source_sha256"] for a in inputs.targets + [c for c in inputs.covariates if c in assets]
                          for bar in panel.observations[a][origin + 1 - context:origin + 1] if bar})
    ranking = sorted([r for r in records if r["step"] == horizon and r["asset_class"] == "equity"], key=lambda r: r["return"], reverse=True)
    return {
        "kind": "forecast", "created_at": now(), "governance": "RESEARCH_ONLY", "promotion_allowed": False,
        "data_mode": panel.mode, "model": model.metadata, "experiment": experiment,
        "interval": panel.interval, "horizon_unit": "observed 5-minute bars" if panel.interval == "5m" else "observed NSE sessions",
        "origin": panel.values.index[origin], "cutoff": panel.cutoffs[origin], "context": context, "horizon": horizon,
        "targets": inputs.targets, "covariates": inputs.covariates, "technical_features": technical,
        "input_sha256": inputs.fingerprint, "source_sha256": used_hashes, "forecasts": records, "ranking": ranking,
        "time_axis": "Next observed NSE bars; global and commodity values are latest available at each decision cutoff. Session breaks are not elapsed forecast time.",
        "limitations": ["Forecast quantiles are not calibrated direction probabilities", "No model promotion or trading execution",
                        "Starter universe has selection and survivorship bias", "Observed session union is not an independently verified exchange calendar"] + (
            ["Historical publication timing/revisions are unverified", "Continuous futures roll provenance may be unavailable"]
            if panel.mode == "RETROSPECTIVE_RESEARCH" else ["Synthetic data: no market accuracy evidence"] if panel.mode == "SYNTHETIC_DEMO" else []
        ),
    }


def run_forecast(store: Store, model, experiment: str = "joint", context: int = 128, horizon: int = 5,
                 allow_retrospective: bool = False, asset_ids: list[str] | None = None, technical: bool = False,
                 latest_complete: bool = False) -> dict:
    assets = store.assets()
    panel = align(store, asset_ids or list(assets), allow_retrospective=allow_retrospective)
    if panel.mode != "SYNTHETIC_DEMO" and (utc(now()) - utc(panel.cutoffs[-1])).total_seconds() > 96 * 3600:
        raise DataError("Anchor history is stale; collect recent sessions before producing a current forecast")
    origin = len(panel.values) - 1
    if latest_complete:
        # Explicit user-selected research policy, bounded to one domestic session.
        # Never disguise an older origin as a current forecast or extend the search indefinitely.
        for candidate in range(origin, max(context - 2, origin - (75 if store.interval == "5m" else 1)), -1):
            try:
                prepare(panel, assets, candidate, context, experiment, technical)
                origin = candidate
                break
            except DataError:
                continue
        else:
            raise DataError("No complete context within the most recent observed session; inspect coverage or select a supported subset")
    payload = forecast_payload(panel, assets, model, experiment, context, horizon, origin=origin, technical=technical)
    payload.update(origin_policy="latest_complete" if latest_complete else "latest_expected",
                   latest_panel_origin=panel.values.index[-1], lag_bars=len(panel.values) - 1 - origin,
                   forecast_status="LAGGED_INPUTS" if origin < len(panel.values) - 1 else "LATEST_OBSERVED_INPUTS")
    return {"run_id": store.save_run(payload), **payload}


def metrics(records: list[dict], horizon: int) -> dict:
    terminal = [r for r in records if r["step"] == horizon]
    if not terminal:
        return {"observations": 0, "return_mae": None, "directional_hit_rate": None, "rank_ic": None,
                "interval_80_coverage": None, "normalized_pinball": None, "per_asset_price_mae": {}}
    frame = pd.DataFrame(terminal)
    ics = []
    for _, group in frame.groupby("origin"):
        if len(group) >= 3 and group.predicted_return.nunique() > 1 and group.actual_return.nunique() > 1:
            ics.append(float(group.predicted_return.rank().corr(group.actual_return.rank())))
    predicted, actual = frame.predicted_return.to_numpy(), frame.actual_return.to_numpy()
    errors = []
    coverage = []
    for record in terminal:
        quantiles = np.array([record["quantiles"][f"{q:.1f}"] for q in QUANTILES])
        error = (record["actual_price"] - quantiles) / record["base_price"]
        errors.append(float(np.maximum(QUANTILES * error, (QUANTILES - 1) * error).mean()))
        coverage.append(float(quantiles[0] <= record["actual_price"] <= quantiles[-1]))
    return {
        "observations": len(terminal), "origins": int(frame.origin.nunique()),
        "return_mae": float(np.abs(actual - predicted).mean()),
        "directional_hit_rate": float((np.sign(actual) == np.sign(predicted)).mean()),
        "flat_prediction_fraction": float((predicted == 0).mean()),
        "rank_ic": float(np.mean(ics)) if ics else None, "rank_ic_origins": len(ics),
        "interval_80_coverage": float(np.mean(coverage)), "normalized_pinball": float(np.mean(errors)),
        "per_asset_price_mae": {asset_id: float(np.abs(group.actual_price - group.price).mean()) for asset_id, group in frame.groupby("asset_id")},
    }


def walk_forward(store: Store, model, experiment: str, context: int = 128, horizon: int = 5,
                 folds: int = 12, stride: int | None = None, holdout_fraction: float = 0.2,
                 allow_retrospective: bool = False, asset_ids: list[str] | None = None,
                 technical: bool = False) -> dict:
    if context < 32 or not 1 <= horizon <= 64 or horizon >= context or folds < 1 or not 0.1 <= holdout_fraction <= 0.5:
        raise DataError("Invalid walk-forward context, horizon, folds, or holdout fraction")
    stride = horizon if stride is None else stride
    if stride < horizon:
        raise DataError("Stride must be at least the horizon so evaluation windows do not overlap")
    assets = store.assets()
    panel = align(store, asset_ids or list(assets), allow_retrospective=allow_retrospective)
    development_end = int(len(panel.values) * (1 - holdout_fraction))
    origins = list(range(context - 1 + (21 if technical else 0), development_end - horizon, stride))[-folds:]
    if not origins:
        raise DataError("Insufficient development history after reserving the temporal holdout")
    records, completed, skipped, inputs = [], [], [], []
    for origin in origins:
        session = panel.values.index[origin]
        try:
            payload = forecast_payload(panel, assets, model, experiment, context, horizon, origin, technical)
            # Evaluate equities for comparable cross-experiment metrics. Joint forecasts
            # still contain all assets and can be inspected in standalone forecast runs.
            fold_records = []
            for prediction in payload["forecasts"]:
                if prediction["asset_class"] != "equity":
                    continue
                asset_id, step = prediction["asset_id"], prediction["step"]
                future = panel.observations[asset_id][origin + 1:origin + horizon + 1]
                if any(b is None or b["corporate_action"] != "none" or b["roll_event"] == "roll" for b in future):
                    raise DataError(f"{asset_id}: missing, split, or unverified target window")
                actual = float(panel.values[asset_id].iloc[origin + step])
                if not np.isfinite(actual) or actual <= 0:
                    raise DataError(f"{asset_id}: invalid target price")
                fold_records.append({**prediction, "origin": session,
                                     "target_session": panel.values.index[origin + step], "actual_price": actual,
                                     "actual_return": actual / prediction["base_price"] - 1,
                                     "predicted_return": prediction["return"]})
            records.extend(fold_records)
            completed.append(session)
            inputs.append({"origin": session, "cutoff": payload["cutoff"], "input_sha256": payload["input_sha256"], "source_sha256": payload["source_sha256"]})
        except DataError as exc:
            skipped.append({"origin": session, "reason": str(exc)})
        print(f"{model.name}/{experiment}: {session} ({len(completed)} completed, {len(skipped)} skipped)", flush=True)
    payload = {
        "kind": "backtest", "created_at": now(), "governance": "RESEARCH_ONLY", "promotion_allowed": False,
        "data_mode": panel.mode, "status": "COMPLETE" if len(completed) == len(origins) else "PARTIAL" if completed else "BLOCKED",
        "interval": panel.interval,
        "model": model.metadata, "experiment": experiment, "context": context, "horizon": horizon, "stride": stride,
        "requested_folds": folds, "scheduled_folds": len(origins), "completed_folds": len(completed),
        "origins": completed, "skipped": skipped, "inputs": inputs, "records": records,
        "metrics": metrics(records, horizon), "metrics_by_horizon": {str(s): metrics(records, s) for s in range(1, horizon + 1)},
        "holdout": {"start": panel.values.index[development_end], "end": panel.values.index[-1],
                    "sessions": len(panel.values) - development_end, "evaluated": False,
                    "status": "RESERVED_IN_THIS_RUN_NOT_AN_INDEPENDENT_FUTURE_LOCKBOX"},
        "limitations": ["Zero-shot development evaluation; no fine-tuning or promotion", "Starter universe selection and survivorship bias",
                        "Metrics compare equity closes, not executable post-close trades", "No transaction-cost or profitability claim",
                        "A chronological holdout in existing history is not a fresh future lockbox",
                        "Quantile coverage is measured here; direction confidence is not calibrated"] + (
            ["Historical availability, revisions, and continuous-futures rolls are not verified"] if panel.mode == "RETROSPECTIVE_RESEARCH" else
            ["Synthetic data: these are software checks, not market accuracy"] if panel.mode == "SYNTHETIC_DEMO" else []
        ),
    }
    return {"run_id": store.save_run(payload), **payload}


def compare(store: Store, model, **options) -> dict:
    runs = [walk_forward(store, NaiveModel(), "stock_only", **{**options, "technical": False})]
    for experiment in EXPERIMENTS:
        runs.append(walk_forward(store, model, experiment, **options))
    common = set.intersection(*(set(run["origins"]) for run in runs))
    comparison = []
    for run in runs:
        selected = [r for r in run["records"] if r["origin"] in common]
        comparison.append({"run_id": run["run_id"], "model": run["model"]["engine"], "experiment": run["experiment"],
                           "metrics": metrics(selected, run["horizon"])})
    payload = {"kind": "comparison", "created_at": now(), "governance": "RESEARCH_ONLY", "promotion_allowed": False,
               "data_mode": runs[0]["data_mode"], "common_origins": sorted(common), "comparisons": comparison,
               "status": "COMPLETE" if common and all(r["status"] == "COMPLETE" for r in runs) else "PARTIAL" if common else "BLOCKED",
               "interpretation": "Compare macro_control against stock_covariates to isolate adding commodities. All rows use common equity targets and origins.",
               "limitations": runs[0]["limitations"]}
    return {"run_id": store.save_run(payload), **payload}
