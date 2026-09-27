"""Accès à Hyperliquid (SDK officiel hyperliquid-python-sdk) pour le perp HIP-3 xyz:SP500.

Adressage HIP-3 dans le SDK :
- ``Info``/``Exchange`` doivent être construits avec ``perp_dexs=["xyz"]`` pour
  charger la meta du dex déployé par trade.xyz (sinon "xyz:SP500" est inconnu) ;
- le marché se nomme ``"xyz:SP500"`` dans les ordres ;
- les requêtes d'état utilisateur prennent ``dex="xyz"`` (chaque dex HIP-3 a
  son propre clearinghouse) : ``info.user_state(addr, "xyz")``.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, List, Optional

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from . import config

log = logging.getLogger(__name__)

FILLS_PAGE_SIZE = 2000  # maximum renvoyé par userFillsByTime
MAX_FILL_PAGES = 10


@dataclass(frozen=True)
class Position:
    size: float  # szi signé : > 0 long, < 0 short, 0 flat
    entry_px: Optional[float]
    raw: Optional[dict]


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    filled_size: float
    avg_px: Optional[float]
    error: Optional[str]


def parse_order_response(resp: Any) -> OrderResult:
    """Interprète la réponse de /exchange pour un ordre unique."""
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        return OrderResult(False, 0.0, None, f"réponse en erreur : {resp}")
    try:
        status = resp["response"]["data"]["statuses"][0]
    except (KeyError, IndexError, TypeError):
        return OrderResult(False, 0.0, None, f"réponse inattendue : {resp}")
    if "filled" in status:
        filled = status["filled"]
        return OrderResult(True, float(filled["totalSz"]), float(filled["avgPx"]), None)
    if "error" in status:
        return OrderResult(False, 0.0, None, status["error"])
    return OrderResult(False, 0.0, None, f"ordre non exécuté : {status}")


def is_long_opening_fill(fill: dict) -> bool:
    """Fill d'achat qui fait passer la position de <= 0 à > 0 (ouverture du long)."""
    if fill.get("side") != "B":
        return False
    start = float(fill["startPosition"])
    return start <= 0 < start + float(fill["sz"])


def round_size_down(size: float, sz_decimals: int) -> float:
    factor = 10**sz_decimals
    return math.floor(size * factor + 1e-9) / factor


class HyperliquidClient:
    def __init__(self, account_address: str, secret_key: Optional[str], network: str):
        self.base_url = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
        self.account_address = account_address
        self.exchange: Optional[Exchange] = None
        if secret_key:
            wallet = eth_account.Account.from_key(secret_key)
            self.exchange = Exchange(
                wallet,
                self.base_url,
                account_address=account_address,
                perp_dexs=[config.PERP_DEX],
            )
            self.info = self.exchange.info
        else:
            self.info = Info(self.base_url, skip_ws=True, perp_dexs=[config.PERP_DEX])

        if config.COIN not in self.info.name_to_coin:
            raise RuntimeError(f"{config.COIN} introuvable dans la meta du dex '{config.PERP_DEX}'")
        self.asset = self.info.name_to_asset(config.COIN)
        self.sz_decimals = self.info.asset_to_sz_decimals[self.asset]

    # --- Lecture -------------------------------------------------------------
    @property
    def agent_address(self) -> Optional[str]:
        return self.exchange.wallet.address if self.exchange else None

    def check_agent(self) -> None:
        """Vérifie que la clé est bien un agent wallet approuvé pour le compte."""
        agent = self.agent_address
        if agent is None:
            return
        if agent.lower() == self.account_address.lower():
            log.warning(
                "La clé fournie est celle du compte principal. Utilisez plutôt un API wallet (agent) : "
                "il ne peut pas retirer de fonds."
            )
            return
        role = self.info.post("/info", {"type": "userRole", "user": agent})
        log.info("Agent wallet %s, rôle : %s", agent, role)
        master = (role or {}).get("data", {}).get("user", "") if isinstance(role, dict) else ""
        if not isinstance(role, dict) or role.get("role") != "agent" or master.lower() != self.account_address.lower():
            raise RuntimeError(
                f"L'adresse {agent} n'est pas un agent wallet approuvé pour {self.account_address} "
                f"(réponse userRole : {role}). Agent expiré ou mauvaise HL_ACCOUNT_ADDRESS ?"
            )

    def user_state(self) -> dict:
        return self.info.user_state(self.account_address, config.PERP_DEX)

    def get_position(self) -> Position:
        state = self.user_state()
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            if pos.get("coin") == config.COIN:
                size = float(pos.get("szi", 0))
                entry_px = float(pos["entryPx"]) if pos.get("entryPx") else None
                return Position(size, entry_px, pos)
        return Position(0.0, None, None)

    def get_open_orders(self) -> List[dict]:
        orders = self.info.open_orders(self.account_address, config.PERP_DEX)
        return [o for o in orders if o.get("coin") == config.COIN]

    def get_fills(self, lookback_days: int = config.FILLS_LOOKBACK_DAYS) -> List[dict]:
        """Fills du compte sur xyz:SP500, du plus ancien au plus récent.

        L'API renvoie au plus 2000 fills par appel, en partant de startTime :
        on pagine vers l'avant et on dédoublonne.
        """
        start_ms = int((time.time() - lookback_days * 86400) * 1000)
        seen, fills = set(), []
        for _ in range(MAX_FILL_PAGES):
            batch = self.info.user_fills_by_time(self.account_address, start_ms, aggregate_by_time=True)
            for f in batch:
                key = (f.get("tid"), f.get("oid"), f["time"], f.get("sz"))
                if key not in seen:
                    seen.add(key)
                    fills.append(f)
            if len(batch) < FILLS_PAGE_SIZE:
                break
            start_ms = batch[-1]["time"]  # même ms incluse : les doublons sont filtrés
        else:
            log.warning("Historique de fills tronqué à %d pages", MAX_FILL_PAGES)
        return sorted((f for f in fills if f.get("coin") == config.COIN), key=lambda f: f["time"])

    def mid_price(self) -> float:
        mids = self.info.all_mids(config.PERP_DEX)
        if config.COIN not in mids:
            raise RuntimeError(f"pas de prix mid pour {config.COIN}")
        return float(mids[config.COIN])

    # --- Écriture (mode --live uniquement) -----------------------------------
    def _require_exchange(self) -> Exchange:
        if self.exchange is None:
            raise RuntimeError("HL_SECRET_KEY absente : impossible d'envoyer un ordre")
        return self.exchange

    def set_leverage(self, is_cross: bool) -> Any:
        return self._require_exchange().update_leverage(config.LEVERAGE, config.COIN, is_cross=is_cross)

    def market_buy(self, size: float, slippage: float) -> Any:
        return self._require_exchange().market_open(config.COIN, True, size, None, slippage)

    def market_close(self, size: float, slippage: float) -> Any:
        return self._require_exchange().market_close(config.COIN, size, None, slippage)
