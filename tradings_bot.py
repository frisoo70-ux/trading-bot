import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import eth_account

# ============================================================
# BOT SOL 15 MIN - VERSION V2 AGRESSIVE
# Long only
# Indicateurs : EMA20 / EMA50 / RSI14 / ATR14 / OBV / VWAP
# Entrée : tendance + momentum + volume + breakout
# Sortie : TP1 ATR, trailing stop ATR, clôture sous EMA20, RSI rollover
# ============================================================

PRIVATE_KEY = os.environ.get("HL_API_SECRET")
WALLET_ADDRESS = os.environ.get("HL_WALLET")

SYMBOL = "SOL"
TRADE_SIZE_USD = 30
CHECK_INTERVAL = 5

BASE_URL = "https://api.hyperliquid.xyz"
TICK_SIZE = 0.01

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_BUY_LEVEL = 55
RSI_OVERBOUGHT = 75
RSI_EXIT_LEVEL = 70

OBV_LOOKBACK = 3
BREAKOUT_LOOKBACK = 2

ATR_STOP_MULT = 1.5
ATR_TP_MULT = 2.0
ATR_TRAIL_MULT = 1.0

COOLDOWN_CANDLES_AFTER_SELL = 2

# Etat interne
last_candle_time = None
trade_state = {
    "in_position": False,
    "entry_price": None,
    "entry_atr": None,
    "tp1_done": False,
    "highest_price": None,
    "trailing_stop": None,
    "rsi_was_overbought": False,
    "cooldown_until_candle": None,
}

META = None
SIZE_DECIMALS = 2


def round_price(price):
    return round(round(price / TICK_SIZE) * TICK_SIZE, 2)


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
        # Spot balance fallback
        data = info_post({"type": "spotClearinghouseState", "user": WALLET_ADDRESS})
        for b in data.get("balances", []):
            if b.get("coin") == "USDC":
                return float(b.get("total", 0))

        # Perp account value
        data2 = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS})
        if not data2:
            return 0

        return float(data2.get("marginSummary", {}).get("accountValue", 0))

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
            szi = float(p.get("szi", 0))

            if szi != 0:
                positions.append({
                    "symbol": p.get("coin"),
                    "size": szi,
                    "entry": float(p.get("entryPx", 0))
                })

        return positions

    except Exception as e:
        print("[ERREUR] Positions:", e)
        return []


def get_sol_position():
    positions = get_positions()
    for pos in positions:
        if pos["symbol"] == SYMBOL and pos["size"] > 0:
            return pos
    return None


def get_candles():
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": SYMBOL,
                "interval": "15m",
                "startTime": int((time.time() - 220 * 60 * 15) * 1000),
                "endTime": int(time.time() * 1000)
            }
        }

        data = info_post(payload)

        if isinstance(data, list) and len(data) > 60:
            rows = []

            for c in data:
                rows.append({
                    "time": int(c["t"]),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c["v"])
                })

            df = pd.DataFrame(rows)

            # Dernière bougie = souvent bougie en cours. On l'ignore pour les signaux.
            confirmed_df = df.iloc[:-1].copy()
            current_price = float(df.iloc[-1]["close"])
            confirmed_time = int(confirmed_df.iloc[-1]["time"])

            return confirmed_df, confirmed_time, current_price

        return pd.DataFrame(), None, None

    except Exception as e:
        print("[ERREUR] Candles:", e)
        return pd.DataFrame(), None, None


def add_indicators(df):
    df = df.copy()

    df["ema20"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / avg_loss.replace(0, 0.0000001)

    df["rsi"] = 100 - (100 / (1 + rs))

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()

    obv_values = [0]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > df["close"].iloc[i - 1]:
            obv_values.append(obv_values[-1] + df["volume"].iloc[i])
        elif df["close"].iloc[i] < df["close"].iloc[i - 1]:
            obv_values.append(obv_values[-1] - df["volume"].iloc[i])
        else:
            obv_values.append(obv_values[-1])

    df["obv"] = obv_values
    df["obv_ma5"] = df["obv"].rolling(5).mean()

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()

    return df


def place_order_qty(side, qty, price, reduce_only=False):
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
        qty = round(abs(qty), SIZE_DECIMALS)

        if qty <= 0:
            print("[ECHEC] Taille invalide:", qty)
            return False

        lp = round_price(price * (1.002 if is_buy else 0.998))

        print("========== ORDER ==========")
        print("SYMBOL =", SYMBOL)
        print("SIDE =", side)
        print("QTY =", qty)
        print("SIZE_DECIMALS =", SIZE_DECIMALS)
        print("PRICE =", price)
        print("LIMIT_PRICE =", lp)
        print("REDUCE_ONLY =", reduce_only)
        print("===========================")

        result = exchange.order(
            SYMBOL,
            is_buy,
            qty,
            lp,
            {"limit": {"tif": "Ioc"}},
            reduce_only=reduce_only
        )

        print("[ORDER]", result)

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])

        if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
            print("[OK]", side, qty, SYMBOL, "@", lp)
            return True

        if statuses and "error" in statuses[0]:
            print("[ECHEC]", statuses[0]["error"])
            return False

        print("[ECHEC]", result)
        return False

    except Exception as e:
        print("[ERREUR] Ordre:", e)
        return False


