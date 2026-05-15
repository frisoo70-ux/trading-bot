import time
import pandas as pd
from datetime import datetime
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
import os

# =========================
# CONFIG
# =========================

PRIVATE_KEY = os.environ.get("HL_API_SECRET", "")

SYMBOL = "SOL"
TRADE_SIZE_USD = 10

STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

EMA_FAST = 9
EMA_SLOW = 21
EMA_DIFF_MIN = 0.15

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

CHECK_INTERVAL = 60
TRADE_COOLDOWN = 300  # 5 minutes

# =========================
# INIT
# =========================

account = Account.from_key(PRIVATE_KEY)

info = Info(constants.MAINNET_API_URL)
exchange = Exchange(account, constants.MAINNET_API_URL)

wallet_address = account.address

# =========================
# HELPERS
# =========================

def get_balance():
    try:
        state = info.user_state(wallet_address)
        return float(state["marginSummary"]["accountValue"])
    except Exception as e:
        print("[ERREUR] Balance:", e)
        return 0


def get_positions():
    try:
        state = info.user_state(wallet_address)

        positions = []

        for p in state.get("assetPositions", []):
            pos = p["position"]

            size = float(pos["szi"])

            if size != 0:
                positions.append({
                    "symbol": pos["coin"],
                    "size": size,
                    "entry": float(pos["entryPx"])
                })

        return positions

    except Exception as e:
        print("[ERREUR] Positions:", e)
        return []


def get_prices():
    try:
        candles = info.candles_snapshot(
            SYMBOL,
            "15m",
            int((time.time() - 100 * 60 * 15) * 1000),
            int(time.time() * 1000)
        )

        return [float(c["c"]) for c in candles]

    except Exception as e:
        print("[ERREUR] Prix:", e)
        return []


def ema(prices, period):
    if len(prices) < period:
        return None

    return pd.Series(prices).ewm(
        span=period,
        adjust=False
    ).mean().iloc[-1]


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None

    s = pd.Series(prices)

    d = s.diff()

    gains = d.where(d > 0, 0).rolling(period).mean()
    losses = -d.where(d < 0, 0).rolling(period).mean()

    rs = gains / losses

    return (100 - (100 / (1 + rs))).iloc[-1]


# =========================
# SIGNAL
# =========================

def signal(prices):

    if len(prices) < EMA_SLOW + RSI_PERIOD:
        return "HOLD"

    ef = ema(prices, EMA_FAST)
    es = ema(prices, EMA_SLOW)
    r = rsi(prices, RSI_PERIOD)

    if ef is None or es is None or r is None:
        return "HOLD"

    # Anti bruit
    if abs(ef - es) < EMA_DIFF_MIN:
        return "HOLD"

    # BUY
    if ef > es and r < RSI_OVERSOLD:
        return "BUY"

    # SELL
    if ef < es and r > RSI_OVERBOUGHT:
        return "SELL"

    return "HOLD"


# =========================
# ORDER
# =========================

def place_order(side, size_usd, price):

    try:

        size = round(size_usd / price, 4)

        is_buy = side == "BUY"

        limit_price = (
            round(price * 1.002, 2)
            if is_buy
            else round(price * 0.998, 2)
        )

        result = exchange.order(
            SYMBOL,
            is_buy,
            size,
            limit_price,
            {"limit": {"tif": "Ioc"}}
        )

        print("[DEBUG ORDER]", result)

        status = result.get("status")

        if status == "ok":
            print(
                f"[OK] {side} {size} {SYMBOL} @ ${limit_price}"
            )
            return True

        print("[ECHEC]", result)

        return False

    except Exception as e:
        print("[ERREUR ORDER]", e)
        return False


# =========================
# RISK MANAGEMENT
# =========================

def check_risk(positions, price):

    for pos in positions:

        if pos["symbol"] != SYMBOL:
            continue

        pnl = (price - pos["entry"]) / pos["entry"]

        # LONG
        if pos["size"] > 0:

            if pnl <= -STOP_LOSS_PCT:
                print(f"[STOP LOSS] {round(pnl * 100, 2)}%")

                place_order(
                    "SELL",
                    abs(pos["size"]) * price,
                    price
                )

                return True

            if pnl >= TAKE_PROFIT_PCT:
                print(f"[TAKE PROFIT] {round(pnl * 100, 2)}%")

                place_order(
                    "SELL",
                    abs(pos["size"]) * price,
                    price
                )

                return True

    return False


# =========================
# MAIN LOOP
# =========================

def run():

    print("=" * 50)
    print(f"BOT HYPERLIQUID - {SYMBOL}")
    print(f"Wallet: {wallet_address}")
    print("=" * 50)

    balance = get_balance()

    print(f"Balance: ${round(balance, 2)}")

    last_signal = "HOLD"

    trades = 0

    last_trade_time = 0

    while True:

        try:

            now = datetime.now().strftime("%H:%M:%S")

            prices = get_prices()

            if not prices:
                print("[ERREUR] Aucun prix")
                time.sleep(CHECK_INTERVAL)
                continue

            price = prices[-1]

            positions = get_positions()

            # Risk management
            if positions:
                if check_risk(positions, price):
                    time.sleep(CHECK_INTERVAL)
                    continue

            sig = signal(prices)

            ef = ema(prices, EMA_FAST)
            es = ema(prices, EMA_SLOW)
            r = rsi(prices, RSI_PERIOD)

            print(
                f"[{now}] "
                f"${price} | "
                f"EMA9: {round(float(ef), 2)} | "
                f"EMA21: {round(float(es), 2)} | "
                f"RSI: {round(float(r), 1)} | "
                f"{sig}"
            )

            # Cooldown anti spam
            cooldown_ok = (
                time.time() - last_trade_time
            ) > TRADE_COOLDOWN

            # Pas de position ouverte
            no_position = len(positions) == 0

            # Nouveau signal
            new_signal = sig != last_signal

            if (
                sig != "HOLD"
                and new_signal
                and no_position
                and cooldown_ok
            ):

                ok = place_order(
                    sig,
                    TRADE_SIZE_USD,
                    price
                )

                last_signal = sig

                if ok:
                    trades += 1
                    last_trade_time = time.time()

                    balance = get_balance()

                    print(
                        f"[TRADE #{trades}] "
                        f"Balance: ${round(balance, 2)}"
                    )

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrêté.")
            break

        except Exception as e:
            print("[ERREUR LOOP]", e)
            time.sleep(CHECK_INTERVAL)


# =========================
# START
# =========================

if __name__ == "__main__":
    run()
