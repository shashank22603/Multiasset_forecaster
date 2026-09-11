from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .domain import Bar, DataError, load_universe, now, utc
from .store import Store


def create_demo(store: Store, sessions: int = 420, seed: int = 17) -> dict:
    if any(store.bars(a) for a in store.assets()):
        raise DataError("Demo generation requires an empty store so synthetic and market data cannot mix")
    store.register(load_universe())
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").date() - timedelta(days=2), periods=sessions)
    common = rng.normal(0.0002, 0.006, sessions)
    commodity = rng.normal(0, 0.009, sessions)
    initial = {"COM:GOLD": 2300, "COM:SILVER": 27, "COM:CRUDE": 75, "COM:NATGAS": 2.5,
               "COM:COPPER": 4.2, "FX:USDINR": 83, "NSE:NIFTY50": 21000, "NSE:INDIAVIX": 16}
    retrieved = now()
    pending = []
    for asset_id, asset in store.assets().items():
        previous = initial.get(asset_id, 900 if asset.asset_class == "equity" else 15000)
        for i, session in enumerate(dates):
            shock = 0.6 * common[i] + rng.normal(0.0001, 0.008)
            if asset.asset_class == "commodity":
                shock += commodity[i]
            elif i and asset.asset_class == "equity":
                shock += 0.18 * commodity[i - 1]
            opening = previous * np.exp(rng.normal(0, 0.002))
            closing = previous * np.exp(shock)
            close_at = datetime.combine(session.date(), datetime.strptime(asset.close_time, "%H:%M").time(), ZoneInfo(asset.timezone))
            pending.append({
                "asset_id": asset_id, "session": session.date().isoformat(), "close_at": close_at.isoformat(),
                "available_at": (close_at + timedelta(minutes=asset.availability_delay_minutes)).isoformat(),
                "retrieved_at": retrieved, "open": float(opening), "close": float(closing),
                "high": float(max(opening, closing) * (1 + rng.uniform(0.001, 0.009))),
                "low": float(min(opening, closing) * (1 - rng.uniform(0.001, 0.009))),
                "volume": float(rng.integers(100000, 3000000)) if asset.asset_class == "equity" else None,
                "source": f"synthetic:seed={seed}", "timing": "synthetic", "corporate_action": "none",
                "roll_event": "none", "contract_id": "SYNTHETIC" if asset.asset_class == "commodity" else None,
            })
            previous = closing
    pending = [p for p in pending if utc(p["available_at"]) <= utc(retrieved)]
    sha = store.save_snapshot(json.dumps({"synthetic": True, "seed": seed, "observations": pending}, sort_keys=True).encode())
    added = store.ingest([Bar(**p, source_sha256=sha) for p in pending])
    for asset_id in store.assets():
        store.set_status(asset_id, {"status": "SYNTHETIC_DEMO", "provider": "synthetic", "seed": seed})
    return {"data_mode": "SYNTHETIC_DEMO", "added": added, "seed": seed}
