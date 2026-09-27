from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from rsi2_bot.strategy import Action, Snapshot, compute_snapshot, decide

D = date(2026, 9, 25)


def snap(close=100.0, sma_trend=90.0, sma_exit=105.0, rsi=5.0):
    return Snapshot(D, close, sma_trend, sma_exit, rsi)


# --- Entrée -----------------------------------------------------------------------
def test_enter_when_above_sma200_and_rsi_below_10_and_flat():
    assert decide(snap(), 0.0, None, False).action == Action.ENTER_LONG


def test_no_entry_below_sma200():
    assert decide(snap(close=100, sma_trend=101), 0.0, None, False).action == Action.STAY_FLAT


@pytest.mark.parametrize("rsi_value", [10.0, 10.01, 50.0])
def test_no_entry_when_rsi_not_below_10(rsi_value):
    assert decide(snap(rsi=rsi_value), 0.0, None, False).action == Action.STAY_FLAT


def test_no_reentry_if_already_traded_this_session():
    assert decide(snap(), 0.0, None, True).action == Action.STAY_FLAT


def test_no_entry_when_already_long():
    # Signal d'entrée présent mais position déjà ouverte : pas de doublon
    d = decide(snap(close=100, sma_exit=105), 0.5, 3, False)
    assert d.action == Action.HOLD


# --- Sortie -----------------------------------------------------------------------
def test_exit_when_close_above_sma5():
    d = decide(snap(close=106, sma_exit=105, rsi=60), 0.5, 2, False)
    assert d.action == Action.EXIT_LONG and "SMA5" in d.reason


def test_time_stop_after_10_trading_days():
    d = decide(snap(close=100, sma_exit=105), 0.5, 10, False)
    assert d.action == Action.EXIT_LONG and "time stop" in d.reason


def test_hold_on_day_9():
    assert decide(snap(close=100, sma_exit=105), 0.5, 9, False).action == Action.HOLD


def test_hold_on_entry_session_even_if_exit_condition():
    assert decide(snap(close=106, sma_exit=105), 0.5, 0, False).action == Action.HOLD


# --- Anomalies --------------------------------------------------------------------
def test_short_position_aborts():
    assert decide(snap(), -0.1, None, False).action == Action.ABORT


def test_incomplete_indicators_abort():
    assert decide(snap(sma_trend=float("nan")), 0.0, None, False).action == Action.ABORT


def test_unknown_entry_date_aborts():
    assert decide(snap(), 0.5, None, False).action == Action.ABORT


# --- Snapshot sur données réelles ----------------------------------------------------
def test_compute_snapshot_on_gspc_fixture():
    df = pd.read_csv(Path(__file__).parent / "fixtures" / "gspc_daily_close.csv")
    closes = pd.Series(df["close"].to_numpy(), index=df["date"].to_numpy())
    s = compute_snapshot(closes)
    assert s.date == "2026-09-25"
    assert s.close == pytest.approx(7743.41)
    assert s.sma_exit == pytest.approx(7736.582, abs=0.01)
    assert s.sma_trend == pytest.approx(7205.20, abs=0.01)
    assert s.rsi == pytest.approx(74.948, abs=1e-3)
    assert decide(s, 0.0, None, False).action == Action.STAY_FLAT
