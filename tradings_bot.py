import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import csv
from pathlib import Path
import eth_account
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ============================================================
# BOT MULTI-COINS V3
# SOL / XRP / kSHIB
# Timeframe : 15 min
# Long only
# Une seule position ouverte à la fois
#
# Indicateurs :
# EMA20 / EMA50 / RSI14 / ATR14 / OBV_MA5 / VWAP / Breakout
#
# Entrée :
# Le bot scanne SOL, XRP, kSHIB.
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

SYMBOLS = ["SOL", "XRP", "kSHIB"]
TRADE_SIZE_USD = 30
CHECK_INTERVAL = 60

TRADES_CSV_PATH = Path("trades.csv")
POST_TRADE_CSV_PATH = Path("post_trade_analysis.csv")
POST_TRADE_PENDING_PATH = Path("post_trade_pending.json")
LAST_MONTHLY_REPORT_FILE = Path("last_monthly_report.txt")
BOT_CONFIG_PATH = Path("bot_config.json")
ADAPTIVE_CONFIG_PATH = Path("adaptive_config.json")

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DASHBOARD_PORT = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8000")))

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

# MODE C - Capitulation / rebond après forte baisse
MODE_C_RSI_MAX = 40
MODE_C_DROP_LOOKBACK = 8
MODE_C_MIN_DROP_PCT = 0.035
MODE_C_VOLUME_MULT = 1.2
MODE_C_WICK_ATR_MULT = 0.5

# MODE E - Early Momentum / impulsion précoce
MODE_E_VOLUME_MULT = 2.0
MODE_E_BODY_RATIO_MIN = 0.60
MODE_E_ATR_STOP_MULT = 2.5
MODE_E_MIN_RSI = 35
MODE_E_MAX_RSI = 78

# MODE E INTRABAR
# Permet au Mode E d'acheter sans attendre la clôture 15m.
# Il utilise les indicateurs de la dernière bougie 15m clôturée + le prix courant.
MODE_E_INTRABAR_ENABLED = True
MODE_E_INTRABAR_MIN_MOVE_PCT = 0.003
MODE_E_INTRABAR_VOLUME_MULT = 1.5
MODE_E_INTRABAR_MAX_DISTANCE_EMA20_PCT = 0.018

# MODE F - Candle Reversal
MODE_F_MIN_PATTERN_SCORE = 2.0
MODE_F_VOLUME_MULT = 1.3
MODE_F_RSI_MAX = 65
MODE_F_ATR_STOP_MULT = 2.5

# SMART EXIT - modes de retournement C / E / F
SMART_EXIT_REVERSAL_MODES = ["MODE_C_CAPITULATION", "MODE_D_EMA_PULLBACK", "MODE_E_EARLY_MOMENTUM", "MODE_F_CANDLE_REVERSAL"]
SMART_EXIT_CLOSE_SCORE = 7
SMART_EXIT_MODE_THRESHOLDS = {
    "MODE_C_CAPITULATION": 6.5,
    "MODE_D_EMA_PULLBACK": 5.5,
    "MODE_E_EARLY_MOMENTUM": 4.5,
    "MODE_F_CANDLE_REVERSAL": 5.0,
}
SMART_EXIT_MODE_TIGHTEN_THRESHOLDS = {
    "MODE_C_CAPITULATION": 4.0,
    "MODE_D_EMA_PULLBACK": 3.5,
    "MODE_E_EARLY_MOMENTUM": 3.0,
    "MODE_F_CANDLE_REVERSAL": 3.5,
}
SMART_EXIT_HEALTH_EXIT_LEVEL = {
    "MODE_C_CAPITULATION": 35,
    "MODE_D_EMA_PULLBACK": 40,
    "MODE_E_EARLY_MOMENTUM": 55,
    "MODE_F_CANDLE_REVERSAL": 45,
}
SMART_EXIT_TIGHTEN_SCORE = 4
SMART_EXIT_VOLUME_MULT = 1.3
SMART_EXIT_ATR_STOP_MULT = 2.5
POST_TRADE_FORCE_FINALIZE_AFTER_MS = 4 * 60 * 60 * 1000

# ADAPTIVE INTELLIGENCE V4.8
ADAPTIVE_ENABLED_DEFAULT = True
ADAPTIVE_MIN_TRADES_MODE = 3
ADAPTIVE_MIN_TRADES_PATTERN = 2
ADAPTIVE_MIN_TRADES_TOKEN = 3
ADAPTIVE_MIN_MULT = 0.70
ADAPTIVE_MAX_MULT = 1.30
ADAPTIVE_HARD_BLOCK_MULT = 0.65
ADAPTIVE_RECENT_TRADES_WINDOW = 50
POST_TRADE_RECALC_HISTORICAL_ENABLED = True
POST_TRADE_DATA_MISSING_STATUS = "DATA_MISSING"

COOLDOWN_CANDLES_AFTER_SELL = 2

MIN_HOLD_CANDLES_AFTER_BUY = 1

last_candle_times = {symbol: None for symbol in SYMBOLS}

# Historique en mémoire pour commandes Telegram /analysis et /log
ANALYSIS_HISTORY = []
LOG_HISTORY = []
MAX_ANALYSIS_HISTORY = 500
MAX_LOG_HISTORY = 300

trade_state = {
    "in_position": False,
    "symbol": None,
    "entry_price": None,
    "entry_atr": None,
    "tp1_done": False,
    "highest_price": None,
    "trailing_stop": None,
    "cooldown_until_candle": None,
    "entry_candle_time": None,

    # Infos pour résumé final Telegram
    "entry_balance": None,
    "signal_mode": None,
    "score": None,
    "pattern_score": None,
    "bull_flag": None,
    "ascending_triangle": None,
    "compression_breakout": None,
    "higher_lows": None,
    "mode_c_reversal": None,
    "mode_c_drop_pct": None,
    "mode_c_volume_ok": None,
    "mode_c_wick_ok": None,
    "mode_d_entry_ema_low": None,
    "mode_d_target_ema_high": None,
    "mode_d_type": None,
    "mode_e_volume_ratio": None,
    "mode_e_body_ratio": None,
    "mode_e_intrabar": None,
    "mode_e_intrabar_move_pct": None,
    "mode_f_pattern": None,
    "mode_f_score": None,
    "mode_f_details_text": None,
    "smart_exit_score": None,
    "smart_exit_health": None,
    "tp1_price": None,
    "tp1_exec_price": None,
    "tp1_qty": None,
}

META_BY_SYMBOL = {}
SIZE_DECIMALS_BY_SYMBOL = {symbol: 2 for symbol in SYMBOLS}
LAST_ENTRY_PRICE_BY_SYMBOL = {symbol: None for symbol in SYMBOLS}
LAST_SELL_REASON = None
TELEGRAM_LAST_UPDATE_ID = None



DEFAULT_BOT_CONFIG = {
    "modes": {
        "MODE_A_CLASSIQUE": True,
        "MODE_B_PATTERN": True,
        "MODE_C_CAPITULATION": True,
        "MODE_D_EMA_PULLBACK": True,
        "MODE_E_EARLY_MOMENTUM": True,
        "MODE_F_CANDLE_REVERSAL": True,
    },
    "symbols": {
        "SOL": True,
        "XRP": True,
        "kSHIB": True,
    },
    "dashboard": {
        "refresh_seconds": 10
    }
}


