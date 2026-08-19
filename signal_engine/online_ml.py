from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

from config import get_settings

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    'rsi', 'macd_hist_pct', 'ema20_50_pct', 'ema9_20_pct', 'ema50_200_pct',
    'atr_pct', 'bb_position', 'adx', 'body_atr', 'body_ratio', 'momentum3',
    'momentum10', 'distance_high_atr', 'distance_low_atr', 'timeframe_norm',
]


def vectorize(features: dict[str, Any]) -> np.ndarray:
    return np.array([[float(features.get(name, 0.0)) for name in FEATURE_NAMES]], dtype=float)


class OnlineDirectionModel:
    def __init__(self) -> None:
        settings = get_settings()
        self.path = Path(settings.model_dir) / 'otc_online_direction.joblib'
        self.meta_path = Path(settings.model_dir) / 'otc_online_meta.json'
        self.min_samples = settings.online_ml_min_samples
        self.scaler = StandardScaler()
        self.model = SGDClassifier(loss='log_loss', alpha=0.0008, random_state=42)
        self.samples = 0
        self.fitted = False
        self._mtime = 0.0
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                payload = joblib.load(self.path)
                self.scaler = payload['scaler']
                self.model = payload['model']
                self.samples = int(payload.get('samples', 0))
                self.fitted = bool(payload.get('fitted', self.samples > 0))
                self._mtime = self.path.stat().st_mtime
        except Exception as exc:
            logger.warning('Could not load online ML model; starting fresh: %s', exc)

    def _payload(self) -> dict[str, Any]:
        return {'scaler': self.scaler, 'model': self.model, 'samples': self.samples, 'fitted': self.fitted, 'features': FEATURE_NAMES}

    def dumps(self) -> bytes:
        buffer = io.BytesIO()
        joblib.dump(self._payload(), buffer)
        return buffer.getvalue()

    def loads(self, payload_bytes: bytes) -> None:
        payload = joblib.load(io.BytesIO(payload_bytes))
        self.scaler = payload['scaler']
        self.model = payload['model']
        self.samples = int(payload.get('samples', 0))
        self.fitted = bool(payload.get('fitted', self.samples > 0))

    def _save(self) -> None:
        if os.getenv('VERCEL'):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix('.tmp')
            joblib.dump(self._payload(), tmp_path)
            tmp_path.replace(self.path)
            self._mtime = self.path.stat().st_mtime
            self.meta_path.write_text(json.dumps({'samples': self.samples,'fitted': self.fitted,'min_samples_for_weight': self.min_samples,'feature_names': FEATURE_NAMES}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            logger.warning('Could not persist local ML model copy: %s', exc)

    def _maybe_reload(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_mtime > self._mtime:
                self._load()
        except Exception as exc:
            logger.warning('Could not refresh online ML model: %s', exc)

    def predict(self, features: dict[str, Any]) -> tuple[str | None, float]:
        self._maybe_reload()
        if not self.fitted or self.samples < self.min_samples:
            return None, 0.50
        x = vectorize(features)
        xs = self.scaler.transform(x)
        p_up = float(self.model.predict_proba(xs)[0][1])
        direction = 'CALL' if p_up >= 0.5 else 'PUT'
        confidence = p_up if direction == 'CALL' else 1.0 - p_up
        return direction, float(np.clip(confidence, 0.5, 0.98))

    def learn(self, features: dict[str, Any], went_up: bool) -> None:
        if not features:
            return
        x = vectorize(features)
        y = np.array([1 if went_up else 0], dtype=int)
        self.scaler.partial_fit(x)
        xs = self.scaler.transform(x)
        if not self.fitted:
            self.model.partial_fit(xs, y, classes=np.array([0, 1], dtype=int))
            self.fitted = True
        else:
            self.model.partial_fit(xs, y)
        self.samples += 1
        self._save()
        logger.info('Online ML learned sample #%s label=%s', self.samples, int(went_up))


online_model = OnlineDirectionModel()
