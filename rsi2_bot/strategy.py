"""Règles de la stratégie RSI(2) mean reversion. Aucune I/O ici : tout est testable."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

import pandas as pd

from . import config
from .indicators import rsi, sma


@dataclass(frozen=True)
class Snapshot:
    """Valeurs des indicateurs sur la dernière bougie daily clôturée."""

    date: date
    close: float
    sma_trend: float
    sma_exit: float
    rsi: float

    def is_complete(self) -> bool:
        return not any(math.isnan(v) for v in (self.close, self.sma_trend, self.sma_exit, self.rsi))


class Action(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"
    HOLD = "HOLD"
    STAY_FLAT = "STAY_FLAT"
    ABORT = "ABORT"  # situation anormale : aucune action, intervention manuelle


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def compute_snapshot(closes: pd.Series) -> Snapshot:
    """Calcule SMA200, SMA5 et RSI(2) et renvoie la dernière ligne."""
    if closes.empty:
        raise ValueError("aucune clôture")
    trend = sma(closes, config.SMA_TREND_LENGTH)
    exit_ = sma(closes, config.SMA_EXIT_LENGTH)
    rsi_ = rsi(closes, config.RSI_LENGTH)
    return Snapshot(
        date=closes.index[-1],
        close=float(closes.iloc[-1]),
        sma_trend=float(trend.iloc[-1]),
        sma_exit=float(exit_.iloc[-1]),
        rsi=float(rsi_.iloc[-1]),
    )


def decide(
    snap: Snapshot,
    position_size: float,
    bars_held: Optional[int],
    traded_this_session: bool,
) -> Decision:
    """Décision du jour.

    position_size : taille signée réelle sur Hyperliquid (szi).
    bars_held : nombre de séances clôturées depuis la séance d'entrée
        (0 = position ouverte sur la séance courante).
    traded_this_session : un fill a déjà eu lieu sur la séance courante
        (évite de ré-entrer si le script est relancé après une sortie).
    """
    if not snap.is_complete():
        return Decision(Action.ABORT, "indicateurs incomplets (historique insuffisant)")

    if position_size < 0:
        return Decision(Action.ABORT, f"position SHORT inattendue ({position_size}) : intervention manuelle requise")

    if position_size > 0:
        if bars_held is None:
            return Decision(Action.ABORT, "position ouverte mais date d'entrée inconnue")
        if bars_held == 0:
            return Decision(Action.HOLD, "position ouverte sur la séance courante")
        if snap.close > snap.sma_exit:
            return Decision(
                Action.EXIT_LONG,
                f"clôture {snap.close:.2f} > SMA{config.SMA_EXIT_LENGTH} {snap.sma_exit:.2f}",
            )
        if bars_held >= config.MAX_HOLDING_DAYS:
            return Decision(
                Action.EXIT_LONG,
                f"time stop : position tenue {bars_held} jours de bourse (max {config.MAX_HOLDING_DAYS})",
            )
        return Decision(
            Action.HOLD,
            f"clôture <= SMA{config.SMA_EXIT_LENGTH}, jour {bars_held}/{config.MAX_HOLDING_DAYS}",
        )

    # Pas de position
    if traded_this_session:
        return Decision(Action.STAY_FLAT, "déjà tradé sur cette séance, pas de ré-entrée")

    above_trend = snap.close > snap.sma_trend
    oversold = snap.rsi < config.RSI_ENTRY_THRESHOLD
    if above_trend and oversold:
        return Decision(
            Action.ENTER_LONG,
            f"clôture {snap.close:.2f} > SMA{config.SMA_TREND_LENGTH} {snap.sma_trend:.2f} "
            f"et RSI({config.RSI_LENGTH}) {snap.rsi:.2f} < {config.RSI_ENTRY_THRESHOLD:g}",
        )
    reasons = []
    if not above_trend:
        reasons.append(f"clôture <= SMA{config.SMA_TREND_LENGTH}")
    if not oversold:
        reasons.append(f"RSI({config.RSI_LENGTH}) >= {config.RSI_ENTRY_THRESHOLD:g}")
    return Decision(Action.STAY_FLAT, "pas de signal : " + " et ".join(reasons))
