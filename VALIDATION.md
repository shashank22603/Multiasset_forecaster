# Validation record

Verified locally on 2026-09-11. These measurements describe this saved data snapshot, not future provider coverage or model accuracy.

## Working implementation

- Daily and genuine five-minute collection, separate observation identity/storage per interval, source snapshot hashing, and CSV imports.
- Availability-based multi-asset alignment, explicit domestic bar gaps, time zones, corporate-action/roll checks and technical features.
- Native TimesFM 3.0 joint forecasts and stock forecasts with past-only covariates.
- Last-price baseline and controlled chronological comparisons; no model promotion.
- Local dashboard at `http://127.0.0.1:8050`, with five-minute mode as its default.
- 37 regression tests passed. Upstream Starlette emits two deprecation warnings in its test client.
- Chromium checks passed for desktop and 390-pixel mobile layouts, interval switching, candles, search, filters, comparison tables, actual/predicted charts, and coverage. Screenshots and the machine-readable browser report are in `artifacts/`.
- Offline synthetic demo verified independently in `/tmp/timesfm-demo-validation/demo`; its 11,340 generated rows are not mixed with market data.

## Observed coverage

| Interval | Populated instruments | Stored distinct observations | Original publication timing fully verified |
| --- | ---: | ---: | ---: |
| Daily | 27 / 27 | 17,949 | 0 |
| Five-minute | 27 / 27 | 68,019 | 0 |

The five-minute collection starts on 2026-08-12 and reaches 2026-09-11, with different hours and gaps by instrument. The public provider limits available intraday history. All real public-history experiments are labelled `RETROSPECTIVE_RESEARCH`.

Counts do not establish completeness. The most recent 128 five-minute NSE decision slots have missing observations for the five stock targets. Daily sector-index histories also contain gaps. Inspect `data/coverage-5m.json` and `data/coverage-1d.json` for per-asset counts, last observation time, age, and missing recent slots.

## Real TimesFM execution

Runtime: `timesfm==3.0.2`, CPU PyTorch, two CPU threads. Official checkpoint: `google/timesfm-3.0-pytorch`, immutable revision `43046b85ec22d584a13f8098c2ed39c889e129c2`.

Both five-minute experiment paths ran on the 17-asset core, using 64 context bars and a five-bar horizon. The latest complete shared input origin was **2026-09-11 15:15 IST**, three five-minute slots behind the latest observed NSE panel. These outputs are explicitly marked `LAGGED_INPUTS`.

- Joint forecast: `data/runs/forecast-01417cc0e30ee01f.json` — 17 targets, 85 point forecasts with nine quantiles each.
- Stock/covariate forecast: `data/runs/forecast-b55cbb5240846ed0.json` — five equity targets and twelve past-only cross-asset covariates.
- Controlled comparison: `data/runs/comparison-bd7f6aafcf8f02a0.json`.

The first intraday comparison scheduled twelve folds. **Only one origin had complete inputs and targets for all five configurations**, giving five common terminal stock outcomes. Other folds were explicitly skipped for missing context or target bars. This is a small integration check, not a defensible estimate of predictive skill.

| Configuration | Return MAE | Directional hit rate | Rank IC | 80% interval coverage |
| --- | ---: | ---: | ---: | ---: |
| Last-price baseline | 0.1222% | 0% (flat forecasts) | undefined | 60% |
| TimesFM stocks only | 0.1987% | 0% | -0.20 | 60% |
| TimesFM stocks + indices/global/FX | 0.1548% | 0% | 0.00 | 60% |
| TimesFM stocks + all cross-asset covariates | 0.1620% | 0% | -0.80 | 60% |
| TimesFM joint assets | 0.1620% | 0% | -0.80 | 60% |

These observations **do not show an improvement from adding commodities**. No accuracy, calibrated confidence, profitability, or production-readiness claim is supported. Keep the research state until substantially more complete data and independent out-of-sample evaluation are available.

The feature export was also exercised on the real core panel: `artifacts/features-5m.csv` contains 29,325 asset/bar rows plus a source-hash manifest. Missing prices and unavailable volume remain missing.
