# Multi-Asset TimesFM Research

A local research application for **NSE stocks, Indian indices, commodities, global indices, and USDINR**, built in `/home/shashank/projects/timesfm`.

It collects genuine **5-minute and daily OHLCV**, preserves source snapshots, aligns observations by availability time, runs **TimesFM 3.0**, and compares forecasts using chronological walk-forward evaluation. The dashboard includes candlesticks, forecast intervals, stock rankings, actual-versus-predicted charts, experiment history, and data coverage.

**This is research software. No model is promoted to trading.** Synthetic demo results, retrospective historical results, and verified point-in-time research are labelled separately.

## Run the existing installation

Python dependencies and the official TimesFM checkpoint have already been installed in this directory. Real historical observations and initial research runs are stored in `data/`.

See [`VALIDATION.md`](VALIDATION.md) for the measured coverage, real model runs, test results, and current evaluation limits.

```bash
cd /home/shashank/projects/timesfm
make serve
```

Open **http://127.0.0.1:8050**. The API documentation is at **http://127.0.0.1:8050/docs**.

The dashboard opens in **5-minute mode**. Use the **Bar interval** selector in the header to switch between 5 minutes and daily. Charts, coverage, experiment history and new research runs use the selected interval. Both intervals are stored independently in the same database.

If the dashboard is already running, open the URL directly. Stop a foreground server with `Ctrl+C`. To use another port:

```bash
.venv/bin/python -m timesfm_lab serve --port 8051
```

The dashboard reads local data. **Refresh data view refreshes the display; it does not download new market prices.** Run the collection command below to update observations. There is no automatic live-feed scheduler; this is genuine intraday resolution with on-demand collection, not a guaranteed real-time subscription.

## Five-minute workflow

```bash
cd /home/shashank/projects/timesfm

# Collect genuine 5-minute bars. Default lookback: last 30 calendar days.
.venv/bin/python -m timesfm_lab --interval 5m collect

# Inspect actual intraday coverage and missing bars in recent model context.
.venv/bin/python -m timesfm_lab --interval 5m audit

# Forecast the next five observed 5-minute bars using 64 bars of context.
.venv/bin/python -m timesfm_lab --interval 5m forecast \
  --experiment stock_covariates --context 64 --horizon 5 \
  --allow-retrospective --latest-complete

# Joint stock/commodity/index forecasts at the same 5-minute resolution.
.venv/bin/python -m timesfm_lab --interval 5m forecast \
  --experiment joint --context 64 --horizon 5 \
  --allow-retrospective --latest-complete

# Five-minute walk-forward commodity comparison.
.venv/bin/python -m timesfm_lab --interval 5m compare \
  --context 64 --horizon 5 --folds 12 --allow-retrospective

# Intraday technical and cross-asset feature export.
.venv/bin/python -m timesfm_lab --interval 5m features \
  --allow-retrospective --output artifacts/features-5m.csv

make serve
```

`--interval` is a global option and goes **before** the subcommand. Without it, CLI commands use daily bars.

At this resolution, `--context 64` means 320 minutes of observed bars and `--horizon 5` means 25 minutes **within a continuous session**. Overnight/weekend breaks are excluded from the bar count, so five future bars may span a session break. The UI labels future steps rather than inventing wall-clock timestamps.

Yahoo's [documented intraday history limit](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html) is approximately **60 days**, and individual instruments can have gaps or delayed quotes. Older 5-minute requests fail explicitly. Keep downloaded bars locally to grow your archive, or import a broker/licensed historical feed with `--interval 5m import-csv` and `interval=5m` in every CSV row. Daily candles are never expanded into artificial intraday observations.

`--latest-complete` explicitly permits a **lagged** forecast origin within the latest observed NSE session when newer input bars are missing. The saved run and UI expose `lag_bars`, the actual cutoff, and `LAGGED_INPUTS`. It does not fill missing bars or label an older forecast as current. Omit this flag to require the latest expected observed bar. If no complete context exists within one session, the run fails with a coverage error. In the dashboard, the equivalent checkbox is **Allow a lagged complete context**.

Intraday provider timestamps are treated as bar **opens**; the collector adds five minutes for `close_at`, then an explicit assumed one-minute availability delay. It rejects unfinished bars. The NSE decision cutoff is one minute after each five-minute bar close, and domestic assets must have that exact completed bar. Already-closed global markets can contribute older available values only within their configured age limit. These are still retrospective research inputs, not verified live publication records.

## Install from scratch

