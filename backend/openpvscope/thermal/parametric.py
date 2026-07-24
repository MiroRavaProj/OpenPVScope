"""Parametric MLP temperature fallback (pure NumPy, optional)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from openpvscope.thermal.dji_sdk import find_dji_tsdk_root

MODEL_FILENAME = "parametric_mlp_v1.npz"
META_FILENAME = "parametric_mlp_v1_meta.json"


def _thermal_data_candidates(filename: str) -> list[Path]:
    candidates: list[Path] = []
    root = find_dji_tsdk_root()
    if root is not None:
        for base in (root, root.parent if root.name.upper() == "DJI_TSDK" else root):
            candidates.append(base / "thermal_data" / filename)
    try:
        import sys

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "engines" / "dji_tsdk" / "thermal_data" / filename)
            candidates.append(Path(sys._MEIPASS) / "thermal_data" / filename)
    except Exception:
        pass
    here = Path(__file__).resolve()
    try:
        candidates.append(here.parents[3] / "engines" / "dji_tsdk" / "thermal_data" / filename)
    except IndexError:
        pass
    candidates.append(Path(os.path.abspath(".")) / "engines" / "dji_tsdk" / "thermal_data" / filename)
    candidates.append(Path(os.path.abspath(".")) / "thermal_data" / filename)
    return candidates


def default_model_path() -> Path | None:
    for path in _thermal_data_candidates(MODEL_FILENAME):
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=4)
def _load_weights_cached(npz_path: str) -> dict:
    data = np.load(npz_path)
    n_layers = int(data["n_layers"][0])
    return {
        "scaler_mean": data["scaler_mean"],
        "scaler_scale": data["scaler_scale"],
        "Ws": [data[f"W{i}"] for i in range(n_layers)],
        "bs": [data[f"b{i}"] for i in range(n_layers)],
    }


@lru_cache(maxsize=4)
def _load_meta_cached(meta_path: str) -> dict:
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_model_paths(model_path: str | Path | None = None) -> tuple[Path, Path]:
    if model_path is None:
        mp = default_model_path()
        if mp is None:
            raise FileNotFoundError(
                f"Parametric MLP model not found. Expected thermal_data/{MODEL_FILENAME}"
            )
        meta = mp.with_name(META_FILENAME)
    else:
        mp = Path(model_path)
        if mp.suffix == ".joblib":
            raise ValueError(
                "Runtime expects NumPy model (.npz). Export with export_mlp_to_numpy first."
            )
        meta = mp.with_name(META_FILENAME)
    if not mp.is_file():
        raise FileNotFoundError(f"Parametric model not found: {mp}")
    return mp, meta


def predict_mlp(X: np.ndarray, weights: dict) -> np.ndarray:
    """Forward pass: StandardScaler + ReLU MLP (matches sklearn export)."""
    x = (X - weights["scaler_mean"]) / weights["scaler_scale"]
    Ws = weights["Ws"]
    bs = weights["bs"]
    for i in range(len(Ws) - 1):
        x = x @ Ws[i] + bs[i]
        np.maximum(x, 0.0, out=x)
    return (x @ Ws[-1] + bs[-1]).ravel()


def estimate_temp_from_raw(
    raw: np.ndarray,
    emissivity: float,
    distance: float,
    humidity: float,
    reflection: float,
    model_path: str | Path | None = None,
    calib_byte0: int | None = None,
) -> np.ndarray:
    """
    Map SDK uint16 raw grid to float32 temperature (Celsius).

    Uses trained MLP f(raw, calib_byte0, emissivity, distance, humidity, reflection).
    """
    mp, meta_path = resolve_model_paths(model_path)
    weights = _load_weights_cached(str(mp))
    meta = _load_meta_cached(str(meta_path)) if meta_path.is_file() else {}
    raw_scale = float(meta.get("raw_scale", 65535.0))

    flat = raw.ravel().astype(np.float64)
    n = flat.size
    byte0_val = float(-1 if calib_byte0 is None else int(calib_byte0))

    X = np.column_stack(
        [
            flat / raw_scale,
            np.full(n, byte0_val),
            np.full(n, float(emissivity)),
            np.full(n, float(distance)),
            np.full(n, float(humidity)),
            np.full(n, float(reflection)),
        ]
    ).astype(np.float64)

    pred = predict_mlp(X, weights).astype(np.float32)
    return pred.reshape(raw.shape)


def try_parametric_temperature_fallback(
    raw_array: np.ndarray,
    emissivity: float,
    distance: float,
    humidity: float,
    reflection: float,
    *,
    parametric_model_path: str | Path | None = None,
    calib_byte0: int | None = None,
) -> np.ndarray | None:
    """Apply MLP parametric model to SDK raw grid. Returns float32 temp or None."""
    model_path = parametric_model_path or default_model_path()
    if not model_path or not Path(model_path).is_file():
        return None
    try:
        return estimate_temp_from_raw(
            raw_array,
            float(emissivity),
            float(distance),
            float(humidity),
            float(reflection),
            model_path=model_path,
            calib_byte0=calib_byte0,
        )
    except Exception:
        return None
