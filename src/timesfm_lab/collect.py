from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .domain import Bar, DataError, now, utc
from .store import Store


def collect_yahoo(store: Store, asset_ids: list[str], start: str | None = None, end: str | None = None) -> dict:
    """Daily public research data; publication times and continuous rolls are unverified.

    Keep raw OHLC and split events. Never use today's adjusted-close history as PIT history.
    The retained snapshot is the provider library's returned table, not an HTTP wire capture.
    """
    import yfinance as yf

    if start is None:
        start = (utc(now()) - timedelta(days=30)).date().isoformat() if store.interval == "5m" else "2024-01-01"
    if store.interval == "5m" and datetime.fromisoformat(start).date() < (utc(now()) - timedelta(days=59)).date():
        raise DataError("Yahoo 5-minute history is limited to the recent 60 days; choose a recent start or import an archival feed CSV")
    yf.set_tz_cache_location(str(store.root / "provider_cache"))
    assets = store.assets()
    outcomes = {}
    for asset_id in asset_ids:
        asset = assets[asset_id]
        try:
            frame = yf.Ticker(asset.symbol).history(
                start=start, end=end, interval=store.interval, auto_adjust=False, prepost=False,
                back_adjust=False, actions=True, repair=False, timeout=20, raise_errors=True,
            )
            if frame.empty:
                raise DataError("Provider returned no observations")
            retrieved = now()
            snapshot = json.dumps({
                "provider": "yfinance", "library_version": yf.__version__, "symbol": asset.symbol,
                "start": start, "end": end, "retrieved_at": retrieved,
                "interval": store.interval,
                "table": json.loads(frame.reset_index().to_json(orient="records", date_format="iso")),
            }, sort_keys=True).encode()
            sha = store.save_snapshot(snapshot)
            bars, rejected, pending = [], [], 0

            def number(row, key):
                value = row.get(key)
                return float(value) if value is not None and math.isfinite(float(value)) else None

            for stamp, row in frame.iterrows():
                # Daily provider labels designate exchange sessions, not publication instants.
                session = stamp.date().isoformat()
                if store.interval == "5m":
                    close_at = stamp.to_pydatetime() + timedelta(minutes=5)
                    # Provider intraday timestamps mark bar opens. Retain the completed
                    # five-minute close and an explicit assumed one-minute feed delay.
                    session = close_at.astimezone(ZoneInfo(asset.timezone)).date().isoformat()
                    available = close_at + timedelta(minutes=1)
                else:
                    close_at = datetime.fromisoformat(f"{session}T{asset.close_time}:00").replace(tzinfo=ZoneInfo(asset.timezone))
                    available = close_at + timedelta(minutes=asset.availability_delay_minutes)
                if utc(available) > utc(retrieved):
                    pending += 1  # Never ingest today's incomplete daily candle.
                    continue
                close = number(row, "Close")
                if close is None:
                    rejected.append({"session": session, "reason": "missing close"})
                    continue
                split = number(row, "Stock Splits")
                bar = Bar(
                    asset_id=asset_id, session=session, close_at=close_at.isoformat(),
                    available_at=available.isoformat(), retrieved_at=retrieved, close=close,
                    open=number(row, "Open"), high=number(row, "High"), low=number(row, "Low"),
                    volume=number(row, "Volume"), source=f"yahoo:{asset.symbol}", source_sha256=sha,
                    timing="assumed", corporate_action="split" if split else "none" if split == 0 else "unknown",
                    interval=store.interval,
                    roll_event="unknown" if asset.series_kind == "continuous_future" else "none",
                )
                try:
                    bar.validate(asset)
                    bars.append(bar)
                except DataError as exc:
                    rejected.append({"session": session, "reason": str(exc)})
            added = store.ingest(bars)
            if not bars:
                raise DataError("No complete, valid observations")
            outcome = {
                "status": "PARTIAL" if rejected else "RETROSPECTIVE_RESEARCH", "provider": "yahoo",
                "added": added, "observations": len(bars), "start": bars[0].session, "end": bars[-1].session,
                "interval": store.interval, "first_close_at": bars[0].close_at, "last_close_at": bars[-1].close_at,
                "pending_incomplete": pending, "rejected": rejected, "source_sha256": sha,
                "limitations": ["assumed_publication_time", "historical_revisions_unverified"] + (
                    ["continuous_futures_rolls_unverified", "USD_proxy_not_MCX"] if asset.series_kind == "continuous_future" else []
                ),
            }
        except Exception as exc:
            outcome = {"status": "ERROR", "provider": "yahoo", "error": f"{type(exc).__name__}: {exc}"}
        store.set_status(asset_id, outcome)
        outcomes[asset_id] = outcome
        print(f"{asset_id}: {outcome['status']} ({outcome.get('observations', 0)} observations)", flush=True)
    return outcomes


def import_csv(store: Store, path: str | Path) -> dict:
    """Import explicit availability and action provenance; all-or-nothing validation."""
    raw = Path(path).read_bytes()
    sha = store.save_snapshot(raw, "csv")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows:
        raise DataError("CSV has no observations")
    bars = []
    for number, row in enumerate(rows, 2):
        try:
            payload = {key: value for key, value in row.items() if value != ""}
            payload["source_sha256"] = sha
            payload.setdefault("retrieved_at", now())
            for key in ("open", "high", "low", "close", "volume"):
                if key in payload:
                    payload[key] = float(payload[key])
            bars.append(Bar(**payload))
        except (TypeError, ValueError) as exc:
            raise DataError(f"CSV row {number}: {exc}") from exc
    count = store.ingest(bars)
    for asset_id in {b.asset_id for b in bars}:
        store.set_status(asset_id, {"status": "IMPORTED", "provider": "csv", "source_sha256": sha})
    return {"added": count, "source_sha256": sha}
