import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import eth_account
from eth_account.messages import encode_defunct
import hashlib

PRIVATE_KEY = os.environ.get("HL_API_SECRET")
WALLET_ADDRESS = os.environ.get("HL_WALLET")
SYMBOL = "SOL"
TRADE_SIZE_USD = 40
STOP_LOSS_PCT = 0.02
MA_BUY = 50
MA_SELL = 20
CHECK_INTERVAL = 60
BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

def round_price(price):
    return round(round(price / TICK_SIZE) * TICK_SIZE, 2)

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
        if not data2:
            return 0
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

def get_candles():
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": SYMBOL,
                "interval": "15m",
                "startTime": int((time.time() - 150 * 60 * 15) * 1000),
                "endTime": int(time.time() * 1000)
            }
        }
        data = info_post(payload)
        if isinstance(data, list):
            closes = [float(c["c"]) for c in data]
            return closes
        return []
    except Exception as e:
        print("[ERREUR] Candles: " + str(e))
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

def sign_action(action, nonce):
    account = eth_account.Account.from_key(PRIVATE_KEY)
    action_str = json.dumps(action, separators=(",", ":"), sort_keys=True)
    msg_str = action_str + str(nonce) + "a"
    msg_hash = hashlib.sha256(msg_str.encode()).digest()
    msg = encode_defunct(primitive=msg_hash)
    signed = account.sign_message(msg)
    return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}

def place_order(side, size_usd, price, reduce_only=False):
    try:
        nonce = int(time.time() * 1000)
        idx = get_asset_index()
        if idx == -1:
            print("[ERREUR] Asset introuvable")
            return False
        is_buy = side == "BUY"
        size = round(size_usd / price, 4)
        lp = round_price(price * (1.002 if is_buy else 0.998))
        action = {
            "type": "order",
            "orders": [{
                "a": idx,
                "b": is_buy,
                "p": str(lp),
                "s": str(size),
                "r": reduce_only,
                "t": {"limit": {"tif": "Ioc"}}
            }],
            "grouping": "na"
        }
        signature = sign_action(action, nonce)
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
            "vaultAddress": WALLET_ADDRESS
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/exchange", headers=headers, data=json.dumps(payload), timeout=10)
        result = r.json()
        print("[DEBUG] Ordre: " + str(result)[:200])
        if isinstance(result, dict):
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
                print("[OK] " + side + " " + str(size) + " " + SYMBOL + " @ $" + str(lp))
                return True
            elif statuses and "error" in statuses[0]:
                print("[ECHEC] " + str(statuses[0]["error"]))
                return False
            else:
                print("[ECHEC] " + str(result))
                return False
        else:
            print("[ECHEC] Reponse inattendue: " + str(result))
            return False
    except Exception as e:
        print("[ERREUR] Ordre: " + str(e))
        return False

def close_pos(pos, price):
    size_usd = abs(pos["size"]) * price
    return place_order("SELL", size_usd, price, reduce_only=True)

def ma(closes, period):
    if len(closes) < period:
        return None
    return pd.Series(closes).rolling(period).mean().iloc[-1]

def get_signal(closes, positions):
    if len(closes) < MA_BUY + 1:
        return "HOLD", None, None
    last_close = closes[-1]
    prev_close = closes[-2]
    ma50 = ma(closes, MA_BUY)
    ma20 = ma(closes, MA_SELL)
    if ma50 is None or ma20 is None:
        return "HOLD", ma20, ma50
    in_position = len(positions) > 0
    if not in_position:
        if prev_close <= ma50 and last_close > ma50:
            return "BUY", ma20, ma50
    else:
        if last_close < ma20:
            return "SELL", ma20, ma50
    return "HOLD", ma20, ma50

def check_risk(positions, price):
    for pos in positions:
        if pos["symbol"] != SYMBOL:
            continue
        pnl = (price - pos["entry"]) / pos["entry"]
        if pos["size"] > 0 and pnl <= -STOP_LOSS_PCT:
            print("[STOP-LOSS] " + str(round(pnl * 100, 2)) + "%")
            close_pos(pos, price)
            return True
    return False

def run():
    print("BOT MA50/MA20 - Price Action")
    print("BUY: cloture au-dessus MA50 | SELL: cloture en-dessous MA20")
    print("Symbole: " + SYMBOL + " | Taille: $" + str(TRADE_SIZE_USD))
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    print("Solde: $" + str(round(get_balance(), 2)))
    print("---")
    trades = 0
    last_candle_time = None
    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            closes = get_candles()
            if not closes:
                time.sleep(CHECK_INTERVAL)
                continue
            price = closes[-1]
            positions = get_positions()
            if positions:
                if check_risk(positions, price):
                    time.sleep(CHECK_INTERVAL)
                    continue
            sig, ma20_val, ma50_val = get_signal(closes, positions)
            ma20_str = str(round(float(ma20_val), 2)) if ma20_val is not None else "N/A"
            ma50_str = str(round(float(ma50_val), 2)) if ma50_val is not None else "N/A"
            pos_str = "EN POSITION" if positions else "HORS POSITION"
            print("[" + now + "] $" + str(price) + " | MA20: " + ma20_str + " | MA50: " + ma50_str + " | " + sig + " | " + pos_str)
            if sig == "BUY" and not positions and get_balance() >= TRADE_SIZE_USD:
                if place_order("BUY", TRADE_SIZE_USD, price):
                    trades += 1
                    print("Trade #" + str(trades) + " LONG ouvert | Solde: $" + str(round(get_balance(), 2)))
            elif sig == "SELL" and positions:
                if close_pos(positions[0], price):
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