def place_order_usd(side, size_usd, price, reduce_only=False):
    qty = round(size_usd / price, SIZE_DECIMALS)
    return place_order_qty(side, qty, price, reduce_only=reduce_only)


def close_position_fraction(pos, fraction, price):
    qty = abs(pos["size"]) * fraction
    return place_order_qty("SELL", qty, price, reduce_only=True)


def close_position_full(pos, price):
    return close_position_fraction(pos, 1.0, price)


def reset_trade_state_after_sell(candle_time):
    global trade_state

    trade_state = {
        "in_position": False,
        "entry_price": None,
        "entry_atr": None,
        "tp1_done": False,
        "highest_price": None,
        "trailing_stop": None,
        "rsi_was_overbought": False,
        "cooldown_until_candle": candle_time + COOLDOWN_CANDLES_AFTER_SELL * 15 * 60 * 1000,
    }


def sync_state_with_position(pos):
    global trade_state

    if pos is None:
        if trade_state["in_position"]:
            reset_trade_state_after_sell(int(time.time() * 1000))
        return

    if not trade_state["in_position"]:
        trade_state["in_position"] = True
        trade_state["entry_price"] = pos["entry"]
        trade_state["entry_atr"] = None
        trade_state["tp1_done"] = False
        trade_state["highest_price"] = pos["entry"]
        trade_state["trailing_stop"] = None
        trade_state["rsi_was_overbought"] = False


def buy_signal(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]
    prev3 = df.iloc[-4]

    trend_ok = last["ema20"] > last["ema50"]
    rsi_ok = last["rsi"] > RSI_BUY_LEVEL
    vwap_ok = last["close"] > last["vwap"]

    obv_rising = (
        last["obv"] > prev1["obv"]
        and prev1["obv"] > prev2["obv"]
        and prev2["obv"] > prev3["obv"]
    )

    breakout_level = max(prev1["high"], prev2["high"])
    breakout_ok = last["close"] > breakout_level

    green_candle = last["close"] > last["open"]

    return trend_ok and rsi_ok and vwap_ok and obv_rising and breakout_ok and green_candle, {
        "trend_ok": trend_ok,
        "rsi_ok": rsi_ok,
        "vwap_ok": vwap_ok,
        "obv_rising": obv_rising,
        "breakout_ok": breakout_ok,
        "green_candle": green_candle,
        "breakout_level": breakout_level,
    }


def manage_open_position(pos, df, current_price, candle_time):
    global trade_state

    last = df.iloc[-1]

    entry = trade_state["entry_price"] or pos["entry"]
    atr_value = trade_state["entry_atr"] or last["atr"]

    if atr_value is None or pd.isna(atr_value) or atr_value <= 0:
        print("[INFO] ATR indisponible pour gestion position.")
        return

    if trade_state["entry_atr"] is None:
        trade_state["entry_atr"] = atr_value

    if trade_state["highest_price"] is None:
        trade_state["highest_price"] = max(entry, current_price)

    if current_price > trade_state["highest_price"]:
        trade_state["highest_price"] = current_price

    trailing = trade_state["highest_price"] - ATR_TRAIL_MULT * atr_value
    trade_state["trailing_stop"] = trailing

    pnl_pct = ((current_price - entry) / entry) * 100
    tp1_price = entry + ATR_TP_MULT * atr_value
    hard_stop = entry - ATR_STOP_MULT * atr_value

    if last["rsi"] >= RSI_OVERBOUGHT:
        trade_state["rsi_was_overbought"] = True

    rsi_rollover = trade_state["rsi_was_overbought"] and last["rsi"] < RSI_EXIT_LEVEL
    ema_break = last["close"] < last["ema20"]
    hard_stop_hit = current_price <= hard_stop
    trailing_hit = current_price <= trailing

    print(
        f"[POSITION] Entry={entry:.4f} Prix={current_price:.4f} "
        f"PnL={pnl_pct:.2f}% ATR={atr_value:.4f} TP1={tp1_price:.4f} "
        f"Trail={trailing:.4f} Stop={hard_stop:.4f} RSI={last['rsi']:.2f}"
    )

    # TP1 : vendre 50 % une seule fois
    if not trade_state["tp1_done"] and current_price >= tp1_price:
        print("[TP1] Objectif +2 ATR atteint. Vente de 50%.")
        if close_position_fraction(pos, 0.5, current_price):
            trade_state["tp1_done"] = True
        return

    # Sorties complètes
    if hard_stop_hit:
        print("[SELL] Stop ATR touché.")
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return

    if trailing_hit and trade_state["tp1_done"]:
        print("[SELL] Trailing stop ATR touché.")
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return

    if ema_break:
        print("[SELL] Clôture sous EMA20.")
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return

    if rsi_rollover:
        print("[SELL] RSI overbought puis retour sous 70.")
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return


