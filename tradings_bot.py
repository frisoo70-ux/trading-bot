import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import eth_account

# ============================================================
# BOT MULTI-COINS V3
# SOL / XRP / AAVE
# Timeframe : 15 min
# Long only
# Une seule position ouverte à la fois
#
# Indicateurs :
# EMA20 / EMA50 / RSI14 / ATR14 / OBV_MA5 / VWAP / Breakout
#
# Entrée :
# Le bot scanne SOL, XRP, AAVE.
# Si plusieurs signaux BUY sont valides, il choisit le meilleur score.
#
# Sorties :
# Stop ATR 2.5
# TP1 +2 ATR avec vente 50 %
# Trailing après TP1
# Sortie clôture sous EMA20
# Pas de sortie RSI
#
# Telegram :
# Notifications BUY / SELL / TP1 / erreurs / solde / PnL
# ============================================================

PRIVATE_KEY = os.environ.get("HL_API_SECRET")
WALLET_ADDRESS = os.environ.get("HL_WALLET")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOLS = ["SOL", "XRP", "AAVE"]
TRADE_SIZE_USD = 30
CHECK_INTERVAL = 60

BASE_URL = "https://api.hyperliquid.xyz"

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_BUY_LEVEL = 55
RSI_BUY_MAX = 70

ATR_STOP_MULT = 2.5
ATR_TP_MULT = 2.0
ATR_TRAIL_MULT = 1.0

COOLDOWN_CANDLES_AFTER_SELL = 2

last_candle_times = {symbol: None for symbol in SYMBOLS}

trade_state = {
    "in_position": False,
    "symbol": None,
    "entry_price": None,
    "entry_atr": None,
    "tp1_done": False,
    "highest_price": None,
    "trailing_stop": None,
    "cooldown_until_candle": None,
}

META_BY_SYMBOL = {}
SIZE_DECIMALS_BY_SYMBOL = {symbol: 2 for symbol in SYMBOLS}
LAST_ENTRY_PRICE_BY_SYMBOL = {symbol: None for symbol in SYMBOLS}


def notify_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("[ERREUR TELEGRAM]", e)


def info_post(payload):
    try:
        headers = {"Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/info", headers=headers, data=json.dumps(payload), timeout=10)
        return r.json()
    except Exception as e:
        print("[ERREUR] API:", e)
        return {}


def get_meta_all():
    try:
        data = info_post({"type": "meta"})
        meta_by_symbol = {}
        for asset in data.get("universe", []):
            name = asset.get("name")
            if name in SYMBOLS:
                meta_by_symbol[name] = asset
        return meta_by_symbol
    except Exception as e:
        print("[ERREUR] Meta:", e)
        return {}


def get_size_decimals(symbol):
    return int(SIZE_DECIMALS_BY_SYMBOL.get(symbol, 2))


def round_order_price(price):
    price = float(price)
    if price >= 1000:
        return round(price, 1)
    if price >= 10:
        return round(price, 2)
    if price >= 1:
        return round(price, 4)
    return round(price, 5)


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
            symbol = p.get("coin")
            szi = float(p.get("szi", 0))
            if symbol in SYMBOLS and szi != 0:
                positions.append({"symbol": symbol, "size": szi, "entry": float(p.get("entryPx", 0))})
        return positions
    except Exception as e:
        print("[ERREUR] Positions:", e)
        return []


def get_open_position():
    positions = get_positions()
    for pos in positions:
        if pos["symbol"] in SYMBOLS and pos["size"] > 0:
            return pos
    return None


def get_candles(symbol):
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": "15m",
                "startTime": int((time.time() - 220 * 60 * 15) * 1000),
                "endTime": int(time.time() * 1000),
            },
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
                    "volume": float(c["v"]),
                })
            df = pd.DataFrame(rows)
            confirmed_df = df.iloc[:-1].copy()
            current_price = float(df.iloc[-1]["close"])
            confirmed_time = int(confirmed_df.iloc[-1]["time"])
            return confirmed_df, confirmed_time, current_price
        return pd.DataFrame(), None, None
    except Exception as e:
        print(f"[ERREUR] Candles {symbol}:", e)
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