def load_bot_config():
    try:
        if BOT_CONFIG_PATH.exists():
            with open(BOT_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        else:
            cfg = {}

        merged = json.loads(json.dumps(DEFAULT_BOT_CONFIG))

        for section in ["modes", "symbols", "dashboard"]:
            if isinstance(cfg.get(section), dict):
                merged[section].update(cfg[section])

        # Sécurité : si un nouveau symbole existe dans SYMBOLS, on l'ajoute actif par défaut
        for symbol in SYMBOLS:
            merged["symbols"].setdefault(symbol, True)

        return merged

    except Exception as e:
        print("[ERREUR CONFIG LOAD]", e)
        return json.loads(json.dumps(DEFAULT_BOT_CONFIG))


def save_bot_config(cfg):
    try:
        with open(BOT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print("[ERREUR CONFIG SAVE]", e)
        return False


def is_mode_enabled(mode_name):
    cfg = load_bot_config()
    return bool(cfg.get("modes", {}).get(mode_name, True))


def is_symbol_enabled(symbol):
    cfg = load_bot_config()
    return bool(cfg.get("symbols", {}).get(symbol, True))


def get_enabled_symbols():
    cfg = load_bot_config()
    return [s for s in SYMBOLS if bool(cfg.get("symbols", {}).get(s, True))]


def dashboard_auth_ok(handler):
    if not DASHBOARD_TOKEN:
        return True

    parsed = urlparse(handler.path)
    query = parse_qs(parsed.query)

    token_query = query.get("token", [""])[0]
    token_header = handler.headers.get("X-Dashboard-Token", "")

    return token_query == DASHBOARD_TOKEN or token_header == DASHBOARD_TOKEN


def json_safe(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    return value



POST_TRADE_HORIZONS = [
    ("15m", 15 * 60 * 1000),
    ("30m", 30 * 60 * 1000),
    ("1h", 60 * 60 * 1000),
    ("2h", 2 * 60 * 60 * 1000),
    ("4h", 4 * 60 * 60 * 1000),
]


def ensure_post_trade_files():
    try:
        if not POST_TRADE_CSV_PATH.exists():
            cols = [
                "date", "symbol", "mode", "exit_reason",
                "entry", "exit", "real_pnl_usdc", "pnl_at_exit_pct",
                "pnl_15m_pct", "pnl_30m_pct", "pnl_1h_pct", "pnl_2h_pct", "pnl_4h_pct",
                "max_gain_after_exit_pct", "max_loss_after_exit_pct",
                "exit_efficiency_pct", "verdict", "status",
            ]
            pd.DataFrame(columns=cols).to_csv(POST_TRADE_CSV_PATH, index=False)
            print("[POST TRADE] post_trade_analysis.csv créé")

        if not POST_TRADE_PENDING_PATH.exists():
            with open(POST_TRADE_PENDING_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)
            print("[POST TRADE] post_trade_pending.json créé")

    except Exception as e:
        print("[ERREUR POST TRADE ENSURE]", e)


def ensure_post_trade_csv():
    ensure_post_trade_files()


def load_pending_post_trades():
    try:
        if POST_TRADE_PENDING_PATH.exists():
            with open(POST_TRADE_PENDING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        print("[ERREUR POST TRADE LOAD]", e)
    return []


def save_pending_post_trades(items):
    try:
        with open(POST_TRADE_PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[ERREUR POST TRADE SAVE]", e)


def add_post_trade_tracking(symbol, exit_price, exit_reason, final_balance):
    try:
        ensure_post_trade_files()

        entry = trade_state.get("entry_price")
        if not entry:
            print("[POST TRADE] Non lancé : entry_price manquant dans trade_state")
            return

        entry_balance = trade_state.get("entry_balance")
        real_pnl = None
        if entry_balance is not None and final_balance is not None:
            real_pnl = round(final_balance - entry_balance, 6)

        now_ms = int(time.time() * 1000)

        item = {
            "id": f"{symbol}_{now_ms}",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "mode": trade_state.get("signal_mode"),
            "exit_reason": exit_reason,
            "entry": float(entry),
            "exit": float(exit_price),
            "real_pnl_usdc": real_pnl,
            "pnl_at_exit_pct": round(((float(exit_price) - float(entry)) / float(entry)) * 100, 4),
            "exit_time_ms": now_ms,
            "horizons": {},
            "max_price": float(exit_price),
            "min_price": float(exit_price),
            "status": "PENDING",
        }

        pending = load_pending_post_trades()
        pending.append(item)
        save_pending_post_trades(pending)

        print(f"[POST TRADE] Tracking lancé pour {symbol} après sortie {exit_reason}")
        print(f"[POST TRADE] Pending total = {len(pending)}")

    except Exception as e:
        print("[ERREUR POST TRADE ADD]", e)



def add_post_trade_tracking_direct(
    symbol,
    mode,
    entry_price,
    exit_price,
    exit_reason,
    entry_balance=None,
    final_balance=None,
):
    """
    Version robuste du Post Trade Analyzer.
    Elle ne dépend pas de trade_state, donc elle fonctionne même si le state est nettoyé.
    """
    try:
        ensure_post_trade_files()

        if entry_price is None or exit_price is None:
            print("[POST TRADE DIRECT] Non lancé : entry ou exit manquant")
            return False

        entry_f = float(entry_price)
        exit_f = float(exit_price)

        real_pnl = None
        if entry_balance is not None and final_balance is not None:
            try:
                real_pnl = round(float(final_balance) - float(entry_balance), 6)
            except Exception:
                real_pnl = None

        now_ms = int(time.time() * 1000)

        item = {
            "id": f"{symbol}_{now_ms}",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "mode": mode,
            "exit_reason": exit_reason,
            "entry": entry_f,
            "exit": exit_f,
            "real_pnl_usdc": real_pnl,
            "pnl_at_exit_pct": round(((exit_f - entry_f) / entry_f) * 100, 4),
            "exit_time_ms": now_ms,
            "horizons": {},
            "max_price": exit_f,
            "min_price": exit_f,
            "status": "PENDING",
        }

        pending = load_pending_post_trades()

        # Anti-doublon simple : même symbole + même sortie dans les 10 dernières secondes
        for old in pending[-5:]:
            if (
                old.get("symbol") == symbol
                and abs(float(old.get("exit", 0)) - exit_f) < 1e-9
                and abs(int(old.get("exit_time_ms", 0)) - now_ms) < 10000
            ):
                print("[POST TRADE DIRECT] Doublon évité")
                return True

        pending.append(item)
        save_pending_post_trades(pending)

        print(f"[POST TRADE DIRECT] Tracking lancé pour {symbol} après sortie {exit_reason}")
        print(f"[POST TRADE DIRECT] Pending total = {len(pending)}")
        return True

    except Exception as e:
        print("[ERREUR POST TRADE DIRECT]", e)
        return False



def finalize_post_trade_item(item):
    try:
        ensure_post_trade_files()

        entry = float(item["entry"])
        exit_price = float(item["exit"])
        max_price = float(item.get("max_price", exit_price))
        min_price = float(item.get("min_price", exit_price))

        max_gain_after = ((max_price - exit_price) / exit_price) * 100
        max_loss_after = ((min_price - exit_price) / exit_price) * 100

        pnl_at_exit = float(item.get("pnl_at_exit_pct", 0))
        max_possible_from_entry = ((max_price - entry) / entry) * 100

        if max_possible_from_entry > 0:
            efficiency = (pnl_at_exit / max_possible_from_entry) * 100
            efficiency = max(min(efficiency, 100), -100)
        else:
            efficiency = None

        if max_gain_after >= 0.8:
            verdict = "SORTIE_TROP_TOT"
        elif max_loss_after <= -0.8 and max_gain_after < 0.4:
            verdict = "BONNE_SORTIE"
        else:
            verdict = "NEUTRE"

        row = {
            "date": item.get("date"),
            "symbol": item.get("symbol"),
            "mode": item.get("mode"),
            "exit_reason": item.get("exit_reason"),
            "entry": item.get("entry"),
            "exit": item.get("exit"),
            "real_pnl_usdc": item.get("real_pnl_usdc"),
            "pnl_at_exit_pct": item.get("pnl_at_exit_pct"),
            "pnl_15m_pct": item.get("horizons", {}).get("15m"),
            "pnl_30m_pct": item.get("horizons", {}).get("30m"),
            "pnl_1h_pct": item.get("horizons", {}).get("1h"),
            "pnl_2h_pct": item.get("horizons", {}).get("2h"),
            "pnl_4h_pct": item.get("horizons", {}).get("4h"),
            "max_gain_after_exit_pct": round(max_gain_after, 4),
            "max_loss_after_exit_pct": round(max_loss_after, 4),
            "exit_efficiency_pct": round(efficiency, 2) if efficiency is not None else None,
            "verdict": verdict,
            "status": "DONE",
        }

        df = pd.read_csv(POST_TRADE_CSV_PATH) if POST_TRADE_CSV_PATH.exists() else pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(POST_TRADE_CSV_PATH, index=False)

        print(f"[POST TRADE] Analyse finalisée {item.get('symbol')} verdict={verdict}")

    except Exception as e:
        print("[ERREUR POST TRADE FINALIZE]", e)


def update_post_trade_tracking(symbol, current_price):
    try:
        force_finalize_expired_post_trades()
        pending = load_pending_post_trades()
        if not pending:
            return

        now_ms = int(time.time() * 1000)
        remaining = []

        for item in pending:
            if item.get("symbol") != symbol:
                remaining.append(item)
                continue

            exit_price = float(item["exit"])
            current_price_f = float(current_price)

            item["max_price"] = max(float(item.get("max_price", exit_price)), current_price_f)
            item["min_price"] = min(float(item.get("min_price", exit_price)), current_price_f)

            elapsed = now_ms - int(item.get("exit_time_ms", now_ms))

            for label, horizon_ms in POST_TRADE_HORIZONS:
                if elapsed >= horizon_ms and label not in item.get("horizons", {}):
                    item.setdefault("horizons", {})[label] = round(((current_price_f - exit_price) / exit_price) * 100, 4)

            if elapsed >= POST_TRADE_HORIZONS[-1][1]:
                finalize_post_trade_item(item)
            else:
                remaining.append(item)

        save_pending_post_trades(remaining)

    except Exception as e:
        print("[ERREUR POST TRADE UPDATE]", e)


def load_post_trade_dataframe():
    try:
        ensure_post_trade_files()
        return pd.read_csv(POST_TRADE_CSV_PATH)
    except Exception:
        return pd.DataFrame()




def build_trade_ai_comment(row):
    """
    Analyse simple et explicable d'un trade.
    Ne modifie pas les décisions du bot : uniquement dashboard.
    """
    try:
        mode = str(row.get("mode", ""))
        symbol = str(row.get("symbol", ""))
        pnl = float(row.get("real_pnl_usdc", 0) or 0)
        pnl_pct = float(row.get("pnl_price_pct", 0) or 0)
        exit_reason = str(row.get("exit_reason", ""))
        pattern_f = str(row.get("mode_f_pattern", "") or "")
        details_f = str(row.get("mode_f_details_text", "") or "")

        verdict = "NEUTRE"
        if pnl > 0:
            verdict = "TRADE GAGNANT"
        elif pnl < 0:
            verdict = "TRADE PERDANT"

        comments = []
        comments.append(f"Verdict: {verdict}.")
        comments.append(f"Mode utilisé: {mode} sur {symbol}.")

        if "MODE_F" in mode:
            if pattern_f:
                comments.append(f"Entrée Mode F détectée sur pattern: {format_mode_f_patterns(pattern_f)}.")
            if details_f:
                comments.append("Le pattern détecté indique un possible retournement haussier, mais il doit idéalement être confirmé par volume, OBV ou reprise EMA20.")
            if pnl < 0:
                comments.append("Ce trade Mode F perdant suggère que le retournement n'a pas été confirmé suffisamment vite.")
                comments.append("À surveiller: volume faible, prix sous EMA20 ou OBV encore fragile au moment de l'entrée.")

        if "MODE_C" in mode:
            comments.append("Mode C = tentative de rebond après capitulation. Ce mode peut acheter tôt, mais doit être surveillé par Smart Exit.")
            if pnl < 0:
                comments.append("Le rebond n'a pas pris. Un filtre volume/OBV plus strict pourrait réduire ce type d'entrée.")

        if "MODE_E" in mode:
            comments.append("Mode E = impulsion précoce. Le point clé est de vérifier si le volume et l'OBV confirment réellement l'accélération.")
            if pnl < 0:
                comments.append("La perte peut indiquer un faux départ d'impulsion.")

        if "EMA20" in exit_reason:
            comments.append("Sortie liée à EMA20 : le trade n'a pas réussi à se maintenir au-dessus du support dynamique court terme.")
        if "TRAIL" in exit_reason:
            comments.append("Sortie trailing : le bot a protégé une partie du mouvement après progression.")
        if "ATR_STOP" in exit_reason or "STOP" in exit_reason:
            comments.append("Sortie stop : le scénario initial a été invalidé par la volatilité.")

        if pnl_pct > 1:
            comments.append("Trade propre : le mouvement a dépassé +1%, ce type de configuration mérite d'être comparé aux autres trades du même mode.")
        elif pnl_pct < -0.5:
            comments.append("Perte significative : à comparer avec le contexte d'entrée pour trouver le filtre manquant.")
        else:
            comments.append("Trade quasi neutre : le coût réel vient surtout du spread/frais/slippage.")

        return "\n".join(comments)

    except Exception as e:
        return f"Analyse IA indisponible: {e}"


def compute_ai_recommendations():
    """
    Génère des recommandations simples depuis l'historique réel.
    Objectif : aide à la décision, pas auto-trading.
    """
    recs = []
    try:
        df = load_trades_dataframe()
        if df.empty:
            return ["Pas encore assez de trades pour générer des recommandations IA."]

        df = df.copy()
        df["real_pnl_usdc"] = pd.to_numeric(df.get("real_pnl_usdc"), errors="coerce").fillna(0)
        df["pnl_price_pct"] = pd.to_numeric(df.get("pnl_price_pct"), errors="coerce").fillna(0)

        total = len(df)
        if total < 5:
            recs.append(f"Seulement {total} trade(s) enregistré(s). Les recommandations restent peu fiables.")
        else:
            recs.append(f"Analyse basée sur {total} trade(s).")

        # Par mode
        if "mode" in df.columns:
            by_mode = df.groupby("mode")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            if not by_mode.empty:
                best = by_mode.sort_values("sum", ascending=False).head(1)
                worst = by_mode.sort_values("sum", ascending=True).head(1)
                for mode, row in best.iterrows():
                    recs.append(f"Mode le plus rentable actuellement: {mode} avec {row['sum']:.3f} USDC sur {int(row['count'])} trade(s).")
                for mode, row in worst.iterrows():
                    if row["sum"] < 0:
                        recs.append(f"Mode à surveiller: {mode} perd {abs(row['sum']):.3f} USDC sur {int(row['count'])} trade(s).")

        # Par token
        if "symbol" in df.columns:
            by_symbol = df.groupby("symbol")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            if not by_symbol.empty:
                worst_sym = by_symbol.sort_values("sum", ascending=True).head(1)
                for sym, row in worst_sym.iterrows():
                    if row["sum"] < 0:
                        recs.append(f"Token à surveiller: {sym} est négatif de {row['sum']:.3f} USDC.")

        # Patterns Mode F
        if "mode_f_pattern" in df.columns:
            fdf = df[df["mode_f_pattern"].fillna("").astype(str) != ""]
            if not fdf.empty:
                pattern_rows = []
                for _, r in fdf.iterrows():
                    patterns = str(r.get("mode_f_pattern", "")).split(",")
                    for p in patterns:
                        p = p.strip()
                        if p:
                            pattern_rows.append({"pattern": p, "pnl": r["real_pnl_usdc"]})
                if pattern_rows:
                    pdf = pd.DataFrame(pattern_rows)
                    perf = pdf.groupby("pattern")["pnl"].agg(["count", "sum", "mean"])
                    bestp = perf.sort_values("mean", ascending=False).head(1)
                    worstp = perf.sort_values("mean", ascending=True).head(1)
                    for p, row in bestp.iterrows():
                        recs.append(f"Pattern Mode F le plus prometteur: {mode_f_pattern_label(p)} | moyenne {row['mean']:.3f} USDC.")
                    for p, row in worstp.iterrows():
                        if row["mean"] < 0:
                            recs.append(f"Pattern Mode F à filtrer/prudence: {mode_f_pattern_label(p)} | moyenne {row['mean']:.3f} USDC.")

        # Post Trade Analyzer
        try:
            pending_items = load_pending_post_trades()
            missing = [x for x in pending_items if str(x.get("status","")).startswith("DATA_MISSING") or str(x.get("status","")).startswith("PENDING_HISTORY")]
            if missing:
                recs.append(f"PostTrade: {len(missing)} analyse(s) attendent des bougies historiques ou ont des données manquantes.")

            post_df = load_post_trade_dataframe()
            if not post_df.empty and "verdict" in post_df.columns:
                too_early = int((post_df["verdict"] == "SORTIE_TROP_TOT").sum())
                good_exit = int((post_df["verdict"] == "BONNE_SORTIE").sum())
                if too_early > good_exit:
                    recs.append("Les sorties semblent parfois trop précoces : envisager trailing plus large ou Smart Exit moins sensible.")
                elif good_exit > too_early:
                    recs.append("Les sorties semblent globalement protectrices : conserver la logique actuelle pour l'instant.")
        except Exception:
            pass

        if len(recs) == 1:
            recs.append("Accumule encore des trades pour obtenir des recommandations plus fiables.")

        return recs[:12]

    except Exception as e:
        return [f"Erreur IA recommandations: {e}"]


def enrich_recent_trades_for_dashboard(rows):
    enriched = []
    for idx, row in enumerate(rows):
        r = dict(row)
        r["_trade_id"] = idx
        r["_ai_comment"] = build_trade_ai_comment(r)
        if r.get("mode_f_pattern"):
            r["_mode_f_pattern_label"] = format_mode_f_patterns(r.get("mode_f_pattern"))
            r["_mode_f_pattern_details"] = format_mode_f_pattern_details(r.get("mode_f_pattern"))
        else:
            r["_mode_f_pattern_label"] = "-"
            r["_mode_f_pattern_details"] = ""
        enriched.append(r)
    return enriched




def clamp_adaptive(x, lo=ADAPTIVE_MIN_MULT, hi=ADAPTIVE_MAX_MULT):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return 1.0

def default_adaptive_config():
    return {
        "enabled": ADAPTIVE_ENABLED_DEFAULT,
        "mode_multipliers": {},
        "pattern_multipliers": {},
        "token_multipliers": {},
        "mode_stats": {},
        "pattern_stats": {},
        "token_stats": {},
        "last_updated": None,
    }

def load_adaptive_config():
    try:
        if ADAPTIVE_CONFIG_PATH.exists():
            with open(ADAPTIVE_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = default_adaptive_config()
            if isinstance(data, dict):
                cfg.update(data)
            return cfg
    except Exception as e:
        print("[ADAPTIVE] load error", e)
    return default_adaptive_config()

def save_adaptive_config(cfg):
    try:
        with open(ADAPTIVE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[ADAPTIVE] save error", e)

def adaptive_perf_to_multiplier(count, total_pnl, avg_pnl, min_trades):
    try:
        count = int(count)
        total_pnl = float(total_pnl)
        avg_pnl = float(avg_pnl)
        if count < min_trades:
            return 1.0
        mult = 1.0
        mult += max(min(avg_pnl * 0.70, 0.18), -0.18)
        mult += max(min(total_pnl * 0.08, 0.10), -0.10)
        return round(clamp_adaptive(mult), 3)
    except Exception:
        return 1.0

def rebuild_adaptive_config_from_trades():
    try:
        cfg = load_adaptive_config()
        df = load_trades_dataframe()
        if df.empty:
            save_adaptive_config(cfg)
            return cfg

        df = df.copy().tail(ADAPTIVE_RECENT_TRADES_WINDOW)
        df["real_pnl_usdc"] = pd.to_numeric(df.get("real_pnl_usdc"), errors="coerce").fillna(0)

        mode_mult, mode_stats = {}, {}
        if "mode" in df.columns:
            for mode, g in df.groupby("mode"):
                pnl = g["real_pnl_usdc"]
                count = int(len(g)); total = float(pnl.sum()); avg = float(pnl.mean()) if count else 0
                wins = int((pnl > 0).sum()); winrate = round((wins/count)*100,2) if count else 0
                mult = adaptive_perf_to_multiplier(count,total,avg,ADAPTIVE_MIN_TRADES_MODE)
                mode_mult[str(mode)] = mult
                mode_stats[str(mode)] = {"trades":count,"wins":wins,"winrate":winrate,"pnl":round(total,4),"avg":round(avg,4),"multiplier":mult}

        token_mult, token_stats = {}, {}
        if "symbol" in df.columns:
            for symbol, g in df.groupby("symbol"):
                pnl = g["real_pnl_usdc"]
                count = int(len(g)); total = float(pnl.sum()); avg = float(pnl.mean()) if count else 0
                wins = int((pnl > 0).sum()); winrate = round((wins/count)*100,2) if count else 0
                mult = adaptive_perf_to_multiplier(count,total,avg,ADAPTIVE_MIN_TRADES_TOKEN)
                token_mult[str(symbol)] = mult
                token_stats[str(symbol)] = {"trades":count,"wins":wins,"winrate":winrate,"pnl":round(total,4),"avg":round(avg,4),"multiplier":mult}

        pattern_rows=[]
        if "mode_f_pattern" in df.columns:
            for _, r in df.iterrows():
                for p in str(r.get("mode_f_pattern","") or "").split(","):
                    p=p.strip()
                    if p:
                        pattern_rows.append({"pattern":p,"pnl":float(r.get("real_pnl_usdc",0) or 0)})
        pattern_mult, pattern_stats = {}, {}
        if pattern_rows:
            pdf=pd.DataFrame(pattern_rows)
            for pat,g in pdf.groupby("pattern"):
                pnl=g["pnl"]; count=int(len(g)); total=float(pnl.sum()); avg=float(pnl.mean()) if count else 0
                wins=int((pnl>0).sum()); winrate=round((wins/count)*100,2) if count else 0
                mult=adaptive_perf_to_multiplier(count,total,avg,ADAPTIVE_MIN_TRADES_PATTERN)
                pattern_mult[str(pat)]=mult
                label=mode_f_pattern_label(pat) if "mode_f_pattern_label" in globals() else pat
                pattern_stats[str(pat)]={"trades":count,"wins":wins,"winrate":winrate,"pnl":round(total,4),"avg":round(avg,4),"multiplier":mult,"label":label}

        cfg["mode_multipliers"]=mode_mult
        cfg["token_multipliers"]=token_mult
        cfg["pattern_multipliers"]=pattern_mult
        cfg["mode_stats"]=mode_stats
        cfg["token_stats"]=token_stats
        cfg["pattern_stats"]=pattern_stats
        cfg["last_updated"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_adaptive_config(cfg)
        return cfg
    except Exception as e:
        print("[ADAPTIVE] rebuild error", e)
        return load_adaptive_config()

def get_adaptive_multiplier(symbol, mode, details=None):
    try:
        cfg = load_adaptive_config()
        if not cfg.get("enabled", True):
            return 1.0, {"enabled": False}
        mode_mult=float(cfg.get("mode_multipliers",{}).get(str(mode),1.0))
        token_mult=float(cfg.get("token_multipliers",{}).get(str(symbol),1.0))
        pattern_mult=1.0
        if details:
            pats=[p for p in str(details.get("mode_f_pattern","") or "").split(",") if p]
            vals=[float(cfg.get("pattern_multipliers",{}).get(p,1.0)) for p in pats]
            if vals: pattern_mult=min(vals)
        final=clamp_adaptive(mode_mult*token_mult*pattern_mult)
        return final, {"enabled":True,"mode_mult":round(mode_mult,3),"token_mult":round(token_mult,3),"pattern_mult":round(pattern_mult,3),"final_mult":round(final,3)}
    except Exception as e:
        return 1.0, {"enabled": False, "error": str(e)}

def should_adaptive_block(symbol, mode, details=None):
    mult, info = get_adaptive_multiplier(symbol, mode, details)
    return mult <= ADAPTIVE_HARD_BLOCK_MULT, mult, info

def build_adaptive_message():
    try:
        cfg = rebuild_adaptive_config_from_trades()
        msg = "🧠 <b>ADAPTIVE INTELLIGENCE V4.8</b>\n"
        msg += f"Enabled: {cfg.get('enabled')}\nDernière MAJ: {cfg.get('last_updated')}\n\n"
        msg += "<b>Modes</b>\n"
        for k,v in cfg.get("mode_stats",{}).items():
            msg += f"- {k}: x{v.get('multiplier')} | {v.get('trades')} trades | PnL {v.get('pnl')} | WR {v.get('winrate')}%\n"
        msg += "\n<b>Tokens</b>\n"
        for k,v in cfg.get("token_stats",{}).items():
            msg += f"- {k}: x{v.get('multiplier')} | {v.get('trades')} trades | PnL {v.get('pnl')} | WR {v.get('winrate')}%\n"
        msg += "\n<b>Patterns</b>\n"
        for k,v in cfg.get("pattern_stats",{}).items():
            msg += f"- {v.get('label',k)}: x{v.get('multiplier')} | {v.get('trades')} trades | PnL {v.get('pnl')} | WR {v.get('winrate')}%\n"
        return msg
    except Exception as e:
        return f"⚠️ Erreur adaptive: {e}"


def compute_dashboard_stats():
    force_finalize_expired_post_trades()
    df = load_trades_dataframe()
    balance = round(get_balance(), 2)

    result = {
        "balance": balance,
        "open_position": get_open_position(),
        "config": load_bot_config(),
        "summary": {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0,
            "pnl_total": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
        },
        "by_mode": [],
        "by_symbol": [],
        "by_exit_reason": [],
        "recent_trades": [],
        "post_trade_analysis": [],
        "post_trade_pending": [],
        "post_trade_summary": {
            "total": 0,
            "pending": 0,
            "sortie_trop_tot": 0,
            "bonne_sortie": 0,
            "neutre": 0,
        },
        "ai_recommendations": compute_ai_recommendations(),
        "adaptive": rebuild_adaptive_config_from_trades(),
        "analysis_history": ANALYSIS_HISTORY[-80:],
        "log_history": LOG_HISTORY[-120:],
    }

    try:
        pending_items = load_pending_post_trades()
        result["post_trade_pending"] = pending_items[-50:]

        post_df = load_post_trade_dataframe()
        if not post_df.empty:
            result["post_trade_analysis"] = post_df.tail(50).fillna("").to_dict(orient="records")
            verdicts = post_df.get("verdict", pd.Series(dtype=str)).fillna("")
            result["post_trade_summary"] = {
                "total": int(len(post_df)),
                "pending": int(len(pending_items)),
                "sortie_trop_tot": int((verdicts == "SORTIE_TROP_TOT").sum()),
                "bonne_sortie": int((verdicts == "BONNE_SORTIE").sum()),
                "neutre": int((verdicts == "NEUTRE").sum()),
            }
        else:
            result["post_trade_summary"]["pending"] = int(len(pending_items))
    except Exception as e:
        print("[ERREUR POST TRADE DASHBOARD]", e)

    if df.empty:
        return result

    df = df.copy()
    df["real_pnl_usdc"] = pd.to_numeric(df.get("real_pnl_usdc"), errors="coerce").fillna(0)
    df["pnl_price_pct"] = pd.to_numeric(df.get("pnl_price_pct"), errors="coerce").fillna(0)

    pnl = df["real_pnl_usdc"]
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = abs(float(pnl[pnl < 0].sum()))

    result["summary"] = {
        "trades": int(len(df)),
        "wins": wins,
        "losses": losses,
        "winrate": round((wins / len(df)) * 100, 2) if len(df) else 0,
        "pnl_total": round(float(pnl.sum()), 4),
        "avg_win": round(float(pnl[pnl > 0].mean()), 4) if wins else 0,
        "avg_loss": round(float(pnl[pnl < 0].mean()), 4) if losses else 0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
    }

    def grouped_stats(col):
        if col not in df.columns:
            return []

        rows = []
        for key, g in df.groupby(col, dropna=False):
            gpnl = g["real_pnl_usdc"]
            gwins = int((gpnl > 0).sum())
            glosses = int((gpnl < 0).sum())
            ggross_profit = float(gpnl[gpnl > 0].sum())
            ggross_loss = abs(float(gpnl[gpnl < 0].sum()))

            rows.append({
                "name": str(key),
                "trades": int(len(g)),
                "wins": gwins,
                "losses": glosses,
                "winrate": round((gwins / len(g)) * 100, 2) if len(g) else 0,
                "pnl": round(float(gpnl.sum()), 4),
                "avg_pnl": round(float(gpnl.mean()), 4) if len(g) else 0,
                "profit_factor": round(ggross_profit / ggross_loss, 3) if ggross_loss else None,
            })

        return sorted(rows, key=lambda x: x["pnl"], reverse=True)

    result["by_mode"] = grouped_stats("mode")
    result["by_symbol"] = grouped_stats("symbol")
    result["by_exit_reason"] = grouped_stats("exit_reason")

    recent = df.tail(50).fillna("").to_dict(orient="records")
    safe_recent = [
        {k: json_safe(v) for k, v in row.items()} for row in recent
    ]
    result["recent_trades"] = enrich_recent_trades_for_dashboard(safe_recent)

    return result


DASHBOARD_HTML = r"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>Trading Bot Dashboard</title>
  <style>
    :root{--bg:#07111f;--card:#101827;--card2:#172033;--txt:#e5e7eb;--muted:#9ca3af;--green:#22c55e;--red:#ef4444;--blue:#38bdf8;--border:#263348}
    *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
    body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--txt);padding-bottom:env(safe-area-inset-bottom)}
    header{padding:14px 14px 10px;padding-top:calc(14px + env(safe-area-inset-top));border-bottom:1px solid var(--border);position:sticky;top:0;background:rgba(7,17,31,.96);backdrop-filter:blur(10px);z-index:3}
    h1{margin:0 0 4px;font-size:20px;line-height:1.15} h3{margin:0 0 12px;font-size:17px}.sub{color:var(--muted);font-size:12px;line-height:1.35}
    .wrap{padding:12px;max-width:1300px;margin:auto}.grid{display:grid;gap:12px}.cards{grid-template-columns:repeat(4,minmax(0,1fr))}.two{grid-template-columns:repeat(2,minmax(0,1fr))}
    .card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:14px;box-shadow:0 6px 18px rgba(0,0,0,.18);overflow:hidden}
    .metric{font-size:25px;font-weight:800;margin-top:6px;letter-spacing:-.3px}.muted{color:var(--muted);font-size:13px}.pos{color:var(--green)}.neg{color:var(--red)}
    .scroll{overflow:auto;max-height:430px;-webkit-overflow-scrolling:touch;border-radius:10px}
    table{width:100%;border-collapse:collapse;font-size:13px;min-width:620px}
    th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
    th{color:var(--muted);font-weight:700;position:sticky;top:0;background:var(--card);z-index:1}
    .toggles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--card2);padding:13px 12px;border-radius:14px;border:1px solid #2b3950;min-height:48px;font-size:14px;font-weight:650}
    input[type=checkbox]{appearance:none;width:48px;height:28px;border-radius:999px;background:#4b5563;position:relative;flex:0 0 auto;transition:.18s;border:1px solid #64748b}
    input[type=checkbox]::after{content:"";width:22px;height:22px;border-radius:50%;background:white;position:absolute;top:2px;left:2px;transition:.18s;box-shadow:0 2px 5px rgba(0,0,0,.35)}
    input[type=checkbox]:checked{background:var(--green);border-color:var(--green)} input[type=checkbox]:checked::after{transform:translateX(20px)}
    button{background:var(--blue);border:0;padding:13px 16px;border-radius:13px;font-weight:800;cursor:pointer;color:#04121d;width:100%;font-size:15px;min-height:48px}
    pre{white-space:pre-wrap;font-size:12px;max-height:360px;overflow:auto;background:#070d18;padding:12px;border-radius:12px;color:#cbd5e1;line-height:1.35;-webkit-overflow-scrolling:touch}
    .mobileHint{display:none;color:var(--muted);font-size:12px;margin:4px 0 8px}
    tr.clickable{cursor:pointer}
    tr.clickable:hover{background:#1f2a3c}
    .modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:99;padding:16px;overflow:auto}
    .modalBox{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px;max-width:760px;margin:40px auto;box-shadow:0 20px 60px rgba(0,0,0,.45)}
    .modalClose{float:right;width:auto;min-height:36px;padding:8px 12px;background:#334155;color:#e5e7eb}
    .detailGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}
    .detailItem{background:var(--card2);padding:10px;border-radius:12px;border:1px solid #2b3950}
    .aiBox{background:#071827;border:1px solid #164e63;border-radius:12px;padding:12px;white-space:pre-wrap;line-height:1.35}
    @media(max-width:900px){.cards,.two{grid-template-columns:1fr}.wrap{padding:10px}.card{padding:13px;border-radius:15px}table{font-size:12px;min-width:720px}th,td{padding:9px 7px}.mobileHint{display:block}}
    @media(max-width:520px){h1{font-size:18px}h3{font-size:16px}.sub{font-size:11px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{font-size:22px}.card{padding:12px}.toggles{grid-template-columns:1fr}.toggle{font-size:15px;padding:14px 13px}.scroll{max-height:360px}pre{max-height:300px;font-size:11px}}
    @media(max-width:380px){.cards{grid-template-columns:1fr}.metric{font-size:24px}}
  </style>
</head>
<body>
<header><h1>🤖 Trading Bot</h1><div class="sub">Dashboard iPhone · V4.8 Adaptive Intelligence · Modes A/B/C/D/E/F/F · SOL / XRP / kSHIB</div></header>
<div class="wrap">
  <div class="grid cards">
    <div class="card"><div class="muted">Solde</div><div id="balance" class="metric">-</div></div>
    <div class="card"><div class="muted">PnL</div><div id="pnl" class="metric">-</div></div>
    <div class="card"><div class="muted">Winrate</div><div id="winrate" class="metric">-</div></div>
    <div class="card"><div class="muted">Trades</div><div id="trades" class="metric">-</div></div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">
      <h3>⚙️ Contrôle du bot</h3>
      <div class="muted" style="margin-bottom:8px">Modes</div><div id="modeToggles" class="toggles"></div>
      <div class="muted" style="margin:14px 0 8px">Tokens</div><div id="symbolToggles" class="toggles"></div>
      <div style="margin-top:14px"><button onclick="saveConfig()">Sauvegarder</button><div id="saveStatus" class="muted" style="margin-top:8px"></div></div>
    </div>
    <div class="card"><h3>📌 Position ouverte</h3><pre id="position">-</pre></div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card"><h3>📊 Par mode</h3><div class="mobileHint">Balaye le tableau vers la droite.</div><div class="scroll"><table id="byMode"></table></div></div>
    <div class="card"><h3>🪙 Par token</h3><div class="mobileHint">Balaye le tableau vers la droite.</div><div class="scroll"><table id="bySymbol"></table></div></div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card"><h3>🚪 Par sortie</h3><div class="mobileHint">Balaye le tableau vers la droite.</div><div class="scroll"><table id="byExit"></table></div></div>
    <div class="card"><h3>🧠 Signaux récents</h3><div class="mobileHint">Balaye le tableau vers la droite.</div><div class="scroll"><table id="analysis"></table></div></div>
  </div>
  <div class="card" style="margin-top:12px"><h3>📜 Derniers trades</h3><div class="mobileHint">Balaye le tableau vers la droite.</div><div class="scroll"><table id="recentTrades"></table></div></div>
  <div class="card" style="margin-top:12px">
    <h3>🔍 Post Trade Analyzer</h3>
    <div class="muted" id="postTradeSummary" style="margin-bottom:8px">-</div>
    <h3 style="margin-top:12px">⏳ Analyses en attente</h3>
    <div class="mobileHint">Ces lignes apparaissent juste après la clôture du trade.</div>
    <div class="scroll"><table id="postTradePending"></table></div>
    <h3 style="margin-top:14px">✅ Analyses terminées</h3>
    <div class="mobileHint">Balaye le tableau vers la droite.</div>
    <div class="scroll"><table id="postTrade"></table></div>
  </div>
  <div class="card" style="margin-top:12px">
    <h3>🧬 Adaptive Intelligence</h3>
    <div id="adaptiveBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🧠 IA Coach</h3>
    <div id="aiCoach" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px"><h3>📋 Logs récents</h3><pre id="logs"></pre></div>

  <div id="tradeModal" class="modal">
    <div class="modalBox">
      <button class="modalClose" onclick="closeTradeModal()">Fermer</button>
      <h3>🔎 Analyse détaillée du trade</h3>
      <div id="tradeDetailContent"></div>
    </div>
  </div>
</div>
<script>
let currentConfig=null;
function cls(v){return Number(v)>=0?'pos':'neg'} function fmt(v,d=2){if(v===null||v===undefined||v==='')return'-';const n=Number(v);return isNaN(n)?v:n.toFixed(d)}
function shortMode(m){if(!m)return'-';return String(m).replace('MODE_','').replace('_CLASSIQUE','').replace('_PATTERN','').replace('_CAPITULATION','').replace('_EMA_PULLBACK','')}
function table(el,cols,rows,clickable=false){const head='<tr>'+cols.map(c=>`<th>${c.label}</th>`).join('')+'</tr>';const body=(rows||[]).map((r,i)=>{const click=clickable?` class="clickable" onclick="openTradeModal(${i})"`:'';return'<tr'+click+'>'+cols.map(c=>{let val=c.get?c.get(r):r[c.key];let klass=c.cls?c.cls(val,r):'';return`<td class="${klass}">${val??'-'}</td>`}).join('')+'</tr>'}).join('');document.getElementById(el).innerHTML=head+body}
let lastTrades=[];
function esc(x){return String(x??'-').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function renderAdaptive(a){
 if(!a){return '-'}
 let out=`Enabled: ${a.enabled}\nMAJ: ${a.last_updated||'-'}\n`;
 out+='\nModes:\n';
 Object.entries(a.mode_stats||{}).forEach(([k,v])=>{out+=`• ${k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 out+='\nTokens:\n';
 Object.entries(a.token_stats||{}).forEach(([k,v])=>{out+=`• ${k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 out+='\nPatterns:\n';
 Object.entries(a.pattern_stats||{}).forEach(([k,v])=>{out+=`• ${v.label||k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 return out;
}
function closeTradeModal(){document.getElementById('tradeModal').style.display='none'}
function openTradeModal(i){const r=lastTrades[i];if(!r)return;const html=`<div class="detailGrid">
<div class="detailItem"><b>Coin</b><br>${esc(r.symbol)}</div>
<div class="detailItem"><b>Mode</b><br>${esc(r.mode)}</div>
<div class="detailItem"><b>Entry</b><br>${esc(r.entry)}</div>
<div class="detailItem"><b>Exit</b><br>${esc(r.exit)}</div>
<div class="detailItem"><b>PnL %</b><br>${esc(r.pnl_price_pct)}</div>
<div class="detailItem"><b>PnL USDC</b><br>${esc(r.real_pnl_usdc)}</div>
<div class="detailItem"><b>Sortie</b><br>${esc(r.exit_reason)}</div>
<div class="detailItem"><b>Pattern F</b><br>${esc(r._mode_f_pattern_label||'-')}</div>
</div>
<h3 style="margin-top:16px">🕯️ Détail pattern</h3>
<div class="aiBox">${esc(r._mode_f_pattern_details||'Aucun pattern Mode F enregistré pour ce trade.')}</div>
<h3 style="margin-top:16px">🧠 Analyse IA</h3>
<div class="aiBox">${esc(r._ai_comment||'-')}</div>`;document.getElementById('tradeDetailContent').innerHTML=html;document.getElementById('tradeModal').style.display='block'}
function renderToggles(cfg){currentConfig=cfg;const modeLabels={MODE_A_CLASSIQUE:'Mode A',MODE_B_PATTERN:'Mode B',MODE_C_CAPITULATION:'Mode C',MODE_D_EMA_PULLBACK:'Mode D',MODE_E_EARLY_MOMENTUM:'Mode E',MODE_F_CANDLE_REVERSAL:'Mode F'};const modeDesc={MODE_A_CLASSIQUE:'Breakout classique tendance confirmée.',MODE_B_PATTERN:'Triangle / compression / patterns forts.',MODE_C_CAPITULATION:'Rebond après forte baisse.',MODE_D_EMA_PULLBACK:'Pullback EMA, sortie sous EMA20.',MODE_E_EARLY_MOMENTUM:'Impulsion volume + OBV. Peut acheter intrabar avant clôture 15m. Sortie 2 clôtures sous EMA20.',MODE_F_CANDLE_REVERSAL:'Patterns bougies haussiers : marteau, englobante, morning star, tweezer, inside breakout.'};document.getElementById('modeToggles').innerHTML=Object.keys(cfg.modes).map(k=>`<label class="toggle"><span>${modeLabels[k]||k}<br><small style="color:#9ca3af;font-weight:500">${modeDesc[k]||''}</small></span><input type="checkbox" data-section="modes" data-key="${k}" ${cfg.modes[k]?'checked':''}></label>`).join('');document.getElementById('symbolToggles').innerHTML=Object.keys(cfg.symbols).map(k=>`<label class="toggle"><span>${k}</span><input type="checkbox" data-section="symbols" data-key="${k}" ${cfg.symbols[k]?'checked':''}></label>`).join('')}
async function saveConfig(){document.querySelectorAll('input[type=checkbox]').forEach(i=>{currentConfig[i.dataset.section][i.dataset.key]=i.checked});const r=await fetch('/api/config'+window.location.search,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentConfig)});document.getElementById('saveStatus').textContent=r.ok?'✅ Configuration sauvegardée':'❌ Erreur sauvegarde';setTimeout(()=>document.getElementById('saveStatus').textContent='',2500);load()}
async function load(){const r=await fetch('/api/data'+window.location.search);const d=await r.json();document.getElementById('balance').textContent=fmt(d.balance,2);document.getElementById('pnl').innerHTML=`<span class="${cls(d.summary.pnl_total)}">${fmt(d.summary.pnl_total,4)}</span>`;document.getElementById('winrate').textContent=fmt(d.summary.winrate,1)+'%';document.getElementById('trades').textContent=d.summary.trades;document.getElementById('position').textContent=d.open_position?JSON.stringify(d.open_position,null,2):'Aucune position ouverte';renderToggles(d.config);
const perfCols=[{label:'Nom',key:'name'},{label:'Trades',key:'trades'},{label:'WR',get:r=>fmt(r.winrate,1)+'%'},{label:'PnL',get:r=>fmt(r.pnl,4),cls:v=>cls(v)},{label:'Avg',get:r=>fmt(r.avg_pnl,4),cls:v=>cls(v)},{label:'PF',get:r=>r.profit_factor===null?'∞':fmt(r.profit_factor,2)}];table('byMode',perfCols,d.by_mode);table('bySymbol',perfCols,d.by_symbol);table('byExit',perfCols,d.by_exit_reason);
table('analysis',[{label:'Heure',key:'time'},{label:'Coin',key:'symbol'},{label:'Sig',key:'signal'},{label:'Mode',get:r=>shortMode(r.mode)},{label:'Close',get:r=>fmt(r.close,5)},{label:'RSI',get:r=>fmt(r.rsi,1)},{label:'Score',get:r=>fmt(r.score,2)},{label:'A/B/C/D/E/F',get:r=>`${r.mode_a}/${r.mode_b}/${r.mode_c}/${r.mode_d}/${r.mode_e}/${r.mode_f}`},{label:'Pattern F',get:r=>r.mode_f_pattern||'-'}],(d.analysis_history||[]).slice(-40).reverse());
lastTrades=(d.recent_trades||[]).slice().reverse();
table('recentTrades',[{label:'Date',key:'date'},{label:'Coin',key:'symbol'},{label:'Mode',get:r=>shortMode(r.mode)},{label:'Entry',key:'entry'},{label:'Exit',key:'exit'},{label:'Sortie',key:'exit_reason'},{label:'TP1',key:'tp1_done'},{label:'PnL %',get:r=>fmt(r.pnl_price_pct,2),cls:v=>cls(v)},{label:'USDC',get:r=>fmt(r.real_pnl_usdc,4),cls:v=>cls(v)},{label:'Pattern F',get:r=>r._mode_f_pattern_label||r.mode_f_pattern||'-'}],lastTrades,true);
document.getElementById('adaptiveBox').textContent=renderAdaptive(d.adaptive);
document.getElementById('aiCoach').textContent=(d.ai_recommendations||[]).map(x=>'• '+x).join('\n')||'-';
const pts=d.post_trade_summary||{};
document.getElementById('postTradeSummary').textContent=`En attente ${pts.pending||0} · Terminées ${pts.total||0} · Trop tôt ${pts.sortie_trop_tot||0} · Bonnes sorties ${pts.bonne_sortie||0} · Neutres ${pts.neutre||0}`;
table('postTradePending',[
{label:'Date',key:'date'},
{label:'Coin',key:'symbol'},
{label:'Mode',get:r=>shortMode(r.mode)},
{label:'Sortie',key:'exit_reason'},
{label:'Entry',key:'entry'},
{label:'Exit',key:'exit'},
{label:'PnL sortie',get:r=>fmt(r.pnl_at_exit_pct,2),cls:v=>cls(v)},
{label:'Max suivi',get:r=>fmt(((Number(r.max_price||r.exit)-Number(r.exit))/Number(r.exit))*100,2),cls:v=>cls(v)},
{label:'Min suivi',get:r=>fmt(((Number(r.min_price||r.exit)-Number(r.exit))/Number(r.exit))*100,2),cls:v=>cls(v)},
{label:'Status',key:'status'},{label:'Raison',get:r=>r.missing_reason||''}
],(d.post_trade_pending||[]).slice().reverse());
table('postTrade',[
{label:'Date',key:'date'},
{label:'Coin',key:'symbol'},
{label:'Mode',get:r=>shortMode(r.mode)},
{label:'Sortie',key:'exit_reason'},
{label:'PnL réel',get:r=>fmt(r.pnl_at_exit_pct,2),cls:v=>cls(v)},
{label:'+15m',get:r=>fmt(r.pnl_15m_pct,2),cls:v=>cls(v)},
{label:'+30m',get:r=>fmt(r.pnl_30m_pct,2),cls:v=>cls(v)},
{label:'+1h',get:r=>fmt(r.pnl_1h_pct,2),cls:v=>cls(v)},
{label:'+2h',get:r=>fmt(r.pnl_2h_pct,2),cls:v=>cls(v)},
{label:'+4h',get:r=>fmt(r.pnl_4h_pct,2),cls:v=>cls(v)},
{label:'Max après',get:r=>fmt(r.max_gain_after_exit_pct,2),cls:v=>cls(v)},
{label:'Min après',get:r=>fmt(r.max_loss_after_exit_pct,2),cls:v=>cls(v)},
{label:'Verdict',key:'verdict'},{label:'Status',key:'status'}
],(d.post_trade_analysis||[]).slice().reverse());
document.getElementById('logs').textContent=(d.log_history||[]).slice(-80).join('\n')}
load();setInterval(load,10000);
</script>
</body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status, content, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if isinstance(content, str):
            content = content.encode("utf-8")

        self.wfile.write(content)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8")

    def do_GET(self):
        if not dashboard_auth_ok(self):
            return self._json({"error": "unauthorized"}, 401)

        parsed = urlparse(self.path)

        if parsed.path == "/":
            return self._send(200, DASHBOARD_HTML)

        if parsed.path == "/api/data":
            return self._json(compute_dashboard_stats())

        if parsed.path == "/api/config":
            return self._json(load_bot_config())

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not dashboard_auth_ok(self):
            return self._json({"error": "unauthorized"}, 401)

        parsed = urlparse(self.path)

        if parsed.path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                cfg = json.loads(body)
                base_cfg = load_bot_config()

                if isinstance(cfg.get("modes"), dict):
                    for k in base_cfg["modes"].keys():
                        if k in cfg["modes"]:
                            base_cfg["modes"][k] = bool(cfg["modes"][k])

                if isinstance(cfg.get("symbols"), dict):
                    for k in base_cfg["symbols"].keys():
                        if k in cfg["symbols"]:
                            base_cfg["symbols"][k] = bool(cfg["symbols"][k])

                save_bot_config(base_cfg)
                return self._json({"ok": True, "config": base_cfg})

            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)

        return self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        return


def start_dashboard_server():
    def _run():
        try:
            server = ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
            print(f"[DASHBOARD] http://0.0.0.0:{DASHBOARD_PORT}")
            server.serve_forever()
        except Exception as e:
            print("[ERREUR DASHBOARD]", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()



def notify_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("[ERREUR TELEGRAM]", e)




def add_log_line(line):
    """
    Stocke une ligne de log en mémoire pour la commande /log.
    """
    try:
        LOG_HISTORY.append(str(line))
        if len(LOG_HISTORY) > MAX_LOG_HISTORY:
            del LOG_HISTORY[:-MAX_LOG_HISTORY]
    except Exception:
        pass


def add_analysis_entry(symbol, last, current_price, details, signal_text):
    """
    Stocke les infos utiles d'une bougie clôturée pour /analysis.
    """
    try:
        ANALYSIS_HISTORY.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "symbol": symbol,
            "signal": signal_text,
            "close": float(last["close"]),
            "current": float(current_price),
            "ema20": float(last["ema20"]),
            "ema50": float(last["ema50"]),
            "rsi": float(last["rsi"]),
            "atr": float(last["atr"]),
            "vwap": float(last["vwap"]),
            "obv": float(last["obv"]),
            "obv_ma5": float(last["obv_ma5"]),
            "trend": bool(details.get("trend_ok")),
            "rsi_ok": bool(details.get("rsi_ok")),
            "vwap_ok": bool(details.get("vwap_ok")),
            "obv_ok": bool(details.get("obv_rising")),
            "breakout": bool(details.get("breakout_ok")),
            "green": bool(details.get("green_candle")),
            "close_above_ema20": bool(details.get("close_above_ema20")),
            "mode": details.get("signal_mode"),
            "mode_a": bool(details.get("mode_a_ok")),
            "mode_b": bool(details.get("mode_b_ok")),
            "mode_c": bool(details.get("mode_c_ok")),
            "mode_d": bool(details.get("mode_d_ok")),
            "mode_e": bool(details.get("mode_e_ok")),
            "mode_f": bool(details.get("mode_f_ok")),
            "mode_e_intrabar": bool(details.get("mode_e_intrabar")),
            "mode_f_pattern": details.get("mode_f_pattern_label"),
            "mode_f_details_text": details.get("mode_f_details_text"),
            "mode_d_d1": bool(details.get("mode_d_pullback_between_emas")),
            "mode_d_d2": bool(details.get("mode_d_cross_both_emas")),
            "pattern_score": float(details.get("pattern_score", 0)),
            "score": float(details.get("score", 0)),
            "bull_flag": bool(details.get("bull_flag")),
            "triangle": bool(details.get("ascending_triangle")),
            "compression": bool(details.get("compression_breakout")),
            "higher_lows": bool(details.get("higher_lows")),
            "mode_c_drop_pct": details.get("mode_c_drop_pct"),
        })

        if len(ANALYSIS_HISTORY) > MAX_ANALYSIS_HISTORY:
            del ANALYSIS_HISTORY[:-MAX_ANALYSIS_HISTORY]
    except Exception as e:
        print("[ERREUR ANALYSIS HISTORY]", e)


def split_telegram_message(text, max_len=3500):
    """
    Telegram limite la taille des messages. On découpe proprement.
    """
    chunks = []
    text = str(text)

    while len(text) > max_len:
        cut = text.rfind("\\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\\n")

    if text:
        chunks.append(text)

    return chunks


def send_long_telegram_message(text):
    for chunk in split_telegram_message(text):
        notify_telegram(chunk)


def build_analysis_message(limit=20):
    try:
        limit = int(limit)
    except Exception:
        limit = 20

    limit = max(5, min(limit, 120))

    if not ANALYSIS_HISTORY:
        return "📊 ANALYSIS\\nAucun historique disponible depuis le dernier redémarrage."

    msg = f"📊 <b>ANALYSIS {limit} dernières bougies par coin</b>\\n"
    msg += "Timeframe: 15m\\n"

    for symbol in SYMBOLS:
        rows = [x for x in ANALYSIS_HISTORY if x.get("symbol") == symbol][-limit:]

        msg += f"\\n<b>{symbol}</b>\\n"

        if not rows:
            msg += "Aucune donnée.\\n"
            continue

        for r in rows:
            mode = r.get("mode") or "None"
            sig = r.get("signal") or "HOLD"

            msg += (
                f"{r['time'][-5:]} | {sig} | "
                f"C={r['close']:.5f} | RSI={r['rsi']:.1f} | "
                f"E20={r['ema20']:.5f} E50={r['ema50']:.5f} | "
                f"VWAP={r['vwap']:.5f} | "
                f"T={r['trend']} V={r['vwap_ok']} OBV={r['obv_ok']} "
                f"BR={r['breakout']} G={r['green']} >E20={r.get('close_above_ema20')} | "
                f"A={r['mode_a']} B={r['mode_b']} C={r['mode_c']} D={r.get('mode_d')} "
                f"D1={r.get('mode_d_d1')} D2={r.get('mode_d_d2')} | "
                f"Pat={r['pattern_score']:.1f} Score={r['score']:.2f} | "
                f"Mode={mode}\\n"
            )

    return msg


def build_log_message(limit=100):
    try:
        limit = int(limit)
    except Exception:
        limit = 100

    limit = max(20, min(limit, 250))

    if not LOG_HISTORY:
        return "📋 LOG\\nAucun log disponible depuis le dernier redémarrage."

    rows = LOG_HISTORY[-limit:]

    return "📋 <b>DERNIERS LOGS</b>\\n" + "\\n".join(rows)



def send_telegram_document(file_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption
            }
            requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        print("[ERREUR TELEGRAM DOCUMENT]", e)


def ensure_trades_csv():
    if TRADES_CSV_PATH.exists():
        return

    headers = [
        "date",
        "symbol",
        "mode",
        "score",
        "pattern_score",
        "bull_flag",
        "ascending_triangle",
        "compression_breakout",
        "higher_lows",
        "mode_c_reversal",
        "mode_c_drop_pct",
        "mode_c_volume_ok",
        "mode_c_wick_ok",
        "mode_d_entry_ema_low",
        "mode_d_target_ema_high",
        "mode_d_type",
        "mode_e_volume_ratio",
        "mode_e_body_ratio",
        "mode_e_intrabar",
        "mode_e_intrabar_move_pct",
        "mode_f_pattern",
        "mode_f_score",
        "mode_f_details_text",
        "smart_exit_score",
        "smart_exit_health",
        "entry",
        "exit",
        "exit_reason",
        "tp1_done",
        "tp1_price",
        "tp1_exec_price",
        "pnl_price_pct",
        "balance_before",
        "balance_after",
        "real_pnl_usdc",
    ]

    with open(TRADES_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)


def append_trade_to_csv(symbol, exit_price, exit_reason, final_balance):
    ensure_trades_csv()

    entry = trade_state.get("entry_price")
    entry_balance = trade_state.get("entry_balance")

    if not entry:
        return None

    pnl_pct = ((exit_price - entry) / entry) * 100
    real_pnl = ""

    if entry_balance is not None:
        real_pnl = final_balance - entry_balance

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        symbol,
        trade_state.get("signal_mode"),
        trade_state.get("score"),
        trade_state.get("pattern_score"),
        trade_state.get("bull_flag"),
        trade_state.get("ascending_triangle"),
        trade_state.get("compression_breakout"),
        trade_state.get("higher_lows"),
        trade_state.get("mode_c_reversal"),
        trade_state.get("mode_c_drop_pct"),
        trade_state.get("mode_c_volume_ok"),
        trade_state.get("mode_c_wick_ok"),
        trade_state.get("mode_d_entry_ema_low"),
        trade_state.get("mode_d_target_ema_high"),
        trade_state.get("mode_d_type"),
        trade_state.get("mode_e_volume_ratio"),
        trade_state.get("mode_e_body_ratio"),
        trade_state.get("mode_e_intrabar"),
        trade_state.get("mode_e_intrabar_move_pct"),
        trade_state.get("mode_f_pattern"),
        trade_state.get("mode_f_score"),
        trade_state.get("mode_f_details_text"),
        trade_state.get("smart_exit_score"),
        trade_state.get("smart_exit_health"),
        round(entry, 8),
        round(exit_price, 8),
        exit_reason,
        trade_state.get("tp1_done"),
        trade_state.get("tp1_price"),
        trade_state.get("tp1_exec_price"),
        round(pnl_pct, 4),
        entry_balance,
        final_balance,
        round(real_pnl, 6) if real_pnl != "" else "",
    ]

    with open(TRADES_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)

    return row


def load_trades_dataframe():
    ensure_trades_csv()

    try:
        df = pd.read_csv(TRADES_CSV_PATH)
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        print("[ERREUR CSV READ]", e)
        return pd.DataFrame()


def build_report_message(df=None, title="📊 RAPPORT TRADES"):
    if df is None:
        df = load_trades_dataframe()

    if df.empty:
        return f"{title}\nAucun trade enregistré."

    total = len(df)
    pnl = pd.to_numeric(df["real_pnl_usdc"], errors="coerce").fillna(0)
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    win_rate = (wins / total) * 100 if total else 0
    pnl_total = pnl.sum()

    msg = (
        f"{title}\n"
        f"Trades: {total}\n"
        f"Gagnants: {wins}\n"
        f"Perdants: {losses}\n"
        f"Win rate: {win_rate:.1f}%\n"
        f"PnL total réel: {pnl_total:.3f} USDC\n"
    )

    try:
        by_symbol = df.assign(real_pnl_usdc=pnl).groupby("symbol")["real_pnl_usdc"].sum()
        msg += "\nPar coin:\n"
        for symbol, value in by_symbol.items():
            msg += f"- {symbol}: {value:.3f} USDC\n"
    except Exception:
        pass

    try:
        by_mode = df.assign(real_pnl_usdc=pnl).groupby("mode")["real_pnl_usdc"].sum()
        msg += "\nPar mode:\n"
        for mode, value in by_mode.items():
            msg += f"- {mode}: {value:.3f} USDC\n"
    except Exception:
        pass

    try:
        tp1_rate = (df["tp1_done"].astype(str).str.lower() == "true").mean() * 100
        msg += f"\nTP1 atteint: {tp1_rate:.1f}% des trades\n"
    except Exception:
        pass

    return msg



def normalize_telegram_command(text_msg):
    """
    Accepte :
    /report
    report
    /report@NomDuBot
    csv
    csc
    """
    cmd = (text_msg or "").strip().lower()

    if "@" in cmd:
        cmd = cmd.split("@")[0]

    if not cmd.startswith("/"):
        cmd = "/" + cmd

    return cmd


def initialize_telegram_offset():
    """
    Ignore les anciens messages Telegram au démarrage.
    Les commandes envoyées APRÈS le démarrage seront lues.
    """
    global TELEGRAM_LAST_UPDATE_ID

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        r = requests.get(url, params={"timeout": 1, "limit": 100}, timeout=5)
        data = r.json()

        if not data.get("ok"):
            print("[TELEGRAM COMMANDS] getUpdates not ok:", data)
            return

        results = data.get("result", [])

        if results:
            TELEGRAM_LAST_UPDATE_ID = results[-1].get("update_id")
            print("[TELEGRAM COMMANDS] Offset initialisé:", TELEGRAM_LAST_UPDATE_ID)
        else:
            print("[TELEGRAM COMMANDS] Aucun ancien message.")

    except Exception as e:
        print("[ERREUR TELEGRAM INIT OFFSET]", e)




def repair_posttrade_from_last_trade():
    """
    Crée une analyse pending à partir de la dernière ligne de trades.csv.
    Utile si un résumé de trade est sorti mais que le Post Trade Analyzer n'a pas accroché.
    """
    try:
        df = load_trades_dataframe()
        if df.empty:
            return "Aucun trade dans trades.csv."

        last = df.tail(1).iloc[0].to_dict()
        pending = load_pending_post_trades()

        symbol = last.get("symbol")
        exit_price = last.get("exit")
        entry_price = last.get("entry")
        exit_reason = last.get("exit_reason")
        mode = last.get("mode")

        if not symbol or entry_price in [None, ""] or exit_price in [None, ""]:
            return "Dernier trade incomplet dans trades.csv."

        # éviter doublon
        for item in pending:
            if (
                item.get("symbol") == symbol
                and str(item.get("entry")) == str(entry_price)
                and str(item.get("exit")) == str(exit_price)
                and item.get("exit_reason") == exit_reason
            ):
                return "Le dernier trade est déjà dans pending."

        ok = add_post_trade_tracking_direct(
            symbol=symbol,
            mode=mode,
            entry_price=entry_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            entry_balance=last.get("balance_before"),
            final_balance=last.get("balance_after"),
        )

        if ok:
            return f"Réparation OK : dernier trade {symbol} ajouté au Post Trade Analyzer."
        return "Réparation échouée."

    except Exception as e:
        return f"Erreur réparation posttrade: {e}"



def build_posttrade_message():
    try:
        ensure_post_trade_files()
        pending = load_pending_post_trades()
        df = load_post_trade_dataframe()

        total_done = 0 if df.empty else len(df)

        msg = (
            "🔍 <b>POST TRADE ANALYZER</b>\n"
            f"Fichier pending: {'OK' if POST_TRADE_PENDING_PATH.exists() else 'ABSENT'}\n"
            f"Fichier analysis: {'OK' if POST_TRADE_CSV_PATH.exists() else 'ABSENT'}\n"
            f"Analyses en attente: {len(pending)}\n"
            f"Analyses terminées: {total_done}\n"
            f"Hook direct: ACTIF\n"
        )

        if pending:
            msg += "\n<b>En attente:</b>\n"
            for item in pending[-5:]:
                msg += (
                    f"- {item.get('symbol')} {item.get('mode')} "
                    f"sortie {item.get('exit_reason')} à {item.get('exit')}\n"
                )

        if not df.empty:
            msg += "\n<b>Dernières analyses:</b>\n"
            for _, row in df.tail(5).iterrows():
                msg += (
                    f"- {row.get('symbol')} {row.get('verdict')} "
                    f"max après: {row.get('max_gain_after_exit_pct')}%\n"
                )

        return msg

    except Exception as e:
        return f"⚠️ Erreur /posttrade: {e}"



def build_posttrade_pending_message():
    try:
        ensure_post_trade_files()
        pending = load_pending_post_trades()

        if not pending:
            return "⏳ <b>POST TRADE PENDING</b>\nAucune analyse en attente."

        msg = f"⏳ <b>POST TRADE PENDING</b>\nAnalyses en attente: {len(pending)}\n"
        for item in pending[-10:]:
            msg += (
                f"\n<b>{item.get('symbol')}</b> {item.get('mode')}\n"
                f"Sortie: {item.get('exit_reason')}\n"
                f"Entry: {item.get('entry')} | Exit: {item.get('exit')}\n"
                f"PnL sortie: {item.get('pnl_at_exit_pct')}%\n"
                f"Max suivi: {round(((float(item.get('max_price', item.get('exit'))) - float(item.get('exit'))) / float(item.get('exit'))) * 100, 4) if item.get('exit') else 'N/A'}%\n"
                f"Min suivi: {round(((float(item.get('min_price', item.get('exit'))) - float(item.get('exit'))) / float(item.get('exit'))) * 100, 4) if item.get('exit') else 'N/A'}%\n"
            )

        return msg

    except Exception as e:
        return f"⚠️ Erreur /posttrade_pending: {e}"


def export_posttrade_csv():
    try:
        ensure_post_trade_files()
        if POST_TRADE_CSV_PATH.exists():
            send_telegram_document(str(POST_TRADE_CSV_PATH), "post_trade_analysis.csv")
            return "📎 post_trade_analysis.csv envoyé."
        return "Aucun fichier post_trade_analysis.csv."
    except Exception as e:
        return f"⚠️ Erreur export posttrade: {e}"


def build_score_message():
    try:
        df = load_trades_dataframe()
        if df.empty:
            return "📊 SCORE\nAucun trade enregistré."

        df = df.copy()
        df["real_pnl_usdc"] = pd.to_numeric(df.get("real_pnl_usdc"), errors="coerce").fillna(0)

        msg = "🧠 <b>SCORE MODES / TOKENS</b>\n"

        try:
            by_mode = df.groupby("mode")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            msg += "\n<b>Par mode</b>\n"
            for mode, row in by_mode.iterrows():
                msg += f"- {mode}: {int(row['count'])} trades | PnL {row['sum']:.3f} | Moy {row['mean']:.3f}\n"
        except Exception:
            pass

        try:
            by_symbol = df.groupby("symbol")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            msg += "\n<b>Par token</b>\n"
            for symbol, row in by_symbol.iterrows():
                msg += f"- {symbol}: {int(row['count'])} trades | PnL {row['sum']:.3f} | Moy {row['mean']:.3f}\n"
        except Exception:
            pass

        return msg

    except Exception as e:
        return f"⚠️ Erreur /score: {e}"


def build_dashboard_message():
    try:
        token_part = ""
        if DASHBOARD_TOKEN:
            token_part = "?token=TON_TOKEN"
        return (
            "🌐 <b>DASHBOARD</b>\n"
            f"Port: {DASHBOARD_PORT}\n"
            f"URL Railway: ouvre ton domaine Railway {token_part}\n"
            "Sections: contrôle modes, stats, trades, Post Trade Analyzer."
        )
    except Exception as e:
        return f"⚠️ Erreur /dashboard: {e}"


def build_backtest_message():
    return (
        "🧪 <b>BACKTEST</b>\n"
        "Le moteur complet de backtest n'est pas encore activé dans cette V4.0.\n"
        "Priorité actuelle: Post Trade Analyzer fiable + dashboard + commandes Telegram.\n"
        "Prochaine étape: backtest paramétrable par token / mode / période."
    )



def build_help_message():
    return (
        "🤖 <b>COMMANDES DISPONIBLES</b>\n"
        "/report : rapport global des trades\n"
        "/stats : statistiques rapides\n"
        "/csv : recevoir trades.csv\n"
        "/analysis : derniers signaux par coin\n"
        "/log : derniers logs bougies\n"
        "/posttrade : état Post Trade Analyzer\n"
        "/posttrade_pending : analyses en attente\n"
        "/posttrade_repair : ajoute le dernier trade au Post Trade si besoin\n        /posttrade_finalize : force la finalisation des analyses expirées\n"
        "/posttrade_export : recevoir post_trade_analysis.csv\n"
        "/score : performance par mode/token\n"
        "/dashboard : rappel accès dashboard\n        /adaptive : état intelligence adaptative\n"
        "/backtest : statut backtest\n"
        "/help : aide"
    )

def check_telegram_commands():
    """
    Commandes disponibles :
    /report ou report
    /csv ou csv ou csc
    /help ou help
    /stats ou stats
    """
    global TELEGRAM_LAST_UPDATE_ID

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {
            "timeout": 1,
            "limit": 20
        }

        if TELEGRAM_LAST_UPDATE_ID is not None:
            params["offset"] = TELEGRAM_LAST_UPDATE_ID + 1

        r = requests.get(url, params=params, timeout=8)
        data = r.json()

        if not data.get("ok"):
            print("[TELEGRAM COMMANDS] getUpdates not ok:", data)
            return

        for update in data.get("result", []):
            TELEGRAM_LAST_UPDATE_ID = update.get("update_id", TELEGRAM_LAST_UPDATE_ID)

            message = update.get("message", {})
            chat = message.get("chat", {})
            chat_id = str(chat.get("id"))

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            raw_text = message.get("text") or ""
            cmd = normalize_telegram_command(raw_text)

            print("[TELEGRAM COMMAND]", raw_text, "=>", cmd)

            if cmd in ["/report", "/stats"]:
                notify_telegram(build_report_message(title="📊 RAPPORT ACTUEL"))
                continue

            if cmd in ["/csv", "/csc"]:
                ensure_trades_csv()
                send_telegram_document(TRADES_CSV_PATH, caption="📎 trades.csv actuel")
                continue

            if cmd.startswith("/analysis"):
                parts = raw_text.strip().split()
                limit = 20
                if len(parts) >= 2:
                    try:
                        limit = int(parts[1])
                    except Exception:
                        limit = 20
                send_long_telegram_message(build_analysis_message(limit))
                continue

            if cmd.startswith("/log"):
                parts = raw_text.strip().split()
                limit = 100
                if len(parts) >= 2:
                    try:
                        limit = int(parts[1])
                    except Exception:
                        limit = 100
                send_long_telegram_message(build_log_message(limit))
                continue

            if cmd.startswith("/posttrade_repair"):
                notify_telegram(repair_posttrade_from_last_trade())
                continue

            if cmd.startswith("/posttrade_finalize"):
                notify_telegram(build_posttrade_finalize_message())
                continue

            if cmd.startswith("/posttrade_pending"):
                send_long_telegram_message(build_posttrade_pending_message())
                continue

            if cmd.startswith("/posttrade_export"):
                notify_telegram(export_posttrade_csv())
                continue

            if cmd.startswith("/posttrade"):
                send_long_telegram_message(build_posttrade_message())
                continue

            if cmd.startswith("/score"):
                send_long_telegram_message(build_score_message())
                continue

            if cmd.startswith("/backtest"):
                notify_telegram(build_backtest_message())
                continue

            if cmd.startswith("/adaptive"):
                send_long_telegram_message(build_adaptive_message())
                continue

            if cmd.startswith("/dashboard"):
                notify_telegram(build_dashboard_message())
                continue

            if cmd == "/help":
                notify_telegram(build_help_message())
                continue

            if cmd.startswith("/"):
                notify_telegram(
                    "Commande inconnue.\n"
                    "Tape /help pour voir les commandes disponibles."
                )

    except Exception as e:
        print("[ERREUR TELEGRAM COMMANDS]", e)




def maybe_send_monthly_report():
    """
    Envoie un rapport mensuel une fois par mois.
    Railway doit rester actif pour que cette vérification se fasse.
    """
    now = datetime.now()

    # Rapport le 1er du mois uniquement
    if now.day != 1:
        return

    current_month_key = now.strftime("%Y-%m")

    if LAST_MONTHLY_REPORT_FILE.exists():
        last_sent = LAST_MONTHLY_REPORT_FILE.read_text(encoding="utf-8").strip()
        if last_sent == current_month_key:
            return

    df = load_trades_dataframe()

    if df.empty:
        return

    # Rapport sur le mois précédent
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    first_day_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_end = first_day_current_month
    previous_month_start = (first_day_current_month - pd.DateOffset(months=1)).to_pydatetime()

    monthly = df[(df["date"] >= previous_month_start) & (df["date"] < previous_month_end)].copy()

    if monthly.empty:
        LAST_MONTHLY_REPORT_FILE.write_text(current_month_key, encoding="utf-8")
        return

    report = build_report_message(
        monthly,
        title=f"📊 RAPPORT MENSUEL {previous_month_start.strftime('%Y-%m')}"
    )

    notify_telegram(report)
    send_telegram_document(TRADES_CSV_PATH, caption="📎 Fichier trades.csv complet")
    LAST_MONTHLY_REPORT_FILE.write_text(current_month_key, encoding="utf-8")



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
    global LAST_ENTRY_PRICE_BY_SYMBOL, LAST_SELL_REASON
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
                LAST_SELL_REASON = None
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
                        f"Raison: {LAST_SELL_REASON or 'NON_SPECIFIEE'}\n"
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
                        f"Raison: {LAST_SELL_REASON or 'NON_SPECIFIEE'}\n"
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
        "entry_candle_time": None,

        "entry_balance": None,
        "signal_mode": None,
        "score": None,
        "pattern_score": None,
        "bull_flag": None,
        "ascending_triangle": None,
        "compression_breakout": None,
        "higher_lows": None,
        "mode_c_reversal": None,
        "mode_c_drop_pct": None,
        "mode_c_volume_ok": None,
        "mode_c_wick_ok": None,
        "mode_d_entry_ema_low": None,
        "mode_d_target_ema_high": None,
        "mode_d_type": None,
        "mode_e_volume_ratio": None,
        "mode_e_body_ratio": None,
        "mode_e_intrabar": None,
        "mode_e_intrabar_move_pct": None,
        "mode_f_pattern": None,
        "mode_f_score": None,
        "mode_f_details_text": None,
        "smart_exit_score": None,
        "smart_exit_health": None,
        "tp1_price": None,
        "tp1_exec_price": None,
        "tp1_qty": None,
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
        trade_state["entry_candle_time"] = int(time.time() * 1000)



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




def detect_mode_c_capitulation(df):
    if len(df) < 40:
        return False, 0, {
            "mode_c_drop_pct": 0,
            "mode_c_volume_ok": False,
            "mode_c_wick_ok": False,
            "mode_c_green_ok": False,
            "mode_c_rsi_ok": False,
        }

    last = df.iloc[-1]
    prev = df.iloc[-2]

    recent = df.iloc[-MODE_C_DROP_LOOKBACK:]
    previous_high = recent["high"].max()
    recent_low = recent["low"].min()
    drop_pct = (previous_high - recent_low) / previous_high if previous_high > 0 else 0

    volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
    volume_ok = last["volume"] > volume_ma20 * MODE_C_VOLUME_MULT if volume_ma20 and volume_ma20 > 0 else False

    lower_wick = min(last["open"], last["close"]) - last["low"]
    wick_ok = lower_wick > last["atr"] * MODE_C_WICK_ATR_MULT if last["atr"] and last["atr"] > 0 else False

    green_ok = last["close"] > last["open"] and last["close"] > prev["close"]
    rsi_ok = last["rsi"] < MODE_C_RSI_MAX
    not_closing_at_low = last["close"] > (last["low"] + (last["high"] - last["low"]) * 0.45)

    ok = (
        drop_pct >= MODE_C_MIN_DROP_PCT
        and rsi_ok
        and green_ok
        and (volume_ok or wick_ok)
        and not_closing_at_low
    )

    score = 0
    if ok:
        score = 2.0 + min(drop_pct * 50, 3.0) + min((lower_wick / last["atr"]) if last["atr"] > 0 else 0, 2.0)

    return ok, score, {
        "mode_c_drop_pct": round(drop_pct * 100, 2),
        "mode_c_volume_ok": volume_ok,
        "mode_c_wick_ok": wick_ok,
        "mode_c_green_ok": green_ok,
        "mode_c_rsi_ok": rsi_ok,
    }






def mode_f_pattern_label(pattern_key):
    labels = {
        "hammer": "Marteau / Hammer",
        "bullish_engulfing": "Englobante haussière",
        "morning_star": "Morning Star",
        "tweezer_bottom": "Tweezer Bottom / Double creux",
        "inside_bar_breakout": "Inside Bar Breakout",
        "bullish_harami": "Bullish Harami",
        "three_white_soldiers": "Three White Soldiers",
        "long_lower_wick_volume": "Longue mèche basse + volume",
    }
    return labels.get(str(pattern_key), str(pattern_key))


def mode_f_pattern_explanation(pattern_key):
    explanations = {
        "hammer": "Rejet vendeur : longue mèche basse après baisse.",
        "bullish_engulfing": "Bougie verte qui englobe le corps rouge précédent.",
        "morning_star": "Séquence rouge + hésitation + reprise verte.",
        "tweezer_bottom": "Deux plus bas proches : support défendu.",
        "inside_bar_breakout": "Compression puis cassure du plus haut de l'inside bar.",
        "bullish_harami": "Petite bougie verte contenue dans la rouge précédente.",
        "three_white_soldiers": "Trois bougies vertes consécutives avec clôtures montantes.",
        "long_lower_wick_volume": "Rejet vendeur confirmé par volume.",
    }
    return explanations.get(str(pattern_key), "")


def format_mode_f_patterns(patterns):
    if not patterns:
        return "Aucun pattern Mode F identifié"
    if isinstance(patterns, str):
        patterns = [p for p in patterns.split(",") if p]
    return ", ".join([mode_f_pattern_label(p) for p in patterns])


def format_mode_f_pattern_details(patterns):
    if not patterns:
        return "Aucun détail disponible."
    if isinstance(patterns, str):
        patterns = [p for p in patterns.split(",") if p]
    lines = []
    for p in patterns:
        lines.append(f"✅ {mode_f_pattern_label(p)} : {mode_f_pattern_explanation(p)}")
    return "\\n".join(lines)



def candle_reversal_patterns(df):
    """MODE F - Candle Reversal : détecte patterns haussiers de bougies."""
    try:
        if len(df) < 30:
            return 0, {"patterns": [], "score": 0}
        last, p1, p2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
        def body(c): return abs(float(c["close"]) - float(c["open"]))
        def rng(c): return max(float(c["high"]) - float(c["low"]), 1e-12)
        def upper_wick(c): return float(c["high"]) - max(float(c["open"]), float(c["close"]))
        def lower_wick(c): return min(float(c["open"]), float(c["close"])) - float(c["low"])
        def green(c): return float(c["close"]) > float(c["open"])
        def red(c): return float(c["close"]) < float(c["open"])

        volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = (float(last["volume"]) / volume_ma20) if volume_ma20 and volume_ma20 > 0 else 0
        base_close = float(df["close"].iloc[-8])
        recent_drop = (base_close - float(last["close"])) / base_close if base_close > 0 else 0
        after_drop = recent_drop > 0.005 or float(last["close"]) < float(last["ema20"])
        last_body = body(last); last_range = rng(last)
        lower = lower_wick(last); upper = upper_wick(last)

        hammer = after_drop and lower >= 2.0 * max(last_body, 1e-12) and upper <= 0.7 * max(last_body, 1e-12) and float(last["close"]) >= float(last["low"]) + 0.55 * last_range
        bullish_engulfing = red(p1) and green(last) and float(last["open"]) <= float(p1["close"]) and float(last["close"]) >= float(p1["open"]) and body(last) >= 0.9 * body(p1)
        morning_star = red(p2) and body(p1) <= 0.55 * body(p2) and green(last) and float(last["close"]) >= (float(p2["open"]) + float(p2["close"])) / 2
        low_tolerance = max(float(last["atr"]) * 0.15, float(last["close"]) * 0.001)
        tweezer_bottom = after_drop and abs(float(last["low"]) - float(p1["low"])) <= low_tolerance and red(p1) and green(last) and float(last["close"]) > float(p1["close"])
        inside_bar_breakout = float(p1["high"]) <= float(p2["high"]) and float(p1["low"]) >= float(p2["low"]) and float(last["close"]) > float(p1["high"]) and green(last)
        bullish_harami = red(p1) and green(last) and float(last["open"]) >= float(p1["close"]) and float(last["close"]) <= float(p1["open"]) and body(last) <= 0.65 * body(p1)
        three_white_soldiers = green(p2) and green(p1) and green(last) and float(p1["close"]) > float(p2["close"]) and float(last["close"]) > float(p1["close"]) and body(p2) > 0 and body(p1) > 0 and body(last) > 0
        long_lower_wick_volume = after_drop and lower >= 1.8 * max(last_body, 1e-12) and volume_ratio >= MODE_F_VOLUME_MULT and green(last)

        weights = [("hammer", hammer, 1.6),("bullish_engulfing", bullish_engulfing, 2.2),("morning_star", morning_star, 2.6),("tweezer_bottom", tweezer_bottom, 1.8),("inside_bar_breakout", inside_bar_breakout, 1.8),("bullish_harami", bullish_harami, 1.4),("three_white_soldiers", three_white_soldiers, 2.2),("long_lower_wick_volume", long_lower_wick_volume, 2.4)]
        patterns=[]; score=0.0
        for name, ok, weight in weights:
            if ok:
                patterns.append(name); score += weight
        if volume_ratio >= MODE_F_VOLUME_MULT: score += 0.5
        if float(last["obv"]) > float(last["obv_ma5"]): score += 0.5
        if float(last["close"]) > float(last["ema20"]): score += 0.5
        return score, {"patterns": patterns, "hammer": hammer, "bullish_engulfing": bullish_engulfing, "morning_star": morning_star, "tweezer_bottom": tweezer_bottom, "inside_bar_breakout": inside_bar_breakout, "bullish_harami": bullish_harami, "three_white_soldiers": three_white_soldiers, "long_lower_wick_volume": long_lower_wick_volume, "volume_ratio": round(volume_ratio,2), "score": round(score,4)}
    except Exception as e:
        print("[ERREUR MODE F CANDLE]", e)
        return 0, {"patterns": [], "score": 0}

def mode_e_intrabar_signal(df, current_price):
    """
    MODE E INTRABAR :
    Achat précoce sur impulsion en cours, sans attendre la clôture 15m.

    On utilise la dernière bougie 15m clôturée pour EMA20/ATR/OBV/volume moyen,
    et le prix courant pour détecter le départ du mouvement.
    """
    if not MODE_E_INTRABAR_ENABLED:
        return False, 0, {}

    if len(df) < 40:
        return False, 0, {}

    last = df.iloc[-1]

    try:
        candle_open = float(last["open"])
        ema20 = float(last["ema20"])
        atr = float(last["atr"])
        rsi = float(last["rsi"])
        obv = float(last["obv"])
        obv_ma5 = float(last["obv_ma5"])

        if candle_open <= 0 or ema20 <= 0:
            return False, 0, {}

        move_pct = (current_price - candle_open) / candle_open
        distance_ema20_pct = abs(current_price - ema20) / ema20

        volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        last_volume = float(last["volume"])
        volume_ratio = (last_volume / volume_ma20) if volume_ma20 and volume_ma20 > 0 else 0

        obv_ok = obv > obv_ma5
        price_ok = current_price > ema20
        move_ok = move_pct >= MODE_E_INTRABAR_MIN_MOVE_PCT
        volume_ok = volume_ratio >= MODE_E_INTRABAR_VOLUME_MULT
        rsi_ok = MODE_E_MIN_RSI <= rsi <= MODE_E_MAX_RSI
        not_too_far = distance_ema20_pct <= MODE_E_INTRABAR_MAX_DISTANCE_EMA20_PCT

        ok = (
            price_ok
            and move_ok
            and volume_ok
            and obv_ok
            and rsi_ok
            and not_too_far
        )

        score = 0
        if ok:
            score = (
                2.5
                + min(volume_ratio, 5.0)
                + min(move_pct * 100, 3.0)
                + min((current_price - ema20) / atr if atr and atr > 0 else 0, 2.0)
            )

        return ok, score, {
            "mode_e_intrabar": ok,
            "mode_e_intrabar_move_pct": round(move_pct * 100, 3),
            "mode_e_intrabar_volume_ratio": round(volume_ratio, 2),
            "mode_e_intrabar_distance_ema20_pct": round(distance_ema20_pct * 100, 3),
        }

    except Exception as e:
        print("[ERREUR MODE E INTRABAR]", e)
        return False, 0, {}



def buy_signal(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]

    trend_ok = last["ema20"] > last["ema50"]
    rsi_ok = RSI_BUY_LEVEL < last["rsi"] < RSI_BUY_MAX
    vwap_ok = last["close"] > last["vwap"]
    obv_rising = last["obv"] > last["obv_ma5"]

    breakout_level = max(prev1["high"], prev2["high"])
    breakout_ok = last["close"] > breakout_level
    green_candle = last["close"] > last["open"]

    patt_score, patt_details = pattern_score(df)
    mode_c_ok, mode_c_score, mode_c_details = detect_mode_c_capitulation(df)

    breakout_strength = max(0, (last["close"] / breakout_level) - 1) if breakout_level > 0 else 0
    vwap_strength = max(0, (last["close"] / last["vwap"]) - 1) if last["vwap"] > 0 else 0

    # Correction OBV négatif
    if obv_rising and last["obv_ma5"] != 0:
        obv_strength = max(0, (last["obv"] - last["obv_ma5"]) / abs(last["obv_ma5"]))
        obv_strength = min(obv_strength, 2.0)
    else:
        obv_strength = 0

    rsi_score = max(0, 1 - abs(last["rsi"] - 62) / 10)

    # MODE A : breakout en tendance confirmée.
    # Correction : close doit être au-dessus EMA20.
    mode_a_ok = (
        trend_ok
        and last["close"] > last["ema20"]
        and rsi_ok
        and vwap_ok
        and obv_rising
        and breakout_ok
        and green_candle
    )

    # MODE B : pattern premium.
    # Correction : close doit être au-dessus EMA20.
    # Les entrées entre EMA50 et EMA20 sont réservées au MODE D.
    mode_b_ok = (
        patt_details["pattern_score"] >= 2.0
        and last["close"] > last["ema20"]
        and breakout_ok
        and obv_rising
        and vwap_ok
        and green_candle
        and 45 < last["rsi"] < 85
    )

    # MODE D - Pullback EMA agressif sans condition RSI.
    # En tendance haussière EMA20 > EMA50 :
    # achat dès que la clôture repasse au-dessus de l'EMA basse = EMA50,
    # tant que le prix reste encore sous EMA20.
    ema_low = min(last["ema20"], last["ema50"])
    ema_high = max(last["ema20"], last["ema50"])
    prev_ema_low = min(prev1["ema20"], prev1["ema50"])

    # MODE D v2 - Pullback EMA agressif sans condition RSI.
    # D1 : rebond progressif, prix entre EMA basse et EMA haute.
    mode_d_pullback_between_emas = (
        trend_ok
        and prev1["close"] <= prev_ema_low
        and last["close"] > ema_low
        and last["close"] < ema_high
    )

    # D2 : rebond fort, la bougie traverse EMA basse ET EMA haute d'un coup.
    mode_d_cross_both_emas = (
        trend_ok
        and prev1["close"] <= prev_ema_low
        and last["close"] >= ema_high
        and green_candle
    )

    mode_d_ok = mode_d_pullback_between_emas or mode_d_cross_both_emas

    # MODE E - Early Momentum :
    # Volume explosif + OBV positif + bougie impulsive + cassure EMA20.
    # Il n'exige pas EMA20 > EMA50 pour capter le début du mouvement.
    volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
    volume_ratio = (last["volume"] / volume_ma20) if volume_ma20 and volume_ma20 > 0 else 0

    candle_range = last["high"] - last["low"]
    candle_body = abs(last["close"] - last["open"])
    body_ratio = (candle_body / candle_range) if candle_range and candle_range > 0 else 0

    mode_e_ok = (
        last["close"] > last["ema20"]
        and green_candle
        and obv_rising
        and volume_ratio >= MODE_E_VOLUME_MULT
        and body_ratio >= MODE_E_BODY_RATIO_MIN
        and MODE_E_MIN_RSI <= last["rsi"] <= MODE_E_MAX_RSI
    )

    mode_f_score, mode_f_details = candle_reversal_patterns(df)
    mode_f_confirmed = (obv_rising or volume_ratio >= MODE_F_VOLUME_MULT or last["close"] > last["ema20"])
    mode_f_ok = (mode_f_score >= MODE_F_MIN_PATTERN_SCORE and mode_f_confirmed and last["rsi"] <= MODE_F_RSI_MAX)

    signal_mode = None
    # Activation / désactivation via dashboard
    mode_a_ok = mode_a_ok and is_mode_enabled("MODE_A_CLASSIQUE")
    mode_b_ok = mode_b_ok and is_mode_enabled("MODE_B_PATTERN")
    mode_c_ok = mode_c_ok and is_mode_enabled("MODE_C_CAPITULATION")
    mode_d_ok = mode_d_ok and is_mode_enabled("MODE_D_EMA_PULLBACK")
    mode_e_ok = mode_e_ok and is_mode_enabled("MODE_E_EARLY_MOMENTUM")
    mode_f_ok = mode_f_ok and is_mode_enabled("MODE_F_CANDLE_REVERSAL")

    if mode_a_ok:
        signal_mode = "MODE_A_CLASSIQUE"
    elif mode_b_ok:
        signal_mode = "MODE_B_PATTERN"
    elif mode_c_ok:
        signal_mode = "MODE_C_CAPITULATION"
    elif mode_d_ok:
        signal_mode = "MODE_D_EMA_PULLBACK"
    elif mode_e_ok:
        signal_mode = "MODE_E_EARLY_MOMENTUM"
    elif mode_f_ok:
        signal_mode = "MODE_F_CANDLE_REVERSAL"

    score = (
        breakout_strength * 100
        + vwap_strength * 10
        + obv_strength * 2
        + rsi_score
        + patt_score
        + mode_c_score
    )

    if mode_b_ok:
        score += 2.0
    if mode_c_ok:
        score += 2.0
    if mode_d_ok:
        score += 1.5
    if mode_e_ok:
        score += 2.5 + min(volume_ratio, 5.0) + min(body_ratio * 2, 2.0)
    if mode_f_ok:
        score += 2.0 + mode_f_score

    ok = mode_a_ok or mode_b_ok or mode_c_ok or mode_d_ok or mode_e_ok or mode_f_ok

    return ok, score, {
        "trend_ok": trend_ok,
        "rsi_ok": rsi_ok,
        "vwap_ok": vwap_ok,
        "obv_rising": obv_rising,
        "breakout_ok": breakout_ok,
        "green_candle": green_candle,
        "close_above_ema20": bool(last["close"] > last["ema20"]),
        "breakout_level": breakout_level,
        "score": score,
        "signal_mode": signal_mode,
        "mode_a_ok": mode_a_ok,
        "mode_b_ok": mode_b_ok,
        "mode_c_ok": mode_c_ok,
        "mode_d_ok": mode_d_ok,
        "mode_d_pullback_between_emas": mode_d_pullback_between_emas,
        "mode_d_cross_both_emas": mode_d_cross_both_emas,
        "mode_e_ok": mode_e_ok,
        "mode_e_volume_ratio": round(volume_ratio, 2),
        "mode_e_body_ratio": round(body_ratio, 2),
        "mode_e_intrabar": False,
        "mode_e_intrabar_move_pct": None,
        "mode_e_intrabar_distance_ema20_pct": None,
        "mode_f_ok": mode_f_ok,
        "mode_f_pattern": ",".join(mode_f_details.get("patterns", [])),
        "mode_f_pattern_label": format_mode_f_patterns(mode_f_details.get("patterns", [])),
        "mode_f_details_text": format_mode_f_pattern_details(mode_f_details.get("patterns", [])),
        "mode_f_score": round(mode_f_score, 4),
        "mode_f_details": mode_f_details,
        "pattern_score": patt_details["pattern_score"],
        "bull_flag": patt_details["bull_flag"],
        "ascending_triangle": patt_details["ascending_triangle"],
        "compression_breakout": patt_details["compression_breakout"],
        "higher_lows": patt_details["higher_lows"],
        "mode_c_reversal": mode_c_ok,
        "mode_c_drop_pct": mode_c_details["mode_c_drop_pct"],
        "mode_c_volume_ok": mode_c_details["mode_c_volume_ok"],
        "mode_c_wick_ok": mode_c_details["mode_c_wick_ok"],
        "mode_d_entry_ema_low": round(ema_low, 5),
        "mode_d_target_ema_high": round(ema_high, 5),
    }



def send_trade_summary(symbol, exit_price, exit_reason, final_balance):
    """
    Notification Telegram compacte à copier/coller dans ChatGPT après chaque trade complet.
    """
    entry = trade_state.get("entry_price")
    entry_balance = trade_state.get("entry_balance")

    if not entry:
        return

    pnl_pct = ((exit_price - entry) / entry) * 100
    real_account_pnl = None

    if entry_balance is not None:
        real_account_pnl = final_balance - entry_balance

    tp1_done = trade_state.get("tp1_done", False)
    tp1_exec = trade_state.get("tp1_exec_price")
    tp1_price = trade_state.get("tp1_price")

    msg = (
        f"📋 <b>RÉSUMÉ TRADE COMPLET</b>\n"
        f"Symbole: {symbol}\n"
        f"Mode: {trade_state.get('signal_mode')}\n"
        f"Score: {trade_state.get('score')}\n"
        f"Pattern score: {trade_state.get('pattern_score')}\n"
        f"Bull flag: {trade_state.get('bull_flag')}\n"
        f"Triangle ascendant: {trade_state.get('ascending_triangle')}\n"
        f"Compression: {trade_state.get('compression_breakout')}\n"
        f"Higher lows: {trade_state.get('higher_lows')}\n"
        f"Mode C Reversal: {trade_state.get('mode_c_reversal')}\n"
        f"Mode C Drop: {trade_state.get('mode_c_drop_pct')}%\n"
        f"Mode D EMA basse: {trade_state.get('mode_d_entry_ema_low')}\n"
        f"Mode D EMA haute cible: {trade_state.get('mode_d_target_ema_high')}\n"
        f"Mode D type: {trade_state.get('mode_d_type')}\n"
        f"Mode E Volume ratio: {trade_state.get('mode_e_volume_ratio')}x\n"
        f"Mode E Body ratio: {trade_state.get('mode_e_body_ratio')}\n"
        f"Mode E Intrabar: {trade_state.get('mode_e_intrabar')}\n"
        f"Mode E Intrabar move: {trade_state.get('mode_e_intrabar_move_pct')}%\n"
        f"Smart Exit score: {trade_state.get('smart_exit_score')}\n"
        f"Smart Exit health: {trade_state.get('smart_exit_health')} / 100\n"
        f"Entry: {entry:.5f}\n"
        f"Sortie finale: {exit_price:.5f}\n"
        f"Raison sortie finale: {exit_reason}\n"
        f"TP1 atteint: {tp1_done}\n"
        f"TP1 théorique: {tp1_price if tp1_price is not None else 'N/A'}\n"
        f"TP1 exécuté: {tp1_exec if tp1_exec is not None else 'N/A'}\n"
        f"PnL final prix: {pnl_pct:.2f}%\n"
        f"Solde avant trade: {entry_balance if entry_balance is not None else 'N/A'} USDC\n"
        f"Solde après trade: {final_balance} USDC\n"
    )

    if real_account_pnl is not None:
        msg += f"Résultat réel compte: {real_account_pnl:.3f} USDC\n"

    msg += "Copier ce message dans ChatGPT pour analyse."

    add_post_trade_tracking_direct(
        symbol=symbol,
        mode=trade_state.get("signal_mode"),
        entry_price=entry,
        exit_price=exit_price,
        exit_reason=exit_reason,
        entry_balance=entry_balance,
        final_balance=final_balance,
    )
    notify_telegram(msg)

    # Journal CSV + rapport stats + envoi fichier Telegram
    append_trade_to_csv(symbol, exit_price, exit_reason, final_balance)
    notify_telegram(build_report_message(title="📊 RAPPORT ACTUEL"))
    send_telegram_document(TRADES_CSV_PATH, caption="📎 trades.csv mis à jour")



def bearish_reversal_patterns(df):
    try:
        if len(df) < 30:
            return 0, {"patterns": []}

        last = df.iloc[-1]
        p1 = df.iloc[-2]
        p2 = df.iloc[-3]

        def body(c): return abs(float(c["close"]) - float(c["open"]))
        def rng(c): return max(float(c["high"]) - float(c["low"]), 1e-12)
        def upper_wick(c): return float(c["high"]) - max(float(c["open"]), float(c["close"]))
        def lower_wick(c): return min(float(c["open"]), float(c["close"])) - float(c["low"])
        def green(c): return float(c["close"]) > float(c["open"])
        def red(c): return float(c["close"]) < float(c["open"])

        volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = (float(last["volume"]) / volume_ma20) if volume_ma20 and volume_ma20 > 0 else 0

        last_body = body(last)
        last_range = rng(last)

        bearish_engulfing = (
            green(p1) and red(last)
            and float(last["open"]) >= float(p1["close"])
            and float(last["close"]) <= float(p1["open"])
            and body(last) >= 0.8 * body(p1)
        )
        shooting_star = (
            upper_wick(last) >= 2.0 * max(last_body, 1e-12)
            and lower_wick(last) <= 0.7 * max(last_body, 1e-12)
            and float(last["close"]) <= float(last["low"]) + 0.45 * last_range
        )
        evening_star = (
            green(p2) and body(p1) <= 0.55 * body(p2)
            and red(last)
            and float(last["close"]) <= (float(p2["open"]) + float(p2["close"])) / 2
        )
        bearish_harami = (
            green(p1) and red(last)
            and float(last["open"]) <= float(p1["close"])
            and float(last["close"]) >= float(p1["open"])
            and body(last) <= 0.65 * body(p1)
        )
        tweezer_top = (
            abs(float(last["high"]) - float(p1["high"])) <= max(float(last["atr"]) * 0.15, float(last["close"]) * 0.001)
            and green(p1) and red(last)
            and float(last["close"]) < float(p1["close"])
        )
        strong_red_volume = red(last) and volume_ratio >= SMART_EXIT_VOLUME_MULT and body(last) / last_range >= 0.55

        patterns = []
        score = 0.0
        for name, ok, weight in [
            ("bearish_engulfing", bearish_engulfing, 4.0),
            ("shooting_star", shooting_star, 3.0),
            ("evening_star", evening_star, 4.0),
            ("bearish_harami", bearish_harami, 2.0),
            ("tweezer_top", tweezer_top, 2.5),
            ("strong_red_volume", strong_red_volume, 3.0),
        ]:
            if ok:
                patterns.append(name)
                score += weight

        return score, {
            "patterns": patterns,
            "volume_ratio": round(volume_ratio, 2),
            "score": round(score, 4),
        }
    except Exception as e:
        print("[ERREUR BEARISH PATTERN]", e)
        return 0, {"patterns": []}


def smart_exit_reversal_decision(df, current_price, entry_price, mode=None):
    """
    Smart Exit unifié pour D / C / E / F.
    Score de faiblesse + Trend Health Score 0-100.
    Plus le score est haut, plus il faut sortir.
    Plus le health est bas, plus la position est dangereuse.
    """
    try:
        if len(df) < 30:
            return False, False, 0, {}

        last = df.iloc[-1]
        prev = df.iloc[-2]
        p2 = df.iloc[-3]

        bearish_score, bearish_details = bearish_reversal_patterns(df)

        volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = (float(last["volume"]) / volume_ma20) if volume_ma20 and volume_ma20 > 0 else 0

        close_under_ema20 = float(last["close"]) < float(last["ema20"])
        two_closes_under_ema20 = close_under_ema20 and float(prev["close"]) < float(prev["ema20"])
        close_under_ema50 = float(last["close"]) < float(last["ema50"])
        obv_bad = float(last["obv"]) < float(last["obv_ma5"])

        rsi = float(last["rsi"])
        rsi_prev = float(prev["rsi"])
        rsi_drop = rsi_prev - rsi
        rsi_weak = rsi < 45
        rsi_momentum_loss = rsi_drop >= 5

        red_candle = float(last["close"]) < float(last["open"])
        prev_red = float(prev["close"]) < float(prev["open"])
        two_red_candles = red_candle and prev_red
        red_volume = red_candle and volume_ratio >= SMART_EXIT_VOLUME_MULT

        no_new_high_2 = float(last["high"]) <= max(float(prev["high"]), float(p2["high"]))
        lower_high = float(last["high"]) < float(prev["high"])
        lower_close = float(last["close"]) < float(prev["close"])

        pnl_pct = ((float(current_price) - float(entry_price)) / float(entry_price)) * 100 if entry_price else 0

        score = float(bearish_score)
        if close_under_ema20:
            score += 2.0
        if two_closes_under_ema20:
            score += 1.5
        if close_under_ema50:
            score += 2.5
        if obv_bad:
            score += 1.5
        if rsi_weak:
            score += 1.0
        if rsi_momentum_loss:
            score += 1.0
        if red_volume:
            score += 2.0
        if two_red_candles:
            score += 1.0
        if no_new_high_2:
            score += 0.8
        if lower_high and lower_close:
            score += 1.0

        recent_high = max(float(last["high"]), float(prev["high"]), float(p2["high"]))
        best_gain_pct = ((recent_high - float(entry_price)) / float(entry_price)) * 100 if entry_price else 0
        giveback_pct = best_gain_pct - pnl_pct

        if best_gain_pct >= 0.8 and giveback_pct >= 0.45:
            score += 2.0
        if best_gain_pct >= 1.5 and giveback_pct >= 0.8:
            score += 2.0

        if pnl_pct <= -0.7 and (close_under_ema20 or obv_bad):
            score += 2.0

        mode_key = str(mode or "")
        close_threshold = SMART_EXIT_MODE_THRESHOLDS.get(mode_key, SMART_EXIT_CLOSE_SCORE)
        tighten_threshold = SMART_EXIT_MODE_TIGHTEN_THRESHOLDS.get(mode_key, SMART_EXIT_TIGHTEN_SCORE)
        health_exit_level = SMART_EXIT_HEALTH_EXIT_LEVEL.get(mode_key, 40)

        health = 100.0
        health -= min(score * 7.0, 70)
        if close_under_ema20:
            health -= 8
        if close_under_ema50:
            health -= 12
        if obv_bad:
            health -= 8
        if rsi_momentum_loss:
            health -= 6
        if two_red_candles:
            health -= 6
        if best_gain_pct >= 0.8 and giveback_pct >= 0.45:
            health -= 10
        health = max(0, min(100, health))

        should_tighten = score >= tighten_threshold or health <= (health_exit_level + 15)
        should_close = score >= close_threshold or health <= health_exit_level

        details = {
            "score": round(score, 2),
            "health": round(health, 1),
            "mode": mode_key,
            "close_threshold": close_threshold,
            "tighten_threshold": tighten_threshold,
            "health_exit_level": health_exit_level,
            "bearish_patterns": ",".join(bearish_details.get("patterns", [])),
            "bearish_pattern_score": bearish_score,
            "close_under_ema20": close_under_ema20,
            "two_closes_under_ema20": two_closes_under_ema20,
            "close_under_ema50": close_under_ema50,
            "obv_bad": obv_bad,
            "rsi": round(rsi, 2),
            "rsi_drop": round(rsi_drop, 2),
            "rsi_weak": rsi_weak,
            "rsi_momentum_loss": rsi_momentum_loss,
            "two_red_candles": two_red_candles,
            "red_volume": red_volume,
            "no_new_high_2": no_new_high_2,
            "lower_high": lower_high,
            "lower_close": lower_close,
            "volume_ratio": round(volume_ratio, 2),
            "pnl_pct": round(pnl_pct, 3),
            "best_gain_pct": round(best_gain_pct, 3),
            "giveback_pct": round(giveback_pct, 3),
        }

        return should_close, should_tighten, score, details

    except Exception as e:
        print("[ERREUR SMART EXIT UNIFIED]", e)
        return False, False, 0, {}



def candle_time_ms(candle):
    try:
        for k in ["t", "T", "time", "timestamp"]:
            if k in candle:
                return int(candle[k])
    except Exception:
        pass
    return None

def candle_close_price(candle):
    try:
        return float(candle.get("c", candle.get("close")))
    except Exception:
        return None

def candle_high_price(candle):
    try:
        return float(candle.get("h", candle.get("high")))
    except Exception:
        return None

def candle_low_price(candle):
    try:
        return float(candle.get("l", candle.get("low")))
    except Exception:
        return None

def fetch_historical_candles_for_posttrade(symbol, start_ms, end_ms):
    try:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": "15m",
                "startTime": int(start_ms),
                "endTime": int(end_ms),
            },
        }
        r = requests.post(BASE_URL + "/info", json=payload, timeout=15)
        data = r.json()
        if isinstance(data, list):
            return data
        print("[POST TRADE HIST] Réponse candles inattendue:", str(data)[:200])
        return []
    except Exception as e:
        print("[ERREUR POST TRADE HIST CANDLES]", e)
        return []

def recalc_post_trade_from_history(item):
    """
    Recalcule une analyse post-trade depuis les bougies historiques 15m.
    Evite les analyses terminées à 0.00 après redémarrage ou absence de tick.
    """
    try:
        if not POST_TRADE_RECALC_HISTORICAL_ENABLED:
            return False, item

        symbol = item.get("symbol")
        exit_price = float(item.get("exit", 0) or 0)
        exit_time_ms = int(item.get("exit_time_ms", 0) or 0)

        if not symbol or exit_price <= 0 or exit_time_ms <= 0:
            item["status"] = POST_TRADE_DATA_MISSING_STATUS
            item["missing_reason"] = "symbol/exit/exit_time_ms manquant"
            return False, item

        end_ms = exit_time_ms + POST_TRADE_FORCE_FINALIZE_AFTER_MS + (20 * 60 * 1000)
        start_ms = max(0, exit_time_ms - (20 * 60 * 1000))
        candles = fetch_historical_candles_for_posttrade(symbol, start_ms, end_ms)

        if not candles:
            item["status"] = POST_TRADE_DATA_MISSING_STATUS
            item["missing_reason"] = "aucune bougie historique récupérée"
            return False, item

        after = []
        for c in candles:
            t = candle_time_ms(c)
            if t is not None and t >= exit_time_ms:
                after.append(c)

        if not after:
            item["status"] = POST_TRADE_DATA_MISSING_STATUS
            item["missing_reason"] = "aucune bougie après la sortie"
            return False, item

        item.setdefault("horizons", {})

        for label, horizon_ms in POST_TRADE_HORIZONS:
            target = exit_time_ms + horizon_ms
            selected = None
            for c in after:
                t = candle_time_ms(c)
                if t is not None and t >= target:
                    selected = c
                    break

            if selected is None:
                item["status"] = "PENDING_HISTORY"
                item["missing_reason"] = f"horizon {label} pas encore disponible"
                return False, item

            close_p = candle_close_price(selected)
            if close_p is not None:
                item["horizons"][label] = round(((close_p - exit_price) / exit_price) * 100, 4)

        highs = [candle_high_price(c) for c in after if candle_high_price(c) is not None]
        lows = [candle_low_price(c) for c in after if candle_low_price(c) is not None]
        if highs:
            item["max_price"] = max(highs)
        if lows:
            item["min_price"] = min(lows)

        item["status"] = "READY_FROM_HISTORY"
        item["missing_reason"] = ""
        return True, item

    except Exception as e:
        print("[ERREUR POST TRADE RECALC HISTORY]", e)
        item["status"] = POST_TRADE_DATA_MISSING_STATUS
        item["missing_reason"] = str(e)
        return False, item


def force_finalize_expired_post_trades():
    """
    Finalise les pending expirés après 4h avec recalcul historique.
    Si les bougies ne sont pas disponibles, le trade reste en pending avec status DATA_MISSING/PENDING_HISTORY.
    """
    try:
        pending = load_pending_post_trades()
        if not pending:
            return 0

        now_ms = int(time.time() * 1000)
        remaining = []
        finalized = 0

        for item in pending:
            elapsed = now_ms - int(item.get("exit_time_ms", now_ms))

            if elapsed >= POST_TRADE_FORCE_FINALIZE_AFTER_MS:
                ok, updated_item = recalc_post_trade_from_history(item)

                if ok:
                    finalize_post_trade_item(updated_item)
                    finalized += 1
                else:
                    remaining.append(updated_item)
            else:
                remaining.append(item)

        save_pending_post_trades(remaining)

        if finalized:
            print(f"[POST TRADE] Historical finalize = {finalized}")

        return finalized

    except Exception as e:
        print("[ERREUR POST TRADE FORCE FINALIZE]", e)
        return 0



def build_posttrade_finalize_message():
    try:
        count = force_finalize_expired_post_trades()
        return f"✅ PostTrade finalize historique lancé. Analyses finalisées: {count}. Les données manquantes restent en pending avec un statut explicite."
    except Exception as e:
        return f"⚠️ Erreur finalize posttrade: {e}"




def is_smart_exit_mode():
    return trade_state.get("signal_mode") in SMART_EXIT_REVERSAL_MODES


def apply_smart_exit_before_legacy(symbol, pos, df, current_price, entry, atr_value, pnl_pct, candle_time, min_hold_active):
    """
    V4.7 FIX :
    Pour les modes C / D / E / F, on force l'évaluation Smart Exit AVANT toute ancienne sortie EMA20.
    Retourne True si la fonction a pris la main et qu'il ne faut pas exécuter la suite legacy.
    """
    global LAST_SELL_REASON

    try:
        if not is_smart_exit_mode():
            return False

        hard_stop = entry - SMART_EXIT_ATR_STOP_MULT * atr_value

        smart_close, smart_tighten, smart_score, smart_details = smart_exit_reversal_decision(
            df, current_price, entry, trade_state.get("signal_mode")
        )

        trade_state["smart_exit_score"] = round(smart_score, 2)
        trade_state["smart_exit_health"] = smart_details.get("health")

        print(
            f"[SMART EXIT CHECK] {symbol} mode={trade_state.get('signal_mode')} "
            f"score={smart_score:.2f} health={smart_details.get('health')} "
            f"close={smart_close} tighten={smart_tighten} pnl={pnl_pct:.2f}%"
        )

        if current_price <= hard_stop:
            LAST_SELL_REASON = "SMART_EXIT_ATR_STOP"
            print("[SMART EXIT] ATR stop touché. Sortie totale.")
            notify_telegram(
                f"🛑 <b>SMART EXIT - ATR STOP</b>\n"
                f"Symbole: {symbol}\n"
                f"Mode: {trade_state.get('signal_mode')}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Stop: {hard_stop:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Health: {smart_details.get('health')} / 100"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return True

        if smart_close and not min_hold_active:
            LAST_SELL_REASON = "SMART_EXIT_UNIFIED"
            print(f"[SMART EXIT] Sortie unifiée score={smart_score} details={smart_details}")
            notify_telegram(
                f"📉 <b>SMART EXIT UNIFIÉ - Sortie</b>\n"
                f"Symbole: {symbol}\n"
                f"Mode: {trade_state.get('signal_mode')}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Score sortie: {smart_score:.2f}\n"
                f"Health: {smart_details.get('health')} / 100\n"
                f"Seuil mode: {smart_details.get('close_threshold')}\n"
                f"2 rouges: {smart_details.get('two_red_candles')}\n"
                f"OBV faible: {smart_details.get('obv_bad')}\n"
                f"Giveback: {smart_details.get('giveback_pct')}%\n"
                f"Sous EMA20: {smart_details.get('close_under_ema20')}"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return True

        if smart_tighten:
            tightened_stop = current_price - 0.8 * atr_value
            current_trail = trade_state.get("trailing_stop")
            if current_trail is None or tightened_stop > current_trail:
                trade_state["trailing_stop"] = tightened_stop
                print(f"[SMART EXIT] Trailing resserré à {tightened_stop:.5f} score={smart_score:.2f}")

            if trade_state.get("trailing_stop") is not None and current_price <= trade_state["trailing_stop"]:
                LAST_SELL_REASON = "SMART_EXIT_TIGHT_TRAIL"
                print("[SMART EXIT] Trailing resserré touché. Sortie totale.")
                if close_position_full(pos, current_price):
                    final_balance = round(get_balance(), 2)
                    send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                    reset_trade_state_after_sell(candle_time)
                return True

        # Pour C/D/E/F, on empêche les anciennes sorties EMA20 legacy.
        return True

    except Exception as e:
        print("[ERREUR APPLY SMART EXIT]", e)
        return False



def manage_open_position(pos, df, current_price, candle_time):
    global trade_state, LAST_SELL_REASON
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

    # Sortie EMA20 moins agressive :
    # on vend uniquement après 2 clôtures 15 min consécutives sous EMA20.
    prev = df.iloc[-2]
    raw_ema_break = (
        last["close"] < last["ema20"]
        and prev["close"] < prev["ema20"]
    )

    entry_candle_time = trade_state.get("entry_candle_time")
    min_hold_ms = MIN_HOLD_CANDLES_AFTER_BUY * 15 * 60 * 1000
    min_hold_active = (
        entry_candle_time is not None
        and candle_time is not None
        and candle_time < entry_candle_time + min_hold_ms
    )

    ema_break = raw_ema_break and not min_hold_active
    hard_stop_hit = current_price <= hard_stop

    print(
        f"[POSITION] {symbol} Entry={entry:.5f} Prix={current_price:.5f} "
        f"PnL={pnl_pct:.2f}% ATR={atr_value:.5f} TP1={tp1_price:.5f} "
        f"Trail={trailing_display} Stop={hard_stop:.5f} RSI={last['rsi']:.2f} "
        f"MinHold={min_hold_active}"
    )

    # Gestion spéciale MODE D :
    # V3.8J : plus de vente 50% sur EMA20.
    # On garde 100% de la position tant qu'une bougie 15m ne clôture pas sous EMA20.
    # L'ATR stop de sécurité reste géré plus bas.
    # V4.7 : Smart Exit obligatoire avant toute ancienne logique de sortie pour C/D/E/F
    if apply_smart_exit_before_legacy(symbol, pos, df, current_price, entry, atr_value, pnl_pct, candle_time, min_hold_active):
        return

    if trade_state.get("signal_mode") == "MODE_D_EMA_PULLBACK" and not is_smart_exit_mode():
        if not min_hold_active and last["close"] < last["ema20"]:
            LAST_SELL_REASON = "MODE_D_CLOSE_UNDER_EMA20"
            print("[MODE D] Clôture sous EMA20. Sortie totale.")
            notify_telegram(
                f"📉 <b>MODE D - Clôture sous EMA20</b>\n"
                f"Symbole: {symbol}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Close: {last['close']:.5f}\n"
                f"EMA20: {last['ema20']:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Solde: {round(get_balance(), 2)} USDC\n"
                f"Action: vente 100%"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        # Important : pour le Mode D, on ignore le TP1 standard et le trailing.
        # Seules sorties : close sous EMA20 ou ATR_STOP.
        if hard_stop_hit:
            LAST_SELL_REASON = "ATR_STOP"
            print("[MODE D] ATR stop touché. Sortie totale.")
            notify_telegram(
                f"🛑 <b>MODE D - ATR STOP</b>\n"
                f"Symbole: {symbol}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Stop: {hard_stop:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Solde: {round(get_balance(), 2)} USDC"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        return

    # Gestion spéciale MODE E :
    # Pas de TP1, pas de vente partielle.
    # Sorties : 2 clôtures sous EMA20 ou ATR stop x2.5.
    if trade_state.get("signal_mode") == "MODE_E_EARLY_MOMENTUM" and not is_smart_exit_mode():
        mode_e_stop = entry - MODE_E_ATR_STOP_MULT * atr_value
        mode_e_ema_break = (
            not min_hold_active
            and last["close"] < last["ema20"]
            and prev["close"] < prev["ema20"]
        )

        if mode_e_ema_break:
            LAST_SELL_REASON = "MODE_E_2_CLOSES_UNDER_EMA20"
            print("[MODE E] 2 clôtures sous EMA20. Sortie totale.")
            notify_telegram(
                f"📉 <b>MODE E - 2 clôtures sous EMA20</b>\n"
                f"Symbole: {symbol}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Close: {last['close']:.5f}\n"
                f"EMA20: {last['ema20']:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Solde: {round(get_balance(), 2)} USDC\n"
                f"Action: vente 100%"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        if current_price <= mode_e_stop:
            LAST_SELL_REASON = "MODE_E_ATR_STOP"
            print("[MODE E] ATR stop touché. Sortie totale.")
            notify_telegram(
                f"🛑 <b>MODE E - ATR STOP</b>\n"
                f"Symbole: {symbol}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Stop: {mode_e_stop:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Solde: {round(get_balance(), 2)} USDC"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        return

    # SMART EXIT pour les modes de retournement C / E / F
    if trade_state.get("signal_mode") in SMART_EXIT_REVERSAL_MODES:
        hard_stop = entry - SMART_EXIT_ATR_STOP_MULT * atr_value

        if current_price <= hard_stop:
            LAST_SELL_REASON = "SMART_EXIT_ATR_STOP"
            print("[SMART EXIT] ATR stop touché. Sortie totale.")
            notify_telegram(
                f"🛑 <b>SMART EXIT - ATR STOP</b>\n"
                f"Symbole: {symbol}\n"
                f"Mode: {trade_state.get('signal_mode')}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"Stop: {hard_stop:.5f}\n"
                f"PnL: {pnl_pct:.2f}%"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        smart_close, smart_tighten, smart_score, smart_details = smart_exit_reversal_decision(df, current_price, entry, trade_state.get("signal_mode"))

        trade_state["smart_exit_score"] = round(smart_score, 2)
        trade_state["smart_exit_health"] = smart_details.get("health")

        if smart_close and not min_hold_active:
            LAST_SELL_REASON = "SMART_EXIT_REVERSAL_SCORE"
            print(f"[SMART EXIT] Sortie score={smart_score} details={smart_details}")
            notify_telegram(
                f"📉 <b>SMART EXIT - Sortie retournement</b>\n"
                f"Symbole: {symbol}\n"
                f"Mode: {trade_state.get('signal_mode')}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Score sortie: {smart_score:.2f}\n"
                f"Patterns: {smart_details.get('bearish_patterns')}\n"
                f"OBV faible: {smart_details.get('obv_bad')}\n"
                f"Volume vendeur: {smart_details.get('red_volume')}\n"
                f"Sous EMA20: {smart_details.get('close_under_ema20')}"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return

        if smart_tighten:
            tightened_stop = current_price - 0.8 * atr_value
            current_trail = trade_state.get("trailing_stop")
            if current_trail is None or tightened_stop > current_trail:
                trade_state["trailing_stop"] = tightened_stop
                print(f"[SMART EXIT] Trailing resserré à {tightened_stop:.5f} score={smart_score}")

            if trade_state.get("trailing_stop") is not None and current_price <= trade_state["trailing_stop"]:
                LAST_SELL_REASON = "SMART_EXIT_TIGHT_TRAIL"
                print("[SMART EXIT] Trailing resserré touché. Sortie totale.")
                if close_position_full(pos, current_price):
                    final_balance = round(get_balance(), 2)
                    send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                    reset_trade_state_after_sell(candle_time)
                return

        return

    if not trade_state["tp1_done"] and current_price >= tp1_price:
        LAST_SELL_REASON = "TP1_PARTIAL"
        trade_state["tp1_price"] = round(tp1_price, 5)
        trade_state["tp1_exec_price"] = round(current_price, 5)
        trade_state["tp1_qty"] = abs(pos["size"]) * 0.5
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
        LAST_SELL_REASON = "ATR_STOP"
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
            final_balance = round(get_balance(), 2)
            send_trade_summary(symbol, current_price, LAST_SELL_REASON or "ATR_STOP", final_balance)
            reset_trade_state_after_sell(candle_time)
        return

    if trailing_hit and trade_state["tp1_done"]:
        LAST_SELL_REASON = "TRAILING_STOP"
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
            final_balance = round(get_balance(), 2)
            send_trade_summary(symbol, current_price, LAST_SELL_REASON or "TRAILING_STOP", final_balance)
            reset_trade_state_after_sell(candle_time)
        return

    if ema_break:
        LAST_SELL_REASON = "EMA20_2_CLOSES"
        print("[SELL] 2 clôtures consécutives sous EMA20.")
        notify_telegram(
            f"📉 <b>Sortie EMA20 - 2 clôtures</b>\n"
            f"Symbole: {symbol}\n"
            f"Entry: {entry:.5f}\n"
            f"Prix: {current_price:.5f}\n"
            f"EMA20: {last['ema20']:.5f}\n"
            f"PnL: {pnl_pct:.2f}%\n"
            f"Solde: {round(get_balance(), 2)} USDC"
        )
        if close_position_full(pos, current_price):
            final_balance = round(get_balance(), 2)
            send_trade_summary(symbol, current_price, LAST_SELL_REASON or "EMA20_2_CLOSES", final_balance)
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

    for symbol in get_enabled_symbols():
        df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
        if df is None:
            continue

        new_candle = confirmed_time != last_candle_times.get(symbol)
        if not new_candle:
            continue

        last_candle_times[symbol] = confirmed_time
        last = df.iloc[-1]
        ok, score, details = buy_signal(df)

        # MODE E INTRABAR :
        # Si aucun signal 15m classique n'est validé, on teste l'impulsion en cours.
        if not ok and is_mode_enabled("MODE_E_EARLY_MOMENTUM"):
            intra_ok, intra_score, intra_details = mode_e_intrabar_signal(df, current_price)
            if intra_ok:
                ok = True
                score = round(intra_score, 4)
                details["score"] = score
                details["signal_mode"] = "MODE_E_EARLY_MOMENTUM"
                details["mode_e_ok"] = True
                details["mode_e_intrabar"] = True
                details["mode_e_intrabar_move_pct"] = intra_details.get("mode_e_intrabar_move_pct")
                details["mode_e_volume_ratio"] = intra_details.get("mode_e_intrabar_volume_ratio")
                details["mode_e_body_ratio"] = None
                details["mode_e_intrabar_distance_ema20_pct"] = intra_details.get("mode_e_intrabar_distance_ema20_pct")

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
            "| mode_c:", details.get("mode_c_ok"),
            "| mode_d:", details.get("mode_d_ok"),
            "| pattern_score:", round(details["pattern_score"], 4),
            "| score:", round(details["score"], 4),
        )

        signal_text = "BUY" if ok else "HOLD"
        add_analysis_entry(symbol, last, current_price, details, signal_text)

        add_log_line(
            f"[CANDLE CLOSE] {now} {symbol} Close={last['close']:.5f} Current={current_price:.5f} "
            f"EMA20={last['ema20']:.5f} EMA50={last['ema50']:.5f} RSI={last['rsi']:.2f} "
            f"ATR={last['atr']:.5f} VWAP={last['vwap']:.5f} OBV={last['obv']:.2f} OBV_MA5={last['obv_ma5']:.2f}"
        )
        add_log_line(
            f"[SIGNAL] {symbol} {signal_text} trend={details['trend_ok']} rsi={details['rsi_ok']} "
            f"vwap={details['vwap_ok']} obv={details['obv_rising']} breakout={details['breakout_ok']} "
            f"green={details['green_candle']} mode={details.get('signal_mode')} "
            f"mode_a={details.get('mode_a_ok')} mode_b={details.get('mode_b_ok')} mode_c={details.get('mode_c_ok')} "
            f"mode_d={details.get('mode_d_ok')} mode_e={details.get('mode_e_ok')} d1={details.get('mode_d_pullback_between_emas')} d2={details.get('mode_d_cross_both_emas')} "
            f"pattern_score={round(details['pattern_score'], 4)} score={round(details['score'], 4)}"
        )

        if ok:
            adaptive_block, adaptive_mult, adaptive_info = should_adaptive_block(symbol, details.get("signal_mode"), details)
            details["adaptive_multiplier"] = adaptive_mult
            details["adaptive_info"] = adaptive_info
            score_before_adaptive = score
            score = round(score * adaptive_mult, 4)
            details["score_before_adaptive"] = score_before_adaptive
            details["score"] = score
            if adaptive_block:
                ok = False
                print(f"[ADAPTIVE BLOCK] {symbol} {details.get('signal_mode')} mult={adaptive_mult} score_before={score_before_adaptive}")


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

    print("BOT MULTI V4.8 ADAPTIVE INTELLIGENCE")
    print("Long only | 15m | Une seule position à la fois")
    print("Indicateurs : EMA20/EMA50 + RSI + ATR + OBV_MA5 + VWAP + Breakout + Patterns + Mode A/B/C/D/E/F")
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
    ensure_trades_csv()
    ensure_post_trade_files()
    save_bot_config(load_bot_config())
    start_dashboard_server()
    initialize_telegram_offset()

    notify_telegram(
        "🤖 <b>BOT MULTI V4.8 Adaptive Intelligence démarré</b>\n"
        f"Coins: {', '.join(SYMBOLS)}\n"
        f"Max position: 1\n"
        f"Solde: {start_balance} USDC\n"
        f"Taille trade: {TRADE_SIZE_USD} USDC\n"
        f"Commandes: /report /csv /analysis /log /posttrade /posttrade_repair /help\n"
        f"Dashboard: port {DASHBOARD_PORT}"
    )

    trades = 0

    while True:
        try:
            check_telegram_commands()
            maybe_send_monthly_report()
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

                add_log_line(
                    f"[POSITION CHECK] {now} {symbol} Close={last['close']:.5f} Current={current_price:.5f} "
                    f"EMA20={last['ema20']:.5f} EMA50={last['ema50']:.5f} RSI={last['rsi']:.2f} "
                    f"ATR={last['atr']:.5f} VWAP={last['vwap']:.5f} OBV={last['obv']:.2f}"
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
                f"Score avant adaptive: {details.get('score_before_adaptive')}\n"
                f"Adaptive multiplier: {details.get('adaptive_multiplier')}\n"
                f"Adaptive info: {details.get('adaptive_info')}\n"
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
                f"Mode C Reversal: {details.get('mode_c_reversal')}\n"
                f"Mode C Drop: {details.get('mode_c_drop_pct')}%\n"
                f"Mode C Volume OK: {details.get('mode_c_volume_ok')}\n"
                f"Mode C Wick OK: {details.get('mode_c_wick_ok')}\n"
                f"Mode D: {details.get('mode_d_ok')}\n"
                f"Mode D type D1 entre EMA: {details.get('mode_d_pullback_between_emas')}\n"
                f"Mode D type D2 cross double EMA: {details.get('mode_d_cross_both_emas')}\n"
                f"Mode D EMA basse: {details.get('mode_d_entry_ema_low')}\n"
                f"Mode D EMA haute cible: {details.get('mode_d_target_ema_high')}\n"
                f"Mode D sortie: clôture sous EMA20\n"
                f"Mode E: {details.get('mode_e_ok')}\n"
                f"Mode E Volume ratio: {details.get('mode_e_volume_ratio')}x\n"
                f"Mode E Body ratio: {details.get('mode_e_body_ratio')}\n"
                f"Mode E Intrabar: {details.get('mode_e_intrabar')}\n"
                f"Mode E Intrabar move: {details.get('mode_e_intrabar_move_pct')}%\n"
                f"Mode E sortie: 2 clôtures sous EMA20\n"
                f"Close > EMA20: {details.get('close_above_ema20')}\n"
                f"Min hold EMA20: {MIN_HOLD_CANDLES_AFTER_BUY} bougie(s)\n"
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
                trade_state["entry_candle_time"] = confirmed_time

                # Sauvegarde infos signal pour résumé final Telegram
                trade_state["entry_balance"] = round(balance, 2)
                trade_state["signal_mode"] = details.get("signal_mode")
                trade_state["score"] = round(best["score"], 4)
                trade_state["pattern_score"] = round(details.get("pattern_score", 0), 4)
                trade_state["bull_flag"] = details.get("bull_flag")
                trade_state["ascending_triangle"] = details.get("ascending_triangle")
                trade_state["compression_breakout"] = details.get("compression_breakout")
                trade_state["higher_lows"] = details.get("higher_lows")
                trade_state["mode_c_reversal"] = details.get("mode_c_reversal")
                trade_state["mode_c_drop_pct"] = details.get("mode_c_drop_pct")
                trade_state["mode_c_volume_ok"] = details.get("mode_c_volume_ok")
                trade_state["mode_c_wick_ok"] = details.get("mode_c_wick_ok")
                trade_state["mode_d_entry_ema_low"] = details.get("mode_d_entry_ema_low")
                trade_state["mode_d_target_ema_high"] = details.get("mode_d_target_ema_high")
                if details.get("mode_d_cross_both_emas"):
                    trade_state["mode_d_type"] = "D2_CROSS_BOTH_EMAS"
                elif details.get("mode_d_pullback_between_emas"):
                    trade_state["mode_d_type"] = "D1_BETWEEN_EMAS"
                else:
                    trade_state["mode_d_type"] = None
                trade_state["mode_e_volume_ratio"] = details.get("mode_e_volume_ratio")
                trade_state["mode_e_body_ratio"] = details.get("mode_e_body_ratio")
                trade_state["mode_e_intrabar"] = details.get("mode_e_intrabar")
                trade_state["mode_e_intrabar_move_pct"] = details.get("mode_e_intrabar_move_pct")
                trade_state["mode_f_pattern"] = details.get("mode_f_pattern")
                trade_state["mode_f_score"] = details.get("mode_f_score")
                trade_state["mode_f_details_text"] = details.get("mode_f_details_text")
                if details.get("signal_mode") in ["MODE_D_EMA_PULLBACK", "MODE_E_EARLY_MOMENTUM"]:
                    trade_state["tp1_price"] = None
                else:
                    trade_state["tp1_price"] = round(current_price + ATR_TP_MULT * last["atr"], 5)
                trade_state["tp1_exec_price"] = None
                trade_state["tp1_qty"] = None
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
