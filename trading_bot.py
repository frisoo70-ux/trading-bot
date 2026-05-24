import time
import json
import hashlib
import requests
import pandas as pd
from eth_account import Account
from eth_account.messages import encode_defunct
from datetime import datetime
import os

PRIVATE_KEY = os.environ.get("HL_API_SECRET")
WALLET_ADDRESS = os.environ.get("HL_WALLET")
SYMBOL = "SOL"
TRADE_SIZE_USD = 10
STOP_LOSS_PCT = 0.02
EMA_FAST = 3
EMA_SLOW = 8
CHECK_INTERVAL = 60
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
        for b in data.get("balances", []):
            if b.get("coin") == "USDC":
                return float(b.get("total", 0))
        data2 = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})
        return float(data2.get("marginSummary", {}).get("accountValue", 0))
    except Exception as e:
        print("[ERREUR] Solde: " + str(e))
        return 0

def get_positions():
    try:
        data = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})
        if not data:
            return []
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
                "interval": "1m",
                "startTime": int((time.time() - 150 * 60 * 15) * 1000),
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
        hash_bytes = hashlib.sha256((action_str + str(nonce)).encode()).digest()
        msg = encode_defunct(primitive=hash_bytes)
        signed = account.sign_message(msg)
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": hex(signed.r),
                "s": hex(signed.s),
                "v": signed.v
            },
            "vaultAddress": WALLET_ADDRESS
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/exchange", headers=headers, data=json.dumps(payload), timeout=10)
        result = r.json()
        print("[DEBUG] Ordre: " + str(result)[:150])
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

def get_signal(prices):
    if len(prices) < EMA_SLOW + 2:
        return "HOLD", None, None
    ef_now = ema(prices, EMA_FAST)
    es_now = ema(prices, EMA_SLOW)
    ef_prev = ema(prices[:-1], EMA_FAST)
    es_prev = ema(prices[:-1], EMA_SLOW)
    if ef_now is None or es_now is None or ef_prev is None or es_prev is None:
        return "HOLD", ef_now, es_now
    golden = ef_prev <= es_prev and ef_now > es_now
    death = ef_prev >= es_prev and ef_now < es_now
    if golden:
        return "BUY", ef_now, es_now
    elif death:
        return "SELL", ef_now, es_now
    return "HOLD", ef_now, es_now

def check_risk(positions, price):
    for pos in positions:
        if pos["symbol"] != SYMBOL:
            continue
        pnl = (price - pos["entry"]) / pos["entry"]
        if pos["size"] > 0 and pnl <= -STOP_LOSS_PCT:
            print("[STOP-LOSS] " + str(round(pnl * 100, 2)) + "%")
            place_order("SELL", abs(pos["size"]) * price, price)
            return True
    return False

def run():
    print("BOT GOLDEN/DEATH CROSS - EMA 20/50")
    print("Symbole: " + SYMBOL + " | Taille: $" + str(TRADE_SIZE_USD))
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    print("Solde: $" + str(round(get_balance(), 2)))
    print("---")
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
                    time.sleep(CHECK_INTERVAL)
                    continue
            sig, ef, es = get_signal(prices)
            ef_str = str(round(float(ef), 2)) if ef is not None else "N/A"
            es_str = str(round(float(es), 2)) if es is not None else "N/A"
            cross = ""
            if sig == "BUY":
                cross = " *** GOLDEN CROSS ***"
            elif sig == "SELL":
                cross = " *** DEATH CROSS ***"
            print("[" + now + "] $" + str(price) + " | EMA20: " + ef_str + " | EMA50: " + es_str + " | " + sig + cross)
            if sig == "BUY" and not positions and get_balance() >= TRADE_SIZE_USD:
                if place_order("BUY", TRADE_SIZE_USD, price):
                    trades += 1
                    print("Trade #" + str(trades) + " LONG ouvert | Solde: $" + str(round(get_balance(), 2)))
            elif sig == "SELL" and positions:
                pos = positions[0]
                if place_order("SELL", abs(pos["size"]) * price, price):
                    print("Trade ferme | Solde: $" + str(round(get_balance(), 2)))
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("Bot arrete. Trades: " + str(trades))
            break
        except Exception as e:
            print("[ERREUR] " + str(e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
