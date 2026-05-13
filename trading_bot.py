import time
import json
import requests
import pandas as pd
from datetime import datetime
import os

PRIVATE_KEY = os.environ.get("HL_API_SECRET", "")
WALLET_ADDRESS = os.environ.get("HL_WALLET", "")
SYMBOL = "SOL"
TRADE_SIZE_USD = 2
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
CHECK_INTERVAL = 60 * 15
BASE_URL = "https://api.hyperliquid.xyz"

def api_post(payload):
    try:
        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/info", headers=headers, data=json.dumps(payload), timeout=10)
        print("[DEBUG] Status: " + str(r.status_code) + " | Body: " + r.text[:100])
        return r.json()
    except Exception as e:
        print("[ERREUR] API: " + str(e))
        return {}

def get_prices():
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": SYMBOL,
                "interval": "15m",
                "startTime": int((time.time() - 100 * 60 * 15) * 1000),
                "endTime": int(time.time() * 1000)
            }
        }
        data = api_post(payload)
        if isinstance(data, list):
            return [float(c["c"]) for c in data]
        return []
    except Exception as e:
        print("[ERREUR] Prix: " + str(e))
        return []

def get_balance():
    try:
        payload = {
            "type": "clearinghouseState",
            "user": WALLET_ADDRESS
        }
        data = api_post(payload)
        return float(data.get("marginSummary", {}).get("accountValue", 0))
    except Exception as e:
        print("[ERREUR] Solde: " + str(e))
        return 0

def get_positions():
    try:
        payload = {
            "type": "clearinghouseState",
            "user": WALLET_ADDRESS
        }
        data = api_post(payload)
        positions = []
        for pos in data.get("assetPositions", []):
            p = pos.get("position", {})
            if float(p.get("szi", 0)) != 0:
                positions.append({
                    "symbol": p.get("coin"),
                    "size": float(p.get("szi", 0)),
                    "entry": float(p.get("entryPx", 0))
                })
        return positions
    except Exception as e:
        print("[ERREUR] Positions: " + str(e))
        return []

def ema(prices, period):
    if len(prices) < period:
        return None
    return pd.Series(prices).ewm(span=period, adjust=False).mean().iloc[-1]

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    s = pd.Series(prices)
    d = s.diff()
    g = d.where(d > 0, 0).rolling(period).mean()
    l = -d.where(d < 0, 0).rolling(period).mean()
    return (100 - 100 / (1 + g / l)).iloc[-1]

def signal(prices):
    if len(prices) < EMA_SLOW + RSI_PERIOD:
        return "HOLD"
    ef = ema(prices, EMA_FAST)
    es = ema(prices, EMA_SLOW)
    r = rsi(prices, RSI_PERIOD)
    if ef and es and r:
        if ef > es and r < RSI_OVERSOLD:
            return "BUY"
        if ef < es and r > RSI_OVERBOUGHT:
            return "SELL"
    return "HOLD"

def run():
    print("BOT REEL - " + SYMBOL + " | $" + str(TRADE_SIZE_USD) + " par trade")
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    balance = get_balance()
    print("Solde: $" + str(round(balance, 2)))
    last = "HOLD"
    trades = 0
    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            prices = get_prices()
            if not prices:
                time.sleep(CHECK_INTERVAL)
                continue
            price = prices[-1]
            sig = signal(prices)
            ef = ema(prices, EMA_FAST)
            r = rsi(prices, RSI_PERIOD)
            ef_str = str(round(ef, 2)) if ef else "N/A"
            r_str = str(round(r, 1)) if r else "N/A"
            print("[" + now + "] $" + str(price) + " | EMA9: " + ef_str + " | RSI: " + r_str + " | " + sig)
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("Bot arrete.")
            break
        except Exception as e:
            print("[ERREUR] " + str(e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
