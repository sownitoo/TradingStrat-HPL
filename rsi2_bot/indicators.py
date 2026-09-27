"""Indicateurs techniques, calculés à l'identique de TradingView (Pine Script).

- SMA : ``ta.sma(close, n)`` = moyenne arithmétique des n dernières clôtures.
- RSI : ``ta.rsi(close, n)`` = RSI de Wilder, lissage RMA (alpha = 1/n) initialisé
  par la moyenne simple des n premières variations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, length: int) -> pd.Series:
    """Moyenne mobile simple. NaN tant qu'il n'y a pas ``length`` valeurs."""
    if length < 1:
        raise ValueError("length doit être >= 1")
    return close.rolling(window=length, min_periods=length).mean()


def _rsi_from_averages(avg_up: float, avg_down: float) -> float:
    # Même convention que la doc Pine de ta.rsi :
    # down == 0 ? 100 : up == 0 ? 0 : 100 - 100 / (1 + up / down)
    if avg_down == 0:
        return 100.0
    if avg_up == 0:
        return 0.0
    return 100.0 - 100.0 / (1.0 + avg_up / avg_down)


def rsi(close: pd.Series, length: int) -> pd.Series:
    """RSI de Wilder, compatible TradingView ``ta.rsi``.

    La première valeur est disponible à l'index ``length`` (il faut ``length``
    variations). Les valeurs suivantes utilisent la RMA :
    ``avg = (avg_prec * (length - 1) + valeur) / length``.
    """
    if length < 1:
        raise ValueError("length doit être >= 1")
    values = close.to_numpy(dtype=float)
    if np.isnan(values).any():
        raise ValueError("la série de clôtures contient des NaN")

    out = np.full(len(values), np.nan)
    if len(values) <= length:
        return pd.Series(out, index=close.index, name=f"rsi{length}")

    change = np.diff(values)
    gains = np.maximum(change, 0.0)
    losses = np.maximum(-change, 0.0)

    avg_up = gains[:length].mean()
    avg_down = losses[:length].mean()
    out[length] = _rsi_from_averages(avg_up, avg_down)

    for i in range(length, len(change)):
        avg_up = (avg_up * (length - 1) + gains[i]) / length
        avg_down = (avg_down * (length - 1) + losses[i]) / length
        out[i + 1] = _rsi_from_averages(avg_up, avg_down)

    return pd.Series(out, index=close.index, name=f"rsi{length}")
