from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

ASSET_CLASSES = {"equity", "india_index", "commodity", "global_index", "fx"}


class DataError(ValueError):
    """A data or forecasting contract could not be satisfied."""


def utc(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise DataError("Timestamps must include an explicit timezone")
    return dt.astimezone(timezone.utc)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Asset:
    asset_id: str
    name: str
    asset_class: str
    symbol: str
    exchange: str
    timezone: str
    currency: str
    unit: str
    close_time: str
    availability_delay_minutes: int
    series_kind: str = "spot"
    sector: str | None = None
    fno_member: bool | None = None
    max_age_hours: int = 96

    def __post_init__(self):
        if self.asset_class not in ASSET_CLASSES or not self.asset_id:
            raise DataError("Invalid asset identity or asset class")
        ZoneInfo(self.timezone)
        datetime.strptime(self.close_time, "%H:%M")
        if self.series_kind not in {"spot", "equity", "index", "continuous_future"}:
            raise DataError("Invalid series kind")
        if self.max_age_hours <= 0 or self.availability_delay_minutes < 0:
            raise DataError("Invalid observation age or availability delay")


def load_universe(path: str | Path | None = None) -> dict[str, Asset]:
    content = Path(path).read_text() if path else files("timesfm_lab").joinpath("default_universe.json").read_text()
    assets = [Asset(**row) for row in json.loads(content)["assets"]]
    if len({a.asset_id for a in assets}) != len(assets):
        raise DataError("Duplicate asset identifiers")
    return {a.asset_id: a for a in assets}


@dataclass(frozen=True)
class Bar:
    asset_id: str
    session: str
    close_at: str
    available_at: str
    retrieved_at: str
    close: float
    source: str
    source_sha256: str
    timing: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    price_basis: str = "raw"
    corporate_action: str = "unknown"
    contract_id: str | None = None
    roll_event: str = "unknown"
    interval: str = "1d"

    def validate(self, asset: Asset) -> None:
        if self.interval not in {"1d", "5m"}:
            raise DataError("Supported bar intervals are 1d and 5m")
        if self.asset_id != asset.asset_id:
            raise DataError("Bar does not match the registered asset")
        date.fromisoformat(self.session)
        close_at, available_at, retrieved_at = map(utc, (self.close_at, self.available_at, self.retrieved_at))
        if self.interval == "5m" and (close_at.minute % 5 != 0 or close_at.second or close_at.microsecond):
            raise DataError("Five-minute bars must close on a five-minute boundary")
        if available_at < close_at or retrieved_at < available_at:
            raise DataError("Require close_at <= available_at <= retrieved_at")
        if close_at.astimezone(ZoneInfo(asset.timezone)).date().isoformat() != self.session:
            raise DataError("Session must match the exchange-local close date")
        if not self.source or len(self.source_sha256) != 64:
            raise DataError("Source and SHA-256 provenance are required")
        try:
            int(self.source_sha256, 16)
        except ValueError as exc:
            raise DataError("Invalid SHA-256") from exc
        if self.close is None:
            raise DataError("Missing close; preserve it as a missing observation, not a zero bar")
        for key in ("open", "high", "low", "close", "volume"):
            value = getattr(self, key)
            if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
                raise DataError(f"{key} must be finite or null")
        if self.volume is not None and self.volume < 0:
            raise DataError("Negative volume")
        if asset.series_kind != "continuous_future" and self.close <= 0:
            raise DataError("Non-positive cash price")
        if self.high is not None and any(v is not None and v > self.high for v in (self.open, self.close, self.low)):
            raise DataError("OHLC exceeds high")
        if self.low is not None and any(v is not None and v < self.low for v in (self.open, self.close, self.high)):
            raise DataError("OHLC falls below low")
        if self.timing not in {"verified", "assumed", "synthetic"}:
            raise DataError("Unknown availability provenance")
        if self.price_basis not in {"raw", "pit_adjusted"}:
            raise DataError("Retrospectively adjusted prices are not accepted as PIT prices")
        if self.corporate_action not in {"none", "split", "unknown"}:
            raise DataError("Unknown corporate-action state")
        if self.roll_event not in {"none", "roll", "unknown"}:
            raise DataError("Unknown roll state")

    def to_dict(self) -> dict:
        result = asdict(self)
        for key in ("close_at", "available_at", "retrieved_at"):
            result[key] = utc(result[key]).isoformat()
        return result
