import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import eth_account

PRIVATE_KEY = os.environ.get("HL_API_SECRET")
WALLET_ADDRESS = os.environ.get("HL_WALLET")
SYMBOL = "SOL"
TRADE_SIZE_USD = 30
STOP_LOSS_PCT = 0.03
CHECK_INTERVAL = 60
BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

trade_mode = None
touched_target = False
last_candle_time = None

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
        if isinstance(data, list) and len(data) > 0:
            closes = [float(c["c"]) for c in data]
            last_time = int(data[-1]["t"])
            return closes, last_time
        return [], None
    except Exception as e:
        print("[ERREUR] Candles: " + str(e))
        return [], None

def place_order(side, size_usd, price, reduce_only=False):
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        wallet = eth_account.Account.from_key(PRIVATE_KEY)
        exchange = Exchange(wallet, constants.MAINNET_API_URL, account_address=WALLET_ADDRESS)
        is_buy = side == "BUY"
        size = round(size_usd / price, 2)
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

def ema(closes, period):
    if len(closes) < period:
        return None
    return float(pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1])

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
    global trade_mode, touched_target, last_candle_time
    print("BOT PRICE ACTION - SOL 15min - CLOTURES UNIQUEMENT")
    print("Mode 1 (MA20>MA50): BUY cloture>MA50 | SELL touche MA20 puis cloture<MA20")
    print("Mode 2 (MA20<MA50): BUY cloture>MA20 | SELL touche MA50 puis cloture<MA50")
    print("Symbole: " + SYMBOL + " | Taille: $" + str(TRADE_SIZE_USD))
    print("Wallet: " + WALLET_ADDRESS[:8] + "...")
    print("Solde: $" + str(round(get_balance(), 2)))
    print("---")
    trades = 0
    trade_mode = None
    touched_target = False
    last_candle_time = None

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            closes, candle_time = get_candles()

            if not closes or len(closes) < 52 or candle_time is None:
                time.sleep(CHECK_INTERVAL)
                continue

            price = closes[-1]
            prev_close = closes[-2]
            positions = get_positions()

            ma20 = ema(closes, 20)
            ma50 = ema(closes, 50)

            if ma20 is None or ma50 is None:
                time.sleep(CHECK_INTERVAL)
                continue

            mode1 = ma20 > ma50
            mode2 = ma20 < ma50
            tendance = "HAUSSIERE (M1)" if mode1 else "BAISSIERE (M2)"
            new_candle = candle_time != last_candle_time

            if positions:
                if check_risk(positions, price):
                    trade_mode = None
                    touched_target = False
                    time.sleep(CHECK_INTERVAL)
                    continue

                sig = "HOLD"
                status = ""

                if trade_mode == "mode1":
                    if price >= ma20:
                        touched_target = True
                    if touched_target and new_candle and price < ma20:
                        sig = "SELL"
                    status = "MA20 TOUCHEE" if touched_target else "ATTENTE MA20"

                elif trade_mode == "mode2":
                    if price >= ma50:
                        touched_target = True
                    if touched_target and new_candle and price < ma50:
                        sig = "SELL"
                    status = "MA50 TOUCHEE" if touched_target else "ATTENTE MA50"

                print("[" + now + "] $" + str(price) + " | EMA20: " + str(round(ma20, 2)) + " | EMA50: " + str(round(ma50, 2)) + " | " + sig + " | EN POSITION | " + status)

                if sig == "SELL":
                    if close_pos(positions[0], price):
                        last_candle_time = candle_time
                        trade_mode = None
                        touched_target = False
                        print("Trade ferme | Solde: $" + str(round(get_balance(), 2)))

            else:
                trade_mode = None
                touched_target = False
                sig = "HOLD"
                mode_label = ""

                if new_candle:
                    if mode1 and prev_close <= ma50 and price > ma50:
                        sig = "BUY"
                        trade_mode = "mode1"
                        mode_label = "MODE 1 haussier"
                    elif mode2 and prev_close <= ma20 and price > ma20:
                        sig = "BUY"
                        trade_mode = "mode2"
                        mode_label = "MODE 2 baissier"

                candle_str = "NOUVELLE BOUGIE" if new_candle else "BOUGIE EN COURS"
                print("[" + now + "] $" + str(price) + " | EMA20: " + str(round(ma20, 2)) + " | EMA50: " + str(round(ma50, 2)) + " | " + sig + " | HORS POSITION | " + tendance + " | " + candle_str)

                if sig == "BUY" and get_balance() >= TRADE_SIZE_USD:
                    if place_order("BUY", TRADE_SIZE_USD, price):
                        last_candle_time = candle_time
                        trades += 1
                        print("Trade #" + str(trades) + " | " + mode_label + " | Solde: $" + str(round(get_balance(), 2)))

            last_candle_time = candle_time
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrete. Trades: " + str(trades))
            break
        except Exception as e:
            print("[ERREUR] " + str(e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
