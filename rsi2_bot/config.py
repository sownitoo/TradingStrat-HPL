"""Paramètres de la stratégie et chargement de la configuration (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent

# --- Stratégie (figée) -------------------------------------------------------
SIGNAL_TICKER = "^GSPC"  # S&P 500 cash (yfinance) : source des signaux
RSI_LENGTH = 2
RSI_ENTRY_THRESHOLD = 10.0  # entrée si RSI(2) < 10
SMA_TREND_LENGTH = 200  # filtre de tendance : clôture > SMA200
SMA_EXIT_LENGTH = 5  # sortie si clôture > SMA5
MAX_HOLDING_DAYS = 10  # time stop en jours de bourse

# --- Exécution ---------------------------------------------------------------
# Marché HIP-3 déployé par trade.xyz. Le SDK adresse un marché HIP-3 par
# "<dex>:<COIN>" à condition de charger le dex via `perp_dexs=["xyz"]`.
PERP_DEX = "xyz"
COIN = "xyz:SP500"
LEVERAGE = 1  # levier fixe, volontairement non paramétrable
MIN_ORDER_NOTIONAL_USD = 10.0  # minimum Hyperliquid par ordre

# Fenêtre de recherche des fills pour retrouver la date d'entrée de la position
FILLS_LOOKBACK_DAYS = 60


@dataclass(frozen=True)
class Settings:
    secret_key: Optional[str]
    account_address: Optional[str]
    network: str
    position_size_usd: float
    slippage: float
    margin_mode: str
    log_file: Path

    @property
    def is_cross(self) -> bool:
        return self.margin_mode == "cross"


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Lit le fichier .env (à la racine du projet par défaut) puis l'environnement."""
    load_dotenv(env_file or PROJECT_DIR / ".env", override=False)

    network = os.getenv("HL_NETWORK", "mainnet").strip().lower()
    if network not in ("mainnet", "testnet"):
        raise ValueError(f"HL_NETWORK invalide : {network!r} (mainnet|testnet)")

    margin_mode = os.getenv("MARGIN_MODE", "isolated").strip().lower()
    if margin_mode not in ("isolated", "cross"):
        raise ValueError(f"MARGIN_MODE invalide : {margin_mode!r} (isolated|cross)")

    log_file = Path(os.getenv("LOG_FILE", "logs/rsi2_bot.log"))
    if not log_file.is_absolute():
        log_file = PROJECT_DIR / log_file

    return Settings(
        secret_key=(os.getenv("HL_SECRET_KEY") or "").strip() or None,
        account_address=(os.getenv("HL_ACCOUNT_ADDRESS") or "").strip() or None,
        network=network,
        position_size_usd=float(os.getenv("POSITION_SIZE_USD", "500")),
        slippage=float(os.getenv("SLIPPAGE", "0.01")),
        margin_mode=margin_mode,
        log_file=log_file,
    )