def place_order_qty(symbol, side, qty, price, reduce_only=False):
    global LAST_ENTRY_PRICE_BY_SYMBOL
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        wallet = eth_account.Account.from_key(PRIVATE_KEY)
        exchange = Exchange(wallet, constants.MAINNET_API_URL, account_address=WALLET_ADDRESS)

        is_buy = side == "BUY"
        qty = round(abs(qty), get_size_decimals(symbol))
        if qty <= 0:
            print("[ECHEC] Taille invalide:", symbol, qty)
            return False

        lp = round_order_price(price * (1.002 if is_buy else 0.998))

        print("========== ORDER ==========")
        print("SYMBOL =", symbol)
        print("SIDE =", side)
        print("QTY =", qty)
        print("SIZE_DECIMALS =", get_size_decimals(symbol))
        print("PRICE =", price)
        print("LIMIT_PRICE =", lp)
        print("REDUCE_ONLY =", reduce_only)
        print("===========================")

        result = exchange.order(symbol, is_buy, qty, lp, {"limit": {"tif": "Ioc"}}, reduce_only=reduce_only)
        print("[ORDER]", result)

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])

        if result.get("status") == "ok" and statuses and "error" not in statuses[0]:
            print("[OK]", side, qty, symbol, "@", lp)

            filled = statuses[0].get("filled", {})
            avg_px = float(filled.get("avgPx", lp))
            total_sz = float(filled.get("totalSz", qty))
            balance_after = round(get_balance(), 2)
            emoji = "🟢" if side == "BUY" else "🔴"

            if side == "BUY":
                LAST_ENTRY_PRICE_BY_SYMBOL[symbol] = avg_px
                notify_telegram(
                    f"{emoji} <b>BUY exécuté</b>\n"
                    f"Symbole: {symbol}\n"
                    f"Quantité: {total_sz}\n"
                    f"Prix moyen: {avg_px:.5f}\n"
                    f"Montant approx: {total_sz * avg_px:.2f} USDC\n"
                    f"Solde: {balance_after} USDC"
                )
            else:
                entry_ref = trade_state.get("entry_price") or LAST_ENTRY_PRICE_BY_SYMBOL.get(symbol)
                if entry_ref:
                    pnl_pct = ((avg_px - float(entry_ref)) / float(entry_ref)) * 100
                    pnl_usdc = (avg_px - float(entry_ref)) * total_sz
                    notify_telegram(
                        f"{emoji} <b>SELL exécuté</b>\n"
                        f"Symbole: {symbol}\n"
                        f"Quantité: {total_sz}\n"
                        f"Entry: {float(entry_ref):.5f}\n"
                        f"Sortie: {avg_px:.5f}\n"
                        f"PnL: {pnl_pct:.2f}%\n"
                        f"Gain/perte approx: {pnl_usdc:.3f} USDC\n"
                        f"Solde: {balance_after} USDC\n"
                        f"Reduce only: {reduce_only}"
                    )
                else:
                    notify_telegram(
                        f"{emoji} <b>SELL exécuté</b>\n"
                        f"Symbole: {symbol}\n"
                        f"Quantité: {total_sz}\n"
                        f"Prix moyen: {avg_px:.5f}\n"
                        f"Solde: {balance_after} USDC\n"
                        f"Reduce only: {reduce_only}"
                    )
            return True

        if statuses and "error" in statuses[0]:
            error_msg = statuses[0]["error"]
            print("[ECHEC]", error_msg)
            notify_telegram(f"⚠️ <b>Erreur ordre</b>\nSymbole: {symbol}\nSide: {side}\nErreur: {error_msg}")
            return False

        print("[ECHEC]", result)
        return False

    except Exception as e:
        print("[ERREUR] Ordre:", e)
        notify_telegram(f"⚠️ <b>Erreur ordre exception</b>\nSymbole: {symbol}\nSide: {side}\nErreur: {e}")
        return False


def place_order_usd(symbol, side, size_usd, price, reduce_only=False):
    qty = round(size_usd / price, get_size_decimals(symbol))
    return place_order_qty(symbol, side, qty, price, reduce_only=reduce_only)


def close_position_fraction(pos, fraction, price):
    symbol = pos["symbol"]
    qty = abs(pos["size"]) * fraction
    return place_order_qty(symbol, "SELL", qty, price, reduce_only=True)


def close_position_full(pos, price):
    return close_position_fraction(pos, 1.0, price)


def reset_trade_state_after_sell(candle_time):
    global trade_state
    trade_state = {
        "in_position": False,
        "symbol": None,
        "entry_price": None,
        "entry_atr": None,
        "tp1_done": False,
        "highest_price": None,
        "trailing_stop": None,
        "cooldown_until_candle": candle_time + COOLDOWN_CANDLES_AFTER_SELL * 15 * 60 * 1000,
    }


