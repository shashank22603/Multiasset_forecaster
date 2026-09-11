from __future__ import annotations

import importlib.metadata
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .domain import DataError

QUANTILES = np.arange(1, 10) / 10


@dataclass
class Prediction:
    point: np.ndarray
    quantiles: np.ndarray

    def validate(self, targets: int, horizon: int):
        if self.point.shape != (targets, horizon) or self.quantiles.shape != (targets, horizon, 9):
            raise DataError(f"Unexpected model output shapes: {self.point.shape}, {self.quantiles.shape}")
        if not np.isfinite(self.point).all() or not np.isfinite(self.quantiles).all():
            raise DataError("Model returned non-finite forecasts")
        if (np.diff(self.quantiles, axis=-1) < -1e-6).any():
            raise DataError("Model returned crossing quantiles")


class NaiveModel:
    name = "last_price_baseline"

    @property
    def metadata(self):
        return {"engine": self.name, "interval_method": "empirical_context_log_returns_by_horizon", "calibrated": False}

    def predict(self, targets: np.ndarray, horizon: int, covariates: np.ndarray | None = None) -> Prediction:
        if covariates is not None:
            raise DataError("Last-price baseline does not consume covariates")
        point = np.repeat(targets[:, -1:], horizon, axis=1)
        quantiles = np.empty((*point.shape, 9))
        for step in range(1, horizon + 1):
            differences = np.log(targets[:, step:] / targets[:, :-step])
            quantiles[:, step - 1, :] = targets[:, -1:] * np.exp(np.quantile(differences, QUANTILES, axis=1).T)
        result = Prediction(point, quantiles)
        result.validate(targets.shape[0], horizon)
        return result


def download_model(root: str | Path = ".cache/huggingface", revision: str | None = None) -> dict:
    # Resolve a mutable reference once; all inference records the immutable revision.
    os.environ.setdefault("HF_HOME", str(Path(root).resolve()))
    from huggingface_hub import HfApi, snapshot_download

    repo = "google/timesfm-3.0-pytorch"
    resolved = HfApi().model_info(repo, revision=revision or "main").sha
    path = snapshot_download(repo, revision=resolved, cache_dir=str(Path(root) / "hub"),
                             allow_patterns=["*.safetensors", "config.json"])
    manifest = {"repo": repo, "revision": resolved, "path": str(Path(path).resolve()),
                "weights_license": "timesfm-non-commercial-license-v1.0"}
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / "timesfm-model.json").write_text(json.dumps(manifest, indent=2))
    return manifest


class TimesFMModel:
    name = "timesfm_3.0"

    def __init__(self, cache: str | Path = ".cache/huggingface", device: str = "cpu", threads: int = 2):
        manifest_path = Path(cache) / "timesfm-model.json"
        if not manifest_path.exists():
            raise DataError("TimesFM checkpoint is not downloaded. Run: timesfm-lab download-model")
        self.manifest = json.loads(manifest_path.read_text())
        try:
            import torch
            from timesfm3 import TimesFM3Forecaster
        except ImportError as exc:
            raise DataError("TimesFM runtime is missing. Run: make install-model") from exc
        torch.set_num_threads(threads)
        self.forecaster = TimesFM3Forecaster.from_pretrained(
            self.manifest["path"], device=device, per_core_batch_size=1,
        )

    @property
    def metadata(self):
        return {"engine": self.name, "package_version": importlib.metadata.version("timesfm"),
                "checkpoint": self.manifest["repo"], "revision": self.manifest["revision"],
                "weights_license": self.manifest["weights_license"], "calibrated": False,
                "quantile_sorting": True, "symmetric_averaging": False}

    def predict(self, targets: np.ndarray, horizon: int, covariates: np.ndarray | None = None) -> Prediction:
        if targets.ndim != 2 or not np.isfinite(targets).all():
            raise DataError("Targets must be a finite (variates, context) matrix")
        if covariates is not None and (covariates.ndim != 2 or covariates.shape[1] != targets.shape[1] or not np.isfinite(covariates).all()):
            raise DataError("Covariates must be finite, past-only, and match the context length")
        if targets.shape[0] + (0 if covariates is None else covariates.shape[0]) > 32:
            raise DataError("This CPU research configuration supports at most 32 total channels; choose an explicit asset subset")
        # Use the forecaster directly: the evaluator may subsample covariates above 32 channels.
        # Reject missingness before upstream's internal interpolation can change our data.
        output = next(self.forecaster.predict_batch(
            contexts=[targets.astype(np.float32)], horizon=horizon,
            past_only_covariates=[covariates.astype(np.float32)] if covariates is not None else None,
            past_future_covariates=None, return_quantiles=True,
            use_symmetric_averaging=False, sort_quantiles=True, make_positive=False,
        ))
        prediction = Prediction(np.asarray(output.forecast, dtype=float).reshape(targets.shape[0], horizon),
                                np.asarray(output.quantiles, dtype=float).reshape(targets.shape[0], horizon, 9))
        prediction.validate(targets.shape[0], horizon)
        return prediction
