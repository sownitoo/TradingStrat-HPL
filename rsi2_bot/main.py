"""Point d'entrée : une exécution = une décision quotidienne, puis fin du programme.

Usage :
    python -m rsi2_bot              # dry-run (défaut) : n'envoie aucun ordre
    python -m rsi2_bot --live       # mode réel
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from . import config
from .config import Settings, load_settings
from .hyperliquid_client import (
    HyperliquidClient,
    Position,
    is_long_opening_fill,
    parse_order_response,
    round_size_down,
)
from .market_data import NY_TZ, bars_since, completed_bars, fetch_daily_closes, is_fresh, session_of
from .strategy import Action, compute_snapshot, decide

log = logging.getLogger("rsi2_bot")

EXIT_OK = 0
EXIT_ERROR = 1


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S%z")
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    # Le SDK logge le payload signé en DEBUG : on reste en INFO.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)


def _json(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def run(
    settings: Settings,
    live: bool,
    size_usd: float,
    allow_stale: bool = False,
    client_factory: Callable[..., HyperliquidClient] = HyperliquidClient,
    closes_loader: Callable[[str], pd.Series] = fetch_daily_closes,
    now: Optional[datetime] = None,
) -> int:
    mode = "LIVE" if live else "DRY-RUN"
    now_ny = (now or datetime.now(tz=NY_TZ)).astimezone(NY_TZ)
    log.info("=" * 70)
    log.info(
        "Démarrage %s | %s | signal %s -> exécution %s | taille %.2f USD | levier %dx | marge %s | réseau %s",
        mode, now_ny.isoformat(timespec="seconds"), config.SIGNAL_TICKER, config.COIN,
        size_usd, config.LEVERAGE, settings.margin_mode, settings.network,
    )

    if size_usd < config.MIN_ORDER_NOTIONAL_USD:
        log.error("Taille %.2f USD < minimum %.0f USD", size_usd, config.MIN_ORDER_NOTIONAL_USD)
        return EXIT_ERROR
    if live and not (settings.secret_key and settings.account_address):
        log.error("Mode --live : HL_SECRET_KEY et HL_ACCOUNT_ADDRESS doivent être définis dans .env")
        return EXIT_ERROR

    # 1. Signaux sur le S&P 500 cash -------------------------------------------
    closes = completed_bars(closes_loader(config.SIGNAL_TICKER), now_ny)
    snap = compute_snapshot(closes)
    log.info(
        "Bougie %s du %s : clôture=%.2f | SMA%d=%.2f | SMA%d=%.2f | RSI(%d)=%.2f",
        config.SIGNAL_TICKER, snap.date, snap.close, config.SMA_TREND_LENGTH, snap.sma_trend,
        config.SMA_EXIT_LENGTH, snap.sma_exit, config.RSI_LENGTH, snap.rsi,
    )
    fresh = is_fresh(closes, now_ny)
    if not fresh:
        msg = (
            f"Dernière bougie clôturée = {snap.date}, pas celle d'aujourd'hui ({now_ny.date()}) : "
            "jour férié, week-end, séance pas encore close ou données pas encore publiées."
        )
        if live and not allow_stale:
            log.warning("%s Aucun ordre envoyé.", msg)
            return EXIT_OK
        log.warning("%s Décision calculée à titre indicatif.", msg)

    # 2. État réel sur Hyperliquid ---------------------------------------------
    client: Optional[HyperliquidClient] = None
    fills: List[dict] = []
    if settings.account_address:
        client = client_factory(settings.account_address, settings.secret_key, settings.network)
        if client.exchange is not None:
            client.check_agent()
        position = client.get_position()
        state = client.user_state()
        log.info(
            "Compte %s (dex %s) : valeur=%s USDC, retirable=%s USDC",
            settings.account_address, config.PERP_DEX,
            state.get("marginSummary", {}).get("accountValue"), state.get("withdrawable"),
        )
        log.info("Position %s : taille=%s, prix d'entrée=%s, détail=%s",
                 config.COIN, position.size, position.entry_px, _json(position.raw))
        open_orders = client.get_open_orders()
        if open_orders:
            log.error("Ordres ouverts sur %s : %s", config.COIN, _json(open_orders))
            if live:
                log.error("Aucune action en présence d'ordres ouverts (intervention manuelle).")
                return EXIT_ERROR
        fills = client.get_fills()
    else:
        log.warning("HL_ACCOUNT_ADDRESS absent : position supposée nulle (dry-run uniquement).")
        position = Position(0.0, None, None)

    sessions = list(closes.index)
    bars_held: Optional[int] = None
    if position.size > 0:
        openings = [f for f in fills if is_long_opening_fill(f)]
        if openings:
            entry_session = session_of(openings[-1]["time"], sessions)
            bars_held = bars_since(entry_session, sessions)
            log.info("Entrée détectée via les fills : séance %s (fill %s), %d jour(s) de bourse écoulé(s)",
                     entry_session, _json(openings[-1]), bars_held)
        else:
            bars_held = config.MAX_HOLDING_DAYS
            log.warning("Aucun fill d'ouverture sur %d jours : position plus ancienne que le time stop.",
                        config.FILLS_LOOKBACK_DAYS)
    traded_this_session = any(session_of(f["time"], sessions) == snap.date for f in fills)

    # 3. Décision ----------------------------------------------------------------
    decision = decide(snap, position.size, bars_held, traded_this_session)
    log.info("DÉCISION : %s — %s", decision.action.value, decision.reason)

    summary = {
        "mode": mode, "bar_date": snap.date, "fresh": fresh, "close": round(snap.close, 2),
        f"sma{config.SMA_TREND_LENGTH}": round(snap.sma_trend, 2),
        f"sma{config.SMA_EXIT_LENGTH}": round(snap.sma_exit, 2),
        f"rsi{config.RSI_LENGTH}": round(snap.rsi, 2),
        "position": position.size, "bars_held": bars_held,
        "decision": decision.action.value, "reason": decision.reason, "order": None,
    }

    # 4. Exécution ---------------------------------------------------------------
    rc = EXIT_OK
    if decision.action == Action.ABORT:
        rc = EXIT_ERROR
    elif decision.action in (Action.ENTER_LONG, Action.EXIT_LONG):
        rc = _execute(decision.action, client, position, settings, live, size_usd, summary)

    log.info("RÉSUMÉ %s", _json(summary))
    return rc


def _execute(action, client, position, settings, live, size_usd, summary) -> int:
    if client is None:
        log.info("[DRY-RUN] Pas de compte configuré : ordre %s non simulé sur le carnet.", action.value)
        return EXIT_OK

    mid = client.mid_price()
    if action == Action.ENTER_LONG:
        size = round_size_down(size_usd / mid, client.sz_decimals)
        side = "BUY"
    else:
        size = abs(position.size)
        side = "SELL (reduce-only)"
    notional = size * mid
    order = {
        "coin": config.COIN, "side": side, "size": size, "mid": mid,
        "notional_usd": round(notional, 2), "type": "market IOC", "slippage": settings.slippage,
        "leverage": config.LEVERAGE, "margin": settings.margin_mode,
    }
    summary["order"] = order
    log.info("ORDRE prévu : %s", _json(order))

    if action == Action.ENTER_LONG and notional < config.MIN_ORDER_NOTIONAL_USD:
        log.error("Notionnel %.2f USD < minimum %.0f USD : ordre annulé", notional, config.MIN_ORDER_NOTIONAL_USD)
        return EXIT_ERROR

    if not live:
        log.info("[DRY-RUN] Ordre NON envoyé. Relancer avec --live pour trader.")
        return EXIT_OK

    if action == Action.ENTER_LONG:
        lev_resp = client.set_leverage(settings.is_cross)
        log.info("Réponse update_leverage(%dx, %s) : %s", config.LEVERAGE, settings.margin_mode, _json(lev_resp))
        if not isinstance(lev_resp, dict) or lev_resp.get("status") != "ok":
            log.error("Échec du réglage du levier : ordre d'entrée annulé")
            return EXIT_ERROR
        resp = client.market_buy(size, settings.slippage)
    else:
        resp = client.market_close(size, settings.slippage)

    log.info("Réponse exchange : %s", _json(resp))
    result = parse_order_response(resp)
    summary["order"]["result"] = result.__dict__
    after = client.get_position()
    log.info("Position après ordre : taille=%s, prix d'entrée=%s", after.size, after.entry_px)
    if not result.ok:
        log.error("Ordre NON exécuté : %s", result.error)
        return EXIT_ERROR
    if result.filled_size + 1e-12 < size:
        log.warning("Exécution partielle : %s / %s", result.filled_size, size)
    log.info("Ordre exécuté : %s %s @ %s", result.filled_size, config.COIN, result.avg_px)
    return EXIT_OK


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="rsi2_bot", description="Bot RSI(2) mean reversion S&P 500 -> xyz:SP500")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="affiche la décision sans passer d'ordre (défaut)")
    mode.add_argument("--live", action="store_true", help="envoie réellement les ordres sur Hyperliquid")
    p.add_argument("--size-usd", type=float, help="taille de position en USD (défaut : POSITION_SIZE_USD du .env)")
    p.add_argument("--allow-stale", action="store_true",
                   help="en --live, trader même si la bougie du jour n'est pas disponible (déconseillé)")
    p.add_argument("--env-file", type=Path, help="chemin du fichier .env (défaut : .env du projet)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.env_file)
    setup_logging(settings.log_file)
    size_usd = args.size_usd if args.size_usd is not None else settings.position_size_usd

    # Verrou : empêche deux exécutions simultanées (cron + lancement manuel).
    lock_path = settings.log_file.parent / "rsi2_bot.lock"
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Une autre exécution est en cours (%s) : abandon.", lock_path)
            return EXIT_ERROR
        try:
            return run(settings, live=args.live, size_usd=size_usd, allow_stale=args.allow_stale)
        except Exception:
            log.exception("Erreur fatale : aucune action supplémentaire.")
            return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