def sync_state_with_position(pos):
    global trade_state
    if pos is None:
        if trade_state["in_position"]:
            reset_trade_state_after_sell(int(time.time() * 1000))
        return
    if not trade_state["in_position"] or trade_state.get("symbol") != pos["symbol"]:
        trade_state["in_position"] = True
        trade_state["symbol"] = pos["symbol"]
        trade_state["entry_price"] = pos["entry"]
        trade_state["entry_atr"] = None
        trade_state["tp1_done"] = False
        trade_state["highest_price"] = pos["entry"]
        trade_state["trailing_stop"] = None



def detect_bull_flag(df):
    if len(df) < 25:
        return False, 0

    recent = df.iloc[-12:]
    impulse = df.iloc[-24:-12]

    impulse_move = (impulse["close"].iloc[-1] - impulse["close"].iloc[0]) / impulse["close"].iloc[0]
    consolidation_range = (recent["high"].max() - recent["low"].min()) / recent["close"].iloc[-1]

    highs_flat = recent["high"].max() <= impulse["high"].max() * 1.03
    pullback_controlled = recent["low"].min() > impulse["close"].iloc[0]

    ok = impulse_move > 0.012 and consolidation_range < 0.018 and highs_flat and pullback_controlled
    score = 1.5 + min(impulse_move * 50, 1.5) if ok else 0

    return ok, score


def detect_ascending_triangle(df):
    if len(df) < 10:
        return False, 0

    recent = df.iloc[-8:]
    resistance = recent["high"].max()
    resistance_tolerance = resistance * 0.004

    highs_near_resistance = (resistance - recent["high"]).abs().mean() < resistance_tolerance * 2

    lows = recent["low"].values
    higher_lows = lows[-1] > lows[-3] > lows[-5]
    close_near_resistance = recent["close"].iloc[-1] > resistance * 0.995

    ok = highs_near_resistance and higher_lows and close_near_resistance
    score = 2.0 if ok else 0

    return ok, score


def detect_compression_breakout(df):
    if len(df) < 25:
        return False, 0

    recent = df.iloc[-8:]
    previous = df.iloc[-20:-8]

    recent_range = (recent["high"].max() - recent["low"].min()) / recent["close"].iloc[-1]
    previous_range = (previous["high"].max() - previous["low"].min()) / previous["close"].iloc[-1]

    range_contracting = recent_range < previous_range * 0.75
    breakout = recent["close"].iloc[-1] > recent["high"].iloc[:-1].max()

    ok = range_contracting and breakout
    score = 1.8 if ok else 0

    return ok, score


def detect_higher_lows(df):
    if len(df) < 8:
        return False, 0

    lows = df["low"].iloc[-6:].values
    ok = lows[-1] > lows[-3] > lows[-5]
    score = 1.0 if ok else 0

    return ok, score


def pattern_score(df):
    bull_flag, bull_flag_score = detect_bull_flag(df)
    triangle, triangle_score = detect_ascending_triangle(df)
    compression, compression_score = detect_compression_breakout(df)
    higher_lows, higher_lows_score = detect_higher_lows(df)

    total = bull_flag_score + triangle_score + compression_score + higher_lows_score

    return total, {
        "bull_flag": bull_flag,
        "ascending_triangle": triangle,
        "compression_breakout": compression,
        "higher_lows": higher_lows,
        "pattern_score": total,
    }



