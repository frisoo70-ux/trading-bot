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
        print("[ERREUR API]", e)
        return {}


def get_candles():
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": SYMBOL,
                "interval": "15m",
                "startTime": int((time.time() - 200 * 60 * 15) * 1000),
                "endTime": int(time.time() * 1000)
            }
        }

        data = info_post(payload)

        if isinstance(data, list) and len(data) > 0:
            closes = [float(c["c"]) for c in data]
            times = [int(c["t"]) for c in data]
            return closes, times

        return [], []

    except Exception as e:
        print("[ERREUR CANDLES]", e)
        return [], []


def ema(closes, period):
    if len(closes) < period:
        return None
    return float(pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1])


def place_order(side, size_usd, price):
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        wallet = eth_account.Account.from_key(PRIVATE_KEY)
        exchange = Exchange(wallet, constants.MAINNET_API_URL, account_address=WALLET_ADDRESS)

        is_buy = side == "BUY"
        size = round(size_usd / price, 4)

        lp = round_price(price * (1.002 if is_buy else 0.998))

        result = exchange.order(
            SYMBOL,
            is_buy,
            size,
            lp,
            {"limit": {"tif": "Ioc"}},
            reduce_only=False
        )

        print("[ORDER]", result)
        return result.get("status") == "ok"

    except Exception as e:
        print("[ERREUR ORDER]", e)
        return False


def run():
    global trade_mode, touched_target, last_candle_time

    print("BOT EMA CROSS CLEAN VERSION")
    print("SYMBOL:", SYMBOL)

    while True:
        try:
            closes, times = get_candles()

            if len(closes) < 60:
                time.sleep(CHECK_INTERVAL)
                continue

            # 🔥 on prend UNIQUEMENT les bougies clôturées
            current_time = times[-2]

            # éviter double traitement
            if current_time == last_candle_time:
                time.sleep(CHECK_INTERVAL)
                continue

            last_candle_time = current_time

            # bougies clôturées uniquement
            prev_close = closes[-3]
            current_close = closes[-2]

            # EMA sur données propres
            ma20 = ema(closes[:-1], 20)
            ma50 = ema(closes[:-1], 50)

            if ma20 is None or ma50 is None:
                continue

            mode1 = ma20 > ma50
            mode2 = ma20 < ma50

            print(f"\n[CANDLE CLOSE] {datetime.now()}")
            print(f"Prev={prev_close:.2f} Curr={current_close:.2f}")
            print(f"EMA20={ma20:.2f} EMA50={ma50:.2f}")

            signal = None

            # MODE 1
            if mode1:
                if prev_close <= ma50 and current_close > ma50:
                    signal = "BUY"
                    trade_mode = "mode1"

            # MODE 2
            elif mode2:
                if prev_close <= ma20 and current_close > ma20:
                    signal = "BUY"
                    trade_mode = "mode2"

            if signal == "BUY":
                print("🚀 BUY SIGNAL")

                # price execution = dernière clôture réelle
                price = current_close

                place_order("BUY", TRADE_SIZE_USD, price)

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("STOP BOT")
            break

        except Exception as e:
            print("[ERROR]", e)
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
