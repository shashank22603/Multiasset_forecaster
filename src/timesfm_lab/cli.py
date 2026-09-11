from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .domain import DataError, load_universe
from .store import Store


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Multi-asset TimesFM 3.0 research")
    command.add_argument("--data-dir", default="data", help="Isolated SQLite, raw snapshots, and run directory")
    command.add_argument("--universe", help="Custom universe JSON (default: packaged 27-asset starter universe)")
    command.add_argument("--interval", choices=["1d", "5m"], default="1d", help="Bar resolution, stored separately for each interval")
    sub = command.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Register asset identities")
    sub.add_parser("audit", help="Actual per-asset coverage, freshness and provenance")
    collect = sub.add_parser("collect", help="Collect retrospective public daily or five-minute history")
    collect.add_argument("--start", help="Default: 2024-01-01 for daily, last 30 days for five-minute data")
    collect.add_argument("--end")
    collect.add_argument("--assets", help="Comma-separated registered asset IDs")
    load = sub.add_parser("import-csv", help="Import explicitly timestamped observations with provenance")
    load.add_argument("path")
    sub.add_parser("demo", help="Create isolated synthetic data in <data-dir>/demo")
    model = sub.add_parser("download-model", help="Download and pin official TimesFM 3.0 weights")
    model.add_argument("--revision")
    serve = sub.add_parser("serve", help="Serve the local dashboard and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8050)
    for name in ("forecast", "backtest", "compare", "features"):
        run = sub.add_parser(name)
        run.add_argument("--allow-retrospective", action="store_true", help="Explicitly permit historical data with unverified publication timing")
        run.add_argument("--assets", help="Comma-separated registered asset IDs; NIFTY50 remains the observed calendar anchor")
        run.add_argument("--profile", choices=["core", "full"], default="core", help="Explicit 17-asset core or entire registered universe")
        if name == "features":
            run.add_argument("--output", default="artifacts/features.csv")
            continue
        run.add_argument("--model", choices=["timesfm", "naive"], default="timesfm")
        run.add_argument("--context", type=int, default=128)
        run.add_argument("--horizon", type=int, default=5)
        run.add_argument("--technical", action="store_true")
        if name == "forecast":
            run.add_argument("--latest-complete", action="store_true", help="Explicitly permit a lagged complete context within the latest session; origin and lag are recorded")
        if name != "compare":
            run.add_argument("--experiment", choices=["stock_only", "macro_control", "stock_covariates", "joint"], default="joint" if name == "forecast" else "stock_covariates")
        if name != "forecast":
            run.add_argument("--folds", type=int, default=12)
            run.add_argument("--stride", type=int)
            run.add_argument("--holdout-fraction", type=float, default=0.2)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "download-model":
            from .models import download_model
            result = download_model(revision=args.revision)
        elif args.command == "demo":
            if args.interval != "1d":
                raise DataError("Synthetic demo uses daily bars; collect or import real 5-minute data for intraday research")
            from .demo import create_demo
            from .models import NaiveModel
            from .research import run_forecast, walk_forward
            store = Store(Path(args.data_dir) / "demo")
            result = create_demo(store)
            run_forecast(store, NaiveModel(), allow_retrospective=True)
            walk_forward(store, NaiveModel(), "stock_only", allow_retrospective=True)
            result["serve_command"] = f"timesfm-lab --data-dir {store.root} serve"
        else:
            store = Store(args.data_dir, interval=args.interval)
            store.register(load_universe(args.universe))
            if args.command == "init":
                result = {"registered": len(store.assets()), "data_dir": str(store.root.resolve())}
            elif args.command == "audit":
                from .panel import audit
                result = audit(store)
                (store.root / f"coverage-{store.interval}.json").write_text(json.dumps(result, indent=2))
            elif args.command == "collect":
                from .collect import collect_yahoo
                result = collect_yahoo(store, selected_assets(args, store), args.start, args.end)
            elif args.command == "import-csv":
                from .collect import import_csv
                result = import_csv(store, args.path)
            elif args.command == "serve":
                import uvicorn

                from .api import create_app
                uvicorn.run(create_app(store), host=args.host, port=args.port)
                return 0
            elif args.command == "features":
                from .panel import align, features
                panel = align(store, selected_assets(args, store), allow_retrospective=args.allow_retrospective)
                table = features(panel)
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                table.to_csv(output, index=False)
                result = {"path": str(output), "rows": len(table), "data_mode": panel.mode}
                output.with_suffix(".manifest.json").write_text(json.dumps({**result, "source_sha256": panel.source_hashes}, indent=2))
            else:
                from .models import NaiveModel, TimesFMModel
                from .research import compare, run_forecast, walk_forward
                if args.model == "naive" and (args.technical or args.command == "compare" or args.experiment not in {"stock_only", "joint"}):
                    raise DataError("Naive baseline supports stock_only or joint without covariates; compare requires TimesFM")
                model = NaiveModel() if args.model == "naive" else TimesFMModel()
                options = {"context": args.context, "horizon": args.horizon, "allow_retrospective": args.allow_retrospective,
                           "asset_ids": selected_assets(args, store), "technical": args.technical}
                if args.command == "forecast":
                    result = run_forecast(store, model, args.experiment, latest_complete=args.latest_complete, **options)
                else:
                    options.update(folds=args.folds, stride=args.stride, holdout_fraction=args.holdout_fraction)
                    result = compare(store, model, **options) if args.command == "compare" else walk_forward(store, model, args.experiment, **options)
                result = {key: value for key, value in result.items() if key not in {"forecasts", "records", "inputs", "ranking"}}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if isinstance(result, dict) and (result.get("status") == "BLOCKED" or any(isinstance(v, dict) and v.get("status") == "ERROR" for v in result.values())) else 0
    except (DataError, KeyError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def selected_assets(args, store: Store) -> list[str]:
    from .profiles import core_assets
    selected = args.assets.split(",") if args.assets else core_assets(store.assets()) if getattr(args, "profile", "full") == "core" else list(store.assets())
    if not selected or len(set(selected)) != len(selected) or any(a not in store.assets() for a in selected):
        raise DataError("Select unique, registered asset IDs")
    return selected