def buy_signal(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]

    trend_ok = last["ema20"] > last["ema50"]
    rsi_ok = RSI_BUY_LEVEL < last["rsi"] < RSI_BUY_MAX
    vwap_ok = last["close"] > last["vwap"]

    # OBV_MA5 :
    # OBV actuel au-dessus de sa moyenne mobile 5.
    obv_rising = last["obv"] > last["obv_ma5"]

    breakout_level = max(prev1["high"], prev2["high"])
    breakout_ok = last["close"] > breakout_level

    green_candle = last["close"] > last["open"]

    patt_score, patt_details = pattern_score(df)

    # Force du breakout et du contexte
    breakout_strength = max(0, (last["close"] / breakout_level) - 1) if breakout_level > 0 else 0
    vwap_strength = max(0, (last["close"] / last["vwap"]) - 1) if last["vwap"] > 0 else 0
    obv_strength = max(0, (last["obv"] / last["obv_ma5"]) - 1) if last["obv_ma5"] and last["obv_ma5"] != 0 else 0
    rsi_score = max(0, 1 - abs(last["rsi"] - 62) / 10)

    # MODE A : signal classique, plus prudent
    mode_a_ok = (
        trend_ok
        and rsi_ok
        and vwap_ok
        and obv_rising
        and breakout_ok
        and green_candle
    )

    # MODE B : pattern premium, plus agressif.
    # Il peut acheter avant le croisement EMA20/EMA50 si la structure est forte.
    # Conditions :
    # - pattern_score >= 2.0
    # - cassure confirmée
    # - OBV au-dessus OBV_MA5
    # - prix au-dessus VWAP
    # - bougie verte
    # - RSI pas surchauffé
    mode_b_ok = (
        patt_details["pattern_score"] >= 2.0
        and breakout_ok
        and obv_rising
        and vwap_ok
        and green_candle
        and 45 < last["rsi"] < 85
    )

    signal_mode = None

    if mode_a_ok:
        signal_mode = "MODE_A_CLASSIQUE"
    elif mode_b_ok:
        signal_mode = "MODE_B_PATTERN"

    score = (
        breakout_strength * 100
        + vwap_strength * 10
        + obv_strength * 2
        + rsi_score
        + patt_score
    )

    # Bonus si signal pattern premium
    if mode_b_ok:
        score += 2.0

    ok = mode_a_ok or mode_b_ok

    return ok, score, {
        "trend_ok": trend_ok,
        "rsi_ok": rsi_ok,
        "vwap_ok": vwap_ok,
        "obv_rising": obv_rising,
        "breakout_ok": breakout_ok,
        "green_candle": green_candle,
        "breakout_level": breakout_level,
        "score": score,
        "signal_mode": signal_mode,
        "mode_a_ok": mode_a_ok,
        "mode_b_ok": mode_b_ok,
        "pattern_score": patt_details["pattern_score"],
        "bull_flag": patt_details["bull_flag"],
        "ascending_triangle": patt_details["ascending_triangle"],
        "compression_breakout": patt_details["compression_breakout"],
        "higher_lows": patt_details["higher_lows"],
    }


def manage_open_position(pos, df, current_price, candle_time):
    global trade_state
    symbol = pos["symbol"]
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

    pnl_pct = ((current_price - entry) / entry) * 100
    tp1_price = entry + ATR_TP_MULT * atr_value
    hard_stop = entry - ATR_STOP_MULT * atr_value

    if trade_state["tp1_done"]:
        trailing = trade_state["highest_price"] - ATR_TRAIL_MULT * atr_value
        trade_state["trailing_stop"] = trailing
        trailing_hit = current_price <= trailing
        trailing_display = f"{trailing:.5f}"
    else:
        trailing = None
        trade_state["trailing_stop"] = None
        trailing_hit = False
        trailing_display = "INACTIF avant TP1"

    ema_break = last["close"] < last["ema20"]
    hard_stop_hit = current_price <= hard_stop

    print(
        f"[POSITION] {symbol} Entry={entry:.5f} Prix={current_price:.5f} "
        f"PnL={pnl_pct:.2f}% ATR={atr_value:.5f} TP1={tp1_price:.5f} "
        f"Trail={trailing_display} Stop={hard_stop:.5f} RSI={last['rsi']:.2f}"
    )

    if not trade_state["tp1_done"] and current_price >= tp1_price:
        print("[TP1] Objectif +2 ATR atteint. Vente de 50%.")
        notify_telegram(
            f"🎯 <b>TP1 atteint</b>\n"
            f"Symbole: {symbol}\n"
            f"Entry: {entry:.5f}\n"
            f"Prix: {current_price:.5f}\n"
            f"TP1: {tp1_price:.5f}\n"
            f"PnL: {pnl_pct:.2f}%\n"
            f"Solde: {round(get_balance(), 2)} USDC\n"
            f"Action: vente 50%"
        )
        if close_position_fraction(pos, 0.5, current_price):
            trade_state["tp1_done"] = True
        return

    if hard_stop_hit:
        print("[SELL] Stop ATR touché.")
        notify_telegram(
            f"🛑 <b>Stop ATR touché</b>\n"
            f"Symbole: {symbol}\n"
            f"Entry: {entry:.5f}\n"
            f"Prix: {current_price:.5f}\n"
            f"Stop: {hard_stop:.5f}\n"
            f"PnL: {pnl_pct:.2f}%\n"
            f"Solde: {round(get_balance(), 2)} USDC"
        )
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return

    if trailing_hit and trade_state["tp1_done"]:
        print("[SELL] Trailing stop ATR touché.")
        notify_telegram(
            f"📉 <b>Trailing stop touché</b>\n"
            f"Symbole: {symbol}\n"
            f"Entry: {entry:.5f}\n"
            f"Prix: {current_price:.5f}\n"
            f"Trail: {trailing:.5f}\n"
            f"PnL: {pnl_pct:.2f}%\n"
            f"Solde: {round(get_balance(), 2)} USDC"
        )
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return

    if ema_break:
        print("[SELL] Clôture sous EMA20.")
        notify_telegram(
            f"📉 <b>Sortie EMA20</b>\n"
            f"Symbole: {symbol}\n"
            f"Entry: {entry:.5f}\n"
            f"Prix: {current_price:.5f}\n"
            f"EMA20: {last['ema20']:.5f}\n"
            f"PnL: {pnl_pct:.2f}%\n"
            f"Solde: {round(get_balance(), 2)} USDC"
        )
        if close_position_full(pos, current_price):
            reset_trade_state_after_sell(candle_time)
        return


