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
MA_BUY = 20
MA_SELL = 50
CHECK_INTERVAL = 60
BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

# Etat interne
touched_ma20 = False

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
            return [float(c["c"]) for c in data]
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

def place_order(side, size_usd, price, reduce_only=False):
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        wallet = eth_account.Account.from_key(PRIVATE_KEY)
        exchange = Exchange(wallet, constants.MAINNET_API_URL, account_address=WALLET_ADDRESS)
        is_buy = side == "BUY"
        size = round(size_usd / price, 4)
        lp = round_price(price * (1.002 if is_buy else 0.998))
        result = exchange.order(SYMBOL, is_buy, size, lp, {"limit": {"tif": "Ioc"}}, reduce_only=reduce_only)
        print("[DEBUG] Ordre: " + str(result)[:200])
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
    global touched_ma20
    print("BOT MA50/MA20 - Price Action")
    print("BUY: cloture > MA50 | SELL: touche MA20 puis cloture < MA20")
    print("Symbole: " + SYMBOL + " | Taille: $" + str(TRADE_SIZE_USD))
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    print("Solde: $" + str(round(get_balance(), 2)))
    print("---")
    trades = 0
    touched_ma20 = False

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            closes = get_candles()
            if not closes or len(closes) < MA_BUY + 1:
                time.sleep(CHECK_INTERVAL)
                continue

            price = closes[-1]
            prev_close = closes[-2]
            positions = get_positions()

            ma20_val = ma(closes, MA_SELL)
            ma50_val = ma(closes, MA_BUY)

            if ma20_val is None or ma50_val is None:
                time.sleep(CHECK_INTERVAL)
                continue

            ma20 = float(ma20_val)
            ma50 = float(ma50_val)

            if positions:
                if check_risk(positions, price):
                    touched_ma20 = False
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Verifier si le prix a touche MA20
                if price >= ma20:
                    touched_ma20 = True

                # Vendre si MA20 touchee ET cloture en dessous
                sig = "HOLD"
                if touched_ma20 and price < ma20:
                    sig = "SELL"

                status = "MA20 TOUCHEE" if touched_ma20 else "ATTENTE MA20"
                print("[" + now + "] $" + str(price) + " | MA20: " + str(round(ma20, 2)) + " | MA50: " + str(round(ma50, 2)) + " | " + sig + " | EN POSITION | " + status)

                if sig == "SELL":
                    if close_pos(positions[0], price):
                        touched_ma20 = False
                        print("Trade ferme | Solde: $" + str(round(get_balance(), 2)))

            else:
                # Acheter si cloture au dessus MA50
                sig = "HOLD"
                if prev_close <= ma50 and price > ma50:
                    sig = "BUY"

                print("[" + now + "] $" + str(price) + " | MA20: " + str(round(ma20, 2)) + " | MA50: " + str(round(ma50, 2)) + " | " + sig + " | HORS POSITION")

                if sig == "BUY" and get_balance() >= TRADE_SIZE_USD:
                    if place_order("BUY", TRADE_SIZE_USD, price):
                        trades += 1
                        touched_ma20 = False
                        print("Trade #" + str(trades) + " LONG ouvert | Solde: $" + str(round(get_balance(), 2)))

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrete. Trades: " + str(trades))
            break
        except Exception as e:
            print("[ERREUR] " + str(e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
