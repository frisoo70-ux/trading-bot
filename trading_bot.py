import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
import eth_account

PRIVATE_KEY = os.environ.get("HL_API_SECRET", "")
WALLET_ADDRESS = os.environ.get("HL_WALLET", "")
SYMBOL = "SOL"
TRADE_SIZE_USD = 40
STOP_LOSS_PCT = 0.02
EMA_FAST = 20
EMA_SLOW = 50
CHECK_INTERVAL = 60 * 15
BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

def get_exchange():
    wallet = eth_account.Account.from_key(PRIVATE_KEY)
    return Exchange(wallet, constants.MAINNET_API_URL, account_address=WALLET_ADDRESS)

def round_price(price):
    return round(round(price / TICK_SIZE) * TICK_SIZE, 5)

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

def get_prices():
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": SYMBOL,
                "interval": "15m",
                "startTime": int((time.time() - 150 * 60) * 1000),
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

def place_order(exchange, side, size_usd, price):
    try:
        is_buy = side == "BUY"
        size = max(round(size_usd / price, 0), 100)
        lp = round_price(price * (1.002 if is_buy else 0.998))
        result = exchange.order(SYMBOL, is_buy, size, lp, {"limit": {"tif": "Ioc"}}, reduce_only=False)
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

def close_position(exchange, pos, price):
    try:
        size = abs(pos["size"])
        lp = round_price(price * 0.998)
        result = exchange.order(SYMBOL, False, size, lp, {"limit": {"tif": "Ioc"}}, reduce_only=True)
        print("[DEBUG] Close: " + str(result)[:200])
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
            print("[OK] CLOSE " + str(size) + " " + SYMBOL)
            return True
        else:
            print("[ECHEC] Close: " + str(result))
            return False
    except Exception as e:
        print("[ERREUR] Close: " + str(e))
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
    golden = ef_prev < es_prev and ef_now > es_now
    death = ef_prev > es_prev and ef_now < es_now
    if golden:
        return "BUY", ef_now, es_now
    elif death:
        return "SELL", ef_now, es_now
    return "HOLD", ef_now, es_now

def check_risk(exchange, positions, price):
    for pos in positions:
        if pos["symbol"] != SYMBOL:
            continue
        pnl = (price - pos["entry"]) / pos["entry"]
        if pos["size"] > 0 and pnl <= -STOP_LOSS_PCT:
            print("[STOP-LOSS] " + str(round(pnl * 100, 2)) + "%")
            close_position(exchange, pos, price)
            return True
    return False

def run():
    print("BOT GOLDEN/DEATH CROSS - EMA " + str(EMA_FAST) + "/" + str(EMA_SLOW))
    print("Symbole: " + SYMBOL + " | Taille: $" + str(TRADE_SIZE_USD))
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    exchange = get_exchange()
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
                if check_risk(exchange, positions, price):
                    time.sleep(CHECK_INTERVAL)
                    continue
            sig, ef, es = get_signal(prices)
            ef_str = str(round(float(ef), 5)) if ef is not None else "N/A"
            es_str = str(round(float(es), 5)) if es is not None else "N/A"
            cross = ""
            if sig == "BUY":
                cross = " *** GOLDEN CROSS ***"
            elif sig == "SELL":
                cross = " *** DEATH CROSS ***"
            print("[" + now + "] $" + str(price) + " | EMA" + str(EMA_FAST) + ": " + ef_str + " | EMA" + str(EMA_SLOW) + ": " + es_str + " | " + sig + cross)
            if sig == "BUY" and not positions and get_balance() >= TRADE_SIZE_USD:
                if place_order(exchange, "BUY", TRADE_SIZE_USD, price):
                    trades += 1
                    print("Trade #" + str(trades) + " LONG ouvert | Solde: $" + str(round(get_balance(), 2)))
            elif sig == "SELL" and positions:
                if close_position(exchange, positions[0], price):
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
