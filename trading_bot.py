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
TRADE_SIZE_USD = 40
STOP_LOSS_PCT = 0.03
CHECK_INTERVAL = 60

BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

trade_mode = None
touched_target = False

META = None
SIZE_DECIMALS = 4


def round_price(price):
    return round(round(price / TICK_SIZE) * TICK_SIZE, 2)
    
    
pnl_pct = ((price - positions[0]["entry"]) / positions[0]["entry"]) * 100

print(
    f"[{now}] ${price} | "
    f"MA20:{round(ma20,2)} | "
    f"MA50:{round(ma50,2)} | "
    f"PnL:{round(pnl_pct,2)}% | "
    f"{sig}"
)

def info_post(payload):
    try:
        headers = {"Content-Type": "application/json"}
        r = requests.post(
            BASE_URL + "/info",
            headers=headers,
            data=json.dumps(payload),
            timeout=10
        )
        return r.json()
    except Exception as e:
        print("[ERREUR] API:", e)
        return {}


def get_meta():
    try:
        data = info_post({"type": "meta"})
        for asset in data.get("universe", []):
            if asset.get("name") == SYMBOL:
                return asset
    except Exception as e:
        print("[ERREUR] Meta:", e)
    return None


def get_balance():
    try:
        data = info_post({"type": "spotClearinghouseState", "user": WALLET_ADDRESS})

        for b in data.get("balances", []):
            if b.get("coin") == "USDC":
                return float(b.get("total", 0))

        data2 = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})

        if not data2:
            return 0

        return float(
            data2.get("marginSummary", {}).get("accountValue", 0)
        )

    except Exception as e:
        print("[ERREUR] Solde:", e)
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
        print("[ERREUR] Positions:", e)
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
        print("[ERREUR] Candles:", e)
        return []


def place_order(side, size_usd, price, reduce_only=False):
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        wallet = eth_account.Account.from_key(PRIVATE_KEY)

        exchange = Exchange(
            wallet,
            constants.MAINNET_API_URL,
            account_address=WALLET_ADDRESS
        )

        is_buy = side == "BUY"

        size = round(size_usd / price, SIZE_DECIMALS)

        if size <= 0:
            print("[ECHEC] Taille invalide:", size)
            return False

        lp = round_price(
            price * (1.002 if is_buy else 0.998)
        )

        print("========== ORDER ==========")
        print("SYMBOL =", SYMBOL)
        print("BUY =", is_buy)
        print("SIZE_USD =", size_usd)
        print("SIZE =", size)
        print("SIZE_DECIMALS =", SIZE_DECIMALS)
        print("PRICE =", price)
        print("LIMIT_PRICE =", lp)
        print("REDUCE_ONLY =", reduce_only)
        print("===========================")

        result = exchange.order(
            SYMBOL,
            is_buy,
            size,
            lp,
            {"limit": {"tif": "Ioc"}},
            reduce_only=reduce_only
        )

        print("[DEBUG] Ordre =", result)

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])

        if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
            print(f"[OK] {side} {size} {SYMBOL} @ ${lp}")
            return True

        if statuses and "error" in statuses[0]:
            print("[ECHEC]", statuses[0]["error"])
            return False

        print("[ECHEC]", result)
        return False

    except Exception as e:
        print("[ERREUR] Ordre:", e)
        return False


def close_pos(pos, price):
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        wallet = eth_account.Account.from_key(PRIVATE_KEY)

        exchange = Exchange(
            wallet,
            constants.MAINNET_API_URL,
            account_address=WALLET_ADDRESS
        )

        size = abs(pos["size"])
        lp = round_price(price * 0.998)

        print("========== CLOSE ORDER ==========")
        print("SIZE =", size)
        print("LIMIT_PRICE =", lp)
        print("=================================")

        result = exchange.order(
            SYMBOL,
            False,
            size,
            lp,
            {"limit": {"tif": "Ioc"}},
            reduce_only=True
        )

        print("[DEBUG CLOSE] =", result)

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])

        if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
            return True

        if statuses and "error" in statuses[0]:
            print("[ECHEC CLOSE]", statuses[0]["error"])

        return False

    except Exception as e:
        print("[ERREUR CLOSE]", e)
        return False


def sma(closes, period):
    if len(closes) < period:
        return None

    return float(pd.Series(closes).rolling(period).mean().iloc[-1])


def check_risk(positions, price):
    for pos in positions:
        if pos["symbol"] != SYMBOL:
            continue

        pnl = (price - pos["entry"]) / pos["entry"]

        if pos["size"] > 0 and pnl <= -STOP_LOSS_PCT:
            print("[STOP-LOSS]", round(pnl * 100, 2), "%")
            close_pos(pos, price)
            return True

    return False


def run():
    global trade_mode, touched_target
    global META, SIZE_DECIMALS

    META = get_meta()

    if META:
        SIZE_DECIMALS = META.get("szDecimals", 4)

    print("[INFO] META =", META)
    print("[INFO] SIZE_DECIMALS =", SIZE_DECIMALS)

    print("BOT PRICE ACTION - SOL 15min")
    print("Symbole:", SYMBOL)
    print("Taille: $", TRADE_SIZE_USD)
    print("Wallet:", WALLET_ADDRESS[:8] + "...")
    print("Solde: $", round(get_balance(), 2))
    print("---")

    trades = 0
    trade_mode = None
    touched_target = False

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")

            closes = get_candles()

            if not closes or len(closes) < 52:
                time.sleep(CHECK_INTERVAL)
                continue

            price = closes[-1]
            prev_close = closes[-2]

            positions = get_positions()

            ma20 = sma(closes, 20)
            ma50 = sma(closes, 50)

            if ma20 is None or ma50 is None:
                time.sleep(CHECK_INTERVAL)
                continue

            mode1 = ma20 > ma50
            mode2 = ma20 < ma50

            if positions:

                if check_risk(positions, price):
                    trade_mode = None
                    touched_target = False
                    time.sleep(CHECK_INTERVAL)
                    continue

                sig = "HOLD"

                if trade_mode == "mode1":
                    if price >= ma20:
                        touched_target = True

                    if touched_target and price < ma20:
                        sig = "SELL"

                elif trade_mode == "mode2":
                    if price < ma20:
                        sig = "SELL"

                print(f"[{now}] ${price} | MA20:{round(ma20,2)} | MA50:{round(ma50,2)} | {sig} | EN POSITION")

                if sig == "SELL":
                    if close_pos(positions[0], price):
                        trade_mode = None
                        touched_target = False

            else:
                sig = "HOLD"
                mode_label = ""

                if mode1 and prev_close <= ma50 and price > ma50:
                    sig = "BUY"
                    trade_mode = "mode1"
                    mode_label = "MODE1"

                elif mode2 and prev_close <= ma50 and price > ma50:
                    sig = "BUY"
                    trade_mode = "mode2"
                    mode_label = "MODE2"

                print(f"[{now}] ${price} | MA20:{round(ma20,2)} | MA50:{round(ma50,2)} | {sig}")

                balance = get_balance()

                if balance < TRADE_SIZE_USD:
                    print("[INFO] Solde insuffisant:", balance)

                elif sig == "BUY":
                    if place_order("BUY", TRADE_SIZE_USD, price):
                        trades += 1
                        print(f"Trade #{trades} {mode_label}")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrÃªtÃ©")
            break

        except Exception as e:
            print("[ERREUR]", e)
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
