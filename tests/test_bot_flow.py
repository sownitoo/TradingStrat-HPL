"""Tests de bout en bout de `run()` avec un faux client Hyperliquid (aucun appel réseau)."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rsi2_bot.config import Settings
from rsi2_bot.hyperliquid_client import (
    Position,
    is_long_opening_fill,
    parse_order_response,
    round_size_down,
)
from rsi2_bot.main import EXIT_ERROR, EXIT_OK, run
from rsi2_bot.market_data import NY_TZ

SESSIONS = [d.date() for d in pd.bdate_range(end="2026-09-25", periods=300)]
AFTER_CLOSE = datetime(2026, 9, 25, 16, 15, tzinfo=NY_TZ)  # = 22h15 Paris
OK_FILL = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"filled": {"totalSz": "0.064", "avgPx": "7740.0", "oid": 1}}]}}}


def ms(d, hour=16, minute=15):
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY_TZ).timestamp() * 1000)


def oversold_closes():
    """Tendance haussière puis deux fortes baisses : clôture > SMA200, RSI(2) < 10, clôture < SMA5."""
    c = 100 + 0.1 * np.arange(len(SESSIONS))
    c[-2] -= 1.0
    c[-1] -= 2.0
    return pd.Series(c, index=SESSIONS)


def rising_closes():
    """Tendance haussière régulière : clôture > SMA5, RSI(2) = 100."""
    return pd.Series(100 + 0.1 * np.arange(len(SESSIONS)), index=SESSIONS)


def opening_fill(session, sz="0.064"):
    return {"coin": "xyz:SP500", "side": "B", "sz": sz, "startPosition": "0.0", "dir": "Open Long",
            "time": ms(session), "px": "7740.0", "oid": 1, "tid": 1}


def closing_fill(session, sz="0.064"):
    return {"coin": "xyz:SP500", "side": "A", "sz": sz, "startPosition": sz, "dir": "Close Long",
            "time": ms(session), "px": "7800.0", "oid": 2, "tid": 2}


class FakeClient:
    def __init__(self, position=0.0, fills=(), open_orders=(), lev_status="ok", live=True):
        self.position = position
        self.fills = list(fills)
        self.open_orders = list(open_orders)
        self.lev_status = lev_status
        self.exchange = object() if live else None
        self.sz_decimals = 3
        self.calls = []

    def check_agent(self):
        self.calls.append("check_agent")

    def user_state(self):
        return {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000"}

    def get_position(self):
        return Position(self.position, 7740.0 if self.position else None, None)

    def get_open_orders(self):
        return self.open_orders

    def get_fills(self):
        return self.fills

    def mid_price(self):
        return 7750.0

    def set_leverage(self, is_cross):
        self.calls.append(("set_leverage", is_cross))
        return {"status": self.lev_status, "response": {"type": "default"}}

    def market_buy(self, size, slippage):
        self.calls.append(("market_buy", size, slippage))
        self.position = size
        return OK_FILL

    def market_close(self, size, slippage):
        self.calls.append(("market_close", size, slippage))
        self.position = 0.0
        return OK_FILL

    @property
    def orders(self):
        return [c for c in self.calls if isinstance(c, tuple) and c[0].startswith("market_")]


def settings(tmp_path: Path, key="0xabc"):
    return Settings(secret_key=key, account_address="0xUSER", network="mainnet", position_size_usd=500,
                    slippage=0.01, margin_mode="isolated", log_file=tmp_path / "bot.log")


def go(tmp_path, client, closes, live=True, now=AFTER_CLOSE, **kw):
    return run(settings(tmp_path), live=live, size_usd=500, client_factory=lambda *a: client,
               closes_loader=lambda _t: closes, now=now, **kw)


# --- Entrées ------------------------------------------------------------------------
def test_dry_run_never_sends_orders(tmp_path):
    client = FakeClient(live=False)
    assert go(tmp_path, client, oversold_closes(), live=False) == EXIT_OK
    assert client.orders == [] and ("set_leverage", False) not in client.calls


def test_live_entry_sets_leverage_then_buys_rounded_size(tmp_path):
    client = FakeClient()
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert "check_agent" in client.calls
    assert client.calls.index(("set_leverage", False)) < client.calls.index(("market_buy", 0.064, 0.01))
    # 500 / 7750 = 0.06451... arrondi vers le bas à 3 décimales (szDecimals)
    assert client.orders == [("market_buy", 0.064, 0.01)]


def test_no_entry_if_leverage_update_fails(tmp_path):
    client = FakeClient(lev_status="err")
    assert go(tmp_path, client, oversold_closes()) == EXIT_ERROR
    assert client.orders == []


def test_second_run_same_day_does_not_double_entry(tmp_path):
    """Script relancé après une entrée : la position lue sur Hyperliquid bloque le doublon."""
    client = FakeClient(position=0.064, fills=[opening_fill(SESSIONS[-1])])
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert client.orders == []


def test_no_reentry_after_exit_same_session(tmp_path):
    fills = [opening_fill(SESSIONS[-11]), closing_fill(SESSIONS[-1])]
    client = FakeClient(position=0.0, fills=fills)
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert client.orders == []


def test_stale_data_blocks_live_orders(tmp_path):
    client = FakeClient()
    monday = datetime(2026, 9, 28, 16, 15, tzinfo=NY_TZ)
    assert go(tmp_path, client, oversold_closes(), now=monday) == EXIT_OK
    assert client.orders == []


def test_open_orders_block_live(tmp_path):
    client = FakeClient(open_orders=[{"coin": "xyz:SP500", "oid": 9}])
    assert go(tmp_path, client, oversold_closes()) == EXIT_ERROR
    assert client.orders == []


def test_live_requires_credentials(tmp_path):
    s = settings(tmp_path, key=None)
    assert run(s, live=True, size_usd=500, closes_loader=lambda _t: oversold_closes(), now=AFTER_CLOSE) == EXIT_ERROR


# --- Sorties ------------------------------------------------------------------------
def test_exit_when_close_above_sma5(tmp_path):
    client = FakeClient(position=0.064, fills=[opening_fill(SESSIONS[-3])])
    assert go(tmp_path, client, rising_closes()) == EXIT_OK
    assert client.orders == [("market_close", 0.064, 0.01)]


def test_time_stop_uses_entry_date_from_fills(tmp_path):
    client = FakeClient(position=0.064, fills=[opening_fill(SESSIONS[-11])])  # 10 séances écoulées
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert client.orders == [("market_close", 0.064, 0.01)]


def test_hold_before_time_stop(tmp_path):
    client = FakeClient(position=0.064, fills=[opening_fill(SESSIONS[-10])])  # 9 séances
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert client.orders == []


def test_position_without_opening_fill_is_considered_old(tmp_path):
    client = FakeClient(position=0.064, fills=[])
    assert go(tmp_path, client, oversold_closes()) == EXIT_OK
    assert client.orders == [("market_close", 0.064, 0.01)]


def test_log_file_contains_indicators_and_decision(tmp_path):
    import logging

    from rsi2_bot.main import setup_logging

    setup_logging(tmp_path / "bot.log")
    try:
        go(tmp_path, FakeClient(), oversold_closes())
    finally:
        for h in logging.getLogger().handlers:
            h.flush()
    text = (tmp_path / "bot.log").read_text(encoding="utf-8")
    for needle in ("RSI(2)=", "SMA200=", "SMA5=", "DÉCISION : ENTER_LONG", "ORDRE prévu", "Réponse exchange"):
        assert needle in text
    logging.getLogger().handlers.clear()


# --- Utilitaires ---------------------------------------------------------------------
def test_parse_order_response():
    assert parse_order_response(OK_FILL).ok
    err = {"status": "ok", "response": {"data": {"statuses": [{"error": "Insufficient margin"}]}}}
    assert parse_order_response(err).error == "Insufficient margin"
    assert not parse_order_response({"status": "err", "response": "nope"}).ok
    assert not parse_order_response(None).ok


@pytest.mark.parametrize(
    "fill, expected",
    [
        ({"side": "B", "startPosition": "0.0", "sz": "1"}, True),
        ({"side": "B", "startPosition": "-0.5", "sz": "1"}, True),  # short -> long
        ({"side": "B", "startPosition": "0.5", "sz": "1"}, False),  # renforcement
        ({"side": "A", "startPosition": "1", "sz": "1"}, False),
    ],
)
def test_is_long_opening_fill(fill, expected):
    assert is_long_opening_fill(fill) is expected


def test_round_size_down():
    assert round_size_down(0.0645161, 3) == 0.064
    assert round_size_down(0.064, 3) == 0.064
