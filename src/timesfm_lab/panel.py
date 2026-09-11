from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from .domain import DataError, now, utc
from .store import Store


@dataclass
class Panel:
    values: pd.DataFrame
    ages: pd.DataFrame
    observations: dict[str, list[dict | None]]
    cutoffs: list[str]
    mode: str
    source_hashes: list[str]
    interval: str = "1d"


def align(store: Store, asset_ids: list[str], anchor: str = "NSE:NIFTY50", allow_retrospective: bool = False,
          decision_delay_minutes: int | None = None, until: str | None = None) -> Panel:
    """As-of join on the observed anchor calendar. No invented sessions or future fill.

    Each historical cell uses only versions whose availability precedes that row's
    decision time. Later corrections do not rewrite old context cells.
    """
    assets = store.assets()
    decision_delay_minutes = (1 if store.interval == "5m" else 30) if decision_delay_minutes is None else decision_delay_minutes
    if anchor not in assets or any(asset_id not in assets for asset_id in asset_ids):
        raise DataError("Unregistered panel asset or anchor")
    if decision_delay_minutes < 0:
        raise DataError("Decision delay must be non-negative")
    anchor_bars = store.bars(anchor, latest=True)
    if not anchor_bars:
        raise DataError(f"No anchor calendar observations: {anchor}")
    observed_now = utc(until or now())
    # Union of observed domestic sessions prevents a hole in the anchor feed from
    # silently deleting a session that was observed by another domestic instrument.
    domestic = {b["close_at"]: b for b in anchor_bars}
    for asset_id, asset in assets.items():
        if asset.exchange == assets[anchor].exchange and asset.asset_class in {"equity", "india_index"}:
            domestic.update({b["close_at"]: b for b in store.bars(asset_id, latest=True)})
    if store.interval == "5m":
        by_date = {}
        for bar in domestic.values():
            by_date.setdefault(bar["session"], []).append(utc(bar["close_at"]))
        closes = [stamp.to_pydatetime() for group in by_date.values() for stamp in pd.date_range(min(group), max(group), freq="5min")]
        schedule = [(close.isoformat(), close + timedelta(minutes=decision_delay_minutes)) for close in sorted(closes)]
    else:
        by_date = {b["session"]: utc(b["close_at"]) for b in sorted(domestic.values(), key=lambda b: b["close_at"])}
        schedule = [(session, close + timedelta(minutes=decision_delay_minutes)) for session, close in sorted(by_date.items())]
    schedule = [(session, cutoff) for session, cutoff in schedule if cutoff <= observed_now]
    if not schedule:
        raise DataError("No completed anchor sessions")
    index = pd.Index([s for s, _ in schedule], name="session")
    values = pd.DataFrame(np.nan, index=index, columns=asset_ids)
    ages = values.copy()
    selected, hashes, timings = {}, set(), set()
    for asset_id in asset_ids:
        asset = assets[asset_id]
        bars = store.bars(asset_id)
        if not allow_retrospective:
            bars = [b for b in bars if b["timing"] == "verified"]
        candidates = sorted([(utc(b["available_at"]), utc(b["close_at"]), b) for b in bars], key=lambda x: (x[0], x[1], x[2]["retrieved_at"]))
        cursor, by_session, cells = 0, {}, []
        for session, cutoff in schedule:
            while cursor < len(candidates) and candidates[cursor][0] <= cutoff:
                _, close_at, bar = candidates[cursor]
                by_session[close_at] = bar
                cursor += 1
            current = by_session[max(by_session)] if by_session else None
            if current:
                age = (cutoff - utc(current["close_at"])).total_seconds() / 3600
                exact_required = asset.asset_class in {"equity", "india_index"} and asset.exchange == assets[anchor].exchange
                exact_match = utc(current["close_at"]) == cutoff - timedelta(minutes=decision_delay_minutes) if store.interval == "5m" else current["session"] == session
                if age > asset.max_age_hours or (exact_required and not exact_match):
                    current = None
                else:
                    values.loc[session, asset_id] = current["close"]
                    ages.loc[session, asset_id] = age
                    hashes.add(current["source_sha256"])
                    timings.add(current["timing"])
            cells.append(current)
        selected[asset_id] = cells
    if "synthetic" in timings and len(timings) > 1:
        raise DataError("Synthetic and real observations cannot be mixed in one panel")
    mode = "SYNTHETIC_DEMO" if "synthetic" in timings else "RETROSPECTIVE_RESEARCH" if "assumed" in timings else "POINT_IN_TIME_RESEARCH"
    return Panel(values, ages, selected, [cutoff.isoformat() for _, cutoff in schedule], mode, sorted(hashes), store.interval)


