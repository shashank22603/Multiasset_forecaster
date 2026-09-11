import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from timesfm_lab.domain import Bar, load_universe
from timesfm_lab.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "market")
    universe = load_universe()
    ids = ["NSE:NIFTY50", "NSE:RELIANCE", "NSE:TCS", "NSE:INFY", "COM:GOLD", "GLOBAL:SP500"]
    store.register({a: universe[a] for a in ids})
    dates = pd.bdate_range("2025-01-01", periods=220)
    rng = np.random.default_rng(55)
    pending = []
    for asset_id, asset in store.assets().items():
        price = 1000
        for session in dates:
            price *= np.exp(rng.normal(.0004, .01))
            close_at = datetime.combine(session.date(), datetime.strptime(asset.close_time, "%H:%M").time(), ZoneInfo(asset.timezone))
            pending.append({"asset_id": asset_id, "session": session.date().isoformat(),
                            "close_at": close_at.isoformat(), "available_at": (close_at + timedelta(minutes=15)).isoformat(),
                            "retrieved_at": "2026-01-01T00:00:00+00:00", "close": float(price), "source": "test_fixture",
                            "timing": "verified", "corporate_action": "none", "roll_event": "none", "contract_id": "GC_TEST" if asset_id == "COM:GOLD" else None})
    sha = store.save_snapshot(json.dumps(pending).encode())
    store.ingest([Bar(**row, source_sha256=sha) for row in pending])
    return store
