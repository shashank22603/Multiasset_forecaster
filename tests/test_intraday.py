import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from timesfm_lab.domain import Bar, DataError, utc
from timesfm_lab.models import NaiveModel
from timesfm_lab.panel import align
from timesfm_lab.research import forecast_payload, prepare, walk_forward


@pytest.fixture
def intraday(store):
    selected = store.at_interval("5m")
    pending = []
    for asset_id, asset in selected.assets().items():
        for day in ("2025-10-20", "2025-10-21", "2025-10-22", "2025-10-23"):
            for i, stamp in enumerate(pd.date_range(f"{day} 09:20", f"{day} 15:30", freq="5min", tz="Asia/Kolkata")):
                close_at = stamp.to_pydatetime()
                pending.append({"asset_id": asset_id, "session": close_at.astimezone(ZoneInfo(asset.timezone)).date().isoformat(),
                                "close_at": close_at.isoformat(), "available_at": (close_at + timedelta(minutes=1)).isoformat(),
                                "retrieved_at": "2026-01-01T00:00:00Z", "close": 100 + i + len(asset_id),
                                "source": "intraday_fixture", "timing": "verified", "interval": "5m",
                                "corporate_action": "none", "roll_event": "none", "contract_id": "FIXTURE"})
    sha = selected.save_snapshot(json.dumps(pending).encode())
    selected.ingest([Bar(**bar, source_sha256=sha) for bar in pending])
    return selected


def test_five_minute_bars_coexist_with_daily_without_overwriting(intraday):
    assert len(intraday.bars("NSE:TCS", latest=True)) == 300
    assert len(intraday.at_interval("1d").bars("NSE:TCS", latest=True)) == 220
    closes = [b["close_at"] for b in intraday.bars("NSE:TCS")]
    assert len(set(closes)) == 300


def test_five_minute_alignment_uses_close_instead_of_date(intraday):
    panel = align(intraday, list(intraday.assets()))
    assert panel.interval == "5m"
    assert len(panel.values) == 300
    assert panel.values.index.is_unique
    for i in (10, 20, 90):
        close = utc(panel.observations["NSE:TCS"][i]["close_at"])
        assert utc(panel.cutoffs[i]) - close == timedelta(minutes=1)
        assert close == utc(panel.values.index[i])


def test_missing_entire_intraday_slot_is_preserved(intraday):
    timestamp = "2025-10-21T04:05:00+00:00"
    with intraday.connect() as conn:
        conn.execute("DELETE FROM bars WHERE interval='5m' AND close_at=?", (timestamp,))
    panel = align(intraday, list(intraday.assets()))
    assert timestamp in panel.values.index
    assert np.isnan(panel.values.loc[timestamp, "NSE:TCS"])
    with pytest.raises(DataError, match="missing"):
        prepare(panel, intraday.assets(), 110, 64, "stock_only")


def test_intraday_forecast_steps_and_walk_forward_targets(intraday):
    panel = align(intraday, list(intraday.assets()))
    run = forecast_payload(panel, intraday.assets(), NaiveModel(), "stock_only", 64, 5)
    assert run["interval"] == "5m"
    assert run["horizon_unit"] == "observed 5-minute bars"
    result = walk_forward(intraday, NaiveModel(), "stock_only", context=64, folds=2, horizon=5)
    assert result["interval"] == "5m"
    assert result["completed_folds"] == 2
    for record in result["records"]:
        # These selected test folds occur within a session, so five bars is 25 minutes.
        assert utc(record["target_session"]) - utc(record["origin"]) == timedelta(minutes=5 * record["step"])
    assert not intraday.at_interval("1d").runs()
    assert intraday.runs()[0]["interval"] == "5m"


def test_rejects_wrong_interval_and_non_boundary_close(intraday):
    bar = intraday.bars("NSE:TCS")[0]
    with pytest.raises(DataError, match="interval"):
        intraday.at_interval("1d").ingest([Bar(**bar)])
    bar["close_at"] = "2025-10-20T03:51:00+00:00"
    with pytest.raises(DataError, match="boundary"):
        intraday.ingest([Bar(**bar)])


def test_latest_complete_policy_keeps_the_lag_explicit(intraday, monkeypatch):
    from timesfm_lab.research import run_forecast
    monkeypatch.setattr("timesfm_lab.research.now", lambda: "2025-10-23T10:10:00+00:00")
    with intraday.connect() as conn:
        conn.execute("DELETE FROM bars WHERE interval='5m' AND asset_id='NSE:TCS' AND close_at='2025-10-23T10:00:00+00:00'")
    with pytest.raises(DataError, match="missing"):
        run_forecast(intraday, NaiveModel(), "stock_only", context=64)
    run = run_forecast(intraday, NaiveModel(), "stock_only", context=64, latest_complete=True)
    assert run["lag_bars"] == 1
    assert run["forecast_status"] == "LAGGED_INPUTS"
    assert run["origin"] == "2025-10-23T09:55:00+00:00"


def test_intraday_csv_import_requires_explicit_interval(intraday, tmp_path):
    import csv

    from timesfm_lab.collect import import_csv
    row = intraday.bars("NSE:TCS")[0].copy()
    row.pop("source_sha256")
    path = tmp_path / "intraday.csv"
    with path.open("w") as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    result = import_csv(intraday, path)
    assert result["added"] == 1
    assert len(intraday.bars("NSE:TCS", latest=True)) == 300