def features(panel: Panel) -> pd.DataFrame:
    """Past-only OHLCV, technical, and cross-asset features; missing stays missing."""
    frames = []
    returns = panel.values.pct_change(fill_method=None)
    for asset_id in panel.values:
        rows = panel.observations[asset_id]
        price = panel.values[asset_id]
        ret = returns[asset_id].copy()
        breaks = pd.Series([bool(b and (b["corporate_action"] == "split" or b["roll_event"] == "roll")) for b in rows], index=price.index)
        ret = ret.mask(breaks)
        item = pd.DataFrame({"session": price.index, "asset_id": asset_id, "close": price.values, "return_1": ret.values})
        for key in ("open", "high", "low", "volume"):
            item[key] = [b[key] if b else np.nan for b in rows]
        item["age_hours"] = panel.ages[asset_id].values
        # Window statistics require a full uninterrupted history; split/roll gaps propagate.
        for window in (5, 20):
            item[f"momentum_{window}"] = (1 + ret).rolling(window, min_periods=window).apply(np.prod, raw=True).values - 1
            item[f"volatility_{window}"] = ret.rolling(window, min_periods=window).std().values * np.sqrt(252 * (75 if panel.interval == "5m" else 1))
        delta = price.diff().mask(breaks)
        gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss)
        rsi = rsi.mask((loss == 0) & (gain == 0), 50)
        item["rsi_14"] = rsi.values
        item["sma_20"] = price.where(~breaks).rolling(20).mean().values
        item["intraday_range"] = (item["high"] - item["low"]) / item["close"].abs().replace(0, np.nan)
        for context_id in panel.values:
            if context_id != asset_id:
                other = returns[context_id].copy()
                other_breaks = [bool(b and (b["corporate_action"] == "split" or b["roll_event"] == "roll")) for b in panel.observations[context_id]]
                other.loc[other_breaks] = np.nan
                item[f"correlation_20:{context_id}"] = ret.rolling(20).corr(other).values
        frames.append(item)
    result = pd.concat(frames, ignore_index=True)
    for column in result.columns.difference(["session", "asset_id"]):
        result[column] = pd.to_numeric(result[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return result


def audit(store: Store) -> dict:
    assets, statuses = store.assets(), store.statuses()
    try:
        panel = align(store, list(assets), allow_retrospective=True)
    except DataError:
        panel = None
    entries = []
    for asset_id, asset in assets.items():
        bars = store.bars(asset_id, latest=True)
        latest = bars[-1] if bars else None
        flags = []
        if not bars:
            flags.append("NO_DATA")
        if any(b["timing"] != "verified" for b in bars):
            flags.append("PUBLICATION_TIMING_UNVERIFIED")
        if any(b["timing"] == "synthetic" for b in bars):
            flags.append("SYNTHETIC_DEMO")
        if asset.series_kind == "continuous_future" and any(b["roll_event"] == "unknown" or b["contract_id"] is None for b in bars):
            flags.append("CONTRACT_ROLLS_UNVERIFIED")
        if asset.asset_class == "equity" and any(b["corporate_action"] == "unknown" for b in bars):
            flags.append("CORPORATE_ACTIONS_UNVERIFIED")
        age = (utc(now()) - utc(latest["close_at"])).total_seconds() / 3600 if latest else None
        if age is not None and age > asset.max_age_hours:
            flags.append("STALE")
        prior = bars[-2]["close"] if len(bars) > 1 else None
        entries.append({
            **asset.__dict__, "observations": len(bars), "start": bars[0]["session"] if bars else None,
            "end": latest["session"] if latest else None, "close": latest["close"] if latest else None,
            "last_close_at": latest["close_at"] if latest else None,
            "missing_recent_128": int(panel.values[asset_id].tail(128).isna().sum()) if panel is not None else None,
            "aligned_observations": int(panel.values[asset_id].notna().sum()) if panel is not None else 0,
            "change": latest["close"] / prior - 1 if prior and latest else None,
            "age_hours": age, "flags": flags, "collection": statuses.get(asset_id, {"status": "NOT_COLLECTED"}),
        })
    return {"created_at": now(), "interval": store.interval, "governance": "RESEARCH_ONLY", "promotion_allowed": False,
            "calendar": "Observed domestic session union; interior five-minute gaps retained; not an independently verified exchange calendar",
            "registered": len(entries), "populated": sum(e["observations"] > 0 for e in entries),
            "verified_timing": sum(e["observations"] > 0 and "PUBLICATION_TIMING_UNVERIFIED" not in e["flags"] for e in entries),
            "universe_scope": "Configured starter universe; not a verified complete NSE or F&O universe",
            "assets": entries}
