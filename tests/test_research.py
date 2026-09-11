import numpy as np
import pytest

from timesfm_lab.domain import DataError
from timesfm_lab.models import NaiveModel, Prediction, TimesFMModel
from timesfm_lab.panel import align
from timesfm_lab.research import compare, forecast_payload, metrics, prepare, walk_forward


class RecordingModel(NaiveModel):
    name = "test_double"

    def __init__(self):
        self.inputs = []

    def predict(self, targets, horizon, covariates=None):
        self.inputs.append((targets.copy(), None if covariates is None else covariates.copy()))
        return super().predict(targets, horizon)


def test_experiment_channels_and_no_future_covariates(store):
    panel = align(store, list(store.assets()))
    stock = prepare(panel, store.assets(), 150, 64, "stock_only")
    control = prepare(panel, store.assets(), 150, 64, "macro_control")
    cross = prepare(panel, store.assets(), 150, 64, "stock_covariates")
    joint = prepare(panel, store.assets(), 150, 64, "joint")
    assert stock.cov is None
    assert "COM:GOLD" not in control.covariates
    assert "COM:GOLD" in cross.covariates
    assert set(joint.targets) == set(store.assets())
    assert joint.cov is None
    assert cross.cov.shape[1] == cross.x.shape[1] == 64
    assert set(stock.targets) == set(cross.targets)


def test_future_values_cannot_change_forecast_or_features(store):
    panel = align(store, list(store.assets()))
    model = RecordingModel()
    before = forecast_payload(panel, store.assets(), model, "stock_covariates", 64, 5, origin=150, technical=True)
    panel.values.iloc[151:] = 1e9
    after = forecast_payload(panel, store.assets(), model, "stock_covariates", 64, 5, origin=150, technical=True)
    assert before["input_sha256"] == after["input_sha256"]
    assert before["forecasts"] == after["forecasts"]
    assert before["promotion_allowed"] is False
    assert all(r["asset_class"] == "equity" for r in before["ranking"])


def test_backtest_reserves_holdout_and_nonoverlap(store):
    result = walk_forward(store, NaiveModel(), "stock_only", context=64, horizon=5, folds=5)
    assert result["completed_folds"] == 5
    assert result["metrics"]["observations"] == 15
    assert result["holdout"]["evaluated"] is False
    assert all(r["target_session"] < result["holdout"]["start"] for r in result["records"])
    for previous, following in zip(result["origins"], result["origins"][1:]):
        last_target = max(r["target_session"] for r in result["records"] if r["origin"] == previous)
        assert last_target <= following
    with pytest.raises(DataError, match="Stride"):
        walk_forward(store, NaiveModel(), "stock_only", horizon=5, stride=1)


def test_compare_uses_identical_origins_and_equity_targets(store):
    result = compare(store, RecordingModel(), context=64, horizon=5, folds=3)
    assert len(result["common_origins"]) == 3
    assert len(result["comparisons"]) == 5
    assert all(r["metrics"]["observations"] == 9 for r in result["comparisons"])
    assert result["promotion_allowed"] is False


def test_all_missing_context_blocks_and_reports_skipped_folds(store):
    with store.connect() as conn:
        conn.execute("DELETE FROM bars WHERE asset_id='NSE:TCS'")
    result = walk_forward(store, NaiveModel(), "stock_only", context=64, folds=3)
    assert result["status"] == "BLOCKED"
    assert result["metrics"]["return_mae"] is None
    assert len(result["skipped"]) == 3


def test_constant_rankings_have_no_invented_ic():
    rows = [{"asset_id": str(i), "origin": "2025-01-01", "step": 1, "base_price": 100., "price": 100.,
             "actual_price": actual, "predicted_return": 0., "actual_return": actual / 100 - 1,
             "quantiles": {f"{q/10:.1f}": 90. + q * 2 for q in range(1, 10)}} for i, actual in enumerate([99, 102, 101])]
    result = metrics(rows, 1)
    assert result["rank_ic"] is None
    assert result["flat_prediction_fraction"] == 1
    assert result["directional_hit_rate"] == 0
    assert result["return_mae"] == pytest.approx(.04 / 3)
    assert result["interval_80_coverage"] == 1


def test_model_rejects_bad_output_shape_and_crossing_quantiles():
    with pytest.raises(DataError, match="shapes"):
        Prediction(np.zeros((1, 5)), np.zeros((1, 5, 8))).validate(1, 5)
    with pytest.raises(DataError, match="crossing"):
        Prediction(np.ones((1, 5)), np.tile(np.arange(9)[::-1], (1, 5, 1))).validate(1, 5)


def test_adapter_uses_native_joint_call_with_past_only_covariates():
    class Backend:
        def predict_batch(self, **kwargs):
            self.kwargs = kwargs
            yield type("Output", (), {"forecast": np.ones((3, 5)), "quantiles": np.ones((3, 5, 9))})()
    model = TimesFMModel.__new__(TimesFMModel)
    model.forecaster = Backend()
    targets, cov = np.ones((3, 64)), np.ones((2, 64))
    prediction = model.predict(targets, 5, cov)
    assert prediction.point.shape == (3, 5)
    assert model.forecaster.kwargs["contexts"][0].shape == (3, 64)
    assert model.forecaster.kwargs["past_only_covariates"][0].shape == (2, 64)
    assert model.forecaster.kwargs["past_future_covariates"] is None
    with pytest.raises(DataError, match="finite"):
        model.predict(np.full((3, 64), np.nan), 5)
    with pytest.raises(DataError, match="32"):
        model.predict(np.ones((33, 64)), 5)
