“””
Bot de Trading RÉEL - Stratégie EMA Cross + RSI
Plateforme: Hyperliquid
Mode: PRODUCTION

⚠️ AVERTISSEMENT: Ce bot trade avec de l’argent réel.
Le trading comporte des risques de perte en capital.
“””

import time
import json
import requests
import pandas as pd
import eth_account
from eth_account.signers.local import LocalAccount
from datetime import datetime
import os

# ============================================================

# CONFIGURATION

# ============================================================

PRIVATE_KEY = os.environ.get(“HL_API_SECRET”, “”)
WALLET_ADDRESS = os.environ.get(“HL_WALLET”, “”)

SYMBOL = “SOL”
TRADE_SIZE_USD = 2
LEVERAGE = 1
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

CHECK_INTERVAL = 60 * 15  # 15 minutes

# ============================================================

# HYPERLIQUID API

# ============================================================

BASE_URL = “https://api.hyperliquid.xyz”

def get_account() -> LocalAccount:
return eth_account.Account.from_key(PRIVATE_KEY)

def get_market_prices(symbol: str, limit: int = 100) -> list:
try:
url = f”{BASE_URL}/info”
payload = {
“type”: “candleSnapshot”,
“req”: {
“coin”: symbol,
“interval”: “15m”,
“startTime”: int((time.time() - limit * 60 * 15) * 1000),
“endTime”: int(time.time() * 1000)
}
}
response = requests.post(url, json=payload, timeout=10)
data = response.json()
prices = [float(candle[“c”]) for candle in data]
return prices
except Exception as e:
print(f”[ERREUR] Prix: {e}”)
return []

def get_balance() -> float:
try:
url = f”{BASE_URL}/info”
payload = {
“type”: “clearinghouseState”,
“user”: WALLET_ADDRESS
}
response = requests.post(url, json=payload, timeout=10)
data = response.json()
return float(data.get(“marginSummary”, {}).get(“accountValue”, 0))
except Exception as e:
print(f”[ERREUR] Solde: {e}”)
return 0

def get_open_positions() -> list:
try:
url = f”{BASE_URL}/info”
payload = {
“type”: “clearinghouseState”,
“user”: WALLET_ADDRESS
}
response = requests.post(url, json=payload, timeout=10)
data = response.json()
positions = []
for pos in data.get(“assetPositions”, []):
p = pos.get(“position”, {})
if float(p.get(“szi”, 0)) != 0:
positions.append({
“symbol”: p.get(“coin”),
“size”: float(p.get(“szi”, 0)),
“entry_price”: float(p.get(“entryPx”, 0))
})
return positions
except Exception as e:
print(f”[ERREUR] Positions: {e}”)
return []

def get_asset_index(symbol: str) -> int:
“”“Récupère l’index de l’asset sur Hyperliquid”””
try:
url = f”{BASE_URL}/info”
payload = {“type”: “meta”}
response = requests.post(url, json=payload, timeout=10)
data = response.json()
for i, asset in enumerate(data.get(“universe”, [])):
if asset.get(“name”) == symbol:
return i
return -1
except Exception as e:
print(f”[ERREUR] Asset index: {e}”)
return -1

def place_order(symbol: str, side: str, size_usd: float, current_price: float) -> bool:
“”“Place un ordre réel sur Hyperliquid”””
try:
account = get_account()
nonce = int(time.time() * 1000)
asset_index = get_asset_index(symbol)

```
    if asset_index == -1:
        print(f"[ERREUR] Asset {symbol} introuvable")
        return False

    size = round(size_usd / current_price, 4)
    is_buy = side == "BUY"

    if is_buy:
        limit_price = round(current_price * 1.002, 2)
    else:
        limit_price = round(current_price * 0.998, 2)

    action = {
        "type": "order",
        "orders": [{
            "a": asset_index,
            "b": is_buy,
            "p": str(limit_price),
            "s": str(size),
            "r": False,
            "t": {"limit": {"tif": "Ioc"}}
        }],
        "grouping": "na"
    }

    action_hash = hash(json.dumps(action, sort_keys=True) + str(nonce))
    message = f"\x19Ethereum Signed Message:\n32{action_hash}"
    signed = account.sign_message(
        eth_account.messages.encode_defunct(text=str(action_hash))
    )

    payload = {
        "action": action,
        "nonce": nonce,
        "signature": {
            "r": hex(signed.r),
            "s": hex(signed.s),
            "v": signed.v
        },
        "vaultAddress": None
    }

    url = f"{BASE_URL}/exchange"
    response = requests.post(url, json=payload, timeout=10)
    result = response.json()

    if result.get("status") == "ok":
        print(f"[✅ ORDRE] {side} {size} {symbol} @ ${limit_price}")
        return True
    else:
        print(f"[❌ ORDRE] Échec: {result}")
        return False

except Exception as e:
    print(f"[ERREUR] Ordre: {e}")
    return False
```

