"""Bougies daily du S&P 500 cash (^GSPC) via yfinance et utilitaires de calendrier."""

from __future__ import annotations

import bisect
from datetime import date, datetime, time
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
US_CLOSE = time(16, 0)


def fetch_daily_closes(ticker: str, period: str = "2y") -> pd.Series:
    """Clôtures daily indexées par date (``datetime.date``, heure de New York).

    Deux ans d'historique : largement assez pour la SMA200 et pour que le RSI
    (lissage de Wilder) soit indépendant de son initialisation.
    """
    import yfinance as yf  # import local : les tests n'en ont pas besoin

    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty or "Close" not in df:
        raise RuntimeError(f"yfinance n'a renvoyé aucune donnée pour {ticker}")
    closes = df["Close"].dropna()
    idx = closes.index
    if idx.tz is not None:
        idx = idx.tz_convert(NY_TZ)
    closes.index = [ts.date() for ts in idx]
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    return closes.astype(float).rename("close")


def completed_bars(closes: pd.Series, now_ny: datetime) -> pd.Series:
    """Retire la bougie du jour si la séance US n'est pas encore clôturée."""
    if len(closes) and closes.index[-1] == now_ny.date() and now_ny.time() < US_CLOSE:
        return closes.iloc[:-1]
    return closes


def is_fresh(closes: pd.Series, now_ny: datetime) -> bool:
    """True si la dernière bougie clôturée est celle du jour (heure de New York)."""
    return len(closes) > 0 and closes.index[-1] == now_ny.date()


def session_of(ts_ms: int, sessions: Sequence[date]) -> Optional[date]:
    """Séance à laquelle rattacher un fill : la dernière séance <= date NY du fill.

    Un fill passé à 16h15 NY le jour J (exécution du bot après la clôture)
    appartient à la séance J. ``sessions`` doit être trié.
    """
    fill_day = datetime.fromtimestamp(ts_ms / 1000, tz=NY_TZ).date()
    i = bisect.bisect_right(list(sessions), fill_day)
    return sessions[i - 1] if i > 0 else None


def bars_since(entry_session: date, sessions: Sequence[date]) -> int:
    """Nombre de séances clôturées strictement après la séance d'entrée."""
    return len(sessions) - bisect.bisect_right(list(sessions), entry_session)