def prepare_df_for_symbol(symbol):
    df, confirmed_time, current_price = get_candles(symbol)
    if df.empty or confirmed_time is None or current_price is None:
        return None, None, None
    if len(df) < 80:
        return None, None, None
    df = add_indicators(df)
    df = df.dropna().copy()
    if len(df) < 60:
        return None, None, None
    return df, confirmed_time, current_price


def scan_symbols_for_best_buy():
    global last_candle_times
    candidates = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol in SYMBOLS:
        df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
        if df is None:
            continue

        new_candle = confirmed_time != last_candle_times.get(symbol)
        if not new_candle:
            continue

        last_candle_times[symbol] = confirmed_time
        last = df.iloc[-1]
        ok, score, details = buy_signal(df)

        print(f"\n[CANDLE CLOSE] {now} {symbol}")
        print(
            f"Close={last['close']:.5f} Current={current_price:.5f} "
            f"EMA20={last['ema20']:.5f} EMA50={last['ema50']:.5f} "
            f"RSI={last['rsi']:.2f} ATR={last['atr']:.5f} "
            f"VWAP={last['vwap']:.5f} OBV={last['obv']:.2f} OBV_MA5={last['obv_ma5']:.2f}"
        )
        print(
            "[SIGNAL]",
            symbol,
            "BUY" if ok else "HOLD",
            "| trend:", details["trend_ok"],
            "| rsi:", details["rsi_ok"],
            "| vwap:", details["vwap_ok"],
            "| obv:", details["obv_rising"],
            "| breakout:", details["breakout_ok"],
            "| green:", details["green_candle"],
            "| breakout_level:", round(details["breakout_level"], 5),
            "| patterns:", {
                "bull_flag": details["bull_flag"],
                "triangle": details["ascending_triangle"],
                "compression": details["compression_breakout"],
                "higher_lows": details["higher_lows"],
            },
            "| mode:", details.get("signal_mode"),
            "| mode_a:", details.get("mode_a_ok"),
            "| mode_b:", details.get("mode_b_ok"),
            "| pattern_score:", round(details["pattern_score"], 4),
            "| score:", round(details["score"], 4),
        )

        if ok:
            candidates.append({
                "symbol": symbol,
                "df": df,
                "confirmed_time": confirmed_time,
                "current_price": current_price,
                "score": score,
                "details": details,
                "last": last,
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def run():
    global META_BY_SYMBOL, SIZE_DECIMALS_BY_SYMBOL, trade_state

    print("BOT MULTI V3.3 MODE B RSI 85 - SOL / XRP / AAVE")
    print("Long only | 15m | Une seule position à la fois")
    print("Indicateurs : EMA20/EMA50 + RSI + ATR + OBV_MA5 + VWAP + Breakout + Patterns + Mode A/B")
    print("Taille fixe:", TRADE_SIZE_USD, "USDC")

    if not PRIVATE_KEY or not WALLET_ADDRESS:
        print("[ERREUR] Variables manquantes: HL_API_SECRET ou HL_WALLET")
        return

    META_BY_SYMBOL = get_meta_all()
    for symbol in SYMBOLS:
        meta = META_BY_SYMBOL.get(symbol)
        if meta:
            SIZE_DECIMALS_BY_SYMBOL[symbol] = int(meta.get("szDecimals", SIZE_DECIMALS_BY_SYMBOL[symbol]))

    print("[INFO] META =", META_BY_SYMBOL)
    print("[INFO] SIZE_DECIMALS =", SIZE_DECIMALS_BY_SYMBOL)
    print("Wallet:", WALLET_ADDRESS[:8] + "...")

    start_balance = round(get_balance(), 2)
    print("Solde:", start_balance, "USDC")
    print("---")

    notify_telegram(
        "🤖 <b>BOT MULTI V3.3 Mode B RSI 85 démarré</b>\n"
        f"Coins: {', '.join(SYMBOLS)}\n"
        f"Max position: 1\n"
        f"Solde: {start_balance} USDC\n"
        f"Taille trade: {TRADE_SIZE_USD} USDC"
    )

    trades = 0

    while True:
        try:
            pos = get_open_position()
            sync_state_with_position(pos)

            if pos:
                symbol = pos["symbol"]
                df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
                if df is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                last = df.iloc[-1]
                new_candle = confirmed_time != last_candle_times.get(symbol)

                if new_candle:
                    last_candle_times[symbol] = confirmed_time
                    print(f"\n[CANDLE CLOSE - POSITION] {now} {symbol}")
                else:
                    print(f"\n[PRICE CHECK - POSITION] {now} {symbol}")

                print(
                    f"Close={last['close']:.5f} Current={current_price:.5f} "
                    f"EMA20={last['ema20']:.5f} EMA50={last['ema50']:.5f} "
                    f"RSI={last['rsi']:.2f} ATR={last['atr']:.5f} "
                    f"VWAP={last['vwap']:.5f} OBV={last['obv']:.2f}"
                )

                manage_open_position(pos, df, current_price, confirmed_time)
                time.sleep(CHECK_INTERVAL)
                continue

            cooldown_until = trade_state.get("cooldown_until_candle")
            best = scan_symbols_for_best_buy()

            if best is None:
                time.sleep(CHECK_INTERVAL)
                continue

            symbol = best["symbol"]
            confirmed_time = best["confirmed_time"]
            current_price = best["current_price"]
            last = best["last"]
            details = best["details"]

            if cooldown_until and confirmed_time < cooldown_until:
                print("[COOLDOWN] Attente après vente.")
                time.sleep(CHECK_INTERVAL)
                continue

            balance = get_balance()
            if balance < TRADE_SIZE_USD:
                print("[ECHEC] Solde insuffisant:", balance)
                notify_telegram(
                    f"⚠️ <b>Solde insuffisant</b>\n"
                    f"Solde: {balance:.2f} USDC\n"
                    f"Trade demandé: {TRADE_SIZE_USD} USDC"
                )
                time.sleep(CHECK_INTERVAL)
                continue

            print(f"[BUY SIGNAL] Achat confirmé sur {symbol}. Score={best['score']:.4f}")
            notify_telegram(
                f"🚀 <b>BUY SIGNAL confirmé</b>\n"
                f"Symbole: {symbol}\n"
                f"Prix: {current_price:.5f}\n"
                f"Score: {best['score']:.4f}\n"
                f"Mode: {details.get('signal_mode')}\n"
                f"RSI: {last['rsi']:.2f}\n"
                f"ATR: {last['atr']:.5f}\n"
                f"EMA20: {last['ema20']:.5f}\n"
                f"EMA50: {last['ema50']:.5f}\n"
                f"VWAP: {last['vwap']:.5f}\n"
                f"OBV: {last['obv']:.2f}\n"
                f"OBV_MA5: {last['obv_ma5']:.2f}\n"
                f"Pattern score: {details['pattern_score']:.2f}\n"
                f"Bull flag: {details['bull_flag']}\n"
                f"Triangle ascendant: {details['ascending_triangle']}\n"
                f"Compression: {details['compression_breakout']}\n"
                f"Higher lows: {details['higher_lows']}\n"
                f"Solde avant ordre: {round(balance, 2)} USDC"
            )

            if place_order_usd(symbol, "BUY", TRADE_SIZE_USD, current_price, reduce_only=False):
                trades += 1
                trade_state["in_position"] = True
                trade_state["symbol"] = symbol
                trade_state["entry_price"] = current_price
                trade_state["entry_atr"] = last["atr"]
                trade_state["tp1_done"] = False
                trade_state["highest_price"] = current_price
                trade_state["trailing_stop"] = None
                trade_state["cooldown_until_candle"] = None
                print("Trade #", trades, "|", symbol, "| Solde:", round(get_balance(), 2), "USDC")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot arrêté. Trades:", trades)
            break

        except Exception as e:
            print("[ERREUR]", e)
            notify_telegram(f"⚠️ <b>Erreur bot</b>\n{e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
