# Bot RSI(2) mean reversion : S&P 500 → Hyperliquid `xyz:SP500`

Bot Python qui applique une stratégie RSI(2) « mean reversion » en daily :

- **Signaux** calculés sur les bougies daily du **S&P 500 cash** (`^GSPC` via yfinance), pas sur le perp.
- **Exécution** sur le perp HIP-3 **`xyz:SP500`** (dex `xyz` déployé par trade.xyz) avec le SDK officiel
  [`hyperliquid-python-sdk`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk).

| Règle | Condition |
|---|---|
| Entrée long | clôture > SMA200 **et** RSI(2) < 10 **et** aucune position ouverte |
| Sortie | clôture > SMA5 **ou** position tenue depuis 10 jours de bourse (time stop) |
| Taille | paramétrable en USD, **levier 1x fixe** |

Le bot s'exécute **une seule fois** puis s'arrête (pas de boucle infinie) : il est prévu pour être lancé par
cron chaque jour de semaine à **22h15 heure de Paris**, après la clôture US.

> ⚠️ Ce code passe de vrais ordres en mode `--live`. Testez d'abord en dry-run, puis avec une petite taille.
> Rien ici n'est un conseil en investissement.

---

## Sommaire

1. [Fonctionnement](#fonctionnement)
2. [Installation](#installation)
3. [Créer l'agent wallet (API wallet)](#créer-lagent-wallet-api-wallet)
4. [Collatéral sur le dex HIP-3 `xyz`](#collatéral-sur-le-dex-hip-3-xyz)
5. [Configuration (`.env`)](#configuration-env)
6. [Utilisation](#utilisation)
7. [Mise en place du cron](#mise-en-place-du-cron)
8. [Logs](#logs)
9. [Tests et vérification TradingView](#tests-et-vérification-tradingview)
10. [Limites connues](#limites-connues)

---

## Fonctionnement

```
rsi2_bot/
  config.py              paramètres de la stratégie + lecture du .env
  indicators.py          SMA et RSI, identiques à TradingView (ta.sma / ta.rsi)
  strategy.py            règles d'entrée / sortie (fonction pure, testée)
  market_data.py         bougies ^GSPC (yfinance) + calendrier des séances
  hyperliquid_client.py  lecture de l'état et envoi des ordres via le SDK
  main.py                orchestration d'une exécution (CLI)
scripts/
  run_bot.sh             wrapper pour cron
  tradingview_check.py   compare nos indicateurs aux valeurs TradingView du jour
tests/                   tests pytest (+ données figées ^GSPC et TradingView)
```

À chaque exécution :

1. Télécharge ~2 ans de bougies daily `^GSPC`, ignore la bougie du jour si la séance n'est pas close,
   calcule **SMA200, SMA5, RSI(2)** sur la dernière bougie clôturée.
2. Si cette bougie n'est pas celle du jour (férié, données en retard…), **aucun ordre n'est envoyé en `--live`**.
3. Lit l'**état réel sur Hyperliquid** (jamais un simple fichier local) :
   - position `xyz:SP500` via `clearinghouseState` du dex `xyz` ;
   - ordres ouverts sur `xyz:SP500` (s'il y en a, le bot ne fait rien en `--live`) ;
   - fills des 60 derniers jours → **date d'entrée** (dernier achat faisant passer la position de 0 à long)
     pour le time stop, et détection d'un trade déjà fait sur la séance.
4. Décide : `ENTER_LONG`, `EXIT_LONG`, `HOLD`, `STAY_FLAT` ou `ABORT` (situation anormale, ex. position short).
5. Exécute (en `--live` seulement) un ordre au marché (IOC avec slippage max), puis relit la position.

### Protection contre les doublons

- La position est **relue sur l'exchange** : si elle existe déjà, pas de nouvelle entrée.
- Une position ouverte sur la séance courante est conservée (pas de sortie le jour même).
- Si un fill a déjà eu lieu sur la séance courante et que l'on est flat, pas de ré-entrée
  (ex. : sortie par time stop puis script relancé).
- Un verrou fichier (`logs/rsi2_bot.lock`) empêche deux exécutions simultanées.

### Adressage d'un marché HIP-3 avec le SDK

D'après le SDK (`hyperliquid/info.py`, `hyperliquid/exchange.py`) et son exemple
`examples/basic_order_with_builder_deployed_dex.py` :

```python
exchange = Exchange(wallet, constants.MAINNET_API_URL,
                    account_address=ADRESSE_COMPTE_PRINCIPAL,
                    perp_dexs=["xyz"])          # charge la meta du dex HIP-3 "xyz"
exchange.market_open("xyz:SP500", True, taille, None, 0.01)   # nom "<dex>:<COIN>"
exchange.info.user_state(ADRESSE_COMPTE_PRINCIPAL, "xyz")     # état du clearinghouse "xyz"
```

Sans `perp_dexs=["xyz"]`, `"xyz:SP500"` est inconnu du SDK. Chaque dex HIP-3 a son propre clearinghouse :
les positions/ordres se lisent avec `dex="xyz"`. Le SDK calcule lui-même l'identifiant d'actif
(110000 + index ; `xyz:SP500` = 110052 au moment de l'écriture) ; le bot arrondit la taille (vers le bas) à
`szDecimals` (3 pour `xyz:SP500`).

---

## Installation

Prérequis : Python ≥ 3.10, Linux/macOS (cron), accès Internet.

```bash
git clone <ce dépôt> TradingStrat-HPL
cd TradingStrat-HPL
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Vérification rapide (sans aucune clé) :

```bash
./.venv/bin/python -m rsi2_bot          # dry-run : affiche les indicateurs et la décision
./.venv/bin/python -m pytest            # tests
```

---

## Créer l'agent wallet (API wallet)

Un **API wallet** (agent wallet) peut signer des ordres pour votre compte mais **ne peut ni retirer ni
transférer de fonds**. C'est la seule clé à mettre sur le serveur ; la clé de votre wallet principal ne doit
jamais s'y trouver.

1. Connectez votre wallet principal sur <https://app.hyperliquid.xyz/API>.
2. Donnez un nom à l'API wallet (ex. `rsi2-bot`) puis cliquez sur **Generate**.
3. **Copiez la clé privée affichée** (elle ne sera plus visible ensuite).
4. Choisissez la durée de validité, cliquez sur **Authorize API Wallet** et signez avec votre wallet principal.
5. Dans `.env` :
   - `HL_SECRET_KEY` = clé privée de l'**agent** ;
   - `HL_ACCOUNT_ADDRESS` = adresse publique de votre **compte principal** (pas celle de l'agent :
     les positions et les fonds sont sur le compte principal).

Dès qu'une clé est fournie, le bot vérifie via l'endpoint `userRole` que la clé est bien un agent approuvé pour
`HL_ACCOUNT_ADDRESS` et s'arrête sinon. **Les API wallets expirent** : pensez à en recréer un avant la date
d'expiration (une erreur explicite apparaît dans les logs sinon).

Recommandations :

- `.env` est dans `.gitignore` ; gardez-le en `chmod 600`.
- Utilisez un compte dédié au bot : il suppose être seul à trader `xyz:SP500` sur ce compte.
  Un ordre manuel sur ce marché fausserait la date d'entrée utilisée par le time stop.

---

## Collatéral sur le dex HIP-3 `xyz`

Le collatéral de `xyz:SP500` est l'**USDC**, mais chaque dex HIP-3 a sa propre marge, séparée du dex
Hyperliquid principal. Avec un levier 1x, il faut au moins `POSITION_SIZE_USD` + frais disponibles pour ce
dex. Selon la configuration de votre compte :

- transférez des USDC vers le dex `xyz` depuis l'interface (avec votre wallet principal ; l'agent ne peut pas
  transférer de fonds) ;
- ou activez l'abstraction de compte (unified account / DEX abstraction) qui permet d'utiliser le solde
  principal pour les marchés HIP-3 (voir la doc Hyperliquid et `examples/user_abstraction.py` du SDK).

Le bot affiche à chaque exécution la valeur du compte et le montant retirable **sur le dex `xyz`**. Un manque de
marge se traduit par une réponse d'erreur de l'exchange, écrite dans les logs.

Le levier est réglé à **1x** (`update_leverage`) juste avant chaque entrée, en marge **isolée** par défaut
(`MARGIN_MODE=isolated`) : le risque est limité à la marge de la position.

---

## Configuration (`.env`)

| Variable | Défaut | Description |
|---|---|---|
| `HL_SECRET_KEY` | — | clé privée de l'agent wallet (obligatoire en `--live`) |
| `HL_ACCOUNT_ADDRESS` | — | adresse du compte principal (obligatoire en `--live` ; en dry-run, sans elle la position est supposée nulle) |
| `HL_NETWORK` | `mainnet` | `mainnet` ou `testnet` |
| `POSITION_SIZE_USD` | `500` | notionnel de la position en USD (minimum 10) |
| `SLIPPAGE` | `0.01` | slippage max des ordres au marché (1 %) |
| `MARGIN_MODE` | `isolated` | `isolated` ou `cross` |
| `LOG_FILE` | `logs/rsi2_bot.log` | fichier de log |

Les paramètres de la stratégie (RSI 2 < 10, SMA200, SMA5, 10 jours, levier 1x) sont des constantes dans
`rsi2_bot/config.py`.

---

## Utilisation

```bash
# Dry-run (défaut) : calcule tout, lit la position réelle, affiche l'ordre qu'il enverrait. Aucun ordre.
./.venv/bin/python -m rsi2_bot
./.venv/bin/python -m rsi2_bot --dry-run

# Mode réel : seul --live envoie des ordres
./.venv/bin/python -m rsi2_bot --live

# Taille ponctuelle différente du .env
./.venv/bin/python -m rsi2_bot --live --size-usd 250
```

Options : `--size-usd`, `--env-file`, `--allow-stale` (en `--live`, trader même si la bougie du jour n'est pas
disponible ; déconseillé).

Code de sortie : `0` = exécution normale (y compris « rien à faire »), `1` = erreur ou situation anormale
(utilisable par un outil de supervision).

---

## Mise en place du cron

22h15 à Paris tombe toujours après la clôture US de 16h00 à New York, y compris pendant les semaines où les
changements d'heure européen et américain sont décalés (22h15 Paris = 16h15 ou 17h15 à New York). C'est vérifié
par un test.

`crontab -e` puis, **si votre cron gère `CRON_TZ`** (cronie : Fedora, RHEL, Arch…) :

```cron
CRON_TZ=Europe/Paris
15 22 * * 1-5 /home/moi/TradingStrat-HPL/scripts/run_bot.sh --live >> /home/moi/TradingStrat-HPL/logs/cron.log 2>&1
```

**Debian/Ubuntu** (le cron par défaut ignore `CRON_TZ`) : mettez le serveur à l'heure de Paris, puis la même
ligne sans `CRON_TZ` :

```bash
sudo timedatectl set-timezone Europe/Paris
sudo systemctl restart cron
```

(Sur un serveur resté en UTC, 22h15 Paris = 20h15 UTC l'été et 21h15 UTC l'hiver : il faudrait changer la ligne
deux fois par an.)

**Alternative systemd** (gère le fuseau directement) — `~/.config/systemd/user/rsi2-bot.service` :

```ini
[Service]
Type=oneshot
ExecStart=/home/moi/TradingStrat-HPL/scripts/run_bot.sh --live
```

et `~/.config/systemd/user/rsi2-bot.timer` :

```ini
[Timer]
OnCalendar=Mon..Fri 22:15 Europe/Paris
Persistent=false

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now rsi2-bot.timer
loginctl enable-linger $USER   # pour que le timer tourne sans session ouverte
```

Avant de passer en `--live`, laissez tourner quelques jours la ligne cron **sans `--live`** et lisez les logs.
Les jours fériés US, il n'y a pas de bougie du jour : le bot le détecte et ne fait rien.

---

## Logs

Tout est écrit dans `logs/rsi2_bot.log` (rotation 5 Mo × 10) et sur la sortie standard. Exemple (dry-run) :

```
2026-09-25 22:15:03+0200 INFO    rsi2_bot | Démarrage DRY-RUN | 2026-09-25T16:15:03-04:00 | signal ^GSPC -> exécution xyz:SP500 | taille 500.00 USD | levier 1x | marge isolated | réseau mainnet
2026-09-25 22:15:04+0200 INFO    rsi2_bot | Bougie ^GSPC du 2026-09-25 : clôture=7743.41 | SMA200=7205.20 | SMA5=7736.58 | RSI(2)=74.95
2026-09-25 22:15:05+0200 INFO    rsi2_bot | Compte 0x… (dex xyz) : valeur=1000.0 USDC, retirable=1000.0 USDC
2026-09-25 22:15:05+0200 INFO    rsi2_bot | Position xyz:SP500 : taille=0.0, prix d'entrée=None, détail=null
2026-09-25 22:15:05+0200 INFO    rsi2_bot | DÉCISION : STAY_FLAT — pas de signal : RSI(2) >= 10
2026-09-25 22:15:05+0200 INFO    rsi2_bot | RÉSUMÉ {"mode": "DRY-RUN", "bar_date": "2026-09-25", "fresh": true, "close": 7743.41, "sma200": 7205.2, "sma5": 7736.58, "rsi2": 74.95, "position": 0.0, "bars_held": null, "decision": "STAY_FLAT", ...}
```

Lors d'un ordre, le log contient en plus la ligne `ORDRE prévu` (taille, mid, notionnel, slippage, levier),
la réponse brute de `update_leverage`, la **réponse brute de l'exchange** et la position relue après l'ordre.
La ligne `RÉSUMÉ` est du JSON sur une ligne, facile à extraire (`grep RÉSUMÉ logs/rsi2_bot.log`).

---

## Tests et vérification TradingView

```bash
./.venv/bin/python -m pytest
```

- `tests/test_indicators.py` : SMA et RSI (exemple RSI(2) calculé à la main, conventions 0/100, contrôle croisé
  avec une implémentation `ewm` indépendante) et **comparaison avec TradingView**.
- `tests/test_strategy.py` : toutes les règles d'entrée/sortie.
- `tests/test_market_data.py` : séances, bougie non clôturée, 22h15 Paris toujours après la clôture US.
- `tests/test_bot_flow.py` : exécution complète avec un faux client Hyperliquid (dry-run sans ordre, doublons,
  time stop à partir des fills, levier réglé avant l'achat, etc.).

### Valeurs de référence TradingView

Relevées le 27/09/2026 sur TradingView (symbole `SP:SPX`, 1D) via son API scanner, figées dans
`tests/fixtures/tradingview_spx_2026-09-25.json`, et comparées aux calculs du bot sur les clôtures `^GSPC`
de yfinance (`tests/fixtures/gspc_daily_close.csv`) :

| Date | | Clôture | SMA5 | SMA200 | RSI(14) | RSI(7) | RSI(2) bot |
|---|---|---|---|---|---|---|---|
| 2026-09-25 | TradingView | 7743.41 | 7736.582 | 7205.2021 | 56.6860 | 61.0927 | — |
| | bot | 7743.41 | 7736.582 | 7205.2028 | 56.6860 | 61.0927 | 74.95 |
| 2026-09-24 | TradingView | 7704.13 | 7718.000 | 7200.7176 | 53.5413 | 54.8087 | — |
| | bot | 7704.13 | 7718.000 | 7200.7183 | 53.5412 | 54.8087 | 35.69 |
| 2026-09-23 | TradingView | 7706.03 | 7704.726 | 7196.5489 | 53.7164 | — | — |
| | bot | 7706.03 | 7704.726 | 7196.5496 | 53.7164 | 55.1782 | 37.09 |

- SMA5 : identique. SMA200 : écart < 0,001 point (une clôture ancienne diffère de quelques centimes entre
  yfinance et TradingView). RSI : écart < 0,0001.
- L'API de TradingView ne fournit pas RSI(2) ; RSI(14) et RSI(7) utilisent **exactement le même code**
  (RMA de Wilder, comme `ta.rsi`), seule la longueur change. Les valeurs RSI(2) sont figées en test de
  non-régression ; vous pouvez les contrôler sur un graphique TradingView `SP:SPX` 1D avec l'indicateur
  « Relative Strength Index », longueur 2.

Pour revérifier à tout moment sur les 3 dernières séances :

```bash
./.venv/bin/python scripts/tradingview_check.py
```

---

## Limites connues

- **yfinance** n'est pas une source officielle ; à 22h15 la bougie du jour peut, rarement, ne pas être encore
  finalisée. Si elle est absente, le bot ne trade pas ce jour-là.
- Le signal est pris sur la clôture de l'indice cash à 16h00 NY, l'exécution se fait ~15 min plus tard sur le
  perp : le prix d'exécution peut différer de la clôture (base, funding, liquidité hors séance).
- Frais de trading et funding du perp non modélisés dans la stratégie.
- Ordres au marché IOC : une exécution partielle est possible (signalée dans les logs) ; le reste n'est pas
  retenté automatiquement.
- Le time stop repose sur l'historique de fills du compte (60 jours, 10 000 fills les plus récents au maximum
  côté API) : d'où la recommandation d'un compte dédié. Une position sans fill d'ouverture retrouvé est
  considérée comme plus ancienne que le time stop et sera clôturée.
