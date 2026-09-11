CORE_ASSETS = [
    "NSE:RELIANCE", "NSE:TCS", "NSE:INFY", "NSE:HDFCBANK", "NSE:SBIN",
    "NSE:NIFTY50", "NSE:BANKNIFTY", "NSE:INDIAVIX",
    "COM:GOLD", "COM:SILVER", "COM:CRUDE", "COM:NATGAS", "COM:COPPER",
    "GLOBAL:SP500", "GLOBAL:NASDAQ", "GLOBAL:DOW", "FX:USDINR",
]


def core_assets(assets: dict) -> list[str]:
    return [a for a in CORE_ASSETS if a in assets]