def close_position(symbol: str, size: float, current_price: float) -> bool:
side = “SELL” if size > 0 else “BUY”
return place_order(symbol, side, abs(size) * current_price, current_price)

# ============================================================

# INDICATEURS TECHNIQUES

# ============================================================

def calculate_ema(prices: list, period: int):
if len(prices) < period:
return None
series = pd.Series(prices)
ema = series.ewm(span=period, adjust=False).mean()
return ema.iloc[-1]

def calculate_rsi(prices: list, period: int = 14):
if len(prices) < period + 1:
return None
series = pd.Series(prices)
delta = series.diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)
avg_gain = gain.rolling(window=period).mean()
avg_loss = loss.rolling(window=period).mean()
rs = avg_gain / avg_loss
rsi = 100 - (100 / (1 + rs))
return rsi.iloc[-1]

def get_signal(prices: list) -> str:
if len(prices) < EMA_SLOW + RSI_PERIOD:
return ‘HOLD’
ema_fast = calculate_ema(prices, EMA_FAST)
ema_slow = calculate_ema(prices, EMA_SLOW)
rsi = calculate_rsi(prices, RSI_PERIOD)
if ema_fast is None or ema_slow is None or rsi is None:
return ‘HOLD’
if ema_fast > ema_slow and rsi < RSI_OVERSOLD:
return ‘BUY’
elif ema_fast < ema_slow and rsi > RSI_OVERBOUGHT:
return ‘SELL’
return ‘HOLD’

# ============================================================

# GESTION DU RISQUE

# ============================================================

def check_stop_loss_take_profit(positions: list, current_price: float) -> bool:
for pos in positions:
if pos[“symbol”] != SYMBOL:
continue
entry = pos[“entry_price”]
size = pos[“size”]
pnl_pct = (current_price - entry) / entry

```
    if size > 0:
        if pnl_pct <= -STOP_LOSS_PCT:
            print(f"[🛑 STOP-LOSS] PnL: {pnl_pct*100:.2f}% → Fermeture")
            close_position(SYMBOL, size, current_price)
            return True
        elif pnl_pct >= TAKE_PROFIT_PCT:
            print(f"[🎯 TAKE-PROFIT] PnL: {pnl_pct*100:.2f}% → Fermeture")
            close_position(SYMBOL, size, current_price)
            return True
return False
```

# ============================================================

# BOUCLE PRINCIPALE

# ============================================================

def run_bot():
print(”=” * 50)
print(”  BOT DE TRADING RÉEL - EMA Cross + RSI”)
print(”  Plateforme: Hyperliquid | Mode: PRODUCTION ⚠️”)
print(”=” * 50)
print(f”  Paire: {SYMBOL} | Taille: ${TRADE_SIZE_USD}”)
print(f”  Stop-loss: {STOP_LOSS_PCT*100}% | Take-profit: {TAKE_PROFIT_PCT*100}%”)
print(”=” * 50)

```
balance = get_balance()
print(f"  Solde: ${balance:.2f} USDC")
print("=" * 50)
print()

last_signal = 'HOLD'
trade_count = 0

while True:
    try:
        now = datetime.now().strftime("%H:%M:%S")
        prices = get_market_prices(SYMBOL, limit=100)

        if not prices:
            print(f"[{now}] ⚠️ Impossible de récupérer les prix...")
            time.sleep(CHECK_INTERVAL)
            continue

        current_price = prices[-1]
        ema_fast = calculate_ema(prices, EMA_FAST)
        ema_slow = calculate_ema(prices, EMA_SLOW)
        rsi = calculate_rsi(prices, RSI_PERIOD)

        ema_fast_str = f"${ema_fast:,.2f}" if ema_fast else "N/A"
        ema_slow_str = f"${ema_slow:,.2f}" if ema_slow else "N/A"
        rsi_str = f"{rsi:.1f}" if rsi else "N/A"

        positions = get_open_positions()

        if positions:
            closed = check_stop_loss_take_profit(positions, current_price)
            if closed:
                last_signal = 'HOLD'
                time.sleep(CHECK_INTERVAL)
                continue

        signal = get_signal(prices)
        print(f"[{now}] Prix: ${current_price:,.2f} | EMA9: {ema_fast_str} | EMA21: {ema_slow_str} | RSI: {rsi_str} | Signal: {signal}")

        if signal != last_signal and signal != 'HOLD' and not positions:
            success = place_order(SYMBOL, signal, TRADE_SIZE_USD, current_price)
            if success:
                last_signal = signal
                trade_count += 1
                balance = get_balance()
                print(f"  → Trade #{trade_count} | Solde: ${balance:.2f}")

        time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n[INFO] Bot arrêté")
        print(f"[INFO] Total trades: {trade_count}")
        break
    except Exception as e:
        print(f"[ERREUR] {e}")
        time.sleep(CHECK_INTERVAL)
```

if **name** == “**main**”:
run_bot()
