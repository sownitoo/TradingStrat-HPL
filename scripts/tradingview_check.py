"""Compare nos indicateurs (yfinance ^GSPC) aux valeurs TradingView (SP:SPX, 1D) du jour.

TradingView n'expose pas RSI(2) via son API scanner : on compare clôture, SMA5,
SMA200, RSI(14) et RSI(7), calculés par le même code que RSI(2).

    python scripts/tradingview_check.py
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rsi2_bot.indicators import rsi, sma  # noqa: E402
from rsi2_bot.market_data import fetch_daily_closes  # noqa: E402

COLUMNS = ["close", "SMA5", "SMA200", "RSI", "RSI7"]
OFFSETS = [0, 1, 2]


def fetch_tradingview() -> dict:
    cols = [c if k == 0 else f"{c}[{k}]" for k in OFFSETS for c in COLUMNS]
    resp = requests.post(
        "https://scanner.tradingview.com/america/scan",
        json={"symbols": {"tickers": ["SP:SPX"], "query": {"types": []}}, "columns": cols},
        timeout=20,
    )
    resp.raise_for_status()
    return dict(zip(cols, resp.json()["data"][0]["d"]))


def main() -> int:
    tv = fetch_tradingview()
    closes = fetch_daily_closes("^GSPC")
    ours = {"close": closes, "SMA5": sma(closes, 5), "SMA200": sma(closes, 200),
            "RSI": rsi(closes, 14), "RSI7": rsi(closes, 7)}
    rsi2 = rsi(closes, 2)
    tol = {"close": 0.01, "SMA5": 0.01, "SMA200": 0.05, "RSI": 0.01, "RSI7": 0.01}
    ok = True
    # Hypothèse : la dernière bougie yfinance est la même que celle de TradingView.
    for k in OFFSETS:
        day = closes.index[-1 - k]
        print(f"\n{day}   (RSI(2) calculé = {rsi2.iloc[-1 - k]:.2f})")
        for c in COLUMNS:
            key = c if k == 0 else f"{c}[{k}]"
            ref, mine = tv.get(key), float(ours[c].iloc[-1 - k])
            if ref is None:
                continue
            good = abs(mine - ref) <= tol[c]
            ok &= good
            print(f"  {c:7s} TradingView={ref:12.4f}  bot={mine:12.4f}  {'OK' if good else 'ÉCART'}")
    print("\nRésultat :", "tout concorde" if ok else "écarts détectés")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
