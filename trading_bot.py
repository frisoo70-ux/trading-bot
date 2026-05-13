import time
import json
import requests
import pandas as pd
from eth_account import Account
from eth_account.messages import encode_defunct
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

def info_post(payload):
    try:
        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/info", headers=headers, data=json.dumps(payload), timeout=10)
        return r.json()
    except Exception as e:
        print("[ERREUR] API: " + str(e))
        return {}

def get_balance():
    try:
        data = info_post({"type": "spotClearinghouseState", "user": WALLET_ADDRESS})
        print("[DEBUG] SpotState: " + str(data)[:100])
        balances = data.get("balances", [])
        for b in balances:
            if b.get("coin") == "USDC":
                return float(b.get("total", 0))
        data2 = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})
        val = data2.get("marginSummary", {}).get("accountValue", "0")
        return float(val)
    except Exception as e:
        print("[ERREUR] Solde: " + str(e))
        return 0

def get_positions():
    try:
        data = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})
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
        data = info_post(payload)
        if isinstance(data, list):
            return [float(c["c"]) for c in data]
        return []
    except Exception as e:
        print("[ERREUR] Prix: " + str(e))
        return []

def get_asset_index():
    try:
        data = info_post({"type": "meta"})
        for i, a in enumerate(data.get("universe", [])):
            if a.get("name") == SYMBOL:
                return i
        return -1
    except:
        return -1

def place_order(side, size_usd, price):
    try:
        account = Account.from_key(PRIVATE_KEY)
        nonce = int(time.time() * 1000)
        idx = get_asset_index()
        if idx == -1:
            print("[ERREUR] Asset introuvable")
            return False
        size = round(size_usd / price, 4)
        is_buy = side == "BUY"
        lp = round(price * 1.002, 2) if is_buy else round(price * 0.998, 2)

        action = {
            "type": "order",
            "orders": [{
                "a": idx,
                "b": is_buy,
                "p": str(lp),
                "s": str(size),
                "r": False,
                "t": {"limit": {"tif": "Ioc"}}
            }],
            "grouping": "na"
        }

        action_str = json.dumps(action, separators=(",", ":"), sort_keys=True)
        msg = encode_defunct(text=action_str + str(nonce))
        signed = account.sign_message(msg)

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

        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/exchange", headers=headers, data=json.dumps(payload), timeout=10)
        result = r.json()
        print("[DEBUG] Ordre result: " + str(result)[:150])

        if result.get("status") == "ok":
            print("[OK] " + side + " " + str(size) + " " + SYMBOL + " @ $" + str(lp))
            return True
        else:
            print("[ECHEC] " + str(result))
            return False
    except Exception as e:
        print("[ERREUR] Ordre: " + str(e))
        return False

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

def check_risk(positions, price):
    for pos in positions:
        if pos["symbol"] != SYMBOL:
            continue
        pnl = (price - pos["entry"]) / pos["entry"]
        if pos["size"] > 0:
            if pnl <= -STOP_LOSS_PCT:
                print("[STOP-LOSS] " + str(round(pnl * 100, 2)) + "%")
                place_order("SELL", abs(pos["size"]) * price, price)
                return True
            if pnl >= TAKE_PROFIT_PCT:
                print("[TAKE-PROFIT] " + str(round(pnl * 100, 2)) + "%")
                place_order("SELL", abs(pos["size"]) * price, price)
                return True
    return False

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
            positions = get_positions()
            if positions:
                if check_risk(positions, price):
                    last = "HOLD"
                    time.sleep(CHECK_INTERVAL)
                    continue
            sig = signal(prices)
            ef = ema(prices, EMA_FAST)
            r = rsi(prices, RSI_PERIOD)
            ef_str = str(round(ef, 2)) if ef else "N/A"
            r_str = str(round(r, 1)) if r else "N/A"
            print("[" + now + "] $" + str(price) + " | EMA9: " + ef_str + " | RSI: " + r_str + " | " + sig)
            if sig != last and sig != "HOLD" and not positions:
                if place_order(sig, TRADE_SIZE_USD, price):
                    last = sig
                    trades += 1
                    balance = get_balance()
                    print("Trade #" + str(trades) + " | Solde: $" + str(round(balance, 2)))
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("Bot arrete.")
            break
        except Exception as e:
            print("[ERREUR] " + str(e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
