from __future__ import annotations

import json
import logging
import math
from typing import Any

import numpy as np

from config import get_settings

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    'rsi', 'macd_hist_pct', 'ema20_50_pct', 'ema9_20_pct', 'ema50_200_pct',
    'atr_pct', 'bb_position', 'adx', 'body_atr', 'body_ratio', 'momentum3',
    'momentum10', 'distance_high_atr', 'distance_low_atr', 'timeframe_norm',
]


def vectorize(features: dict[str, Any]) -> np.ndarray:
    return np.array([float(features.get(name, 0.0)) for name in FEATURE_NAMES], dtype=float)


class OnlineDirectionModel:
    """Dependency-light online logistic regression with streaming normalization.

    Every resolved real signal updates normalization statistics and weights. State is
    serialized to JSON bytes and persisted by signal_engine.ml_store in Postgres, so
    serverless cold starts do not reset learning.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.min_samples = settings.online_ml_min_samples
        self.learning_rate = 0.035
        self.l2 = 0.0005
        size = len(FEATURE_NAMES)
        self.samples = 0
        self.mean = np.zeros(size, dtype=float)
        self.m2 = np.zeros(size, dtype=float)
        self.weights = np.zeros(size, dtype=float)
        self.bias = 0.0
        self.fitted = False

    def _std(self) -> np.ndarray:
        if self.samples < 2:
            return np.ones(len(FEATURE_NAMES), dtype=float)
        variance = self.m2 / max(1, self.samples - 1)
        return np.sqrt(np.maximum(variance, 1e-8))

    def _normalize(self, x: np.ndarray) -> np.ndarray:
        return np.clip((x - self.mean) / self._std(), -8.0, 8.0)

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-40.0, min(40.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def predict(self, features: dict[str, Any]) -> tuple[str | None, float]:
        if not self.fitted or self.samples < self.min_samples:
            return None, 0.50
        x = self._normalize(vectorize(features))
        p_up = self._sigmoid(float(np.dot(self.weights, x) + self.bias))
        direction = 'CALL' if p_up >= 0.5 else 'PUT'
        confidence = p_up if direction == 'CALL' else 1.0 - p_up
        return direction, float(np.clip(confidence, 0.5, 0.98))

    def learn(self, features: dict[str, Any], went_up: bool) -> None:
        if not features:
            return
        raw = vectorize(features)

        next_n = self.samples + 1
        delta = raw - self.mean
        self.mean = self.mean + delta / next_n
        delta2 = raw - self.mean
        self.m2 = self.m2 + delta * delta2
        self.samples = next_n

        x = self._normalize(raw)
        target = 1.0 if went_up else 0.0
        p_up = self._sigmoid(float(np.dot(self.weights, x) + self.bias))
        error = target - p_up
        self.weights += self.learning_rate * (error * x - self.l2 * self.weights)
        self.bias += self.learning_rate * error
        self.fitted = True
        logger.info('Online ML learned sample #%s label=%s', self.samples, int(went_up))

    def dumps(self) -> bytes:
        payload = {
            'version': 2,
            'features': FEATURE_NAMES,
            'samples': self.samples,
            'fitted': self.fitted,
            'mean': self.mean.tolist(),
            'm2': self.m2.tolist(),
            'weights': self.weights.tolist(),
            'bias': self.bias,
        }
        return json.dumps(payload, separators=(',', ':')).encode('utf-8')

    def loads(self, payload: bytes) -> None:
        if not payload:
            return
        data = json.loads(payload.decode('utf-8'))
        if list(data.get('features') or []) != FEATURE_NAMES:
            raise ValueError('Stored ML feature schema does not match current code')
        self.samples = int(data.get('samples', 0))
        self.fitted = bool(data.get('fitted', self.samples > 0))
        self.mean = np.asarray(data.get('mean', [0.0] * len(FEATURE_NAMES)), dtype=float)
        self.m2 = np.asarray(data.get('m2', [0.0] * len(FEATURE_NAMES)), dtype=float)
        self.weights = np.asarray(data.get('weights', [0.0] * len(FEATURE_NAMES)), dtype=float)
        self.bias = float(data.get('bias', 0.0))


online_model = OnlineDirectionModel()
