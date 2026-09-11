from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import Asset, Bar, DataError, digest, now


class Store:
    def __init__(self, root: str | Path = "data", interval: str = "1d"):
        if interval not in {"1d", "5m"}:
            raise DataError("Supported intervals are 1d and 5m")
        self.interval = interval
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "research.sqlite3"
        with self.connect() as conn:
            # Preserve the previous daily-only tables during the one-time migration.
            columns = [row[1] for row in conn.execute("PRAGMA table_info(bars)")]
            if columns and "interval" not in columns:
                conn.executescript("""
                    ALTER TABLE bars RENAME TO bars_daily_v1;
                    DROP INDEX IF EXISTS bars_lookup;
                    ALTER TABLE collection_status RENAME TO collection_status_daily_v1;
                """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS assets(asset_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bars(
                    asset_id TEXT NOT NULL REFERENCES assets(asset_id), session TEXT NOT NULL,
                    available_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL, payload TEXT NOT NULL,
                    interval TEXT NOT NULL, close_at TEXT NOT NULL,
                    PRIMARY KEY(asset_id, interval, close_at, source_sha256));
                CREATE INDEX IF NOT EXISTS bars_lookup ON bars(asset_id, interval, close_at, available_at);
                CREATE TABLE IF NOT EXISTS collection_status(
                    asset_id TEXT NOT NULL, checked_at TEXT NOT NULL, payload TEXT NOT NULL,
                    interval TEXT NOT NULL, PRIMARY KEY(asset_id, interval));
                CREATE TABLE IF NOT EXISTS runs(
                    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)
            if columns and "interval" not in columns:
                conn.execute("INSERT INTO bars SELECT *, '1d', json_extract(payload, '$.close_at') FROM bars_daily_v1")
                conn.execute("INSERT INTO collection_status SELECT *, '1d' FROM collection_status_daily_v1")

    def at_interval(self, interval: str) -> Store:
        return Store(self.root, interval=interval)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def register(self, assets: dict[str, Asset]):
        from dataclasses import asdict

        with self.connect() as conn:
            for asset in assets.values():
                old = conn.execute("SELECT payload FROM assets WHERE asset_id=?", (asset.asset_id,)).fetchone()
                if old and conn.execute("SELECT 1 FROM bars WHERE asset_id=? LIMIT 1", (asset.asset_id,)).fetchone():
                    original = json.loads(old[0])
                    for key in ("symbol", "exchange", "currency", "unit", "timezone", "series_kind", "asset_class"):
                        if original[key] != getattr(asset, key):
                            raise DataError(f"Cannot change {key} for stored identity {asset.asset_id}; create a new asset ID")
                conn.execute("INSERT OR REPLACE INTO assets VALUES (?,?)", (asset.asset_id, json.dumps(asdict(asset))))

    def assets(self) -> dict[str, Asset]:
        with self.connect() as conn:
            return {row[0]: Asset(**json.loads(row[1])) for row in conn.execute("SELECT * FROM assets ORDER BY asset_id")}

    def save_snapshot(self, content: bytes, suffix: str = "json") -> str:
        sha = digest(content)
        directory = self.root / "raw"
        directory.mkdir(exist_ok=True)
        path = directory / f"{sha}.{suffix}"
        if not path.exists():
            path.write_bytes(content)
        return sha

    def ingest(self, bars: list[Bar]) -> int:
        assets = self.assets()
        verified_snapshots = set()
        # Validate the whole batch before starting a transaction.
        for bar in bars:
            if bar.interval != self.interval:
                raise DataError(f"Bar interval {bar.interval} does not match selected store interval {self.interval}")
            if bar.asset_id not in assets:
                raise DataError(f"Unknown asset: {bar.asset_id}")
            bar.validate(assets[bar.asset_id])
            if bar.source_sha256 not in verified_snapshots:
                candidates = list((self.root / "raw").glob(f"{bar.source_sha256}.*"))
                if not candidates or digest(candidates[0].read_bytes()) != bar.source_sha256:
                    raise DataError("Raw source snapshot is missing or its hash does not match")
                verified_snapshots.add(bar.source_sha256)
        with self.connect() as conn:
            before = conn.total_changes
            for bar in bars:
                payload = bar.to_dict()
                conn.execute("INSERT OR IGNORE INTO bars VALUES (?,?,?,?,?,?,?,?)", (
                    bar.asset_id, bar.session, payload["available_at"], payload["retrieved_at"],
                    bar.source_sha256, json.dumps(payload, allow_nan=False), bar.interval, payload["close_at"],
                ))
            return conn.total_changes - before

    def bars(self, asset_id: str, latest: bool = False) -> list[dict]:
        with self.connect() as conn:
            rows = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM bars WHERE asset_id=? AND interval=? ORDER BY close_at, retrieved_at, available_at", (asset_id, self.interval)
            )]
        if latest:
            return list({row["close_at"]: row for row in rows}.values())
        return rows

    def set_status(self, asset_id: str, payload: dict):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO collection_status VALUES (?,?,?,?)", (asset_id, now(), json.dumps(payload), self.interval))

    def statuses(self) -> dict:
        with self.connect() as conn:
            return {a: {"checked_at": checked, **json.loads(payload)} for a, checked, payload in conn.execute("SELECT asset_id,checked_at,payload FROM collection_status WHERE interval=?", (self.interval,))}

    def save_run(self, payload: dict) -> str:
        payload["interval"] = self.interval
        serialized = json.dumps(payload, allow_nan=False, sort_keys=True)
        run_id = f"{payload['kind']}-{digest(serialized.encode())[:16]}"
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?)", (run_id, payload["created_at"], payload["kind"], serialized))
        directory = self.root / "runs"
        directory.mkdir(exist_ok=True)
        (directory / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, **payload}, indent=2, allow_nan=False))
        return run_id

    def runs(self, limit: int = 25) -> list[dict]:
        with self.connect() as conn:
            return [{"run_id": r[0], **json.loads(r[1])} for r in conn.execute(
                "SELECT run_id,payload FROM runs WHERE COALESCE(json_extract(payload, '$.interval'), '1d')=? ORDER BY created_at DESC LIMIT ?", (self.interval, limit)
            )]

    def run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return {"run_id": run_id, **json.loads(row[0])} if row else None
