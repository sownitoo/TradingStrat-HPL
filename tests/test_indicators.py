import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rsi2_bot.indicators import rsi, sma

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gspc() -> pd.Series:
    """Clôtures daily ^GSPC (yfinance) du 2023-01-03 au 2026-09-25, figées dans le dépôt."""
    df = pd.read_csv(FIXTURES / "gspc_daily_close.csv")
    return pd.Series(df["close"].to_numpy(), index=df["date"].to_numpy(), name="close")


@pytest.fixture(scope="module")
def tradingview() -> dict:
    return json.loads((FIXTURES / "tradingview_spx_2026-09-25.json").read_text())["bars"]


# --- SMA ------------------------------------------------------------------------
def test_sma_simple_values():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = sma(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0, 5.0]


def test_sma_rejects_bad_length():
    with pytest.raises(ValueError):
        sma(pd.Series([1.0]), 0)


# --- RSI ------------------------------------------------------------------------
def test_rsi2_hand_computed():
    # Variations : +1, -0.5, +1, -0.5
    # idx2 : moy. hausse = (1+0)/2 = 0.5, moy. baisse = (0+0.5)/2 = 0.25 -> RS=2   -> 66.667
    # idx3 : hausse = (0.5+1)/2 = 0.75, baisse = (0.25+0)/2 = 0.125       -> RS=6   -> 85.714
    # idx4 : hausse = (0.75+0)/2 = 0.375, baisse = (0.125+0.5)/2 = 0.3125 -> RS=1.2 -> 54.545
    out = rsi(pd.Series([10.0, 11.0, 10.5, 11.5, 11.0]), 2)
    assert out.iloc[:2].isna().all()
    np.testing.assert_allclose(out.iloc[2:].to_numpy(), [200 / 3, 600 / 7, 600 / 11], rtol=1e-12)


def test_rsi_extremes_follow_tradingview_convention():
    up = pd.Series(np.arange(1.0, 11.0))
    down = pd.Series(np.arange(10.0, 0.0, -1.0))
    assert (rsi(up, 2).dropna() == 100.0).all()
    assert (rsi(down, 2).dropna() == 0.0).all()


def test_rsi_too_short_series_is_nan():
    assert rsi(pd.Series([1.0, 2.0]), 2).isna().all()


def test_rsi_matches_ewm_formulation_after_warmup(gspc):
    """Contrôle indépendant : RMA = ewm(alpha=1/n, adjust=False), une fois l'initialisation oubliée."""
    change = gspc.diff()
    up = change.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    down = (-change.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    expected = 100 - 100 / (1 + up / down)
    np.testing.assert_allclose(rsi(gspc, 2).iloc[100:], expected.iloc[100:], atol=1e-9)


# --- Comparaison avec TradingView (SP:SPX, 1D) --------------------------------------
# Valeurs relevées sur TradingView le 2026-09-27 (fixture JSON). TradingView n'expose
# pas RSI(2) via son API, on compare donc RSI(14) et RSI(7) : même formule
# (ta.rsi / RMA de Wilder), seule la longueur change. RSI(2) est ensuite figé en test
# de non-régression.
DATES = ["2026-09-23", "2026-09-24", "2026-09-25"]


@pytest.mark.parametrize("day", DATES)
def test_close_matches_tradingview(gspc, tradingview, day):
    assert gspc[day] == pytest.approx(tradingview[day]["close"], abs=0.005)


@pytest.mark.parametrize("day", DATES)
def test_sma5_matches_tradingview(gspc, tradingview, day):
    assert sma(gspc, 5)[day] == pytest.approx(tradingview[day]["sma5"], abs=0.01)


@pytest.mark.parametrize("day", DATES)
def test_sma200_matches_tradingview(gspc, tradingview, day):
    # Écart constaté < 0.001 point (une clôture ancienne diffère de quelques
    # centimes entre yfinance et TradingView).
    assert sma(gspc, 200)[day] == pytest.approx(tradingview[day]["sma200"], abs=0.01)


@pytest.mark.parametrize("day", DATES)
def test_rsi14_matches_tradingview(gspc, tradingview, day):
    assert rsi(gspc, 14)[day] == pytest.approx(tradingview[day]["rsi14"], abs=1e-3)


@pytest.mark.parametrize("day", ["2026-09-24", "2026-09-25"])
def test_rsi7_matches_tradingview(gspc, tradingview, day):
    assert rsi(gspc, 7)[day] == pytest.approx(tradingview[day]["rsi7"], abs=1e-3)


@pytest.mark.parametrize(
    "day, expected",
    [("2026-09-23", 37.0945), ("2026-09-24", 35.6886), ("2026-09-25", 74.9480)],
)
def test_rsi2_regression(gspc, day, expected):
    assert rsi(gspc, 2)[day] == pytest.approx(expected, abs=1e-3)


def test_rsi2_independent_of_history_start(gspc):
    """Avec >= 50 bougies d'historique, RSI(2) ne dépend plus du point de départ."""
    full = rsi(gspc, 2).iloc[-1]
    short = rsi(gspc.iloc[-60:], 2).iloc[-1]
    assert short == pytest.approx(full, abs=1e-9)
