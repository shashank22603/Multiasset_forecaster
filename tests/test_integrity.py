import json
from dataclasses import replace

import numpy as np
import pytest

from timesfm_lab.collect import import_csv
from timesfm_lab.domain import Bar, DataError, load_universe, utc
from timesfm_lab.panel import align, features
from timesfm_lab.research import prepare


def revision(store, original, **changes):
    payload = {**original, **changes}
    payload["source_sha256"] = store.save_snapshot(json.dumps(payload).encode())
    store.ingest([Bar(**payload)])


def test_universe_represents_requested_asset_classes():
    assets = load_universe()
    assert {a.asset_class for a in assets.values()} == {"equity", "india_index", "commodity", "global_index", "fx"}
    assert {a.asset_id for a in assets.values() if a.asset_class == "commodity"} == {"COM:GOLD", "COM:SILVER", "COM:CRUDE", "COM:NATGAS", "COM:COPPER"}
    assert all(a.fno_member is None for a in assets.values())
    assert all(a.currency == "USD" and a.series_kind == "continuous_future" for a in assets.values() if a.asset_class == "commodity")


def test_timezone_required_and_dst_handled():
    with pytest.raises(DataError, match="timezone"):
        utc("2025-01-02T16:00:00")
    assert utc("2025-07-02T16:00:00-04:00").hour == 20
    assert utc("2025-01-02T16:00:00-05:00").hour == 21


@pytest.mark.parametrize("change", [{"close": float("nan")}, {"volume": -1}, {"high": 1}, {"close": None}, {"available_at": "2024-01-01T00:00:00Z"}, {"retrieved_at": "2024-01-01T00:00:00Z"}, {"price_basis": "adjusted"}])
def test_bad_bars_are_rejected_atomically(store, change):
    base = store.bars("NSE:RELIANCE")[0]
    before = len(store.bars("NSE:RELIANCE"))
    with pytest.raises(DataError):
        store.ingest([Bar(**{**base, **change})])
    assert len(store.bars("NSE:RELIANCE")) == before


def test_snapshot_hash_and_idempotence(store):
    original = store.bars("NSE:TCS")[0]
    assert store.ingest([Bar(**original)]) == 0
    raw = next((store.root / "raw").glob(f"{original['source_sha256']}.*"))
    raw.write_text("corrupted")
    with pytest.raises(DataError, match="hash"):
        store.ingest([Bar(**original)])


def test_identity_cannot_relabel_existing_currency(store):
    assets = store.assets()
    assets["COM:GOLD"] = replace(assets["COM:GOLD"], currency="INR")
    with pytest.raises(DataError, match="currency"):
        store.register(assets)


def test_us_same_day_close_is_not_available_in_india(store):
    panel = align(store, list(store.assets()))
    day = "2025-01-08"
    i = panel.values.index.get_loc(day)
    selected = panel.observations["GLOBAL:SP500"][i]
    assert selected["session"] == "2025-01-07"
    assert utc(selected["available_at"]) < utc(panel.cutoffs[i])
    assert panel.observations["NSE:TCS"][i]["session"] == day


def test_historical_revision_does_not_rewrite_past_context(store):
    before = align(store, list(store.assets()))
    original = store.bars("NSE:TCS")[5]
    revision(store, original, close=9999, available_at="2025-12-01T00:00:00Z", retrieved_at="2026-01-02T00:00:00Z")
    after = align(store, list(store.assets()))
    np.testing.assert_array_equal(before.values.to_numpy(), after.values.to_numpy())


def test_missing_equity_is_not_filled_from_previous_session(store):
    with store.connect() as conn:
        conn.execute("DELETE FROM bars WHERE asset_id='NSE:TCS' AND session='2025-01-08'")
    panel = align(store, list(store.assets()))
    assert np.isnan(panel.values.loc["2025-01-08", "NSE:TCS"])
    assert panel.observations["NSE:TCS"][panel.values.index.get_loc("2025-01-08")] is None


def test_global_stale_value_remains_missing(store):
    with store.connect() as conn:
        conn.execute("DELETE FROM bars WHERE asset_id='GLOBAL:SP500' AND session BETWEEN '2025-01-06' AND '2025-01-17'")
    panel = align(store, list(store.assets()))
    assert np.isnan(panel.values.loc["2025-01-15", "GLOBAL:SP500"])


def test_verified_mode_does_not_use_assumed_downloads(store):
    with store.connect() as conn:
        rows = conn.execute("SELECT asset_id,session,source_sha256,payload FROM bars").fetchall()
        for asset, session, sha, text in rows:
            payload = json.loads(text)
            payload["timing"] = "assumed"
            conn.execute("UPDATE bars SET payload=? WHERE asset_id=? AND session=? AND source_sha256=?", (json.dumps(payload), asset, session, sha))
    panel = align(store, list(store.assets()))
    assert panel.values.isna().all().all()
    retrospective = align(store, list(store.assets()), allow_retrospective=True)
    assert retrospective.mode == "RETROSPECTIVE_RESEARCH"
    assert retrospective.values.notna().any().any()


def test_context_rejects_split_and_unknown_roll(store):
    panel = align(store, list(store.assets()))
    origin = 150
    panel.observations["NSE:TCS"][origin - 10]["corporate_action"] = "split"
    with pytest.raises(DataError, match="split"):
        prepare(panel, store.assets(), origin, 64, "stock_only")
    panel.observations["NSE:TCS"][origin - 10]["corporate_action"] = "none"
    panel.observations["COM:GOLD"][origin - 10]["roll_event"] = "unknown"
    with pytest.raises(DataError, match="contract"):
        prepare(panel, store.assets(), origin, 64, "stock_covariates")


def test_features_preserve_volume_and_return_gaps(store):
    panel = align(store, list(store.assets()))
    panel.values.loc["2025-01-08", "NSE:TCS"] = np.nan
    result = features(panel)
    stock = result[result.asset_id == "NSE:TCS"].set_index("session")
    assert np.isnan(stock.loc["2025-01-08", "return_1"])
    assert np.isnan(stock.loc["2025-01-09", "return_1"])
    assert stock.volume.isna().all()
    assert stock.iloc[:20].volatility_20.isna().all()


def test_import_requires_availability_and_retains_raw_source(store, tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("asset_id,session,close\nNSE:TCS,2025-12-01,150\n")
    with pytest.raises(DataError, match="row 2"):
        import_csv(store, path)