def run():
    global last_candle_time, META, SIZE_DECIMALS, trade_state

    print("BOT SOL 15m V2 AGRESSIF")
    print("Long only | EMA20/EMA50 + RSI + ATR + OBV + VWAP + Breakout")
    print("Taille fixe:", TRADE_SIZE_USD, "USDC")

    if not PRIVATE_KEY or not WALLET_ADDRESS:
        print("[ERREUR] Variables manquantes: HL_API_SECRET ou HL_WALLET")
        return

    META = get_meta()
    if META:
        SIZE_DECIMALS = int(META.get("szDecimals", SIZE_DECIMALS))

    print("[INFO] META =", META)
    print("[INFO] SIZE_DECIMALS =", SIZE_DECIMALS)
    print("Wallet:", WALLET_ADDRESS[:8] + "...")
    print("Solde:", round(get_balance(), 2), "USDC")
    print("---")

    trades = 0

    while True:
        try:
            df, confirmed_time, current_price = get_candles()

            if df.empty or confirmed_time is None or current_price is None:
                time.sleep(CHECK_INTERVAL)
                continue

            if len(df) < 80:
                print("[INFO] Pas assez de bougies.")
                time.sleep(CHECK_INTERVAL)
                continue

            # On traite uniquement la nouvelle bougie 15 min confirmée.
            if confirmed_time == last_candle_time:
                time.sleep(CHECK_INTERVAL)
                continue

            last_candle_time = confirmed_time

            df = add_indicators(df)
            df = df.dropna().copy()

            if len(df) < 60:
                time.sleep(CHECK_INTERVAL)
                continue

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            last = df.iloc[-1]

            pos = get_sol_position()
            sync_state_with_position(pos)

            print("\n[CANDLE CLOSE]", now)
            print(
                f"Close={last['close']:.4f} Current={current_price:.4f} "
                f"EMA20={last['ema20']:.4f} EMA50={last['ema50']:.4f} "
                f"RSI={last['rsi']:.2f} ATR={last['atr']:.4f} "
                f"VWAP={last['vwap']:.4f} OBV={last['obv']:.2f}"
            )

            if pos:
                manage_open_position(pos, df, current_price, confirmed_time)
                time.sleep(CHECK_INTERVAL)
                continue

            # Cooldown après une vente
            cooldown_until = trade_state.get("cooldown_until_candle")
            if cooldown_until and confirmed_time < cooldown_until:
                print("[COOLDOWN] Attente après vente.")
                time.sleep(CHECK_INTERVAL)
                continue

            ok, details = buy_signal(df)

            print(
                "[SIGNAL]",
                "BUY" if ok else "HOLD",
                "| trend:", details["trend_ok"],
                "| rsi:", details["rsi_ok"],
                "| vwap:", details["vwap_ok"],
                "| obv:", details["obv_rising"],
                "| breakout:", details["breakout_ok"],
                "| green:", details["green_candle"],
                "| breakout_level:", round(details["breakout_level"], 4),
            )

            if ok:
                balance = get_balance()

                if balance < TRADE_SIZE_USD:
                    print("[ECHEC] Solde insuffisant:", balance)
                    time.sleep(CHECK_INTERVAL)
                    continue

                print("[BUY SIGNAL] Achat agressif confirmé.")

                if place_order_usd("BUY", TRADE_SIZE_USD, current_price, reduce_only=False):
                    trades += 1

                    trade_state["in_position"] = True
                    trade_state["entry_price"] = current_price
                    trade_state["entry_atr"] = last["atr"]
                    trade_state["tp1_done"] = False
                    trade_state["highest_price"] = current_price
                    trade_state["trailing_stop"] = current_price - ATR_TRAIL_MULT * last["atr"]
                    trade_state["rsi_was_overbought"] = False
                    trade_state["cooldown_until_candle"] = None

                    print("Trade #", trades, "| Solde:", round(get_balance(), 2), "USDC")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrêté. Trades:", trades)
            break

        except Exception as e:
            print("[ERREUR]", e)
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