Requirements: Linux/WSL or another Python-compatible environment, Python 3.12, and [uv](https://docs.astral.sh/uv/). The CPU model weights are approximately 1.3 GB; allow several GB of free disk and RAM for dependencies and inference. A GPU is optional.

```bash
cd /home/shashank/projects/timesfm
export UV_CACHE_DIR=/tmp/timesfm-uv

# Create the local environment and install application/test dependencies.
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'

# Install CPU-only PyTorch and the pinned TimesFM package.
make install-model

# Download the official weights and record an immutable checkpoint revision.
.venv/bin/python -m timesfm_lab download-model

# Register assets, collect history, inspect coverage, then start the UI.
.venv/bin/python -m timesfm_lab init
.venv/bin/python -m timesfm_lab collect --start 2024-01-01
.venv/bin/python -m timesfm_lab audit
make serve
```

No brokerage account or order execution is involved. Public data collection requires network access and can be rate-limited or incomplete. A provider failure is saved per asset; it does not erase previously collected history.

Once installed, `timesfm-lab` is also available inside `.venv/bin/`. The commands below use the explicit Python path so shell activation is unnecessary.

## Collect and inspect data

```bash
# All 27 registered starter assets. End, when supplied, is exclusive.
.venv/bin/python -m timesfm_lab collect --start 2024-01-01

# Refresh a bounded recent period without deleting existing observations.
.venv/bin/python -m timesfm_lab collect --start 2026-08-01

# Collect only specific assets.
.venv/bin/python -m timesfm_lab collect --start 2024-01-01 \
  --assets COM:GOLD,COM:SILVER,COM:CRUDE,COM:NATGAS,COM:COPPER

# Writes data/coverage-1d.json and prints actual coverage and provenance.
.venv/bin/python -m timesfm_lab audit
```

The initial universe is configured in [`default_universe.json`](src/timesfm_lab/default_universe.json):

| Asset category | Starter instruments |
| --- | --- |
| NSE equities | RELIANCE, TCS, INFY, HDFCBANK, SBIN |
| Indian indices | NIFTY 50, Bank NIFTY, FINNIFTY, Midcap 100, IT, Auto, Metal, Pharma, India VIX |
| Commodities | Gold, Silver, WTI Crude Oil, Natural Gas, Copper |
| Global indices | S&P 500, NASDAQ Composite, Dow Jones, Nikkei 225, Hang Seng, DAX, FTSE 100 |
| FX | USDINR |

Commodity inputs are **USD continuous-futures proxies from COMEX/NYMEX, not MCX rupee contracts or spot prices**. Currency, unit, exchange, contract identity, and roll status are separate schema fields. Never combine different contracts or currencies under one asset ID.

The universe is a starter list, not all NSE stocks or a verified current/historical F&O universe. F&O membership stays unknown. Expand using a custom JSON registry with the same schema:

```bash
.venv/bin/python -m timesfm_lab --universe /path/to/universe.json init
.venv/bin/python -m timesfm_lab --universe /path/to/universe.json collect --start 2024-01-01
```

Existing observed identities cannot silently change exchange, provider symbol, currency, or unit. Use new stable IDs for genuinely different instruments.

## Run forecasts

Public historical downloads do not establish when each original observation was published. You must explicitly choose `--allow-retrospective` for these data. Omitting it uses verified publication timing only and can block the run.

```bash
# Experiment 1: jointly forecast every asset in the selected core profile.
.venv/bin/python -m timesfm_lab forecast \
  --model timesfm --experiment joint --profile core \
  --context 128 --horizon 5 --allow-retrospective

# Experiment 2: forecast stocks using indices, commodities, global markets and FX
# as past-only covariates. No realized future commodity prices are provided.
.venv/bin/python -m timesfm_lab forecast \
  --model timesfm --experiment stock_covariates --profile core \
  --context 128 --horizon 5 --allow-retrospective

# A transparent last-price baseline; this is not TimesFM.
.venv/bin/python -m timesfm_lab forecast \
  --model naive --experiment stock_only --allow-retrospective
```

The default **core profile contains 17 assets**: the five equities, NIFTY 50/Bank NIFTY/VIX, all five commodities, S&P 500/NASDAQ/Dow, and USDINR. The full 27-asset universe remains collected and browsable. Some sector/global histories contain gaps that block a complete model context.

Use `--profile full` to request all registered assets. Missing data produces an explicit error rather than silently changing the experiment. Use `--assets NSE:RELIANCE,NSE:TCS,NSE:INFY,COM:GOLD,NSE:NIFTY50` for an explicit custom subset. NIFTY 50 history is still required as the calendar anchor.

In daily mode, forecast horizons are **observed NSE decision sessions**, not calendar days. In five-minute mode they are observed five-minute bars. Future chart positions use `T+1`, `T+2`, etc.; the application does not invent future exchange holiday dates. Global/commodity targets represent the latest available native-currency values on that same decision clock.

In the dashboard, open **Experiments**, choose the model, experiment and profile, explicitly enable retrospective research when appropriate, and click **Run experiment**. A single background worker prevents overlapping UI jobs. Selecting a saved forecast updates the market charts and ranking.

## Walk-forward evaluation and commodity ablation

```bash
# One experiment. The initial smoke comparison used 64 context sessions.
.venv/bin/python -m timesfm_lab backtest \
  --model timesfm --experiment stock_covariates \
  --context 64 --horizon 5 --folds 12 --allow-retrospective

# Last-price baseline plus all four TimesFM experiment configurations.
.venv/bin/python -m timesfm_lab compare \
  --context 64 --horizon 5 --folds 12 --allow-retrospective
```

| Experiment | Forecast targets | Additional past-only inputs |
| --- | --- | --- |
| `stock_only` | Stocks | None |
| `macro_control` | Stocks | Indian indices, global indices, FX |
| `stock_covariates` | Stocks | All macro-control inputs **plus commodities** |
| `joint` | All selected assets | None; all assets are native target channels |

Compare **`macro_control` against `stock_covariates`** to measure the incremental effect of commodities. The comparison uses the intersection of successful origins and the same equity targets for every model. Standalone joint forecast runs also expose commodity and index forecasts.

The evaluator:

- Uses only observations available by each origin's cutoff; never supplies realized future covariates.
- Reserves the latest 20% of observed sessions from evaluation in that run. This historical holdout is **not an independently frozen future lockbox**.
- Uses non-overlapping forecast windows (`stride >= horizon`), no shuffled splits, and no hyperparameter selection.
- Rejects missing/stale context, non-positive prices, and windows crossing known raw-price splits or contract rolls. Strict PIT mode also rejects unknown corporate-action/contract provenance.
- Reports return MAE, directional hit rate, mean cross-sectional Spearman ranking IC, 80% interval coverage, normalized pinball loss, and per-asset price MAE. Per-horizon metrics are also saved.
- Leaves undefined metrics null. A constant last-price ranking has no ranking IC; its flat predictions are exposed rather than treated as directional skill.
- Reports completed/skipped origins and reasons. `PARTIAL` means the requested evaluation was not fully covered.

These are close-price forecasting metrics, **not executable strategy returns**. There is no profitability, transaction-cost-adjusted performance, calibrated direction-probability, or production-readiness claim. More assets, history, independent timing/action/roll evidence, a trading simulation and a fresh future lockbox are needed before considering deployment.

The default pretrained weights use Google's [TimesFM non-commercial license](https://huggingface.co/google/timesfm-3.0-pytorch). The project uses them for local research. See the [official TimesFM repository](https://github.com/google-research/timesfm) for the current API and weight license.

## Features and trusted CSV import

The feature engine exports raw OHLCV, returns, momentum, realized volatility, RSI, moving averages, intraday ranges, cross-asset rolling correlations and observation ages. Null volume remains null. Rolling statistics require complete history.

```bash
.venv/bin/python -m timesfm_lab features --allow-retrospective \
  --output artifacts/features.csv
```

This writes a CSV and a source-hash manifest. `forecast`/`backtest --technical` additionally supplies return, momentum, volatility and RSI channels as past-only covariates; it needs warm-up history. Technical features are **not automatically used** by the default raw-price experiments. The CPU adapter refuses more than 32 total channels rather than silently subsampling. Choose a smaller explicit asset subset for technical experiments.

For a trusted feed, import CSV rows with explicit timing and provenance:

```bash
.venv/bin/python -m timesfm_lab import-csv /path/to/observations.csv
```

Required columns: `asset_id,session,close_at,available_at,close,source,timing`.

Optional columns: `retrieved_at,open,high,low,volume,price_basis,corporate_action,contract_id,roll_event,interval`. `interval` defaults to `1d`; specify `5m` explicitly for an intraday import and select `--interval 5m` on the CLI. Timestamps must include a timezone; `session` is the exchange-local date. Intraday `close_at` must fall on a five-minute boundary. `retrieved_at` defaults to import time. The importer computes the source SHA-256 from the original CSV bytes.

Allowed timing values are `verified`, `assumed`, and `synthetic`. Use `verified` only with evidence of original publication availability. Allowed price bases are `raw` and `pit_adjusted`; today's retrospectively adjusted history is not accepted as PIT data. Actions are `none`, `split`, or `unknown`; rolls are `none`, `roll`, or `unknown`. A verified commodity series additionally needs its contract identity and roll history. The importer validates the entire batch before inserting observations.

## Data alignment and limitations

The daily decision cutoff is **16:00 Asia/Kolkata** by default: NSE's configured 15:30 close plus 30 minutes. Five-minute mode uses a one-minute delay after each bar close. Cross-market observations join only after their `available_at` time, and local time zones handle daylight-saving offsets. Same-day US closes therefore cannot enter an earlier Indian decision.

The calendar uses the union of observed domestic session timestamps, with missing interior five-minute slots retained. This prevents a gap in the NIFTY feed from silently deleting a bar observed by another domestic instrument. It is not an independently verified exchange calendar; an entirely missing exchange session or an unobserved opening/closing segment cannot be certified from these data alone.

NSE target observations must match the decision session exactly. Cross-market values may be carried only backward from an already available observation up to the asset's configured age limit, with age retained. No interpolation, backward filling from future data, zero-filled missing prices, or guessed corporate-action adjustments are used.

Public daily timestamps are session labels. The Yahoo collector derives conservative approximate close/publication timestamps and marks them **assumed**. It retains unadjusted OHLC and supplied split events, rejects invalid candles, and excludes incomplete daily observations. Actual venue calendars, early closes, provider revisions, dividend-adjusted total returns and continuous-contract roll adjustments are not independently verified. These limitations prevent a PIT or trading-validity claim for public historical results.

News, fundamentals, and event/ensemble models are a future separate layer. They are not fabricated or inserted into the raw TimesFM matrix.

## Offline synthetic demo

This verifies the application without network access or model weights. Demo data is kept in a separate database and labelled throughout the UI.

```bash
make demo
.venv/bin/python -m timesfm_lab --data-dir data/demo serve --port 8051
```

Open http://127.0.0.1:8051. The generator requires an empty demo store; to make another demo without deleting anything:

```bash
.venv/bin/python -m timesfm_lab --data-dir /tmp/timesfm-example demo
.venv/bin/python -m timesfm_lab --data-dir /tmp/timesfm-example/demo serve --port 8051
```

## Tests and stored artifacts

```bash
make check
```

The suite exercises availability leakage, historical revisions, missing observations, time zones, OHLC validation, immutable identity, snapshot hashes, action/roll boundaries, chronological evaluation, common-origin comparison and API behavior. The real model is exercised separately using the downloaded official checkpoint; unit-test doubles are not market accuracy evidence.

With the server running, the optional browser check is:

```bash
# Chromium is already installed in this workspace. On a fresh installation:
PLAYWRIGHT_BROWSERS_PATH=.cache/ms-playwright .venv/bin/python -m playwright install chromium

.venv/bin/python scripts/browser_smoke.py
```

It saves desktop/mobile screenshots under `artifacts/`. Add `--submit` to exercise a real TimesFM forecast through the dashboard, which saves a new research run. Fresh Linux systems may also require Chromium's system libraries; this workspace has the missing libraries extracted locally under `.cache/browser-libs/`.

```text
src/timesfm_lab/
  default_universe.json    Asset identities, currencies, units, timing assumptions
  domain.py / store.py     Validated observations and versioned SQLite storage
  collect.py / panel.py    Public/CSV ingestion, availability alignment and features
  models.py / research.py  TimesFM adapter, baseline and walk-forward experiments
  api.py / static/         Local API and dashboard
  cli.py                  Command-line entrypoint
tests/                    Integrity, evaluation and API regression tests
data/
  research.sqlite3        Assets, observation versions, collection status and runs
  raw/                    SHA-256-addressed provider tables / imported CSVs
  runs/                   Immutable experiment JSON reports
  coverage-1d.json        Latest explicit daily coverage audit
  coverage-5m.json        Latest explicit five-minute coverage audit
  demo/                   Optional isolated synthetic database
.cache/huggingface/        Official weights and pinned revision manifest
artifacts/                Exported features, screenshots and validation reports
```

Each forecast records its model revision, ordered targets/covariates, origin/cutoff, input fingerprint and source snapshots. Backtests retain per-origin input manifests and predicted/actual observations. The dashboard can download the full saved run JSON. Data, downloaded environments, caches and generated artifacts are excluded from version control.

## Troubleshooting

- **Missing/stale context:** inspect Data coverage and `data/coverage-1d.json` or `data/coverage-5m.json`. Collect the missing history, choose an explicit supported subset, or use a shorter justified context. For intraday data, `--latest-complete` can select an explicitly lagged complete context within the latest session. The application does not fill gaps to force a result.
- **No verified observations:** public history requires the explicit retrospective research option. Use a feed with original availability evidence for strict PIT runs.
- **Checkpoint not downloaded:** run `make install-model`, then `.venv/bin/python -m timesfm_lab download-model`.
- **Port already in use:** use another port or stop the server you started; do not kill unrelated services.
- **Slow CPU inference:** use a smaller context/universe or fewer evaluation folds. The model defaults to two CPU threads and a batch size of one.
- **Read-only uv cache:** set `UV_CACHE_DIR=/tmp/timesfm-uv` as in the installation commands.
- **Provider errors/partial coverage:** failed or rejected observations are recorded. A successful download or a populated registry does not prove complete market coverage.

For all available commands:

```bash
.venv/bin/python -m timesfm_lab --help
.venv/bin/python -m timesfm_lab forecast --help
```
