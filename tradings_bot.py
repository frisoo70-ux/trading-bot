import time
import json
import requests
import pandas as pd
from datetime import datetime
import os
import csv
import shutil
import base64
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
TRADE_SIZE_USD = 20
CHECK_INTERVAL = 60

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_CSV_PATH = DATA_DIR / "trades.csv"
POST_TRADE_CSV_PATH = DATA_DIR / "post_trade_analysis.csv"
POST_TRADE_PENDING_PATH = DATA_DIR / "post_trade_pending.json"
LAST_MONTHLY_REPORT_FILE = DATA_DIR / "last_monthly_report.txt"
BOT_CONFIG_PATH = DATA_DIR / "bot_config.json"
ADAPTIVE_CONFIG_PATH = DATA_DIR / "adaptive_config.json"
AUTO_OPTIMIZER_PATH = DATA_DIR / "auto_optimizer.json"

# AUTO-OPTIMIZER V6.8
AUTO_OPTIMIZER_ENABLED = False
AUTO_OPTIMIZER_WINDOW = 80
AUTO_OPTIMIZER_MIN_SAMPLE = 4
AUTO_OPTIMIZER_MIN_TP1_MULT = 0.85
AUTO_OPTIMIZER_MAX_TP1_MULT = 2.00
AUTO_OPTIMIZER_MIN_STOP_MULT = 1.60
AUTO_OPTIMIZER_MAX_STOP_MULT = 3.00
AUTO_OPTIMIZER_MIN_PATIENCE = 0
AUTO_OPTIMIZER_MAX_PATIENCE = 2

# ============================================================
# V7 STABILISATION / PROTECTION
# Basée sur l'analyse des 241 trades fournis.
# - désactive les deux systèmes adaptatifs qui apprenaient trop tôt
# - supprime la sortie mécanique EMA20 après 2 clôtures
# - remet MODE D/E dans la gestion standard quand Smart Exit n'est pas actif
# - utilise un ATR de référence robuste pour éviter les stops trop serrés
# ============================================================
V7_STABILIZATION_ENABLED = True
V7_DISABLE_AUTO_OPTIMIZER = True
V7_DISABLE_AUTO_LEARNING = False
V7_DISABLE_ADAPTIVE_BLOCKING = False
V7_EMA20_EXIT_ENABLED = False
V7_DYNAMIC_ATR_STOP_ENABLED = True
V7_ATR_STOP_MULT = 2.8
V7_SMART_EXIT_ATR_STOP_MULT = 2.8
V7_ATR_REFERENCE_MIN_FACTOR = 0.85


# Sauvegarde GitHub privée (facultative mais recommandée)
# Variables Railway :
# GITHUB_BACKUP_TOKEN = token GitHub finement limité au dépôt
# GITHUB_BACKUP_REPO = propriétaire/nom-du-depot
# GITHUB_BACKUP_BRANCH = main
# GITHUB_BACKUP_PREFIX = bot-data
GITHUB_BACKUP_TOKEN = os.environ.get("GITHUB_BACKUP_TOKEN", "")
GITHUB_BACKUP_REPO = os.environ.get("GITHUB_BACKUP_REPO", "")
GITHUB_BACKUP_BRANCH = os.environ.get("GITHUB_BACKUP_BRANCH", "main")
GITHUB_BACKUP_PREFIX = os.environ.get("GITHUB_BACKUP_PREFIX", "bot-data").strip("/")
GITHUB_BACKUP_ENABLED = bool(GITHUB_BACKUP_TOKEN and GITHUB_BACKUP_REPO)

GITHUB_BACKUP_FILES = [
    "trades.csv",
    "post_trade_pending.json",
    "post_trade_analysis.csv",
    "last_monthly_report.txt",
    "bot_config.json",
    "adaptive_config.json",
]

GITHUB_SYNC_LOCK = threading.Lock()
GITHUB_SYNC_STATE = {
    "enabled": GITHUB_BACKUP_ENABLED,
    "restored": False,
    "last_restore": None,
    "last_backup": None,
    "last_status": "DISABLED" if not GITHUB_BACKUP_ENABLED else "READY",
    "last_error": "",
    "uploaded_files": 0,
    "downloaded_files": 0,
    "dirty": False,
    "worker_running": False,
}


DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DASHBOARD_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8000"))))

TIMEFRAME_OPTIONS = ["1m", "3m", "5m", "15m"]
DEFAULT_TIMEFRAME = "15m"
TIMEFRAME_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}


BASE_URL = "https://api.hyperliquid.xyz"

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_BUY_LEVEL = 55
RSI_BUY_MAX = 70

# TREND SCORE V6.5
TREND_SCORE_MODE_A_MIN = 6
TREND_SCORE_MODE_B_MIN = 4
TREND_SCORE_MODE_D_MIN = 3
TREND_SCORE_MODE_F_MIN = 2


ATR_STOP_MULT = 2.5
ATR_TP_MULT = 2.0

# TP1 ADAPTATIF V6.6
ADAPTIVE_TP1_ENABLED = True
ADAPTIVE_TP1_WINDOW = 30
ADAPTIVE_TP1_MIN_SAMPLE = 3
ADAPTIVE_TP1_MIN_MULT = 0.90
ADAPTIVE_TP1_MAX_MULT = 2.00
ADAPTIVE_TP1_MODE_MULT = {
    "MODE_A_CLASSIQUE": 1.70,
    "MODE_B_PATTERN": 1.45,
    "MODE_C_CAPITULATION": 1.15,
    "MODE_D_EMA_PULLBACK": 1.25,
    "MODE_E_EARLY_MOMENTUM": 1.35,
    "MODE_F_CANDLE_REVERSAL": 1.10,
}
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

# MODE F - Candle Reversal renforcé
# Ancien Mode F : trop permissif, achetait des patterns dans une tendance baissière.
# Nouveau Mode F : pattern + confirmation + contexte.
MODE_F_MIN_PATTERN_SCORE = 3.5
MODE_F_VOLUME_MULT = 1.3
MODE_F_RSI_MAX = 62
MODE_F_ATR_STOP_MULT = 2.5
MODE_F_MAX_EMA20_SLOPE_DOWN_PCT = -0.0015  # -0.15% max sur 3 bougies
MODE_F_MAX_DISTANCE_BELOW_EMA20_PCT = 0.006  # max 0.6% sous EMA20 si pas encore au-dessus
MODE_F_REQUIRE_BREAK_PATTERN_HIGH = True
MODE_F_REQUIRE_OBV_RISING_2 = True
MODE_F_REQUIRE_NOT_UNDER_EMA50 = True
MODE_F_MIN_CONFIRMATIONS = 4

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
SMART_EXIT_ATR_STOP_MULT = 2.8
POST_TRADE_FORCE_FINALIZE_AFTER_MS = 4 * 60 * 60 * 1000

# ADAPTIVE SMART EXIT - apprend avec le Post Trade Analyzer
SMART_EXIT_ADAPTIVE_ENABLED_DEFAULT = False

# SMART EXIT PATIENCE V6.4
SMART_EXIT_PATIENCE_ENABLED = True
SMART_EXIT_PATIENCE_MIN_SAMPLE = 3
SMART_EXIT_PATIENCE_MAX_CANDLES = 2
SMART_EXIT_PATIENCE_MIN_EXPECTED_IMPROVEMENT_PCT = 0.15
SMART_EXIT_PATIENCE_CANCEL_HEALTH = 58
SMART_EXIT_PATIENCE_CRITICAL_HEALTH = 12
SMART_EXIT_PATIENCE_PEAK_GIVEBACK_PCT = 0.18

SMART_EXIT_ADAPTIVE_MIN_ANALYSES = 8
SMART_EXIT_ADAPTIVE_WINDOW = 30
SMART_EXIT_ADAPTIVE_TOO_EARLY_RATIO = 0.50
SMART_EXIT_ADAPTIVE_GOOD_RATIO = 0.45
SMART_EXIT_ADAPTIVE_MAX_LEVEL = 3
SMART_EXIT_ADAPTIVE_THRESHOLD_STEP = 0.75
SMART_EXIT_ADAPTIVE_HEALTH_STEP = 4
SMART_EXIT_ADAPTIVE_TIGHTEN_STEP = 0.50


# ADAPTIVE INTELLIGENCE V4.8
ADAPTIVE_ENABLED_DEFAULT = True
ADAPTIVE_MIN_TRADES_MODE = 8
ADAPTIVE_MIN_TRADES_PATTERN = 5
ADAPTIVE_MIN_TRADES_TOKEN = 6
ADAPTIVE_MIN_MULT = 0.70
ADAPTIVE_MAX_MULT = 1.30
ADAPTIVE_HARD_BLOCK_MULT = 0.75
ADAPTIVE_RECENT_TRADES_WINDOW = 50

# AUTO LEARNING V6
AUTO_LEARNING_ENABLED_DEFAULT = True
AUTO_LEARNING_AUTO_APPLY_DEFAULT = True
AUTO_LEARNING_WINDOW = 80
AUTO_LEARNING_MIN_TRADES_MODE = 8
AUTO_LEARNING_MIN_TRADES_PATTERN = 5
AUTO_LEARNING_MIN_TRADES_TOKEN = 6
AUTO_LEARNING_BAD_WINRATE = 38.0
AUTO_LEARNING_BAD_AVG_PNL = -0.06
AUTO_LEARNING_GOOD_WINRATE = 58.0
AUTO_LEARNING_GOOD_AVG_PNL = 0.04
AUTO_LEARNING_DISABLE_MULT = 0.45
# Aucun blocage Auto Learning n'est définitif.
# Un élément mauvais passe en quarantaine, puis revient automatiquement en phase de retest.
AUTO_LEARNING_BLOCK_HOURS = 12
AUTO_LEARNING_RETEST_TRADES = 3
POST_TRADE_RECALC_HISTORICAL_ENABLED = True
POST_TRADE_DATA_MISSING_STATUS = "DATA_MISSING"

COOLDOWN_CANDLES_AFTER_SELL = 2

# RÉENTRÉE INTELLIGENTE V6.7
SMART_REENTRY_ENABLED = True
SMART_REENTRY_OTHER_TOKEN_MIN_SCORE = 3.0
SMART_REENTRY_OTHER_TOKEN_MIN_TREND_SCORE = 3
SMART_REENTRY_SAME_TOKEN_MIN_SCORE = 5.0
SMART_REENTRY_SAME_TOKEN_STRONG_SCORE = 7.0
SMART_REENTRY_SAME_TOKEN_MIN_TREND_SCORE = 4
SMART_REENTRY_BLOCKED_EXIT_REASONS = {
    "ATR_STOP",
    "SMART_EXIT_ATR_STOP",
    "MODE_E_ATR_STOP",
    "SMART_EXIT_CONFIRMED_CRITICAL",
    "SMART_EXIT_UNIFIED_CRITICAL",
}

MIN_HOLD_CANDLES_AFTER_BUY = 1

# MODE L - Liquidity Sweep (pools, sweep/reclaim, directionnel)
MODE_L_ENABLED_DEFAULT = True
MODE_L_LOOKBACK = 30
MODE_L_PIVOT_LEFT = 2
MODE_L_PIVOT_RIGHT = 2
MODE_L_EQUAL_TOL_PCT = 0.0015
MODE_L_SWEEP_BUFFER_ATR = 0.08
MODE_L_ATR_STOP_MULT = 1.8
MODE_L_MIN_RR = 1.25

last_candle_times = {symbol: None for symbol in SYMBOLS}

# Historique en mémoire pour commandes Telegram /analysis et /log
ANALYSIS_HISTORY = []
LOG_HISTORY = []
API_CACHE = {}
API_STATS = {
    "requests": 0,
    "success": 0,
    "errors": 0,
    "rate_limits": 0,
    "retries": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "last_status": "INIT",
    "last_error": "",
    "last_latency_ms": 0,
    "last_request": None,
}
API_LOCK = threading.Lock()
# Cache graphique borné : données déjà calculées par le moteur, sans appel API dédié.
DASHBOARD_CHART_CACHE = {}
DASHBOARD_CHART_LOCK = threading.Lock()
ORDER_LOCK = threading.Lock()
ORDER_STATE = {
    "pending": False,
    "side": None,
    "symbol": None,
    "reason": None,
    "started_at": None,
    "retry_after": 0,
    "last_error": "",
    "attempts": 0,
}

API_BACKOFF_UNTIL = 0
LAST_KNOWN_BALANCE = None
LAST_KNOWN_POSITIONS = None

LAST_KNOWN_BALANCE = None
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
    "smart_exit_pending": False,
    "smart_exit_pending_since": None,
    "smart_exit_pending_candle_time": None,
    "smart_exit_wait_candles": 0,
    "smart_exit_pending_peak": None,
    "smart_exit_pending_score": None,
    "smart_exit_pending_reason": None,
    "smart_exit_learning_note": None,
    "tp1_price": None,
    "tp1_exec_price": None,
    "tp1_qty": None,
    "tp1_adaptive_mult": None,
    "tp1_adaptive_reason": None,
    "tp1_adaptive_sample": 0,
    "optimizer_profile_key": None,
    "optimizer_tp1_mult": None,
    "optimizer_stop_mult": None,
    "optimizer_patience_candles": None,
    "optimizer_confidence": None,
    "optimizer_reason": None,
    "mode_l_side": None, "mode_l_stop": None, "mode_l_target": None, "mode_l_liquidity": None,
}

META_BY_SYMBOL = {}
SIZE_DECIMALS_BY_SYMBOL = {symbol: 2 for symbol in SYMBOLS}
LAST_ENTRY_PRICE_BY_SYMBOL = {symbol: None for symbol in SYMBOLS}
LAST_SELL_REASON = None
REENTRY_RESCAN_PENDING = False
LAST_EXIT_SYMBOL = None
LAST_EXIT_REASON = None
LAST_EXIT_MODE = None
LAST_EXIT_CANDLE_TIME = None
LAST_EXIT_WALL_TIME = None
TELEGRAM_LAST_UPDATE_ID = None



DEFAULT_BOT_CONFIG = {
    "modes": {
        "MODE_A_CLASSIQUE": True,
        "MODE_B_PATTERN": True,
        "MODE_C_CAPITULATION": True,
        "MODE_D_EMA_PULLBACK": True,
        "MODE_E_EARLY_MOMENTUM": True,
        "MODE_F_CANDLE_REVERSAL": True,
        "MODE_L_LIQUIDITY_SWEEP": True,
    },
    "symbols": {
        "SOL": True,
        "XRP": True,
        "kSHIB": True,
    },
    "dashboard": {
        "refresh_seconds": 10
    },
    "timeframe": DEFAULT_TIMEFRAME
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

        merged["timeframe"] = cfg.get("timeframe", DEFAULT_TIMEFRAME)
        if merged["timeframe"] not in TIMEFRAME_OPTIONS:
            merged["timeframe"] = DEFAULT_TIMEFRAME

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
        schedule_github_backup()
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



def get_active_timeframe():
    try:
        tf = load_bot_config().get("timeframe", DEFAULT_TIMEFRAME)
        return tf if tf in TIMEFRAME_OPTIONS else DEFAULT_TIMEFRAME
    except Exception:
        return DEFAULT_TIMEFRAME


def get_active_timeframe_minutes():
    return TIMEFRAME_MINUTES.get(get_active_timeframe(), TIMEFRAME_MINUTES[DEFAULT_TIMEFRAME])


def get_candle_lookback_seconds(candles=220):
    return candles * get_active_timeframe_minutes() * 60


def get_cooldown_ms():
    return COOLDOWN_CANDLES_AFTER_SELL * get_active_timeframe_minutes() * 60 * 1000

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
                "opportunity_left_pct", "downside_avoided_pct",
                "exit_efficiency_pct", "exit_quality_score",
                "verdict", "status",
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
        schedule_github_backup()
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
        pnl_at_exit = float(item.get("pnl_at_exit_pct", 0) or 0)

        # Ce qui a été laissé sur la table après la sortie.
        opportunity_left = max(0.0, max_gain_after)

        # Baisse évitée après la sortie.
        downside_avoided = max(0.0, -max_loss_after)

        max_possible_from_entry = ((max_price - entry) / entry) * 100
        if max_possible_from_entry > 0:
            efficiency = (pnl_at_exit / max_possible_from_entry) * 100
            efficiency = max(min(efficiency, 100), -100)
        else:
            efficiency = None

        # Score de qualité sur 100.
        # Base élevée, puis pénalité si le prix progresse fortement après la sortie.
        quality_score = 92.0

        # Pénalité principale : opportunité abandonnée.
        quality_score -= min(opportunity_left * 42.0, 70.0)

        # Bonus si la sortie a évité une baisse importante.
        quality_score += min(downside_avoided * 16.0, 18.0)

        # Bonus si le trade est sorti en gain.
        if pnl_at_exit > 0:
            quality_score += min(pnl_at_exit * 8.0, 8.0)
        elif pnl_at_exit < 0:
            # Une petite perte reste acceptable si elle évite une forte baisse.
            quality_score -= min(abs(pnl_at_exit) * 7.0, 10.0)

        # Bonus de cohérence : le prix a peu progressé puis a nettement baissé.
        if opportunity_left <= 0.20 and downside_avoided >= 0.50:
            quality_score += 8.0

        quality_score = round(max(0.0, min(100.0, quality_score)), 1)

        if quality_score >= 90:
            verdict = "EXCELLENTE_SORTIE"
        elif quality_score >= 78:
            verdict = "TRES_BONNE_SORTIE"
        elif quality_score >= 65:
            verdict = "BONNE_SORTIE"
        elif quality_score >= 45:
            verdict = "SORTIE_ACCEPTABLE"
        else:
            verdict = "SORTIE_TROP_TOT"

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
            "opportunity_left_pct": round(opportunity_left, 4),
            "downside_avoided_pct": round(downside_avoided, 4),
            "exit_efficiency_pct": round(efficiency, 2) if efficiency is not None else None,
            "exit_quality_score": quality_score,
            "verdict": verdict,
            "status": "DONE",
        }

        df = pd.read_csv(POST_TRADE_CSV_PATH) if POST_TRADE_CSV_PATH.exists() else pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(POST_TRADE_CSV_PATH, index=False)
        schedule_github_backup(delay_seconds=5)

        print(
            f"[POST TRADE] Analyse finalisée {item.get('symbol')} "
            f"score={quality_score}/100 verdict={verdict}"
        )

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

        try:
            se = rebuild_adaptive_smart_exit_from_posttrade()
            if se.get("sample_size", 0) >= SMART_EXIT_ADAPTIVE_MIN_ANALYSES:
                recs.append(
                    f"Adaptive Smart Exit: {se.get('recommendation')} | sorties trop tôt {se.get('too_early_ratio')}% | niveau {se.get('level')}/3."
                )
        except Exception:
            pass
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
        "smart_exit": {
            "enabled": SMART_EXIT_ADAPTIVE_ENABLED_DEFAULT,
            "auto_apply": True,
            "level": 0,
            "recommendation": "NEUTRE",
            "sample_size": 0,
            "sortie_trop_tot": 0,
            "sortie_acceptable": 0,
            "bonne_sortie": 0,
            "tres_bonne_sortie": 0,
            "excellente_sortie": 0,
            "score_moyen": 0,
            "too_early_ratio": 0,
            "good_ratio": 0,
            "threshold_bonus": 0,
            "health_bonus": 0,
            "tighten_bonus": 0,
            "last_updated": None,
        },
        "auto_learning": {
            "enabled": AUTO_LEARNING_ENABLED_DEFAULT,
            "auto_apply": AUTO_LEARNING_AUTO_APPLY_DEFAULT,
            "sample_size": 0,
            "recommendation": "NEUTRE",
            "mode_multipliers": {},
            "pattern_multipliers": {},
            "token_multipliers": {},
            "blocked_modes": [],
            "blocked_patterns": [],
            "blocked_tokens": [],
            "temporary_blocks": {"modes": {}, "patterns": {}, "tokens": {}},
            "retesting_modes": [],
            "retesting_patterns": [],
            "retesting_tokens": [],
            "notes": [],
            "last_updated": None,
        },
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
        schedule_github_backup()
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
        # Les colonnes de pattern peuvent être renseignées sur d'autres modes :
        # seules les positions réellement ouvertes par le Mode F alimentent ces stats.
        if "mode_f_pattern" in df.columns and "mode" in df.columns:
            mode_f_rows = df[df["mode"].astype(str) == "MODE_F_CANDLE_REVERSAL"]
            for _, r in mode_f_rows.iterrows():
                for p in str(r.get("mode_f_pattern","") or "").split(","):
                    p=p.strip()
                    if p and p.lower() not in ("nan", "none"):
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
        # Les patterns de retournement ne concernent que les trades réellement
        # déclenchés par le Mode F. Ne jamais appliquer ce multiplicateur à A-E ou L.
        if details and str(mode) == "MODE_F_CANDLE_REVERSAL":
            pats=[p.strip() for p in str(details.get("mode_f_pattern","") or "").split(",") if p.strip()]
            vals=[float(cfg.get("pattern_multipliers",{}).get(p,1.0)) for p in pats]
            if vals: pattern_mult=min(vals)
        final=clamp_adaptive(mode_mult*token_mult*pattern_mult)
        return final, {"enabled":True,"mode_mult":round(mode_mult,3),"token_mult":round(token_mult,3),"pattern_mult":round(pattern_mult,3),"final_mult":round(final,3)}
    except Exception as e:
        return 1.0, {"enabled": False, "error": str(e)}

def should_adaptive_block(symbol, mode, details=None):
    if V7_STABILIZATION_ENABLED and V7_DISABLE_ADAPTIVE_BLOCKING:
        return False, 1.0, {"enabled": False, "reason": "V7 stabilization"}
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



def get_live_trade_snapshot_v48():
    """
    Snapshot live robuste pour le dashboard.
    Recalcule PnL / ATR / TP1 / SL depuis la position Hyperliquid + bougies,
    même après redémarrage Railway.
    """
    try:
        pos = get_open_position()
        if not pos:
            return {"in_position": False}

        symbol = pos.get("symbol")
        size = abs(float(pos.get("size", 0) or 0))
        entry = float(pos.get("entry") or trade_state.get("entry_price") or 0)

        df = None
        current_price = None
        atr_value = None
        ema20 = None
        ema50 = None
        rsi = None

        try:
            df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                atr_value = float(last.get("atr")) if not pd.isna(last.get("atr")) else None
                ema20 = float(last.get("ema20")) if not pd.isna(last.get("ema20")) else None
                ema50 = float(last.get("ema50")) if not pd.isna(last.get("ema50")) else None
                rsi = float(last.get("rsi")) if not pd.isna(last.get("rsi")) else None
        except Exception:
            current_price = None

        if current_price is None:
            current_price = pos.get("mark_price") or pos.get("price") or entry

        current_price = float(current_price or 0)

        stored_atr = trade_state.get("entry_atr")
        if stored_atr and not pd.isna(stored_atr):
            atr_value = float(stored_atr)
        elif atr_value is not None:
            atr_value = float(atr_value)

        pnl_pct = ((current_price - entry) / entry) * 100 if entry else 0
        pnl_usdc = (current_price - entry) * size if entry and size else 0

        tp1_done = bool(trade_state.get("tp1_done"))
        tp1_price = trade_state.get("tp1_price")
        hard_stop = None

        if atr_value and entry:
            if not tp1_price:
                tp1_price = trade_state.get("tp1_price")
            if not tp1_price:
                tp1_info = calculate_adaptive_tp1(
                    entry=entry,
                    atr_value=atr_value,
                    mode=trade_state.get("signal_mode"),
                    symbol=symbol,
                )
                tp1_price = tp1_info["price"]
            hard_stop = entry - ATR_STOP_MULT * atr_value

        highest = trade_state.get("highest_price")
        try:
            highest = float(highest) if highest else None
        except Exception:
            highest = None

        if highest is None:
            highest = max(entry, current_price)
            try:
                if df is not None and not df.empty:
                    highs = pd.to_numeric(df["high"], errors="coerce").dropna()
                    if not highs.empty:
                        highest = max(highest, float(highs.tail(80).max()))
            except Exception:
                pass

        trailing_stop = trade_state.get("trailing_stop")
        if trailing_stop is None and atr_value and highest and tp1_done:
            trailing_stop = highest - ATR_TRAIL_MULT * atr_value

        def safe_round(x, n=8):
            try:
                if x is None or pd.isna(x):
                    return None
                return round(float(x), n)
            except Exception:
                return None

        def dist_pct(target):
            try:
                if target is None or current_price == 0:
                    return None
                return ((float(target) - current_price) / current_price) * 100
            except Exception:
                return None

        def progress_between(stop, target):
            try:
                if stop is None or target is None:
                    return None
                stop = float(stop)
                target = float(target)
                if target == stop:
                    return None
                return max(0, min(100, ((current_price - stop) / (target - stop)) * 100))
            except Exception:
                return None

        return {
            "in_position": True,
            "symbol": symbol,
            "mode": trade_state.get("signal_mode") or "RECOVERED_POSITION",
            "score": trade_state.get("score"),
            "entry": safe_round(entry),
            "current_price": safe_round(current_price),
            "size": safe_round(size),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usdc_est": round(pnl_usdc, 6),
            "tp1_done": tp1_done,
            "tp1_price": safe_round(tp1_price),
            "hard_stop": safe_round(hard_stop),
            "trailing_stop": safe_round(trailing_stop),
            "highest_price": safe_round(highest),
            "entry_atr": safe_round(atr_value),
            "ema20": safe_round(ema20),
            "ema50": safe_round(ema50),
            "rsi": safe_round(rsi, 2),
            "distance_to_tp1_pct": round(dist_pct(tp1_price), 4) if dist_pct(tp1_price) is not None else None,
            "distance_to_sl_pct": round(dist_pct(hard_stop), 4) if dist_pct(hard_stop) is not None else None,
            "distance_to_trailing_pct": round(dist_pct(trailing_stop), 4) if dist_pct(trailing_stop) is not None else None,
            "progress_to_tp1_pct": round(progress_between(hard_stop, tp1_price), 2) if hard_stop and tp1_price else None,
            "timeframe": get_active_timeframe(),
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "recovered_from_position": trade_state.get("signal_mode") is None,
        }

    except Exception as e:
        return {"in_position": False, "error": str(e)}



def compute_dashboard_stats():
    force_finalize_expired_post_trades()
    df = load_trades_dataframe()
    balance = round(get_balance(), 2)

    result = {
        "balance": balance,
        "open_position": get_open_position(),
        "live_trade": get_live_trade_snapshot_v48(),
        "config": load_bot_config(),
        "active_timeframe": get_active_timeframe(),
        "timeframe_options": TIMEFRAME_OPTIONS,
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
        "mode_l_stats": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0, "winrate": 0},
        "mode_l_live": {"active": trade_state.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP", "side": trade_state.get("mode_l_side"), "liquidity": trade_state.get("mode_l_liquidity"), "stop": trade_state.get("mode_l_stop"), "target": trade_state.get("mode_l_target")},
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
        "adaptive_smart_exit": rebuild_adaptive_smart_exit_from_posttrade(),
        "auto_learning": rebuild_auto_learning_from_history(),
        "auto_optimizer": rebuild_auto_optimizer_profiles(),
        "analysis_history": ANALYSIS_HISTORY[-80:],
        "log_history": LOG_HISTORY[-120:],
        "api_status": dict(API_STATS),
        "order_state": get_order_state_for_dashboard(),
        "storage_status": persistent_storage_status(),
        "github_sync": github_sync_status(),
        "smart_exit_patience": smart_exit_patience_snapshot(),
        "smart_reentry": {
            "enabled": SMART_REENTRY_ENABLED,
            "rescan_pending": REENTRY_RESCAN_PENDING,
            "last_exit_symbol": LAST_EXIT_SYMBOL,
            "last_exit_reason": LAST_EXIT_REASON,
            "last_exit_mode": LAST_EXIT_MODE,
            "last_exit_candle_time": LAST_EXIT_CANDLE_TIME,
        },
    }

    try:
        if isinstance(result.get("adaptive"), dict):
            result["adaptive"]["smart_exit"] = result.get("adaptive_smart_exit")
            result["adaptive"]["auto_learning"] = result.get("auto_learning")

        pending_items = load_pending_post_trades()
        result["post_trade_pending"] = pending_items[-50:]

        post_df = load_post_trade_dataframe()
        if not post_df.empty:
            result["post_trade_analysis"] = post_df.tail(50).fillna("").to_dict(orient="records")
            verdicts = post_df.get("verdict", pd.Series(dtype=str)).fillna("")
            quality_scores = pd.to_numeric(
                post_df.get("exit_quality_score", pd.Series(dtype=float)),
                errors="coerce"
            )
            result["post_trade_summary"] = {
                "total": int(len(post_df)),
                "pending": int(len(pending_items)),
                "sortie_trop_tot": int((verdicts == "SORTIE_TROP_TOT").sum()),
                "sortie_acceptable": int((verdicts == "SORTIE_ACCEPTABLE").sum()),
                "bonne_sortie": int((verdicts == "BONNE_SORTIE").sum()),
                "tres_bonne_sortie": int((verdicts == "TRES_BONNE_SORTIE").sum()),
                "excellente_sortie": int((verdicts == "EXCELLENTE_SORTIE").sum()),
                "score_moyen": round(float(quality_scores.mean()), 1) if quality_scores.notna().any() else 0,
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
    mode_l_df=df[df.get("mode",pd.Series(index=df.index,dtype=str)).astype(str).str.contains("MODE_L",na=False)]
    if not mode_l_df.empty:
        lp=pd.to_numeric(mode_l_df["real_pnl_usdc"],errors="coerce").fillna(0)
        result["mode_l_stats"]={"trades":int(len(lp)),"wins":int((lp>0).sum()),"losses":int((lp<0).sum()),"pnl":round(float(lp.sum()),4),"winrate":round(float((lp>0).mean()*100),2)}
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
    input[type=radio]{appearance:none;width:28px;height:28px;border-radius:50%;background:#4b5563;border:2px solid #64748b;flex:0 0 auto}
    input[type=radio]:checked{background:var(--green);border-color:var(--green);box-shadow:inset 0 0 0 6px var(--card2)}
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
    .liveGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.liveBox{background:var(--card2);border:1px solid #2b3950;border-radius:14px;padding:12px}.liveLabel{color:var(--muted);font-size:12px}.liveVal{font-size:18px;font-weight:800;margin-top:5px}.bar{height:10px;background:#334155;border-radius:999px;overflow:hidden}.bar span{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--green));width:0%}.liveSub{font-size:12px;color:var(--muted);margin-top:8px}
    .chartHeader{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
    .chartSelect{background:#172033;color:#e5e7eb;border:1px solid #334155;border-radius:9px;padding:10px;min-width:100px}
    .chartSummary{margin:12px 0;padding:10px 12px;background:#0b1422;border:1px solid #263348;border-radius:10px;font-size:12px;line-height:1.5;color:#cbd5e1}
    .chartToolbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:10px 0}
    .chartTool{width:auto;min-height:34px;padding:7px 11px;border:1px solid #334155;border-radius:8px;background:#172033;color:#cbd5e1;font-size:12px;font-weight:700}
    .chartTool.active{background:#075985;border-color:#0ea5e9;color:#e0f2fe}
    .chartCanvas{width:100%;min-height:250px;overflow:hidden;background:#0b1422;border:1px solid #263348;border-radius:12px}
    .chartCanvas svg{display:block;width:100%;height:auto}
    .chartLegend{display:flex;gap:12px;flex-wrap:wrap;margin:10px 2px 4px;font-size:11px;color:#cbd5e1}
    .chartLegend span{display:inline-flex;align-items:center;gap:5px}
    .chartLegend i{display:inline-block;width:17px;height:3px;border-radius:2px}
    .chartOrderbook{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
    .bookSide{background:#0b1422;border:1px solid #263348;border-radius:10px;padding:10px;min-width:0}
    .bookTitle{font-size:12px;font-weight:800;margin-bottom:8px}
    .bookRow{display:grid;grid-template-columns:72px 1fr 72px;align-items:center;gap:7px;font-size:10px;margin:6px 0;font-variant-numeric:tabular-nums}
    .bookTrack{height:7px;background:#263348;border-radius:5px;overflow:hidden}
    .bookFill{height:100%;border-radius:5px}
    @media(max-width:520px){.chartOrderbook{grid-template-columns:1fr}.bookRow{grid-template-columns:64px 1fr 65px}.chartTool{min-height:32px;padding:6px 8px}}

    @media(max-width:700px){.liveGrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:900px){.cards,.two{grid-template-columns:1fr}.wrap{padding:10px}.card{padding:13px;border-radius:15px}table{font-size:12px;min-width:720px}th,td{padding:9px 7px}.mobileHint{display:block}}
    @media(max-width:520px){h1{font-size:18px}h3{font-size:16px}.sub{font-size:11px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{font-size:22px}.card{padding:12px}.toggles{grid-template-columns:1fr}.toggle{font-size:15px;padding:14px 13px}.scroll{max-height:360px}pre{max-height:300px;font-size:11px}}
    @media(max-width:380px){.cards{grid-template-columns:1fr}.metric{font-size:24px}}
  </style>
</head>
<body>
<header><h1>🤖 Trading Bot</h1><div class="sub">Dashboard iPhone · V6.9.1 Adaptive Intelligence Auto Optimizer Dashboard Fix Live PnL Robust · Modes A/B/C/D/E/F · SOL / XRP / kSHIB</div></header>
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
      <div class="muted" style="margin:14px 0 8px">Timeframe</div><div id="timeframeToggles" class="toggles"></div>
      <div class="muted" id="activeTimeframeLabel" style="margin-top:8px">Timeframe actif : -</div>
      <div style="margin-top:14px"><button onclick="saveConfig()">Sauvegarder</button><div id="saveStatus" class="muted" style="margin-top:8px"></div></div>
    </div>
    <div class="card"><h3>📌 Position ouverte</h3><pre id="position">-</pre></div>
  </div>
  <div class="card" id="liveTradePanel" style="margin-top:12px">
    <h3>🔥 Trade en live · PnL / TP1 / SL</h3>
    <div class="liveGrid">
      <div class="liveBox"><div class="liveLabel">Coin</div><div class="liveVal" id="liveSymbol">-</div></div>
      <div class="liveBox"><div class="liveLabel">Mode</div><div class="liveVal" id="liveMode">-</div></div>
      <div class="liveBox"><div class="liveLabel">PnL live</div><div class="liveVal" id="livePnlPct">-</div></div>
      <div class="liveBox"><div class="liveLabel">PnL USDC estimé</div><div class="liveVal" id="livePnlUsdc">-</div></div>
    </div>
    <div class="scroll" style="margin-top:12px"><table id="liveTradeTable"></table></div>
    <div style="margin-top:12px">
      <div class="bar"><span id="liveProgressBar"></span></div>
      <div class="liveSub" id="liveProgressText">Progression entre Stop Loss et TP1 : -</div>
    </div>
  </div>

  <div class="card" id="liquidityChartPanel" style="margin-top:12px">
    <div class="chartHeader">
      <div><h3 style="margin:0">💧 Graphique avancé · Prix, liquidité & carnet</h3><div class="muted">Analyse visuelle uniquement · aucune décision d'ordre déclenchée par le graphique</div></div>
      <select id="chartSymbol" onchange="loadChart()" class="chartSelect">
        <option value="SOL">SOL</option><option value="XRP">XRP</option><option value="kSHIB">kSHIB</option>
      </select>
    </div>
    <div id="chartSummary" class="chartSummary">Chargement des données du moteur…</div>
    <div class="chartToolbar">
      <span class="muted">Historique affiché</span>
      <button type="button" class="chartTool" onclick="setChartWindow(60)">60 bougies</button>
      <button type="button" class="chartTool active" onclick="setChartWindow(120)">120 bougies</button>
      <button type="button" class="chartTool" onclick="setChartWindow(180)">180 bougies</button>
      <button type="button" class="chartTool" onclick="moveChartWindow(-1)">← Plus ancien</button>
      <button type="button" class="chartTool" onclick="moveChartWindow(1)">Plus récent →</button>
      <button type="button" class="chartTool" onclick="loadChart()">↻ Actualiser</button>
    </div>
    <div id="liquidityChart" class="chartCanvas"><div class="muted">Le graphique apparaîtra après le prochain scan du bot.</div></div>
    <div id="chartLegend" class="chartLegend"></div>
    <div id="chartOrderbook" class="chartOrderbook"></div>
    <div class="muted" style="margin-top:9px">EMA20 / EMA50 / VWAP · volumes · niveaux de liquidité estimés par pivots · sweeps détectés · carnet L2 réel (cache 60 s). Les ordres cachés ne sont pas visibles. Les niveaux affichés ne garantissent pas une exécution.</div>
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
    <h3>🟡 Smart Exit adaptatif</h3>
    <div id="smartExitPatienceBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🎛️ Auto-Optimizer</h3>
    <div id="autoOptimizerBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🧬 Adaptive Intelligence</h3>
    <div id="adaptiveBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🧠 IA Coach</h3>
    <div id="aiCoach" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🌐 API Hyperliquid</h3>
    <div id="apiStatusBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>📦 État des ordres</h3>
    <div id="orderStateBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>💾 Stockage local</h3>
    <div id="storageStatusBox" class="aiBox">-</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>☁️ Sauvegarde GitHub</h3>
    <div id="githubSyncBox" class="aiBox">-</div>
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
function renderGithubSync(s){
 if(!s)return '-';
 return `Activée: ${s.enabled?'OUI':'NON'}
Dépôt: ${s.repo||'-'}
Branche: ${s.branch||'-'}
Dossier distant: ${s.prefix||'-'}
Statut: ${s.last_status||'-'}
Dernière restauration: ${s.last_restore||'-'}
Dernière sauvegarde: ${s.last_backup||'-'}
Fichiers restaurés: ${s.downloaded_files||0}
Fichiers sauvegardés: ${s.uploaded_files||0}
En attente: ${s.dirty?'OUI':'NON'}
Dernière erreur: ${s.last_error||'-'}`;
}

function renderStorageStatus(s){
 if(!s)return '-';
 const f=s.files||{};
 return `Dossier: ${s.data_dir||'-'}
Existe: ${s.exists?'OUI':'NON'}
Écriture: ${s.writable?'OK':'ERREUR'}
trades.csv: ${f.trades?'OK':'absent'}
post_trade_pending.json: ${f.post_trade_pending?'OK':'absent'}
post_trade_analysis.csv: ${f.post_trade_analysis?'OK':'absent'}
bot_config.json: ${f.bot_config?'OK':'absent'}`;
}

function renderGithubSync(s){
 if(!s)return '-';
 return `Activée: ${s.enabled?'OUI':'NON'}
Dépôt: ${s.repo||'-'}
Branche: ${s.branch||'-'}
Dossier distant: ${s.prefix||'-'}
Statut: ${s.last_status||'-'}
Dernière restauration: ${s.last_restore||'-'}
Dernière sauvegarde: ${s.last_backup||'-'}
Fichiers restaurés: ${s.downloaded_files||0}
Fichiers sauvegardés: ${s.uploaded_files||0}
En attente: ${s.dirty?'OUI':'NON'}
Dernière erreur: ${s.last_error||'-'}`;
}

function renderOrderState(s){
 if(!s)return '-';
 return `Ordre en attente: ${s.pending?'OUI':'NON'}
Side: ${s.side||'-'}
Symbole: ${s.symbol||'-'}
Raison: ${s.reason||'-'}
Démarré: ${s.started_at||'-'}
Retry dans: ${s.retry_in_seconds||0}s
Tentatives: ${s.attempts||0}
Dernière erreur: ${s.last_error||'-'}`;
}

function renderApiStatus(s){
 if(!s)return '-';
 const total=Number(s.requests||0);
 const ok=Number(s.success||0);
 const hit=Number(s.cache_hits||0);
 const miss=Number(s.cache_misses||0);
 const cacheTotal=hit+miss;
 const successRate=total?((ok/total)*100).toFixed(1):'-';
 const cacheRate=cacheTotal?((hit/cacheTotal)*100).toFixed(1):'-';
 return `Statut: ${s.last_status||'-'}
Dernière requête: ${s.last_request||'-'}
Latence: ${s.last_latency_ms||0} ms
Requêtes: ${total} | Succès: ${ok} | Erreurs: ${s.errors||0}
429: ${s.rate_limits||0} | Retries: ${s.retries||0}
Taux succès: ${successRate}%
Cache hits: ${hit} | misses: ${miss} | hit rate: ${cacheRate}%
Dernière erreur: ${s.last_error||'-'}`;
}

function renderSmartExitPatience(s){
 if(!s)return '-';
 return `État: ${s.active?'PRÉ-SORTIE':'NORMAL'}
Attente prévue: ${s.wait_candles||0} bougie(s)
Score initial: ${s.score??'-'}
Pic depuis pré-sortie: ${s.peak??'-'}
Temps écoulé: ${s.elapsed_seconds||0}s
Raison: ${s.reason||'-'}
Apprentissage: ${s.learning_note||'-'}`;
}

function renderAutoOptimizer(o){
 if(!o)return 'Données Auto-Optimizer indisponibles.';
 const profiles=o.profiles||{};
 const rows=Object.values(profiles).sort((a,b)=>(b.sample||0)-(a.sample||0)).slice(0,12);
 let out=`Activé: ${o.enabled?'OUI':'NON'}\nMAJ: ${o.updated_at||'-'}\nProfils actifs: ${Object.keys(profiles).length}\n`;
 if(rows.length===0){
   out+='\nAucun profil appliqué pour le moment. Il faut au moins 4 trades comparables pour une même combinaison token + mode + timeframe.';
   if(o.last_error){out+=`\nErreur: ${o.last_error}`;}
   return out;
 }
 rows.forEach(p=>{
   out+=`\n• ${p.symbol} | ${p.mode} | ${p.timeframe}\n`;
   out+=`  TP1 ${p.tp1_mult} ATR · Stop ${p.stop_mult} ATR · Patience ${p.patience_candles} bougie(s)\n`;
   out+=`  ${p.sample} trades · WR ${p.winrate}% · Confiance ${p.confidence}%\n`;
 });
 if(o.last_error){out+=`\nDernière erreur: ${o.last_error}`;}
 return out;
}

function renderAdaptive(a){
 if(!a){return '-'}
 let out=`Enabled: ${a.enabled}\nMAJ: ${a.last_updated||'-'}\n`;
 out+='\nModes:\n';
 Object.entries(a.mode_stats||{}).forEach(([k,v])=>{out+=`• ${k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 out+='\nTokens:\n';
 Object.entries(a.token_stats||{}).forEach(([k,v])=>{out+=`• ${k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 out+='\nPatterns:\n';
 Object.entries(a.pattern_stats||{}).forEach(([k,v])=>{out+=`• ${v.label||k}: x${v.multiplier} | ${v.trades} trades | PnL ${v.pnl} | WR ${v.winrate}%\n`});
 const se=a.smart_exit||{};
 out+='\nSmart Exit adaptatif:\n';
 out+=`• Recommandation: ${se.recommendation||'-'}\n`;
 out+=`• Niveau: ${se.level??0}/3 | Auto: ${se.auto_apply?'ON':'OFF'} | Analyses: ${se.sample_size??0}\n`;
 out+=`• Sorties trop tôt: ${se.too_early_ratio??0}% | Bonnes sorties: ${se.good_ratio??0}%\n`;
 out+=`• Bonus seuil: +${se.threshold_bonus??0} | Health assoupli: -${se.health_bonus??0} | Tighten: +${se.tighten_bonus??0}\n`;
 const al=a.auto_learning||{};
 out+='\nAuto Learning V6:\n';
 out+=`• Recommandation: ${al.recommendation||'-'} | Auto: ${al.auto_apply?'ON':'OFF'} | Trades: ${al.sample_size??0}\n`;
 out+=`• WR: ${al.winrate??0}% | PnL fenêtre: ${al.pnl_total??0} USDC\n`;
 if((al.blocked_modes||[]).length){out+=`• Modes en quarantaine: ${(al.blocked_modes||[]).join(', ')}\n`;}
 if((al.blocked_patterns||[]).length){out+=`• Patterns en quarantaine: ${(al.blocked_patterns||[]).join(', ')}\n`;}
 if((al.blocked_tokens||[]).length){out+=`• Tokens en quarantaine: ${(al.blocked_tokens||[]).join(', ')}\n`;}
 if((al.retesting_modes||[]).length){out+=`• Modes en retest: ${(al.retesting_modes||[]).join(', ')}\n`;}
 if((al.retesting_patterns||[]).length){out+=`• Patterns en retest: ${(al.retesting_patterns||[]).join(', ')}\n`;}
 if((al.retesting_tokens||[]).length){out+=`• Tokens en retest: ${(al.retesting_tokens||[]).join(', ')}\n`;}
 out+=`• Quarantaine: ${al.block_duration_hours||12} h · Retest: ${al.retest_trade_count||3} trade(s)\n`;
 (al.notes||[]).forEach(n=>{out+=`• ${n}\n`;});
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

function renderToggles(cfg){
    currentConfig=cfg;

    const modeLabels={
        MODE_A_CLASSIQUE:'Mode A',
        MODE_B_PATTERN:'Mode B',
        MODE_C_CAPITULATION:'Mode C',
        MODE_D_EMA_PULLBACK:'Mode D',
        MODE_E_EARLY_MOMENTUM:'Mode E',
        MODE_F_CANDLE_REVERSAL:'Mode F',
        MODE_L_LIQUIDITY_SWEEP:'Mode L - Liquidity Sweep'
    };

    const modeDesc={
        MODE_A_CLASSIQUE:'Breakout classique tendance confirmée.',
        MODE_B_PATTERN:'Triangle / compression / patterns forts.',
        MODE_C_CAPITULATION:'Rebond après forte baisse.',
        MODE_D_EMA_PULLBACK:'Pullback EMA confirmé : D1 désactivé, D2 attend une bougie de confirmation.',
        MODE_E_EARLY_MOMENTUM:'Impulsion volume + OBV. Peut acheter intrabar avant clôture 15m. Sortie 2 clôtures sous EMA20.',
        MODE_F_CANDLE_REVERSAL:'Patterns bougies haussiers : marteau, englobante, morning star, tweezer, inside breakout.',
        MODE_L_LIQUIDITY_SWEEP:'Détection des poches de liquidité, sweeps et réintégrations.'
    };

    document.getElementById('modeToggles').innerHTML=
        Object.keys(cfg.modes).map(k=>
            `<label class="toggle">
                <span>${modeLabels[k]||k}<br>
                <small style="color:#9ca3af;font-weight:500">${modeDesc[k]||''}</small></span>
                <input type="checkbox" data-section="modes" data-key="${k}" ${cfg.modes[k]?'checked':''}>
            </label>`
        ).join('');

    document.getElementById('symbolToggles').innerHTML=
        Object.keys(cfg.symbols).map(k=>
            `<label class="toggle">
                <span>${k}</span>
                <input type="checkbox" data-section="symbols" data-key="${k}" ${cfg.symbols[k]?'checked':''}>
            </label>`
        ).join('');

    const opts=['1m','3m','5m','15m'];

    document.getElementById('timeframeToggles').innerHTML=
        opts.map(tf=>
            `<label class="toggle">
                <span>${tf}</span>
                <input type="radio" name="timeframe" data-timeframe="${tf}" ${cfg.timeframe===tf?'checked':''}>
            </label>`
        ).join('');

    document.getElementById('activeTimeframeLabel').textContent=
        'Timeframe actif : '+(cfg.timeframe||'15m');
}

async function saveConfig(){document.querySelectorAll('input[type=checkbox]').forEach(i=>{currentConfig[i.dataset.section][i.dataset.key]=i.checked});const tf=document.querySelector('input[name=timeframe]:checked');if(tf){currentConfig.timeframe=tf.dataset.timeframe}const r=await fetch('/api/config'+window.location.search,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentConfig)});document.getElementById('saveStatus').textContent=r.ok?'✅ Configuration sauvegardée':'❌ Erreur sauvegarde';setTimeout(()=>document.getElementById('saveStatus').textContent='',2500);load()}

function renderLiveTradeV48(live){
 const empty=!live||!live.in_position;
 document.getElementById('liveSymbol').textContent=empty?'Aucune':(live.symbol||'-');
 document.getElementById('liveMode').textContent=empty?'-':((live.mode||'-').replace('MODE_',''));
 document.getElementById('livePnlPct').innerHTML=empty?'-':`<span class="${cls(live.pnl_pct)}">${fmt(live.pnl_pct,3)}%</span>`;
 document.getElementById('livePnlUsdc').innerHTML=empty?'-':`<span class="${cls(live.pnl_usdc_est)}">${fmt(live.pnl_usdc_est,4)} USDC</span>`;
 if(empty){
   table('liveTradeTable',[{label:'Entry',get:r=>'-'},{label:'Prix actuel',get:r=>'-'},{label:'TP1',get:r=>'-'},{label:'Stop Loss',get:r=>'-'},{label:'Trailing',get:r=>'-'}],[{}]);
   document.getElementById('liveProgressBar').style.width='0%';
   document.getElementById('liveProgressText').textContent=live&&live.error?('Erreur live: '+live.error):'Aucune position ouverte.';
   return;
 }
 table('liveTradeTable',[
   {label:'Entry',get:r=>fmt(r.entry,6)},
   {label:'Prix actuel',get:r=>fmt(r.current_price,6)},
   {label:'Qty',get:r=>fmt(r.size,6)},
   {label:'ATR',get:r=>r.entry_atr==null?'-':fmt(r.entry_atr,6)},
   {label:'TP1',get:r=>r.tp1_price==null?'-':fmt(r.tp1_price,6)},
   {label:'Dist. TP1',get:r=>r.distance_to_tp1_pct==null?'-':fmt(r.distance_to_tp1_pct,3)+'%'},
   {label:'Stop Loss ATR',get:r=>r.hard_stop==null?'-':fmt(r.hard_stop,6)},
   {label:'Dist. SL',get:r=>r.distance_to_sl_pct==null?'-':fmt(r.distance_to_sl_pct,3)+'%'},
   {label:'Trailing',get:r=>r.trailing_stop==null?'-':fmt(r.trailing_stop,6)},
   {label:'Plus haut',get:r=>r.highest_price==null?'-':fmt(r.highest_price,6)},
   {label:'TP1 fait',get:r=>r.tp1_done?'Oui':'Non'},
   {label:'TF',get:r=>r.timeframe||'-'},
   {label:'Update',get:r=>r.last_update??'-'}
 ],[live]);
 const prog=live.progress_to_tp1_pct==null?0:Math.max(0,Math.min(100,Number(live.progress_to_tp1_pct)));
 document.getElementById('liveProgressBar').style.width=prog+'%';
 let txt=`Progression entre Stop Loss et TP1 : ${fmt(prog,1)}%`;
 if(live.recovered_from_position){txt+=' · position récupérée après redémarrage';}
 document.getElementById('liveProgressText').textContent=txt;
}


let chartBusy=false;
let chartWindow=120;
let chartOffset=0;
function setChartWindow(n){
 chartWindow=n;
 chartOffset=0;
 document.querySelectorAll('.chartTool').forEach(b=>{if((b.textContent||'').indexOf(String(n))>=0)b.classList.add('active');else if((b.textContent||'').indexOf('bougies')>=0)b.classList.remove('active')});
 loadChart();
}
function moveChartWindow(direction){
 const symbol=document.getElementById('chartSymbol').value;
 const suffix=window.location.search?('&'+window.location.search.slice(1)):'';
 fetch('/api/chart?symbol='+encodeURIComponent(symbol)+suffix).then(r=>r.json()).then(d=>{
  const total=(d.candles||[]).length;
  const maxOffset=Math.max(0,total-chartWindow);
  chartOffset=Math.max(0,Math.min(maxOffset,chartOffset-direction*Math.max(10,Math.floor(chartWindow/2))));
  loadChart();
 }).catch(()=>{});
}
function chartLine(bars,key,color,width,dash){
 const pts=bars.map((b,i)=>chartX(i,bars.length)+','+chartY(Number(b[key]))).join(' ');
 return '<polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="'+width+'"'+(dash?' stroke-dasharray="'+dash+'"':'')+' opacity=".96"/>';
}
let chartScale={min:0,max:1,top:24,height:300,left:76,width:1000};
function chartY(v){return chartScale.top+(chartScale.max-Number(v))/(chartScale.max-chartScale.min)*chartScale.height}
function chartX(i,n){return chartScale.left+(i+.5)*chartScale.width/n}
function renderChart(d){
 const host=document.getElementById('liquidityChart');
 const all=d.candles||[];
 const end=Math.max(0,all.length-chartOffset);
  const start=Math.max(0,end-chartWindow);
  const bars=all.slice(start,end);
 if(bars.length<5){host.textContent='Historique insuffisant';return}
 const trade=d.trade||null,book=d.orderbook||{bids:[],asks:[]};
 const levels=bars.map(b=>Number(b.low)).concat(bars.map(b=>Number(b.high)),(d.pools_high||[]).map(Number),(d.pools_low||[]).map(Number));
 if(trade){['entry','stop','target','tp1'].forEach(k=>{if(trade[k]!=null)levels.push(Number(trade[k]))})}
 let min=Math.min.apply(null,levels),max=Math.max.apply(null,levels);
 const pad=(max-min)*.055||Math.abs(max)*.002||.000001;min-=pad;max+=pad;
 const W=1120,H=500,L=82,R=18,T=24,priceH=320,volTop=380,volH=78,plotW=W-L-R;
 chartScale={min:min,max:max,top:T,height:priceH,left:L,width:plotW};
 const y=chartY,x=i=>chartX(i,bars.length),bodyW=Math.max(2,Math.min(9,plotW/bars.length*.58));
 let svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Graphique avancé '+esc(d.symbol)+'" style="background:#0b1422;border-radius:11px">';
 for(let i=0;i<=5;i++){
  const val=max-(max-min)*i/5,yy=y(val);
  svg+='<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="#263348" stroke-width="1"/><text x="'+(L-9)+'" y="'+(yy+4)+'" fill="#9ca3af" font-size="11" text-anchor="end">'+fmt(val,6)+'</text>';
 }
 svg+='<line x1="'+L+'" y1="'+(volTop-12)+'" x2="'+(W-R)+'" y2="'+(volTop-12)+'" stroke="#334155"/>';
 const maxVol=Math.max.apply(null,bars.map(b=>Number(b.volume||0)).concat([1]));
 for(let i=0;i<bars.length;i++){
  const b=bars[i],xx=x(i),up=Number(b.close)>=Number(b.open),col=up?'#22c55e':'#ef4444';
  const yo=y(b.open),yc=y(b.close),yh=y(b.high),yl=y(b.low);
  const tm=new Date(Number(b.time)).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  svg+='<g><title>'+esc(tm)+' · O '+fmt(b.open,6)+' · H '+fmt(b.high,6)+' · B '+fmt(b.low,6)+' · C '+fmt(b.close,6)+' · Vol '+fmt(b.volume,2)+'</title>';
  svg+='<line x1="'+xx+'" y1="'+yh+'" x2="'+xx+'" y2="'+yl+'" stroke="'+col+'" stroke-width="1.5"/>';
  svg+='<rect x="'+(xx-bodyW/2)+'" y="'+Math.min(yo,yc)+'" width="'+bodyW+'" height="'+Math.max(1,Math.abs(yo-yc))+'" fill="'+col+'" rx="1"/>';
  const vh=Number(b.volume||0)/maxVol*volH;
  svg+='<rect x="'+(xx-bodyW/2)+'" y="'+(volTop+volH-vh)+'" width="'+bodyW+'" height="'+Math.max(1,vh)+'" fill="'+(up?'#16a34a':'#b91c1c')+'" opacity=".62"/>';
  svg+='</g>';
 }
 svg+=chartLine(bars,'ema20','#38bdf8',2,'')+chartLine(bars,'ema50','#c084fc',2,'')+chartLine(bars,'vwap','#facc15',1.8,'5 4');
 const hline=(price,color,label,dash,width)=>{
  if(price==null||!isFinite(Number(price))||Number(price)<min||Number(price)>max)return '';
  const yy=y(price);
  return '<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="'+color+'" stroke-width="'+width+'" stroke-dasharray="'+dash+'"/><rect x="'+(W-R-112)+'" y="'+(yy-10)+'" width="110" height="17" rx="4" fill="'+color+'" opacity=".92"/><text x="'+(W-R-7)+'" y="'+(yy+2)+'" fill="#07111f" font-size="10" font-weight="700" text-anchor="end">'+esc(label)+' '+fmt(price,6)+'</text>';
 };
 (d.pools_high||[]).forEach(p=>{svg+=hline(p,'#fb923c','LIQ. HAUTE','6 5',1.4)});
 (d.pools_low||[]).forEach(p=>{svg+=hline(p,'#34d399','LIQ. BASSE','6 5',1.4)});
 if(trade){
  svg+=hline(trade.entry,'#facc15','ENTRÉE','',2);
  svg+=hline(trade.stop,'#fb7185','STOP','4 3',2);
  svg+=hline(trade.target,'#4ade80','CIBLE','4 3',2);
  if(trade.tp1!=null&&trade.tp1!==trade.target)svg+=hline(trade.tp1,'#86efac','TP1','2 4',1.5);
 }
 const current=Number(d.current_price);
 if(current>=min&&current<=max){
  const yy=y(current);svg+='<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="#f8fafc" stroke-width="1.5" stroke-dasharray="3 3"/>';
  svg+='<text x="'+(L+5)+'" y="'+(yy-6)+'" fill="#f8fafc" font-size="10">PRIX ACTUEL</text>';
 }
 for(let i=0;i<5;i++){
  const idx=Math.min(bars.length-1,Math.round(i*(bars.length-1)/4)),b=bars[idx];
  const label=new Date(Number(b.time)).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  svg+='<text x="'+x(idx)+'" y="475" fill="#9ca3af" font-size="10" text-anchor="middle">'+esc(label)+'</text>';
 }
 svg+='<text x="'+L+'" y="'+(volTop-18)+'" fill="#9ca3af" font-size="10">VOLUME</text></svg>';
 host.innerHTML=svg;
 const legend=[['#38bdf8','EMA20'],['#c084fc','EMA50'],['#facc15','VWAP'],['#fb923c','Liquidité haute'],['#34d399','Liquidité basse'],['#f8fafc','Prix actuel']];
 if(trade){legend.push(['#facc15','Entrée'],['#fb7185','Stop'],['#4ade80','Cible / TP'])}
 document.getElementById('chartLegend').innerHTML=legend.map(v=>'<span><i style="background:'+v[0]+'"></i>'+v[1]+'</span>').join('');
 const renderBook=(name,rows,color)=>{
  const maxUsd=Math.max.apply(null,rows.map(v=>Number(v.usd||0)).concat([1]));
  if(!rows.length)return '<div class="bookSide"><div class="bookTitle">'+name+'</div><div class="muted">Carnet indisponible</div></div>';
  return '<div class="bookSide"><div class="bookTitle" style="color:'+color+'">'+name+'</div>'+rows.slice(0,8).map(v=>'<div class="bookRow"><span>'+fmt(v.price,6)+'</span><div class="bookTrack"><div class="bookFill" style="width:'+Math.max(2,Number(v.usd||0)/maxUsd*100)+'%;background:'+color+'"></div></div><span style="text-align:right">'+fmt(v.usd,0)+' $</span></div>').join('')+'</div>';
 };
 document.getElementById('chartOrderbook').innerHTML=renderBook('ACHETEURS · BIDS',book.bids||[],'#22c55e')+renderBook('VENDEURS · ASKS',book.asks||[],'#f87171');
}
async function loadChart(){
 if(chartBusy)return;chartBusy=true;
 try{
  const symbol=document.getElementById('chartSymbol').value;
  const suffix=window.location.search?('&'+window.location.search.slice(1)):'';
  const r=await fetch('/api/chart?symbol='+encodeURIComponent(symbol)+suffix);
  const d=await r.json();
  const summary=document.getElementById('chartSummary');
  if(!d.ready){summary.textContent=d.message||'Données indisponibles';document.getElementById('liquidityChart').innerHTML='<div class="muted">En attente du prochain scan du bot.</div>';return}
  summary.textContent=d.symbol+' · '+d.timeframe+' · '+chartWindow+' bougies'+(chartOffset?' · historique -'+chartOffset:' · récent')+' · Prix '+fmt(d.current_price,6)+' · Biais '+d.bias+' · RSI '+fmt(d.rsi,1)+' · ATR '+fmt(d.atr,6)+' · Mis à jour '+d.updated_at+(d.trade?' · POSITION '+(d.trade.side||'LONG'):'');
  renderChart(d);
  if(d.sweep&&d.sweep.valid)summary.textContent+=' · SWEEP '+d.sweep.side+' confirmé · Niveau '+fmt(d.sweep.level,6)+' · SL '+fmt(d.sweep.stop,6)+' · TP '+fmt(d.sweep.target,6);
 }catch(e){document.getElementById('chartSummary').textContent='Graphique momentanément indisponible'}
 finally{chartBusy=false}
}
async function load(){const r=await fetch('/api/data'+window.location.search);const d=await r.json();document.getElementById('balance').textContent=fmt(d.balance,2);document.getElementById('pnl').innerHTML=`<span class="${cls(d.summary.pnl_total)}">${fmt(d.summary.pnl_total,4)}</span>`;document.getElementById('winrate').textContent=fmt(d.summary.winrate,1)+'%';document.getElementById('trades').textContent=d.summary.trades;document.getElementById('position').textContent=d.open_position?JSON.stringify(d.open_position,null,2):'Aucune position ouverte';renderToggles(d.config);renderLiveTradeV48(d.live_trade);
const perfCols=[{label:'Nom',key:'name'},{label:'Trades',key:'trades'},{label:'WR',get:r=>fmt(r.winrate,1)+'%'},{label:'PnL',get:r=>fmt(r.pnl,4),cls:v=>cls(v)},{label:'Avg',get:r=>fmt(r.avg_pnl,4),cls:v=>cls(v)},{label:'PF',get:r=>r.profit_factor===null?'∞':fmt(r.profit_factor,2)}];table('byMode',perfCols,d.by_mode);table('bySymbol',perfCols,d.by_symbol);table('byExit',perfCols,d.by_exit_reason);
table('analysis',[{label:'Heure',key:'time'},{label:'Coin',key:'symbol'},{label:'Sig',key:'signal'},{label:'Mode',get:r=>shortMode(r.mode)},{label:'Close',get:r=>fmt(r.close,5)},{label:'RSI',get:r=>fmt(r.rsi,1)},{label:'Score',get:r=>fmt(r.score,2)},{label:'A/B/C/D/E/F',get:r=>`${r.mode_a}/${r.mode_b}/${r.mode_c}/${r.mode_d}/${r.mode_e}/${r.mode_f}`},{label:'Pattern F',get:r=>r.mode_f_pattern||'-'}],(d.analysis_history||[]).slice(-40).reverse());
lastTrades=(d.recent_trades||[]).slice().reverse();
table('recentTrades',[{label:'Date',key:'date'},{label:'Coin',key:'symbol'},{label:'Mode',get:r=>shortMode(r.mode)},{label:'Entry',key:'entry'},{label:'Exit',key:'exit'},{label:'Sortie',key:'exit_reason'},{label:'TP1',key:'tp1_done'},{label:'PnL %',get:r=>fmt(r.pnl_price_pct,2),cls:v=>cls(v)},{label:'USDC',get:r=>fmt(r.real_pnl_usdc,4),cls:v=>cls(v)},{label:'Pattern F',get:r=>r._mode_f_pattern_label||r.mode_f_pattern||'-'}],lastTrades,true);
document.getElementById('autoOptimizerBox').textContent=renderAutoOptimizer(d.auto_optimizer);
document.getElementById('adaptiveBox').textContent=renderAdaptive(d.adaptive);
document.getElementById('apiStatusBox').textContent=renderApiStatus(d.api_status);
document.getElementById('orderStateBox').textContent=renderOrderState(d.order_state);
document.getElementById('storageStatusBox').textContent=renderStorageStatus(d.storage_status);
document.getElementById('githubSyncBox').textContent=renderGithubSync(d.github_sync);
document.getElementById('smartExitPatienceBox').textContent=renderSmartExitPatience(d.smart_exit_patience);
document.getElementById('aiCoach').textContent=(d.ai_recommendations||[]).map(x=>'• '+x).join('\n')||'-';
const pts=d.post_trade_summary||{};
document.getElementById('postTradeSummary').textContent=`En attente ${pts.pending||0} · Terminées ${pts.total||0} · Score moyen ${fmt(pts.score_moyen,1)}/100 · Excellentes ${pts.excellente_sortie||0} · Très bonnes ${pts.tres_bonne_sortie||0} · Bonnes ${pts.bonne_sortie||0} · Acceptables ${pts.sortie_acceptable||0} · Trop tôt ${pts.sortie_trop_tot||0}`;
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
{label:'Laissé',get:r=>fmt(r.opportunity_left_pct,2)},
{label:'Baisse évitée',get:r=>fmt(r.downside_avoided_pct,2)},
{label:'Qualité',get:r=>r.exit_quality_score===''?'-':fmt(r.exit_quality_score,1)+'/100'},
{label:'Verdict',key:'verdict'},{label:'Status',key:'status'}
],(d.post_trade_analysis||[]).slice().reverse());
document.getElementById('logs').textContent=(d.log_history||[]).slice(-80).join('\n')}
load();loadChart();setInterval(load,10000);setInterval(loadChart,60000);
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

        if parsed.path == "/api/chart":
            params = parse_qs(parsed.query)
            requested_symbol = str((params.get("symbol") or [""])[0]).strip()
            symbol = next((s for s in SYMBOLS if s.lower() == requested_symbol.lower()), requested_symbol)
            with DASHBOARD_CHART_LOCK:
                snapshot = DASHBOARD_CHART_CACHE.get(symbol)
                if snapshot is not None:
                    snapshot = dict(snapshot)
            if snapshot is None:
                return self._json({"ready": False, "symbol": symbol, "message": "Données en attente du prochain scan."})
            # Carnet réel Hyperliquid, chargé à la demande et mis en cache 60 s.
            try:
                book_raw = info_post(
                    {"type": "l2Book", "coin": symbol},
                    cache_key=f"dashboard_l2book:{symbol}",
                    cache_ttl=60,
                    max_retries=0,
                )
                raw_levels = book_raw.get("levels", []) if isinstance(book_raw, dict) else []
                book = {"bids": [], "asks": []}
                for side_index, side_name in ((0, "bids"), (1, "asks")):
                    side_rows = raw_levels[side_index] if len(raw_levels) > side_index else []
                    for level in side_rows[:8]:
                        price = float(level.get("px", 0) or 0)
                        size = float(level.get("sz", 0) or 0)
                        if price > 0 and size > 0:
                            book[side_name].append({"price": round(price, 8), "size": round(size, 8), "usd": round(price * size, 2)})
                snapshot["orderbook"] = book
            except Exception as book_error:
                snapshot["orderbook"] = {"bids": [], "asks": []}
                snapshot["orderbook_error"] = str(book_error)[:120]
            return self._json(snapshot)

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

                if cfg.get("timeframe") in TIMEFRAME_OPTIONS:
                    old_tf = base_cfg.get("timeframe", DEFAULT_TIMEFRAME)
                    base_cfg["timeframe"] = cfg["timeframe"]
                    if old_tf != base_cfg["timeframe"]:
                        for s in SYMBOLS:
                            last_candle_times[s] = None

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





def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_BACKUP_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "adaptive-trading-bot",
    }


def _github_api_url(filename):
    remote_path = f"{GITHUB_BACKUP_PREFIX}/{filename}" if GITHUB_BACKUP_PREFIX else filename
    return f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{remote_path}"


def _github_get_remote_file(filename):
    if not GITHUB_BACKUP_ENABLED:
        return None
    try:
        response = requests.get(
            _github_api_url(filename),
            headers=_github_headers(),
            params={"ref": GITHUB_BACKUP_BRANCH},
            timeout=20,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        encoded = payload.get("content", "").replace("\n", "")
        return {
            "content": base64.b64decode(encoded) if encoded else b"",
            "sha": payload.get("sha"),
        }
    except Exception as e:
        print("[GITHUB RESTORE ERROR]", filename, e)
        GITHUB_SYNC_STATE["last_error"] = str(e)[:250]
        return None


def github_restore_data():
    """
    Télécharge les fichiers de données depuis GitHub au démarrage.
    Un fichier distant remplace le fichier local temporaire.
    """
    if not GITHUB_BACKUP_ENABLED:
        print("[GITHUB BACKUP] Désactivé : variables manquantes.")
        return False

    downloaded = 0
    try:
        with GITHUB_SYNC_LOCK:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            for filename in GITHUB_BACKUP_FILES:
                remote = _github_get_remote_file(filename)
                if remote is None:
                    continue
                local_path = DATA_DIR / filename
                local_path.write_bytes(remote["content"])
                downloaded += 1

            GITHUB_SYNC_STATE.update({
                "restored": downloaded > 0,
                "last_restore": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_status": "RESTORE_OK" if downloaded > 0 else "NO_REMOTE_DATA",
                "last_error": "",
                "downloaded_files": downloaded,
            })

        print(f"[GITHUB RESTORE] {downloaded} fichier(s) restauré(s).")
        return downloaded > 0

    except Exception as e:
        GITHUB_SYNC_STATE["last_status"] = "RESTORE_ERROR"
        GITHUB_SYNC_STATE["last_error"] = str(e)[:250]
        print("[GITHUB RESTORE ERROR]", e)
        return False


def _github_upload_file(filename):
    local_path = DATA_DIR / filename
    if not local_path.exists():
        return False

    remote = _github_get_remote_file(filename)
    payload = {
        "message": f"Bot data backup: {filename}",
        "content": base64.b64encode(local_path.read_bytes()).decode("ascii"),
        "branch": GITHUB_BACKUP_BRANCH,
    }
    if remote and remote.get("sha"):
        payload["sha"] = remote["sha"]

    response = requests.put(
        _github_api_url(filename),
        headers=_github_headers(),
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return True


def github_backup_now():
    """Sauvegarde tous les fichiers persistants dans le dépôt GitHub privé."""
    if not GITHUB_BACKUP_ENABLED:
        return False

    uploaded = 0
    try:
        with GITHUB_SYNC_LOCK:
            for filename in GITHUB_BACKUP_FILES:
                try:
                    if _github_upload_file(filename):
                        uploaded += 1
                except Exception as file_error:
                    print("[GITHUB BACKUP FILE ERROR]", filename, file_error)
                    GITHUB_SYNC_STATE["last_error"] = f"{filename}: {str(file_error)[:180]}"

            GITHUB_SYNC_STATE.update({
                "last_backup": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_status": "BACKUP_OK" if uploaded > 0 else "NOTHING_TO_BACKUP",
                "uploaded_files": uploaded,
                "dirty": False,
            })

        print(f"[GITHUB BACKUP] {uploaded} fichier(s) sauvegardé(s).")
        return uploaded > 0

    except Exception as e:
        GITHUB_SYNC_STATE["last_status"] = "BACKUP_ERROR"
        GITHUB_SYNC_STATE["last_error"] = str(e)[:250]
        print("[GITHUB BACKUP ERROR]", e)
        return False


def _github_backup_worker(delay_seconds=20):
    try:
        time.sleep(delay_seconds)
        github_backup_now()
    finally:
        GITHUB_SYNC_STATE["worker_running"] = False
        # Si des changements sont arrivés pendant la sauvegarde, relancer une fois.
        if GITHUB_SYNC_STATE.get("dirty") and GITHUB_BACKUP_ENABLED:
            schedule_github_backup(delay_seconds=20)


def schedule_github_backup(delay_seconds=20):
    """
    Regroupe les écritures rapprochées pour éviter trop de commits GitHub.
    """
    if not GITHUB_BACKUP_ENABLED:
        return

    GITHUB_SYNC_STATE["dirty"] = True
    if GITHUB_SYNC_STATE.get("worker_running"):
        return

    GITHUB_SYNC_STATE["worker_running"] = True
    thread = threading.Thread(
        target=_github_backup_worker,
        args=(delay_seconds,),
        daemon=True,
        name="github-backup-worker",
    )
    thread.start()


def github_sync_status():
    status = dict(GITHUB_SYNC_STATE)
    status["repo"] = GITHUB_BACKUP_REPO if GITHUB_BACKUP_ENABLED else ""
    status["branch"] = GITHUB_BACKUP_BRANCH
    status["prefix"] = GITHUB_BACKUP_PREFIX
    return status


def migrate_legacy_data_files():
    """Copie les anciennes données vers DATA_DIR sans écraser les données persistantes."""
    names = [
        "trades.csv", "post_trade_pending.json", "post_trade_analysis.csv",
        "last_monthly_report.txt", "bot_config.json", "adaptive_config.json",
        "adaptive_learning.json", "auto_learning.json", "pattern_learning.json",
        "token_learning.json", "timeframe_learning.json", "smart_exit_learning.json",
    ]
    migrated = []
    try:
        for name in names:
            old_path = Path(name)
            new_path = DATA_DIR / name
            if new_path.exists():
                continue
            if old_path.exists() and old_path.resolve() != new_path.resolve():
                shutil.copy2(old_path, new_path)
                migrated.append(name)
        print("[DATA] Dossier persistant:", DATA_DIR)
        if migrated:
            print("[DATA MIGRATION]", ", ".join(migrated))
    except Exception as e:
        print("[DATA MIGRATION ERROR]", e)


def persistent_storage_status():
    try:
        return {
            "data_dir": str(DATA_DIR),
            "exists": DATA_DIR.exists(),
            "writable": os.access(DATA_DIR, os.W_OK),
            "files": {
                "trades": TRADES_CSV_PATH.exists(),
                "post_trade_pending": POST_TRADE_PENDING_PATH.exists(),
                "post_trade_analysis": POST_TRADE_ANALYSIS_PATH.exists(),
                "bot_config": BOT_CONFIG_PATH.exists(),
            },
        }
    except Exception as e:
        return {"data_dir": str(DATA_DIR), "error": str(e)}


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
        "timeframe",
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

    pnl_pct = (((entry - exit_price) if trade_state.get("mode_l_side") == "SHORT" else (exit_price - entry)) / entry) * 100
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
        get_active_timeframe(),
    ]

    with open(TRADES_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)

    schedule_github_backup(delay_seconds=5)
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




def api_cache_get(key, ttl_seconds):
    try:
        item = API_CACHE.get(key)
        if not item:
            API_STATS["cache_misses"] += 1
            return None
        ts, value = item
        if time.time() - ts <= ttl_seconds:
            API_STATS["cache_hits"] += 1
            return value
        API_STATS["cache_misses"] += 1
        return None
    except Exception:
        return None


def api_cache_set(key, value):
    try:
        API_CACHE[key] = (time.time(), value)
        # Evite que le cache grossisse trop
        if len(API_CACHE) > 200:
            oldest = sorted(API_CACHE.items(), key=lambda kv: kv[1][0])[:50]
            for k, _ in oldest:
                API_CACHE.pop(k, None)
    except Exception:
        pass


def get_candle_cache_ttl():
    tf = get_active_timeframe()
    if tf == "1m":
        return 20
    if tf == "3m":
        return 35
    if tf == "5m":
        return 45
    return 60


def api_backoff_sleep():
    global API_BACKOFF_UNTIL
    wait = API_BACKOFF_UNTIL - time.time()
    if wait > 0:
        print(f"[API BACKOFF] Pause {wait:.1f}s")
        time.sleep(min(wait, 120))


def info_post(payload, cache_key=None, cache_ttl=0, max_retries=3):
    """
    Appel Hyperliquid robuste :
    - cache optionnel
    - backoff après 429
    - retry progressif
    - stats dashboard
    """
    global API_BACKOFF_UNTIL

    if cache_key and cache_ttl:
        cached = api_cache_get(cache_key, cache_ttl)
        if cached is not None:
            return cached

    delays = [2, 5, 10, 20, 60]
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            api_backoff_sleep()

            started = time.time()
            headers = {"Content-Type": "application/json"}

            with API_LOCK:
                API_STATS["requests"] += 1
                API_STATS["last_request"] = payload.get("type")
                r = requests.post(BASE_URL + "/info", headers=headers, data=json.dumps(payload), timeout=15)

            API_STATS["last_latency_ms"] = round((time.time() - started) * 1000, 1)

            if r.status_code == 429:
                API_STATS["rate_limits"] += 1
                API_STATS["errors"] += 1
                API_STATS["last_status"] = "RATE_LIMIT_429"
                API_STATS["last_error"] = "HTTP 429"
                delay = delays[min(attempt, len(delays) - 1)]
                API_BACKOFF_UNTIL = max(API_BACKOFF_UNTIL, time.time() + delay)
                API_STATS["retries"] += 1
                print(f"[API 429] {payload.get('type')} retry dans {delay}s")
                time.sleep(delay)
                continue

            r.raise_for_status()
            data = r.json()

            API_STATS["success"] += 1
            API_STATS["last_status"] = "OK"
            API_STATS["last_error"] = ""

            if cache_key and cache_ttl:
                api_cache_set(cache_key, data)

            return data

        except Exception as e:
            last_error = str(e)
            API_STATS["errors"] += 1
            API_STATS["last_status"] = "ERROR"
            API_STATS["last_error"] = last_error[:180]
            delay = delays[min(attempt, len(delays) - 1)]
            if attempt < max_retries:
                API_STATS["retries"] += 1
                print(f"[API ERROR] {payload.get('type')} {e} retry dans {delay}s")
                time.sleep(delay)
                continue

    print("[ERREUR] API:", last_error)
    return {}

def get_meta_all():
    try:
        data = info_post({"type": "meta"}, cache_key="meta", cache_ttl=3600)
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
    global LAST_KNOWN_BALANCE
    try:
        cached = api_cache_get("balance", 10)
        if cached is not None:
            return cached

        data = info_post({"type": "spotClearinghouseState", "user": WALLET_ADDRESS}, cache_key="spot_balance", cache_ttl=8)
        for b in data.get("balances", []):
            if b.get("coin") == "USDC":
                bal = float(b.get("total", 0))
                if bal > 0:
                    LAST_KNOWN_BALANCE = bal
                    api_cache_set("balance", bal)
                return bal if bal > 0 else (LAST_KNOWN_BALANCE if LAST_KNOWN_BALANCE is not None else -1)

        data2 = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS}, cache_key="ch_state_balance", cache_ttl=8)
        if not data2:
            return LAST_KNOWN_BALANCE if LAST_KNOWN_BALANCE is not None else -1

        bal = float(data2.get("marginSummary", {}).get("accountValue", 0))
        if bal > 0:
            LAST_KNOWN_BALANCE = bal
            api_cache_set("balance", bal)
        return bal if bal > 0 else (LAST_KNOWN_BALANCE if LAST_KNOWN_BALANCE is not None else -1)

    except Exception as e:
        print("[ERREUR] Solde:", e)
        if LAST_KNOWN_BALANCE is not None:
            print("[BALANCE] Dernier solde connu:", LAST_KNOWN_BALANCE)
            return LAST_KNOWN_BALANCE
        return -1

def get_positions():
    global LAST_KNOWN_POSITIONS
    try:
        cached = api_cache_get("positions", 5)
        if cached is not None:
            return cached

        data = info_post({"type": "clearinghouseState", "user": WALLET_ADDRESS}, cache_key="positions_state", cache_ttl=4)
        if not data:
            return LAST_KNOWN_POSITIONS if LAST_KNOWN_POSITIONS is not None else []

        positions = []
        for pos in data.get("assetPositions", []):
            p = pos.get("position", {})
            symbol = p.get("coin")
            szi = float(p.get("szi", 0))
            if symbol in SYMBOLS and szi != 0:
                positions.append({"symbol": symbol, "size": szi, "entry": float(p.get("entryPx", 0))})

        LAST_KNOWN_POSITIONS = positions
        api_cache_set("positions", positions)
        return positions

    except Exception as e:
        print("[ERREUR] Positions:", e)
        return LAST_KNOWN_POSITIONS if LAST_KNOWN_POSITIONS is not None else []

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
                "interval": get_active_timeframe(),
                "startTime": int((time.time() - get_candle_lookback_seconds()) * 1000),
                "endTime": int(time.time() * 1000),
            },
        }
        data = info_post(payload, cache_key=f"candles:{symbol}:{get_active_timeframe()}", cache_ttl=get_candle_cache_ttl())
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



def is_order_blocked(symbol=None, side=None):
    try:
        if not ORDER_STATE.get("pending"):
            return False
        retry_after = float(ORDER_STATE.get("retry_after", 0) or 0)
        if retry_after and time.time() >= retry_after:
            ORDER_STATE["pending"] = False
            ORDER_STATE["side"] = None
            ORDER_STATE["symbol"] = None
            ORDER_STATE["reason"] = None
            ORDER_STATE["retry_after"] = 0
            return False
        return True
    except Exception:
        return False


def set_order_pending(symbol, side, reason="ORDER"):
    try:
        ORDER_STATE["pending"] = True
        ORDER_STATE["symbol"] = symbol
        ORDER_STATE["side"] = side
        ORDER_STATE["reason"] = reason
        ORDER_STATE["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ORDER_STATE["last_error"] = ""
        ORDER_STATE["attempts"] = int(ORDER_STATE.get("attempts", 0) or 0) + 1
    except Exception:
        pass


def clear_order_pending():
    try:
        ORDER_STATE["pending"] = False
        ORDER_STATE["symbol"] = None
        ORDER_STATE["side"] = None
        ORDER_STATE["reason"] = None
        ORDER_STATE["retry_after"] = 0
        ORDER_STATE["last_error"] = ""
        ORDER_STATE["attempts"] = 0
    except Exception:
        pass


def set_order_rate_limited(symbol, side, error, delay_seconds=120):
    try:
        ORDER_STATE["pending"] = True
        ORDER_STATE["symbol"] = symbol
        ORDER_STATE["side"] = side
        ORDER_STATE["reason"] = "RATE_LIMIT_429"
        ORDER_STATE["retry_after"] = time.time() + delay_seconds
        ORDER_STATE["last_error"] = str(error)[:220]
        ORDER_STATE["attempts"] = int(ORDER_STATE.get("attempts", 0) or 0) + 1
    except Exception:
        pass


def get_order_state_for_dashboard():
    try:
        s = dict(ORDER_STATE)
        retry_after = float(s.get("retry_after", 0) or 0)
        s["retry_in_seconds"] = max(0, int(retry_after - time.time())) if retry_after else 0
        return s
    except Exception:
        return {"pending": False}


def place_order_qty(symbol, side, qty, price, reduce_only=False):
    global LAST_ENTRY_PRICE_BY_SYMBOL, LAST_SELL_REASON
    try:
        if is_order_blocked(symbol, side):
            print(f"[ORDER BLOCKED] ordre déjà en attente {ORDER_STATE.get('side')} {ORDER_STATE.get('symbol')} retry={get_order_state_for_dashboard().get('retry_in_seconds')}s")
            return False
        set_order_pending(symbol, side, LAST_SELL_REASON or ("BUY_SIGNAL" if side == "BUY" else "SELL_SIGNAL"))
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

        with ORDER_LOCK:
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
            clear_order_pending()
            return True

        if statuses and "error" in statuses[0]:
            error_msg = statuses[0]["error"]
            print("[ECHEC]", error_msg)
            clear_order_pending()
            notify_telegram(f"⚠️ <b>Erreur ordre</b>\nSymbole: {symbol}\nSide: {side}\nErreur: {error_msg}")
            return False

        clear_order_pending()
        print("[ECHEC]", result)
        return False

    except Exception as e:
        print("[ERREUR] Ordre:", e)
        err = str(e)
        if "429" in err:
            API_STATS["rate_limits"] += 1
            API_STATS["last_status"] = "ORDER_RATE_LIMIT_429"
            API_STATS["last_error"] = err[:180]
            set_order_rate_limited(symbol, side, err, delay_seconds=120)
            notify_telegram("⚠️ Hyperliquid HTTP 429 sur ordre. Ordre bloqué en attente, retry autorisé dans 120 secondes. Aucun double ordre ne sera envoyé.")
        else:
            clear_order_pending()
            notify_telegram(f"⚠️ <b>Erreur ordre exception</b>\nSymbole: {symbol}\nSide: {side}\nErreur: {e}")
        return False


def place_order_usd(symbol, side, size_usd, price, reduce_only=False):
    qty = round(size_usd / price, get_size_decimals(symbol))
    return place_order_qty(symbol, side, qty, price, reduce_only=reduce_only)


# Hyperliquid refuse les ordres dont la valeur notionnelle est < 10 USDC.
# Avec un petit compte, 50% d'une position peut donc être sous le minimum.
# Dans ce cas, on ferme la position entière plutôt que d'envoyer en boucle un TP1 invalide.
MIN_ORDER_NOTIONAL_USD = 10.05  # petite marge pour éviter un ordre à 9.99/10.00 après arrondi

def close_position_fraction(pos, fraction, price):
    symbol = pos["symbol"]
    total_qty = abs(float(pos.get("size", 0) or 0))
    qty = total_qty * fraction
    notional = qty * float(price)

    if qty <= 0 or float(price) <= 0:
        print(f"[SELL] Taille invalide: {symbol} qty={qty} price={price}")
        return False

    # Une réduction partielle sous le minimum d'Hyperliquid est refusée.
    # Si la position totale respecte le minimum, on la ferme entièrement.
    if fraction < 1.0 and notional < MIN_ORDER_NOTIONAL_USD:
        total_notional = total_qty * float(price)
        if total_notional >= MIN_ORDER_NOTIONAL_USD:
            print(
                f"[TP1] 50% impossible: valeur partielle={notional:.2f} USDC < "
                f"minimum={MIN_ORDER_NOTIONAL_USD:.2f}. Fermeture totale pour éviter la boucle d'erreur."
            )
            return place_order_qty(symbol, "BUY" if float(pos.get("size",0))<0 else "SELL", total_qty, price, reduce_only=True)
        print(
            f"[TP1] Position entière sous le minimum: {total_notional:.2f} USDC. "
            "Aucun ordre envoyé."
        )
        return False

    return place_order_qty(symbol, "BUY" if float(pos.get("size",0))<0 else "SELL", qty, price, reduce_only=True)


def close_position_full(pos, price):
    return close_position_fraction(pos, 1.0, price)


def reset_trade_state_after_sell(candle_time):
    global trade_state
    global REENTRY_RESCAN_PENDING, LAST_EXIT_SYMBOL, LAST_EXIT_REASON
    global LAST_EXIT_MODE, LAST_EXIT_CANDLE_TIME, LAST_EXIT_WALL_TIME

    LAST_EXIT_SYMBOL = trade_state.get("symbol")
    LAST_EXIT_REASON = LAST_SELL_REASON
    LAST_EXIT_MODE = trade_state.get("signal_mode")
    LAST_EXIT_CANDLE_TIME = candle_time
    LAST_EXIT_WALL_TIME = time.time()
    REENTRY_RESCAN_PENDING = True

    trade_state = {
        "in_position": False,
        "symbol": None,
        "entry_price": None,
        "entry_atr": None,
        "tp1_done": False,
        "highest_price": None,
        "trailing_stop": None,
        "cooldown_until_candle": candle_time + get_cooldown_ms(),
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
    "smart_exit_pending": False,
    "smart_exit_pending_since": None,
    "smart_exit_pending_candle_time": None,
    "smart_exit_wait_candles": 0,
    "smart_exit_pending_peak": None,
    "smart_exit_pending_score": None,
    "smart_exit_pending_reason": None,
    "smart_exit_learning_note": None,
        "tp1_price": None,
        "tp1_exec_price": None,
        "tp1_qty": None,
    "tp1_adaptive_mult": None,
    "tp1_adaptive_reason": None,
    "tp1_adaptive_sample": 0,
    "optimizer_profile_key": None,
    "optimizer_tp1_mult": None,
    "optimizer_stop_mult": None,
    "optimizer_patience_candles": None,
    "optimizer_confidence": None,
    "optimizer_reason": None,
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

    trade_state.setdefault("smart_exit_pending", False)
    trade_state.setdefault("smart_exit_pending_since", None)
    trade_state.setdefault("smart_exit_pending_candle_time", None)
    trade_state.setdefault("smart_exit_wait_candles", 0)
    trade_state.setdefault("smart_exit_pending_peak", None)
    trade_state.setdefault("smart_exit_pending_score", None)
    trade_state.setdefault("smart_exit_pending_reason", None)
    trade_state.setdefault("smart_exit_learning_note", None)



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




def mode_f_context_filter(df, mode_f_score, mode_f_details, volume_ratio):
    """
    Filtre qualité Mode F.
    Le pattern chandelier seul ne suffit plus.
    Il faut une confirmation de retournement et éviter les rebonds faibles dans tendance baissière.
    """
    try:
        if len(df) < 60:
            return False, {}

        last = df.iloc[-1]
        prev1 = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]

        green_candle = float(last["close"]) > float(last["open"])
        close_above_ema20 = float(last["close"]) > float(last["ema20"])
        close_above_ema50 = float(last["close"]) > float(last["ema50"])

        ema20_slope_pct = (float(last["ema20"]) - float(prev3["ema20"])) / float(prev3["ema20"]) if float(prev3["ema20"]) else -1
        ema20_not_falling_hard = ema20_slope_pct >= MODE_F_MAX_EMA20_SLOPE_DOWN_PCT

        distance_below_ema20_pct = max(0, (float(last["ema20"]) - float(last["close"])) / float(last["close"])) if float(last["close"]) else 1
        close_near_or_above_ema20 = close_above_ema20 or distance_below_ema20_pct <= MODE_F_MAX_DISTANCE_BELOW_EMA20_PCT

        obv_rising_1 = float(last["obv"]) > float(last["obv_ma5"])
        obv_rising_2 = float(last["obv"]) > float(prev1["obv"]) > float(prev2["obv"])

        pattern_high = max(float(prev1["high"]), float(prev2["high"]))
        break_pattern_high = float(last["close"]) > pattern_high

        higher_low = float(last["low"]) >= min(float(prev1["low"]), float(prev2["low"])) * 0.999
        not_under_ema50 = close_above_ema50 or float(last["close"]) >= float(last["ema50"]) * 0.995

        volume_ok = volume_ratio >= MODE_F_VOLUME_MULT
        rsi_ok = 35 <= float(last["rsi"]) <= MODE_F_RSI_MAX
        strong_pattern = mode_f_score >= MODE_F_MIN_PATTERN_SCORE

        confirmations = {
            "strong_pattern": bool(strong_pattern),
            "green_candle": bool(green_candle),
            "break_pattern_high": bool(break_pattern_high),
            "close_near_or_above_ema20": bool(close_near_or_above_ema20),
            "ema20_not_falling_hard": bool(ema20_not_falling_hard),
            "obv_rising_1": bool(obv_rising_1),
            "obv_rising_2": bool(obv_rising_2),
            "volume_ok": bool(volume_ok),
            "higher_low": bool(higher_low),
            "not_under_ema50": bool(not_under_ema50),
            "rsi_ok": bool(rsi_ok),
        }

        confirmation_count = sum(1 for v in confirmations.values() if v)

        blocking_reasons = []
        if not strong_pattern:
            blocking_reasons.append("pattern_score_insuffisant")
        if MODE_F_REQUIRE_BREAK_PATTERN_HIGH and not break_pattern_high:
            blocking_reasons.append("pas_de_cassure_du_plus_haut_pattern")
        if not close_near_or_above_ema20:
            blocking_reasons.append("trop_loin_sous_ema20")
        if not ema20_not_falling_hard:
            blocking_reasons.append("ema20_descend_trop_fort")
        if MODE_F_REQUIRE_OBV_RISING_2 and not obv_rising_2:
            blocking_reasons.append("obv_pas_en_hausse_2_bougies")
        if MODE_F_REQUIRE_NOT_UNDER_EMA50 and not not_under_ema50:
            blocking_reasons.append("prix_trop_sous_ema50")
        if not rsi_ok:
            blocking_reasons.append("rsi_hors_zone")
        if confirmation_count < MODE_F_MIN_CONFIRMATIONS:
            blocking_reasons.append("confirmations_insuffisantes")

        ok = len(blocking_reasons) == 0

        details = {
            "ok": ok,
            "confirmation_count": confirmation_count,
            "confirmations": confirmations,
            "blocking_reasons": blocking_reasons,
            "ema20_slope_pct": round(ema20_slope_pct * 100, 4),
            "distance_below_ema20_pct": round(distance_below_ema20_pct * 100, 4),
            "pattern_high": round(pattern_high, 8),
            "break_pattern_high": break_pattern_high,
            "volume_ratio": round(volume_ratio, 2),
            "obv_rising_2": obv_rising_2,
            "not_under_ema50": not_under_ema50,
        }

        return ok, details

    except Exception as e:
        print("[ERREUR MODE F FILTER]", e)
        return False, {"ok": False, "blocking_reasons": [str(e)]}


def calculate_trend_score(df):
    """Score de tendance progressif sur 8 points, plus explicite qu'un booléen."""
    try:
        if len(df) < 6:
            return 0, {}
        last = df.iloc[-1]
        prev1 = df.iloc[-2]
        prev3 = df.iloc[-4]

        checks = {
            "ema20_above_ema50": bool(last["ema20"] > last["ema50"]),
            "close_above_ema20": bool(last["close"] > last["ema20"]),
            "ema20_rising": bool(last["ema20"] > prev3["ema20"]),
            "ema50_rising": bool(last["ema50"] > prev3["ema50"]),
            "higher_close": bool(last["close"] > prev1["close"]),
            "higher_low": bool(last["low"] > prev1["low"]),
            "obv_above_ma5": bool(last["obv"] > last["obv_ma5"]),
        }

        score = 0
        score += 2 if checks["ema20_above_ema50"] else 0
        score += 2 if checks["close_above_ema20"] else 0
        score += 1 if checks["ema20_rising"] else 0
        score += 1 if checks["ema50_rising"] else 0
        score += 1 if (checks["higher_close"] and checks["higher_low"]) else 0
        score += 1 if checks["obv_above_ma5"] else 0

        missing = [name for name, ok in checks.items() if not ok]
        return int(score), {
            **checks,
            "score": int(score),
            "max_score": 8,
            "missing": missing,
        }
    except Exception as e:
        return 0, {"score": 0, "max_score": 8, "missing": [str(e)]}



def detect_liquidity_sweep(df):
    """Pools de liquidité par pivots/equal highs-lows, sweep puis reclaim confirmé."""
    if df is None or len(df) < MODE_L_LOOKBACK + 5:
        return {"valid": False, "side": None, "reason": "historique insuffisant"}
    w=df.iloc[-(MODE_L_LOOKBACK+3):-1]
    last=df.iloc[-1]; prev=df.iloc[-2]; atr=float(last.get("atr",0) or 0)
    if atr<=0: return {"valid":False,"side":None,"reason":"ATR invalide"}
    highs=[]; lows=[]; hv=w.high.astype(float).tolist(); lv=w.low.astype(float).tolist()
    for i in range(MODE_L_PIVOT_LEFT,len(w)-MODE_L_PIVOT_RIGHT):
        if hv[i]>=max(hv[i-MODE_L_PIVOT_LEFT:i+MODE_L_PIVOT_RIGHT+1]): highs.append(hv[i])
        if lv[i]<=min(lv[i-MODE_L_PIVOT_LEFT:i+MODE_L_PIVOT_RIGHT+1]): lows.append(lv[i])
    def pools(values):
        out=[]
        for x in values:
            group=[v for v in values if abs(v-x)/max(abs(x),1e-12)<=MODE_L_EQUAL_TOL_PCT]
            if len(group)>=2 and not any(abs(y-x)/max(abs(x),1e-12)<=MODE_L_EQUAL_TOL_PCT for y in out): out.append(sum(group)/len(group))
        return sorted(out)
    hp=pools(highs); lp=pools(lows)
    sh=[x for x in hp if prev.high>x+MODE_L_SWEEP_BUFFER_ATR*atr and prev.close<x]
    sl=[x for x in lp if prev.low<x-MODE_L_SWEEP_BUFFER_ATR*atr and prev.close>x]
    if sl and last.close>last.open and last.close>min(sl):
        level=min(sl); targets=[x for x in hp if x>last.close]; target=min(targets) if targets else last.close+2*atr
        stop=min(prev.low,last.low)-MODE_L_ATR_STOP_MULT*atr
        return {"valid":True,"side":"LONG","level":level,"sweep_extreme":float(prev.low),"stop":float(stop),"target":float(target),"atr":atr,"pools_high":hp,"pools_low":lp,"reason":"sell-side sweep + reclaim"}
    if sh and last.close<last.open and last.close>0 and last.close<max(sh):
        level=max(sh); targets=[x for x in lp if x<last.close]; target=max(targets) if targets else last.close-2*atr
        stop=max(prev.high,last.high)+MODE_L_ATR_STOP_MULT*atr
        return {"valid":True,"side":"SHORT","level":level,"sweep_extreme":float(prev.high),"stop":float(stop),"target":float(target),"atr":atr,"pools_high":hp,"pools_low":lp,"reason":"buy-side sweep + reclaim"}
    return {"valid":False,"side":None,"pools_high":hp,"pools_low":lp,"reason":"aucun sweep confirmé"}


def manage_mode_l_position(pos, df, current_price, candle_time):
    """Gestion du Mode L, stop directionnel + TP poche de liquidité."""
    global LAST_SELL_REASON
    side=trade_state.get("mode_l_side") or ("SHORT" if float(pos.get("size",0))<0 else "LONG")
    entry=float(trade_state.get("entry_price") or pos.get("entry") or current_price)
    atr=float(trade_state.get("entry_atr") or df.iloc[-1]["atr"])
    stop=float(trade_state.get("mode_l_stop") or (entry-MODE_L_ATR_STOP_MULT*atr if side=="LONG" else entry+MODE_L_ATR_STOP_MULT*atr))
    target=float(trade_state.get("mode_l_target") or (entry+2*atr if side=="LONG" else entry-2*atr))
    stop_hit=current_price<=stop if side=="LONG" else current_price>=stop
    tp_hit=current_price>=target if side=="LONG" else current_price<=target
    if stop_hit or tp_hit:
        LAST_SELL_REASON="MODE_L_SWEEP_INVALIDATION" if stop_hit else "MODE_L_LIQUIDITY_TP"
        notify_telegram(f"{'🛑' if stop_hit else '🎯'} <b>MODE L {'STOP' if stop_hit else 'TP LIQUIDITÉ'}</b>\n{pos['symbol']} {side}\nEntrée: {entry:.5f}\nPrix: {current_price:.5f}\nNiveau: {stop if stop_hit else target:.5f}")
        if close_position_full(pos,current_price):
            send_trade_summary(pos["symbol"],current_price,LAST_SELL_REASON,round(get_balance(),2)); reset_trade_state_after_sell(candle_time)
    return True

def buy_signal(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]

    trend_score, trend_details = calculate_trend_score(df)
    trend_ok = trend_score >= TREND_SCORE_MODE_A_MIN
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
        trend_score >= TREND_SCORE_MODE_A_MIN
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
        and trend_score >= TREND_SCORE_MODE_B_MIN
        and last["close"] > last["ema20"]
        and breakout_ok
        and obv_rising
        and (vwap_ok or trend_score >= 6)
        and green_candle
        and 45 < last["rsi"] < 85
    )

    # MODE D v3 - Pullback EMA confirmé.
    # Les statistiques ont montré que D1_BETWEEN_EMAS provoquait trop de faux rebonds.
    # D1 est donc désactivé temporairement.
    # D2 n'achète plus sur la bougie de franchissement : il attend UNE clôture
    # supplémentaire qui confirme réellement la reprise au-dessus des deux EMA.
    ema_low = min(last["ema20"], last["ema50"])
    ema_high = max(last["ema20"], last["ema50"])
    prev_ema_low = min(prev1["ema20"], prev1["ema50"])
    prev_ema_high = max(prev1["ema20"], prev1["ema50"])
    prev2_ema_low = min(prev2["ema20"], prev2["ema50"])

    # D1 désactivé : prix seulement entre les EMA = confirmation insuffisante.
    mode_d_pullback_between_emas = False

    # Bougie précédente = bougie de reprise initiale ayant traversé les deux EMA.
    mode_d_previous_cross = (
        prev2["close"] <= prev2_ema_low
        and prev1["close"] >= prev_ema_high
        and prev1["close"] > prev1["open"]
    )

    # Bougie actuelle = confirmation obligatoire.
    # Conditions renforcées : tendance plus solide, maintien au-dessus des deux EMA,
    # clôture supérieure, bougie verte et OBV positif.
    mode_d_cross_both_emas = (
        trend_score >= max(TREND_SCORE_MODE_D_MIN + 1, 4)
        and mode_d_previous_cross
        and last["close"] >= ema_high
        and last["close"] > prev1["close"]
        and last["low"] >= (ema_low * 0.998)
        and green_candle
        and obv_rising
    )

    mode_d_ok = mode_d_cross_both_emas

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
    mode_f_filter_ok, mode_f_filter_details = mode_f_context_filter(df, mode_f_score, mode_f_details, volume_ratio)
    mode_f_ok = mode_f_filter_ok and trend_score >= TREND_SCORE_MODE_F_MIN
    mode_l = detect_liquidity_sweep(df) if is_mode_enabled("MODE_L_LIQUIDITY_SWEEP") else {"valid":False}
    mode_l_ok = bool(mode_l.get("valid"))

    signal_mode = None
    # Activation / désactivation via dashboard
    mode_a_ok = mode_a_ok and is_mode_enabled("MODE_A_CLASSIQUE")
    mode_b_ok = mode_b_ok and is_mode_enabled("MODE_B_PATTERN")
    mode_c_ok = mode_c_ok and is_mode_enabled("MODE_C_CAPITULATION")
    mode_d_ok = mode_d_ok and is_mode_enabled("MODE_D_EMA_PULLBACK")
    mode_e_ok = mode_e_ok and is_mode_enabled("MODE_E_EARLY_MOMENTUM")
    mode_f_ok = mode_f_ok and is_mode_enabled("MODE_F_CANDLE_REVERSAL")

    if mode_l_ok:
        signal_mode = "MODE_L_LIQUIDITY_SWEEP"
    elif mode_a_ok:
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

    # Auto Learning est appliqué une seule fois dans le scan, avec le symbole connu.
    # Cela permet de combiner mode + token + pattern sans double pénalisation.

    score = (
        breakout_strength * 100
        + vwap_strength * 10
        + obv_strength * 2
        + rsi_score
        + patt_score
        + mode_c_score
        + (trend_score / 8.0) * 2.0
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
        score += 1.0 + mode_f_score + min(mode_f_filter_details.get("confirmation_count", 0) * 0.35, 2.5)

    ok = mode_l_ok or mode_a_ok or mode_b_ok or mode_c_ok or mode_d_ok or mode_e_ok or mode_f_ok

    return ok, score, {
        "trend_ok": trend_ok,
        "trend_score": trend_score,
        "trend_score_max": 8,
        "trend_details": trend_details,
        "trend_missing": ",".join(trend_details.get("missing", [])),
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
        "mode_d_previous_cross": mode_d_previous_cross,
        "mode_d_confirmation_required": True,
        "mode_d_d1_disabled": True,
        "mode_e_ok": mode_e_ok,
        "mode_e_volume_ratio": round(volume_ratio, 2),
        "mode_e_body_ratio": round(body_ratio, 2),
        "mode_e_intrabar": False,
        "mode_e_intrabar_move_pct": None,
        "mode_e_intrabar_distance_ema20_pct": None,
        "mode_f_ok": mode_f_ok,
        "mode_l_ok": mode_l_ok,
        "mode_l": mode_l,
        "mode_f_pattern": ",".join(mode_f_details.get("patterns", [])),
        "mode_f_pattern_label": format_mode_f_patterns(mode_f_details.get("patterns", [])),
        "mode_f_details_text": format_mode_f_pattern_details(mode_f_details.get("patterns", [])),
        "mode_f_score": round(mode_f_score, 4),
        "mode_f_details": mode_f_details,
        "mode_f_filter": mode_f_filter_details,
        "mode_f_confirmation_count": mode_f_filter_details.get("confirmation_count"),
        "mode_f_blocking_reasons": ",".join(mode_f_filter_details.get("blocking_reasons", [])),
        "mode_f_ema20_slope_pct": mode_f_filter_details.get("ema20_slope_pct"),
        "mode_f_distance_below_ema20_pct": mode_f_filter_details.get("distance_below_ema20_pct"),
        "auto_learning_multiplier": 1.0,
        "auto_learning_blocked": False,
        "auto_learning_reasons": "applied_once_in_symbol_scan",
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





def _al_stats(df, col, min_trades):
    out = {}
    if col not in df.columns:
        return out
    try:
        for key, g in df.groupby(col, dropna=False):
            key = str(key)
            if key in ["", "nan", "None"]:
                continue
            if len(g) < min_trades:
                continue
            pnl = pd.to_numeric(g.get("real_pnl_usdc"), errors="coerce").fillna(0)
            wins = int((pnl > 0).sum())
            wr = round((wins / len(g)) * 100, 1) if len(g) else 0
            avg = round(float(pnl.mean()), 4) if len(g) else 0
            total = round(float(pnl.sum()), 4)
            mult = 1.0
            if wr <= 25 and avg <= AUTO_LEARNING_BAD_AVG_PNL:
                mult = AUTO_LEARNING_DISABLE_MULT
            elif wr < AUTO_LEARNING_BAD_WINRATE and avg < 0:
                mult = 0.70
            elif avg <= AUTO_LEARNING_BAD_AVG_PNL:
                mult = 0.80
            elif wr >= AUTO_LEARNING_GOOD_WINRATE and avg >= AUTO_LEARNING_GOOD_AVG_PNL:
                mult = 1.25
            elif wr >= 50 and avg > 0:
                mult = 1.10
            out[key] = {
                "trades": int(len(g)),
                "winrate": wr,
                "avg_pnl": avg,
                "total_pnl": total,
                "multiplier": round(mult, 2),
            }
    except Exception as e:
        print("[AUTO LEARNING STATS ERROR]", e)
    return out


def _al_parse_datetime(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _al_temporary_status(stats, previous_states, now=None):
    """Construit les quarantaines temporaires et les phases de retest.

    Cycle : mauvais -> quarantaine temporaire -> retest sur N nouveaux trades
    -> réactivation si amélioration, sinon nouvelle quarantaine.
    Rien n'est bloqué définitivement.
    """
    now = now or datetime.now()
    states = previous_states if isinstance(previous_states, dict) else {}
    new_states = {}
    blocked = []
    retesting = []

    for key, info in (stats or {}).items():
        trade_count = int(info.get("trades", 0) or 0)
        bad_now = float(info.get("multiplier", 1.0) or 1.0) <= AUTO_LEARNING_DISABLE_MULT
        previous = states.get(key, {}) if isinstance(states.get(key, {}), dict) else {}
        status = str(previous.get("status", ""))

        if status == "blocked":
            until = _al_parse_datetime(previous.get("blocked_until"))
            if until and now < until:
                state = dict(previous)
                state["last_seen_trades"] = trade_count
                new_states[key] = state
                blocked.append(key)
                continue

            # Fin automatique de quarantaine : phase de retest obligatoire.
            state = {
                "status": "retesting",
                "retest_started_at": now.isoformat(timespec="seconds"),
                "retest_start_trades": trade_count,
                "retest_until_trades": trade_count + AUTO_LEARNING_RETEST_TRADES,
                "last_seen_trades": trade_count,
            }
            new_states[key] = state
            retesting.append(key)
            continue

        if status == "retesting":
            target = int(previous.get("retest_until_trades", trade_count + AUTO_LEARNING_RETEST_TRADES) or 0)
            if trade_count < target:
                state = dict(previous)
                state["last_seen_trades"] = trade_count
                new_states[key] = state
                retesting.append(key)
                continue

            # Le quota de retest est atteint : décision avec les statistiques actualisées.
            if bad_now:
                until = now + pd.Timedelta(hours=AUTO_LEARNING_BLOCK_HOURS)
                state = {
                    "status": "blocked",
                    "blocked_at": now.isoformat(timespec="seconds"),
                    "blocked_until": until.isoformat(timespec="seconds"),
                    "last_seen_trades": trade_count,
                    "reason": "performance_insuffisante_apres_retest",
                }
                new_states[key] = state
                blocked.append(key)
            # Sinon, aucune entrée persistée : l'élément redevient pleinement actif.
            continue

        # Premier passage sous le seuil : quarantaine limitée dans le temps.
        if bad_now:
            until = now + pd.Timedelta(hours=AUTO_LEARNING_BLOCK_HOURS)
            state = {
                "status": "blocked",
                "blocked_at": now.isoformat(timespec="seconds"),
                "blocked_until": until.isoformat(timespec="seconds"),
                "last_seen_trades": trade_count,
                "reason": "performance_insuffisante",
            }
            new_states[key] = state
            blocked.append(key)

    return new_states, blocked, retesting


def rebuild_auto_learning_from_history():
    try:
        cfg = load_adaptive_config()
        al = cfg.get("auto_learning", {})
        if not isinstance(al, dict):
            al = default_adaptive_config()["auto_learning"]
        al.setdefault("enabled", AUTO_LEARNING_ENABLED_DEFAULT)
        al.setdefault("auto_apply", AUTO_LEARNING_AUTO_APPLY_DEFAULT)

        df = load_trades_dataframe()
        if df.empty:
            al.update({
                "sample_size": 0,
                "recommendation": "PAS_ASSEZ_DE_DONNEES",
                "mode_multipliers": {},
                "pattern_multipliers": {},
                "token_multipliers": {},
                "blocked_modes": [],
                "blocked_patterns": [],
                "blocked_tokens": [],
                "temporary_blocks": {"modes": {}, "patterns": {}, "tokens": {}},
                "retesting_modes": [],
                "retesting_patterns": [],
                "retesting_tokens": [],
                "notes": ["Aucun trade disponible."],
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            cfg["auto_learning"] = al
            save_adaptive_config(cfg)
            return al

        work = df.tail(AUTO_LEARNING_WINDOW).copy()
        work["real_pnl_usdc"] = pd.to_numeric(work.get("real_pnl_usdc"), errors="coerce").fillna(0)
        # L'apprentissage des patterns est limité aux trades du Mode F.
        # Les autres modes ne doivent pas contaminer les statistiques de patterns.
        if "mode_f_pattern" in work.columns and "mode" in work.columns:
            is_mode_f = work["mode"].astype(str).eq("MODE_F_CANDLE_REVERSAL")
            work["auto_pattern"] = ""
            work.loc[is_mode_f, "auto_pattern"] = (
                work.loc[is_mode_f, "mode_f_pattern"].fillna("").astype(str).str.split(",").str[0]
            )
            work.loc[work["auto_pattern"].str.lower().isin(["nan", "none"]), "auto_pattern"] = ""
        else:
            work["auto_pattern"] = ""

        mode_mult = _al_stats(work, "mode", AUTO_LEARNING_MIN_TRADES_MODE)
        pattern_mult = _al_stats(work, "auto_pattern", AUTO_LEARNING_MIN_TRADES_PATTERN)
        token_mult = _al_stats(work, "symbol", AUTO_LEARNING_MIN_TRADES_TOKEN)

        previous_blocks = al.get("temporary_blocks", {}) if isinstance(al.get("temporary_blocks", {}), dict) else {}
        now_dt = datetime.now()
        mode_states, blocked_modes, retesting_modes = _al_temporary_status(
            mode_mult, previous_blocks.get("modes", {}), now_dt
        )
        pattern_states, blocked_patterns, retesting_patterns = _al_temporary_status(
            pattern_mult, previous_blocks.get("patterns", {}), now_dt
        )
        token_states, blocked_tokens, retesting_tokens = _al_temporary_status(
            token_mult, previous_blocks.get("tokens", {}), now_dt
        )
        temporary_blocks = {
            "modes": mode_states,
            "patterns": pattern_states,
            "tokens": token_states,
        }

        total = len(work)
        pnl_total = round(float(work["real_pnl_usdc"].sum()), 4)
        wins = int((work["real_pnl_usdc"] > 0).sum())
        winrate = round((wins / total) * 100, 1) if total else 0

        notes = []
        if blocked_modes:
            notes.append("Modes en quarantaine temporaire: " + ", ".join(blocked_modes))
        if blocked_patterns:
            notes.append("Patterns en quarantaine temporaire: " + ", ".join(blocked_patterns))
        if blocked_tokens:
            notes.append("Tokens en quarantaine temporaire: " + ", ".join(blocked_tokens))
        if retesting_modes:
            notes.append("Modes en retest automatique: " + ", ".join(retesting_modes))
        if retesting_patterns:
            notes.append("Patterns en retest automatique: " + ", ".join(retesting_patterns))
        if retesting_tokens:
            notes.append("Tokens en retest automatique: " + ", ".join(retesting_tokens))

        try:
            post = load_post_trade_dataframe()
            if not post.empty and "verdict" in post.columns:
                rp = post.tail(AUTO_LEARNING_WINDOW)
                too_early = int((rp["verdict"].astype(str) == "SORTIE_TROP_TOT").sum())
                ratio = round((too_early / len(rp)) * 100, 1) if len(rp) else 0
                if ratio >= 50:
                    notes.append(f"Smart Exit trop rapide: {ratio}% sorties trop tôt.")
        except Exception:
            pass

        rec = "NEUTRE"
        if pnl_total < 0 and winrate < 45:
            rec = "MODE_DEFENSIF"
        if blocked_modes or blocked_patterns or blocked_tokens:
            rec = "FILTRAGE_RECOMMANDE"
        if pnl_total > 0 and winrate >= 55:
            rec = "PARAMETRES_OK"

        al.update({
            "enabled": bool(al.get("enabled", True)),
            "auto_apply": bool(al.get("auto_apply", True)),
            "sample_size": int(total),
            "window": AUTO_LEARNING_WINDOW,
            "pnl_total": pnl_total,
            "winrate": winrate,
            "recommendation": rec,
            "mode_multipliers": mode_mult,
            "pattern_multipliers": pattern_mult,
            "token_multipliers": token_mult,
            "blocked_modes": blocked_modes,
            "blocked_patterns": blocked_patterns,
            "blocked_tokens": blocked_tokens,
            "temporary_blocks": temporary_blocks,
            "retesting_modes": retesting_modes,
            "retesting_patterns": retesting_patterns,
            "retesting_tokens": retesting_tokens,
            "block_duration_hours": AUTO_LEARNING_BLOCK_HOURS,
            "retest_trade_count": AUTO_LEARNING_RETEST_TRADES,
            "notes": notes,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        cfg["auto_learning"] = al
        save_adaptive_config(cfg)
        return al
    except Exception as e:
        print("[AUTO LEARNING ERROR]", e)
        return {"enabled": False, "recommendation": "ERREUR", "notes": [str(e)]}


def get_auto_learning_adjustment(mode=None, symbol=None, pattern=None):
    try:
        if V7_STABILIZATION_ENABLED and V7_DISABLE_AUTO_LEARNING:
            return {"multiplier": 1.0, "blocked": False, "reasons": ["V7 stabilization"]}
        al = load_adaptive_config().get("auto_learning", {})
        if not al or not al.get("enabled", True) or not al.get("auto_apply", True):
            return {"multiplier": 1.0, "blocked": False, "reasons": []}
        mult = 1.0
        blocked = False
        reasons = []
        if mode:
            d = al.get("mode_multipliers", {}).get(mode)
            if d:
                mult *= float(d.get("multiplier", 1.0))
                reasons.append(f"mode:{mode}x{d.get('multiplier')}")
            if mode in al.get("blocked_modes", []):
                blocked = True
                reasons.append(f"mode_blocked:{mode}")
        if symbol:
            d = al.get("token_multipliers", {}).get(symbol)
            if d:
                mult *= float(d.get("multiplier", 1.0))
                reasons.append(f"token:{symbol}x{d.get('multiplier')}")
            if symbol in al.get("blocked_tokens", []):
                blocked = True
                reasons.append(f"token_blocked:{symbol}")
        if pattern:
            p = str(pattern).split(",")[0]
            d = al.get("pattern_multipliers", {}).get(p)
            if d:
                mult *= float(d.get("multiplier", 1.0))
                reasons.append(f"pattern:{p}x{d.get('multiplier')}")
            if p in al.get("blocked_patterns", []):
                blocked = True
                reasons.append(f"pattern_blocked:{p}")
        return {"multiplier": round(max(0.25, min(1.8, mult)), 3), "blocked": blocked, "reasons": reasons}
    except Exception as e:
        return {"multiplier": 1.0, "blocked": False, "reasons": [str(e)]}


def rebuild_adaptive_smart_exit_from_posttrade():
    """
    Lit post_trade_analysis.csv et ajuste la sensibilité du Smart Exit.
    Si beaucoup de sorties sont classées SORTIE_TROP_TOT, on assouplit la sortie :
    - seuil de clôture plus haut
    - seuil de resserrage plus haut
    - health exit plus bas
    """
    try:
        cfg = load_adaptive_config()
        se = cfg.get("smart_exit", {})
        if not isinstance(se, dict):
            se = default_adaptive_config()["smart_exit"]

        se.setdefault("enabled", SMART_EXIT_ADAPTIVE_ENABLED_DEFAULT)
        se.setdefault("auto_apply", True)

        df = load_post_trade_dataframe()
        if df.empty or "verdict" not in df.columns:
            se.update({
                "level": 0,
                "recommendation": "NEUTRE",
                "sample_size": 0,
                "sortie_trop_tot": 0,
                "bonne_sortie": 0,
                "neutre": 0,
                "too_early_ratio": 0,
                "good_ratio": 0,
                "threshold_bonus": 0,
                "health_bonus": 0,
                "tighten_bonus": 0,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            cfg["smart_exit"] = se
            save_adaptive_config(cfg)
            return se

        work = df.copy()
        if "exit_reason" in work.columns:
            # on cible surtout SMART_EXIT, mais on garde EMA20 pour le diagnostic de sorties prématurées
            mask = work["exit_reason"].astype(str).str.contains("SMART_EXIT|EMA20_2_CLOSES", case=False, na=False)
            work = work[mask].copy()

        work = work.tail(SMART_EXIT_ADAPTIVE_WINDOW).copy()
        verdicts = work["verdict"].astype(str).fillna("")

        total = int(len(work))
        too_early = int((verdicts == "SORTIE_TROP_TOT").sum())
        good = int(verdicts.isin([
            "BONNE_SORTIE",
            "TRES_BONNE_SORTIE",
            "EXCELLENTE_SORTIE",
        ]).sum())
        neutral = int(verdicts.isin([
            "SORTIE_ACCEPTABLE",
            "NEUTRE",
        ]).sum())

        too_early_ratio = (too_early / total) if total else 0
        good_ratio = (good / total) if total else 0

        level = 0
        recommendation = "NEUTRE"

        if total >= SMART_EXIT_ADAPTIVE_MIN_ANALYSES:
            if too_early_ratio >= 0.70:
                level = 3
                recommendation = "SMART_EXIT_TRES_TROP_AGRESSIF"
            elif too_early_ratio >= 0.60:
                level = 2
                recommendation = "SMART_EXIT_TROP_AGRESSIF"
            elif too_early_ratio >= SMART_EXIT_ADAPTIVE_TOO_EARLY_RATIO:
                level = 1
                recommendation = "SMART_EXIT_LEGEREMENT_TROP_AGRESSIF"
            elif good_ratio >= SMART_EXIT_ADAPTIVE_GOOD_RATIO and too_early_ratio < 0.35:
                level = 0
                recommendation = "SMART_EXIT_CORRECT"
            else:
                level = 0
                recommendation = "NEUTRE"

        level = max(0, min(SMART_EXIT_ADAPTIVE_MAX_LEVEL, int(level)))

        se.update({
            "level": level,
            "recommendation": recommendation,
            "sample_size": total,
            "sortie_trop_tot": too_early,
            "bonne_sortie": good,
            "neutre": neutral,
            "too_early_ratio": round(too_early_ratio * 100, 1),
            "good_ratio": round(good_ratio * 100, 1),
            "threshold_bonus": round(level * SMART_EXIT_ADAPTIVE_THRESHOLD_STEP, 2),
            "health_bonus": round(level * SMART_EXIT_ADAPTIVE_HEALTH_STEP, 2),
            "tighten_bonus": round(level * SMART_EXIT_ADAPTIVE_TIGHTEN_STEP, 2),
            "auto_apply": bool(se.get("auto_apply", True)),
            "enabled": bool(se.get("enabled", SMART_EXIT_ADAPTIVE_ENABLED_DEFAULT)),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        cfg["smart_exit"] = se
        save_adaptive_config(cfg)
        return se

    except Exception as e:
        print("[ADAPTIVE SMART EXIT] rebuild error", e)
        return default_adaptive_config()["smart_exit"]


def get_adaptive_smart_exit_adjustment():
    try:
        if V7_STABILIZATION_ENABLED:
            return {
                "level": 0,
                "threshold_bonus": 0,
                "health_bonus": 0,
                "tighten_bonus": 0,
                "recommendation": "V7 stabilization",
            }
        cfg = load_adaptive_config()
        se = cfg.get("smart_exit", {})
        if not se or not se.get("enabled", True) or not se.get("auto_apply", True):
            return {
                "level": 0,
                "threshold_bonus": 0,
                "health_bonus": 0,
                "tighten_bonus": 0,
                "recommendation": "DISABLED",
            }
        return {
            "level": int(se.get("level", 0) or 0),
            "threshold_bonus": float(se.get("threshold_bonus", 0) or 0),
            "health_bonus": float(se.get("health_bonus", 0) or 0),
            "tighten_bonus": float(se.get("tighten_bonus", 0) or 0),
            "recommendation": se.get("recommendation", "NEUTRE"),
            "sample_size": se.get("sample_size", 0),
            "too_early_ratio": se.get("too_early_ratio", 0),
            "good_ratio": se.get("good_ratio", 0),
        }
    except Exception:
        return {
            "level": 0,
            "threshold_bonus": 0,
            "health_bonus": 0,
            "tighten_bonus": 0,
            "recommendation": "ERROR",
        }


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
        # V7 : EMA20 seule ne déclenche plus de score de sortie.
        # Elle reste informative dans details, mais n'est plus une raison suffisante pour sortir.
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
        adaptive_se = get_adaptive_smart_exit_adjustment()
        close_threshold = SMART_EXIT_MODE_THRESHOLDS.get(mode_key, SMART_EXIT_CLOSE_SCORE) + adaptive_se.get("threshold_bonus", 0)
        tighten_threshold = SMART_EXIT_MODE_TIGHTEN_THRESHOLDS.get(mode_key, SMART_EXIT_TIGHTEN_SCORE) + adaptive_se.get("tighten_bonus", 0)
        health_exit_level = SMART_EXIT_HEALTH_EXIT_LEVEL.get(mode_key, 40) - adaptive_se.get("health_bonus", 0)
        health_exit_level = max(10, health_exit_level)

        health = 100.0
        health -= min(score * 7.0, 70)
        # V7 : pas de pénalité Health pour une simple clôture sous EMA20.
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
            "adaptive_smart_exit_level": adaptive_se.get("level", 0),
            "adaptive_smart_exit_recommendation": adaptive_se.get("recommendation", "NEUTRE"),
            "adaptive_threshold_bonus": adaptive_se.get("threshold_bonus", 0),
            "adaptive_health_bonus": adaptive_se.get("health_bonus", 0),
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
                "interval": get_active_timeframe(),
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



def learn_smart_exit_patience(mode=None, symbol=None):
    """
    Apprentissage statistique déterministe :
    estime si attendre 0, 1 ou 2 bougies aurait historiquement amélioré la sortie.
    Les statistiques sont filtrées par mode, puis par token si l'échantillon est suffisant.
    """
    result = {
        "wait_candles": 0,
        "sample_size": 0,
        "avg_improvement_15m": 0.0,
        "avg_improvement_30m": 0.0,
        "confidence": 0.0,
        "scope": "GLOBAL",
        "note": "Données insuffisantes",
    }
    if not SMART_EXIT_PATIENCE_ENABLED:
        result["note"] = "Patience désactivée"
        return result

    try:
        df = load_post_trade_dataframe()
        if df.empty:
            return result

        work = df.copy()
        if "exit_reason" in work.columns:
            work = work[
                work["exit_reason"].astype(str).str.contains(
                    "SMART_EXIT", case=False, na=False
                )
            ].copy()

        def usable(frame):
            needed = ["pnl_at_exit_pct", "pnl_15m_pct", "pnl_30m_pct"]
            for col in needed:
                if col not in frame.columns:
                    frame[col] = pd.NA
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            return frame.dropna(subset=["pnl_at_exit_pct", "pnl_15m_pct"])

        selected = work
        scope = "GLOBAL"

        if mode and "mode" in work.columns:
            by_mode = usable(work[work["mode"].astype(str) == str(mode)].copy())
            if len(by_mode) >= SMART_EXIT_PATIENCE_MIN_SAMPLE:
                selected = by_mode
                scope = f"MODE:{mode}"

        if symbol and "symbol" in work.columns:
            by_pair = usable(
                work[
                    (work["symbol"].astype(str) == str(symbol))
                    & (work["mode"].astype(str) == str(mode))
                ].copy()
            )
            if len(by_pair) >= SMART_EXIT_PATIENCE_MIN_SAMPLE:
                selected = by_pair
                scope = f"{symbol}+{mode}"
            else:
                by_symbol = usable(work[work["symbol"].astype(str) == str(symbol)].copy())
                if len(by_symbol) >= SMART_EXIT_PATIENCE_MIN_SAMPLE and scope == "GLOBAL":
                    selected = by_symbol
                    scope = f"TOKEN:{symbol}"

        selected = usable(selected).tail(40)
        if len(selected) < SMART_EXIT_PATIENCE_MIN_SAMPLE:
            return result

        improvement_15 = selected["pnl_15m_pct"] - selected["pnl_at_exit_pct"]
        valid_30 = selected.dropna(subset=["pnl_30m_pct"])
        improvement_30 = (
            valid_30["pnl_30m_pct"] - valid_30["pnl_at_exit_pct"]
            if not valid_30.empty else pd.Series(dtype=float)
        )

        avg15 = float(improvement_15.mean())
        avg30 = float(improvement_30.mean()) if not improvement_30.empty else 0.0
        positive15 = float((improvement_15 > 0).mean())
        positive30 = float((improvement_30 > 0).mean()) if not improvement_30.empty else 0.0

        wait = 0
        if avg15 >= SMART_EXIT_PATIENCE_MIN_EXPECTED_IMPROVEMENT_PCT and positive15 >= 0.55:
            wait = 1
        if (
            avg30 >= SMART_EXIT_PATIENCE_MIN_EXPECTED_IMPROVEMENT_PCT + 0.05
            and positive30 >= 0.55
            and len(valid_30) >= SMART_EXIT_PATIENCE_MIN_SAMPLE
        ):
            wait = 2

        wait = min(wait, SMART_EXIT_PATIENCE_MAX_CANDLES)
        confidence = min(100.0, len(selected) * 8.0) * max(positive15, positive30, 0.4)

        result.update({
            "wait_candles": int(wait),
            "sample_size": int(len(selected)),
            "avg_improvement_15m": round(avg15, 3),
            "avg_improvement_30m": round(avg30, 3),
            "confidence": round(confidence, 1),
            "scope": scope,
            "note": (
                f"{scope} · {len(selected)} analyses · "
                f"gain moyen attente 15m {avg15:+.3f}% · 30m {avg30:+.3f}%"
            ),
        })
        return result
    except Exception as e:
        result["note"] = f"Erreur apprentissage patience: {e}"
        return result


def clear_smart_exit_pending(reason=""):
    trade_state["smart_exit_pending"] = False
    trade_state["smart_exit_pending_since"] = None
    trade_state["smart_exit_pending_candle_time"] = None
    trade_state["smart_exit_wait_candles"] = 0
    trade_state["smart_exit_pending_peak"] = None
    trade_state["smart_exit_pending_score"] = None
    trade_state["smart_exit_pending_reason"] = reason


def smart_exit_patience_snapshot():
    started = trade_state.get("smart_exit_pending_since")
    elapsed_seconds = 0
    if started:
        try:
            elapsed_seconds = max(0, int(time.time() - float(started)))
        except Exception:
            elapsed_seconds = 0
    return {
        "active": bool(trade_state.get("smart_exit_pending")),
        "wait_candles": int(trade_state.get("smart_exit_wait_candles") or 0),
        "pending_since": trade_state.get("smart_exit_pending_since"),
        "pending_candle_time": trade_state.get("smart_exit_pending_candle_time"),
        "peak": trade_state.get("smart_exit_pending_peak"),
        "score": trade_state.get("smart_exit_pending_score"),
        "reason": trade_state.get("smart_exit_pending_reason"),
        "learning_note": trade_state.get("smart_exit_learning_note"),
        "elapsed_seconds": elapsed_seconds,
    }


def apply_smart_exit_before_legacy(symbol, pos, df, current_price, entry, atr_value, pnl_pct, candle_time, min_hold_active):
    """
    V6.4 : Smart Exit avec pré-sortie adaptative.
    Le délai (0, 1 ou 2 bougies) est appris sur les résultats post-trade.
    Les protections critiques restent immédiates.
    """
    global LAST_SELL_REASON

    try:
        if not is_smart_exit_mode():
            return False

        stop_atr = get_v7_atr_reference(trade_state.get("entry_atr"), atr_value)
        smart_stop_mult = V7_SMART_EXIT_ATR_STOP_MULT if V7_STABILIZATION_ENABLED else SMART_EXIT_ATR_STOP_MULT
        hard_stop = entry - smart_stop_mult * stop_atr
        smart_close, smart_tighten, smart_score, smart_details = smart_exit_reversal_decision(
            df, current_price, entry, trade_state.get("signal_mode")
        )

        health = float(smart_details.get("health", 50) or 50)
        trade_state["smart_exit_score"] = round(smart_score, 2)
        trade_state["smart_exit_health"] = health

        print(
            f"[SMART EXIT CHECK] {symbol} mode={trade_state.get('signal_mode')} "
            f"score={smart_score:.2f} health={health:.1f} "
            f"close={smart_close} pending={trade_state.get('smart_exit_pending')} "
            f"pnl={pnl_pct:.2f}%"
        )

        # Sécurité absolue : jamais retardée.
        if current_price <= hard_stop:
            clear_smart_exit_pending("ATR_STOP")
            LAST_SELL_REASON = "SMART_EXIT_ATR_STOP"
            notify_telegram(
                f"🛑 <b>SMART EXIT - ATR STOP</b>\n"
                f"Symbole: {symbol}\nMode: {trade_state.get('signal_mode')}\n"
                f"Entry: {entry:.5f}\nPrix: {current_price:.5f}\n"
                f"Stop: {hard_stop:.5f}\nPnL: {pnl_pct:.2f}%\n"
                f"Health: {health:.1f}/100"
            )
            if close_position_full(pos, current_price):
                final_balance = round(get_balance(), 2)
                send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                reset_trade_state_after_sell(candle_time)
            return True

        critical_exit = (
            health <= SMART_EXIT_PATIENCE_CRITICAL_HEALTH
            or (
                bool(smart_details.get("close_under_ema50"))
                and bool(smart_details.get("obv_bad"))
            )
        )

        # Gestion d'une pré-sortie déjà active.
        if trade_state.get("smart_exit_pending"):
            peak = trade_state.get("smart_exit_pending_peak")
            peak = max(float(peak or current_price), float(current_price))
            trade_state["smart_exit_pending_peak"] = peak

            peak_giveback_pct = ((peak - float(current_price)) / peak) * 100 if peak else 0
            tf_ms = get_active_timeframe_minutes() * 60 * 1000
            pending_candle = int(trade_state.get("smart_exit_pending_candle_time") or candle_time or 0)
            elapsed_candles = max(0, int(((candle_time or pending_candle) - pending_candle) // tf_ms))
            required = int(trade_state.get("smart_exit_wait_candles") or 1)

            recovered = (
                not smart_close
                and health >= SMART_EXIT_PATIENCE_CANCEL_HEALTH
            )

            if recovered:
                print("[SMART EXIT PATIENCE] Signal récupéré, pré-sortie annulée.")
                notify_telegram(
                    f"✅ <b>Pré-sortie annulée</b>\nSymbole: {symbol}\n"
                    f"Health revenu à {health:.1f}/100\n"
                    f"Le bot conserve la position."
                )
                clear_smart_exit_pending("RECOVERY")
                return True

            confirm_now = (
                critical_exit
                or peak_giveback_pct >= SMART_EXIT_PATIENCE_PEAK_GIVEBACK_PCT
                or elapsed_candles >= required
            )

            if confirm_now and not min_hold_active:
                if is_order_blocked(symbol, "SELL"):
                    return True
                LAST_SELL_REASON = (
                    "SMART_EXIT_CONFIRMED_CRITICAL" if critical_exit
                    else "SMART_EXIT_CONFIRMED_PATIENCE"
                )
                print(
                    f"[SMART EXIT CONFIRMED] elapsed={elapsed_candles}/{required} "
                    f"giveback={peak_giveback_pct:.3f}% critical={critical_exit}"
                )
                notify_telegram(
                    f"📉 <b>SMART EXIT CONFIRMÉ</b>\n"
                    f"Symbole: {symbol}\nMode: {trade_state.get('signal_mode')}\n"
                    f"Entry: {entry:.5f}\nPrix: {current_price:.5f}\n"
                    f"PnL: {pnl_pct:.2f}%\nScore: {smart_score:.2f}\n"
                    f"Health: {health:.1f}/100\n"
                    f"Attente: {elapsed_candles}/{required} bougie(s)\n"
                    f"Repli depuis pic attente: {peak_giveback_pct:.3f}%\n"
                    f"Apprentissage: {trade_state.get('smart_exit_learning_note')}"
                )
                if close_position_full(pos, current_price):
                    final_balance = round(get_balance(), 2)
                    send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                    reset_trade_state_after_sell(candle_time)
                return True

            print(
                f"[SMART EXIT PATIENCE] attente {elapsed_candles}/{required} "
                f"peak={peak:.6f} giveback={peak_giveback_pct:.3f}%"
            )
            return True

        # Nouveau signal de sortie.
        if smart_close and not min_hold_active:
            learning = learn_smart_exit_patience(
                mode=trade_state.get("signal_mode"),
                symbol=symbol,
            )
            wait_candles = int(learning.get("wait_candles", 0) or 0)
            optimizer_profile = get_optimizer_profile(
                symbol,
                trade_state.get("signal_mode"),
                get_active_timeframe(),
            )
            if optimizer_profile and optimizer_profile.get("patience_candles") is not None:
                wait_candles = int(optimizer_profile.get("patience_candles"))
                learning["note"] = (
                    f"{learning.get('note')} | optimizer="
                    f"{optimizer_profile.get('reason')}"
                )
            trade_state["smart_exit_learning_note"] = learning.get("note")

            if critical_exit or wait_candles <= 0:
                if is_order_blocked(symbol, "SELL"):
                    return True
                LAST_SELL_REASON = (
                    "SMART_EXIT_UNIFIED_CRITICAL" if critical_exit
                    else "SMART_EXIT_UNIFIED"
                )
                notify_telegram(
                    f"📉 <b>SMART EXIT - Sortie immédiate</b>\n"
                    f"Symbole: {symbol}\nMode: {trade_state.get('signal_mode')}\n"
                    f"PnL: {pnl_pct:.2f}%\nScore: {smart_score:.2f}\n"
                    f"Health: {health:.1f}/100\n"
                    f"Apprentissage: {learning.get('note')}"
                )
                if close_position_full(pos, current_price):
                    final_balance = round(get_balance(), 2)
                    send_trade_summary(symbol, current_price, LAST_SELL_REASON, final_balance)
                    reset_trade_state_after_sell(candle_time)
                return True

            trade_state["smart_exit_pending"] = True
            trade_state["smart_exit_pending_since"] = time.time()
            trade_state["smart_exit_pending_candle_time"] = candle_time
            trade_state["smart_exit_wait_candles"] = wait_candles
            trade_state["smart_exit_pending_peak"] = float(current_price)
            trade_state["smart_exit_pending_score"] = round(smart_score, 2)
            trade_state["smart_exit_pending_reason"] = "STATISTICAL_PATIENCE"

            print(f"[SMART EXIT PRE-EXIT] attente adaptative {wait_candles} bougie(s).")
            notify_telegram(
                f"🟡 <b>SMART EXIT - Pré-sortie adaptative</b>\n"
                f"Symbole: {symbol}\nMode: {trade_state.get('signal_mode')}\n"
                f"PnL: {pnl_pct:.2f}%\nScore sortie: {smart_score:.2f}\n"
                f"Health: {health:.1f}/100\n"
                f"Attente prévue: {wait_candles} bougie(s)\n"
                f"Confiance statistique: {learning.get('confidence')}%\n"
                f"{learning.get('note')}\n"
                f"Le stop ATR reste actif."
            )
            return True

        if smart_tighten:
            tightened_stop = current_price - 0.8 * get_v7_atr_reference(trade_state.get("entry_atr"), atr_value)
            current_trail = trade_state.get("trailing_stop")
            if current_trail is None or tightened_stop > current_trail:
                trade_state["trailing_stop"] = tightened_stop

        # Les modes Smart Exit ne passent pas dans les anciennes sorties EMA20.
        return True

    except Exception as e:
        print("[ERREUR APPLY SMART EXIT]", e)
        return False




def _tp1_stats_for_scope(df, mode=None, symbol=None):
    """Retourne des statistiques prudentes sur les trades comparables."""
    if df.empty:
        return None

    work = df.copy()
    if mode and "mode" in work.columns:
        scoped = work[work["mode"].astype(str) == str(mode)].copy()
        if symbol and "symbol" in scoped.columns:
            pair = scoped[scoped["symbol"].astype(str) == str(symbol)].copy()
            if len(pair) >= ADAPTIVE_TP1_MIN_SAMPLE:
                work = pair
            elif len(scoped) >= ADAPTIVE_TP1_MIN_SAMPLE:
                work = scoped
    elif symbol and "symbol" in work.columns:
        scoped = work[work["symbol"].astype(str) == str(symbol)].copy()
        if len(scoped) >= ADAPTIVE_TP1_MIN_SAMPLE:
            work = scoped

    work = work.tail(ADAPTIVE_TP1_WINDOW).copy()
    if len(work) < ADAPTIVE_TP1_MIN_SAMPLE:
        return None

    tp1 = work.get("tp1_done", pd.Series(False, index=work.index))
    tp1_hit = tp1.astype(str).str.lower().eq("true")
    pnl = pd.to_numeric(work.get("pnl_price_pct"), errors="coerce").fillna(0)

    # Trades positifs qui n'ont pas atteint TP1 :
    # signal fort que l'objectif est probablement trop éloigné.
    positive_without_tp1 = ((pnl > 0.10) & (~tp1_hit)).mean()
    tp1_rate = tp1_hit.mean()
    median_positive = float(pnl[pnl > 0].median()) if (pnl > 0).any() else 0.0

    return {
        "sample": int(len(work)),
        "tp1_rate": float(tp1_rate),
        "positive_without_tp1": float(positive_without_tp1),
        "median_positive_pct": median_positive,
    }



def _optimizer_default():
    return {
        "enabled": AUTO_OPTIMIZER_ENABLED,
        "updated_at": None,
        "profiles": {},
        "global": {
            "tp1_mult": 1.25,
            "stop_mult": ATR_STOP_MULT,
            "patience_candles": 0,
            "sample": 0,
            "confidence": 0,
        },
    }


def load_auto_optimizer():
    try:
        if AUTO_OPTIMIZER_PATH.exists():
            data = json.loads(AUTO_OPTIMIZER_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _optimizer_default()
                base.update(data)
                base["profiles"] = data.get("profiles", {}) if isinstance(data.get("profiles"), dict) else {}
                return base
    except Exception as e:
        print("[AUTO OPTIMIZER LOAD ERROR]", e)
    return _optimizer_default()


def save_auto_optimizer(data):
    try:
        AUTO_OPTIMIZER_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        schedule_github_backup(delay_seconds=15)
        return True
    except Exception as e:
        print("[AUTO OPTIMIZER SAVE ERROR]", e)
        return False


def optimizer_profile_key(symbol, mode, timeframe=None):
    return f"{symbol}|{mode or 'UNKNOWN'}|{timeframe or get_active_timeframe()}"


def _clip(value, low, high):
    return max(low, min(high, value))


def rebuild_auto_optimizer_profiles():
    """
    Construit des paramètres prudents par token + mode + timeframe.
    Il s'agit d'une adaptation statistique bornée, pas d'un modèle prédictif.
    """
    result = load_auto_optimizer()
    if not AUTO_OPTIMIZER_ENABLED:
        result["enabled"] = False
        return result

    try:
        trades = load_trades_dataframe()
        post = load_post_trade_dataframe()

        if trades.empty:
            result["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_auto_optimizer(result)
            return result

        work = trades.tail(AUTO_OPTIMIZER_WINDOW).copy()
        for col in ["pnl_price_pct", "real_pnl_usdc", "smart_exit_health"]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce")

        if "timeframe" not in work.columns:
            work["timeframe"] = get_active_timeframe()
        work["timeframe"] = work["timeframe"].fillna(get_active_timeframe()).astype(str)
        work["mode"] = work.get("mode", "").fillna("UNKNOWN").astype(str)
        work["symbol"] = work.get("symbol", "").fillna("").astype(str)
        work["tp1_hit"] = work.get("tp1_done", False).astype(str).str.lower().eq("true")

        post_map = {}
        if not post.empty:
            post = post.copy()
            for c in ["pnl_at_exit_pct", "pnl_15m_pct", "pnl_30m_pct", "max_gain_after_exit_pct"]:
                if c in post.columns:
                    post[c] = pd.to_numeric(post[c], errors="coerce")
            if "mode" in post.columns and "symbol" in post.columns:
                for (sym, mode), g in post.groupby(["symbol", "mode"], dropna=False):
                    g = g.tail(AUTO_OPTIMIZER_WINDOW)
                    imp15 = (g["pnl_15m_pct"] - g["pnl_at_exit_pct"]).dropna() if "pnl_15m_pct" in g else pd.Series(dtype=float)
                    imp30 = (g["pnl_30m_pct"] - g["pnl_at_exit_pct"]).dropna() if "pnl_30m_pct" in g else pd.Series(dtype=float)
                    post_map[(str(sym), str(mode))] = {
                        "imp15": float(imp15.mean()) if not imp15.empty else 0.0,
                        "imp30": float(imp30.mean()) if not imp30.empty else 0.0,
                        "n": int(len(g)),
                    }

        profiles = {}
        for (symbol, mode, timeframe), g in work.groupby(["symbol", "mode", "timeframe"], dropna=False):
            if not symbol or len(g) < AUTO_OPTIMIZER_MIN_SAMPLE:
                continue

            pnl_pct = pd.to_numeric(g["pnl_price_pct"], errors="coerce").dropna()
            real_pnl = pd.to_numeric(g.get("real_pnl_usdc"), errors="coerce").dropna()
            tp1_rate = float(g["tp1_hit"].mean())
            positive_without_tp1 = float(((g["pnl_price_pct"].fillna(0) > 0.10) & (~g["tp1_hit"])).mean())
            winrate = float((real_pnl > 0).mean()) if not real_pnl.empty else float((pnl_pct > 0).mean())
            avg_pnl = float(real_pnl.mean()) if not real_pnl.empty else float(pnl_pct.mean() if not pnl_pct.empty else 0)

            # TP1: rapprocher si rarement atteint, éloigner légèrement s'il est trop facile.
            base_tp1 = float(ADAPTIVE_TP1_MODE_MULT.get(mode, 1.25))
            tp1_mult = base_tp1
            if tp1_rate < 0.20:
                tp1_mult -= 0.25
            elif tp1_rate < 0.35:
                tp1_mult -= 0.15
            elif tp1_rate > 0.70:
                tp1_mult += 0.10
            if positive_without_tp1 >= 0.50:
                tp1_mult -= 0.20
            elif positive_without_tp1 >= 0.30:
                tp1_mult -= 0.10
            tp1_mult = round(_clip(tp1_mult, AUTO_OPTIMIZER_MIN_TP1_MULT, AUTO_OPTIMIZER_MAX_TP1_MULT), 3)

            # Stop: réduire légèrement si les pertes sont fréquentes, sans devenir excessivement serré.
            stop_mult = float(ATR_STOP_MULT)
            loss_rate = 1.0 - winrate
            median_loss_pct = abs(float(pnl_pct[pnl_pct < 0].median())) if (pnl_pct < 0).any() else 0.0
            if loss_rate >= 0.70 and avg_pnl < 0:
                stop_mult -= 0.35
            elif loss_rate >= 0.55 and avg_pnl < 0:
                stop_mult -= 0.20
            if median_loss_pct > 0.60:
                stop_mult -= 0.15
            if winrate >= 0.60 and avg_pnl > 0:
                stop_mult += 0.10
            stop_mult = round(_clip(stop_mult, AUTO_OPTIMIZER_MIN_STOP_MULT, AUTO_OPTIMIZER_MAX_STOP_MULT), 3)

            # Patience: attendre seulement si les sorties passées auraient bénéficié de 15/30 min.
            ps = post_map.get((str(symbol), str(mode)), {"imp15": 0, "imp30": 0, "n": 0})
            patience = 0
            if ps["n"] >= AUTO_OPTIMIZER_MIN_SAMPLE and ps["imp15"] >= 0.15:
                patience = 1
            if ps["n"] >= AUTO_OPTIMIZER_MIN_SAMPLE and ps["imp30"] >= 0.25:
                patience = 2
            patience = int(_clip(patience, AUTO_OPTIMIZER_MIN_PATIENCE, AUTO_OPTIMIZER_MAX_PATIENCE))

            confidence = round(min(100.0, len(g) * 8.0), 1)
            key = optimizer_profile_key(symbol, mode, timeframe)
            profiles[key] = {
                "symbol": str(symbol),
                "mode": str(mode),
                "timeframe": str(timeframe),
                "sample": int(len(g)),
                "winrate": round(winrate * 100, 1),
                "avg_pnl": round(avg_pnl, 4),
                "tp1_rate": round(tp1_rate * 100, 1),
                "positive_without_tp1": round(positive_without_tp1 * 100, 1),
                "tp1_mult": tp1_mult,
                "stop_mult": stop_mult,
                "patience_candles": patience,
                "post_exit_improvement_15m": round(ps["imp15"], 3),
                "post_exit_improvement_30m": round(ps["imp30"], 3),
                "confidence": confidence,
                "reason": (
                    f"{len(g)} trades | WR {winrate*100:.1f}% | "
                    f"TP1 {tp1_rate*100:.1f}% | +15m {ps['imp15']:+.3f}%"
                ),
            }

        result.update({
            "enabled": True,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "profiles": profiles,
        })
        save_auto_optimizer(result)
        return result

    except Exception as e:
        print("[AUTO OPTIMIZER BUILD ERROR]", e)
        result["last_error"] = str(e)
        return result


def get_optimizer_profile(symbol, mode, timeframe=None):
    try:
        if V7_STABILIZATION_ENABLED and V7_DISABLE_AUTO_OPTIMIZER:
            return None
        data = load_auto_optimizer()
        key = optimizer_profile_key(symbol, mode, timeframe)
        profile = data.get("profiles", {}).get(key)
        if profile:
            return profile

        # Recalcul au besoin, puis nouvelle tentative.
        data = rebuild_auto_optimizer_profiles()
        return data.get("profiles", {}).get(key)
    except Exception as e:
        print("[AUTO OPTIMIZER PROFILE ERROR]", e)
        return None


def calculate_adaptive_tp1(entry, atr_value, mode=None, symbol=None):
    """
    Calcule un TP1 fixé à l'entrée.
    Il ne se déplace plus ensuite pendant le trade.
    """
    profile = get_optimizer_profile(symbol, mode, get_active_timeframe())
    base_mult = float(
        profile.get("tp1_mult")
        if profile and profile.get("tp1_mult") is not None
        else ADAPTIVE_TP1_MODE_MULT.get(mode, ATR_TP_MULT)
    )
    mult = base_mult
    reasons = [f"base_{mode or 'DEFAULT'}={base_mult:.2f}ATR"]
    if profile:
        reasons.append(f"optimizer:{profile.get('reason')}")
    sample = 0

    if ADAPTIVE_TP1_ENABLED:
        try:
            stats = _tp1_stats_for_scope(load_trades_dataframe(), mode=mode, symbol=symbol)
            if stats:
                sample = stats["sample"]
                tp1_rate = stats["tp1_rate"]
                missed_positive = stats["positive_without_tp1"]
                median_positive = stats["median_positive_pct"]

                # TP1 rarement atteint : on rapproche progressivement l'objectif.
                if tp1_rate < 0.20:
                    mult -= 0.25
                    reasons.append("TP1<20%:-0.25")
                elif tp1_rate < 0.35:
                    mult -= 0.15
                    reasons.append("TP1<35%:-0.15")
                elif tp1_rate > 0.65:
                    mult += 0.10
                    reasons.append("TP1>65%:+0.10")

                # Beaucoup de trades positifs retournent sans toucher TP1.
                if missed_positive >= 0.50:
                    mult -= 0.20
                    reasons.append("positifs_sans_TP1>=50%:-0.20")
                elif missed_positive >= 0.30:
                    mult -= 0.10
                    reasons.append("positifs_sans_TP1>=30%:-0.10")

                # Si les gains positifs habituels sont modestes par rapport à l'ATR,
                # on rapproche encore légèrement le TP1.
                atr_pct = (float(atr_value) / float(entry)) * 100 if entry else 0
                if atr_pct > 0 and median_positive > 0:
                    observed_mult = median_positive / atr_pct
                    target_from_history = max(
                        ADAPTIVE_TP1_MIN_MULT,
                        min(ADAPTIVE_TP1_MAX_MULT, observed_mult * 0.90)
                    )
                    mult = mult * 0.65 + target_from_history * 0.35
                    reasons.append(f"historique={target_from_history:.2f}ATR")

        except Exception as e:
            reasons.append(f"fallback:{e}")

    mult = round(max(ADAPTIVE_TP1_MIN_MULT, min(ADAPTIVE_TP1_MAX_MULT, mult)), 3)
    price = float(entry) + mult * float(atr_value)

    return {
        "price": price,
        "multiplier": mult,
        "sample": sample,
        "reason": " | ".join(reasons),
    }


def get_v7_atr_reference(entry_atr, current_atr):
    """ATR de référence V7 : évite qu'un stop basé sur un ATR d'entrée
    trop faible soit déclenché par le bruit normal du marché."""
    try:
        e = float(entry_atr or 0)
        c = float(current_atr or 0)
        if e <= 0:
            return c
        if not V7_STABILIZATION_ENABLED or not V7_DYNAMIC_ATR_STOP_ENABLED or c <= 0:
            return e
        return max(e, c * V7_ATR_REFERENCE_MIN_FACTOR)
    except Exception:
        return float(entry_atr or current_atr or 0)


def manage_open_position(pos, df, current_price, candle_time):
    global trade_state, LAST_SELL_REASON
    symbol = pos["symbol"]
    last = df.iloc[-1]

    entry = trade_state["entry_price"] or pos["entry"]
    current_atr = last["atr"]
    atr_value = trade_state["entry_atr"] or current_atr
    atr_stop_value = get_v7_atr_reference(trade_state.get("entry_atr"), current_atr)

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
    tp1_price = trade_state.get("tp1_price")
    if not tp1_price:
        tp1_info = calculate_adaptive_tp1(
            entry=entry,
            atr_value=atr_value,
            mode=trade_state.get("signal_mode"),
            symbol=symbol,
        )
        tp1_price = tp1_info["price"]
        trade_state["tp1_price"] = round(tp1_price, 8)
        trade_state["tp1_adaptive_mult"] = tp1_info["multiplier"]
        trade_state["tp1_adaptive_reason"] = tp1_info["reason"]
        trade_state["tp1_adaptive_sample"] = tp1_info["sample"]
    else:
        tp1_price = float(tp1_price)
    optimizer_profile = get_optimizer_profile(
        symbol,
        trade_state.get("signal_mode"),
        get_active_timeframe(),
    )
    stop_mult = float(
        optimizer_profile.get("stop_mult")
        if optimizer_profile and optimizer_profile.get("stop_mult") is not None
        else ATR_STOP_MULT
    )
    effective_stop_mult = V7_ATR_STOP_MULT if V7_STABILIZATION_ENABLED else stop_mult
    hard_stop = entry - effective_stop_mult * atr_stop_value
    trade_state["optimizer_profile_key"] = optimizer_profile_key(
        symbol, trade_state.get("signal_mode"), get_active_timeframe()
    )
    trade_state["optimizer_tp1_mult"] = (
        optimizer_profile.get("tp1_mult") if optimizer_profile else None
    )
    trade_state["optimizer_stop_mult"] = stop_mult
    trade_state["optimizer_patience_candles"] = (
        optimizer_profile.get("patience_candles") if optimizer_profile else None
    )
    trade_state["optimizer_confidence"] = (
        optimizer_profile.get("confidence") if optimizer_profile else None
    )
    trade_state["optimizer_reason"] = (
        optimizer_profile.get("reason") if optimizer_profile else "profil insuffisant"
    )

    if trade_state["tp1_done"]:
        trailing = trade_state["highest_price"] - ATR_TRAIL_MULT * atr_stop_value
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
    min_hold_ms = MIN_HOLD_CANDLES_AFTER_BUY * get_active_timeframe_minutes() * 60 * 1000
    min_hold_active = (
        entry_candle_time is not None
        and candle_time is not None
        and candle_time < entry_candle_time + min_hold_ms
    )

    ema_break = raw_ema_break and not min_hold_active and V7_EMA20_EXIT_ENABLED
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

    if trade_state.get("signal_mode") == "MODE_D_EMA_PULLBACK" and not is_smart_exit_mode() and V7_EMA20_EXIT_ENABLED:
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
    if trade_state.get("signal_mode") == "MODE_E_EARLY_MOMENTUM" and not is_smart_exit_mode() and V7_EMA20_EXIT_ENABLED:
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
        stop_atr = get_v7_atr_reference(trade_state.get("entry_atr"), atr_value)
        smart_stop_mult = V7_SMART_EXIT_ATR_STOP_MULT if V7_STABILIZATION_ENABLED else SMART_EXIT_ATR_STOP_MULT
        hard_stop = entry - smart_stop_mult * stop_atr

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
        trade_state["tp1_price"] = round(tp1_price, 5)
        trade_state["tp1_exec_price"] = round(current_price, 5)
        partial_qty = abs(pos["size"]) * 0.5
        partial_notional = partial_qty * current_price
        total_notional = abs(pos["size"]) * current_price

        # Si 50% est sous le minimum de 10 USDC, close_position_fraction()
        # bascule automatiquement sur une fermeture totale.
        LAST_SELL_REASON = (
            "TP1_PARTIAL" if partial_notional >= MIN_ORDER_NOTIONAL_USD
            else "TP1_MIN_ORDER_FULL_CLOSE"
        )

        action_text = (
            "vente 50%" if partial_notional >= MIN_ORDER_NOTIONAL_USD
            else "position trop petite pour 50% → vente 100%"
        )
        print(
            f"[TP1] Objectif adaptatif {trade_state.get('tp1_adaptive_mult')} ATR atteint. "
            f"Partiel={partial_notional:.2f} USDC / Total={total_notional:.2f} USDC | {action_text}"
        )

        if close_position_fraction(pos, 0.5, current_price):
            trade_state["tp1_done"] = True
            notify_telegram(
                f"🎯 <b>TP1 exécuté</b>\n"
                f"Symbole: {symbol}\n"
                f"Entry: {entry:.5f}\n"
                f"Prix: {current_price:.5f}\n"
                f"TP1: {tp1_price:.5f}\n"
                f"Multiplicateur: {trade_state.get('tp1_adaptive_mult')} ATR\n"
                f"Apprentissage: {trade_state.get('tp1_adaptive_reason')}\n"
                f"PnL: {pnl_pct:.2f}%\n"
                f"Solde: {round(get_balance(), 2)} USDC\n"
                f"Action: {action_text}"
            )
        else:
            print("[TP1] Ordre non exécuté; TP1 reste en attente sans notifier un faux TP1.")
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


def update_dashboard_chart_cache(symbol, df, current_price, confirmed_time):
    """Mémorise un mini-snapshot graphique à partir du dataframe déjà traité."""
    try:
        confirmed_time = int(confirmed_time)
        def chart_safe_float(value):
            try:
                return round(float(value), 8) if value is not None else None
            except (TypeError, ValueError):
                return None

        active_trade = None
        if trade_state.get("in_position") and trade_state.get("symbol") == symbol:
            active_trade = {
                "side": trade_state.get("mode_l_side") or "LONG",
                "entry": chart_safe_float(trade_state.get("entry_price")),
                "stop": chart_safe_float(trade_state.get("mode_l_stop") or trade_state.get("trailing_stop")),
                "target": chart_safe_float(trade_state.get("mode_l_target")),
                "tp1": chart_safe_float(trade_state.get("tp1_price")),
                "mode": trade_state.get("signal_mode"),
            }
        with DASHBOARD_CHART_LOCK:
            existing = DASHBOARD_CHART_CACHE.get(symbol)
            if existing and existing.get("confirmed_time") == confirmed_time:
                existing = dict(existing)
                existing["current_price"] = round(float(current_price), 8)
                existing["trade"] = active_trade
                existing["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                DASHBOARD_CHART_CACHE[symbol] = existing
                return
        rows = []
        for _, r in df.tail(220).iterrows():
            rows.append({
                "time": int(r["time"]),
                "open": round(float(r["open"]), 8),
                "high": round(float(r["high"]), 8),
                "low": round(float(r["low"]), 8),
                "close": round(float(r["close"]), 8),
                "volume": round(float(r.get("volume", 0) or 0), 8),
                "ema20": round(float(r["ema20"]), 8),
                "ema50": round(float(r["ema50"]), 8),
                "vwap": round(float(r["vwap"]), 8),
            })
        last = df.iloc[-1]
        sweep = detect_liquidity_sweep(df)
        close = float(last["close"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        vwap = float(last["vwap"])
        if close > ema20 and ema20 > ema50 and close > vwap:
            bias = "HAUSSIER"
        elif close < ema20 and ema20 < ema50 and close < vwap:
            bias = "BAISSIER"
        else:
            bias = "NEUTRE / MIXTE"
        snapshot = {
            "ready": True,
            "trade": active_trade,
            "symbol": symbol,
            "timeframe": get_active_timeframe(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "confirmed_time": int(confirmed_time),
            "current_price": round(float(current_price), 8),
            "bias": bias,
            "rsi": round(float(last["rsi"]), 2),
            "atr": round(float(last["atr"]), 8),
            "candles": rows,
            "pools_high": [round(float(x), 8) for x in sweep.get("pools_high", [])[:8]],
            "pools_low": [round(float(x), 8) for x in sweep.get("pools_low", [])[:8]],
            "sweep": {
                "valid": bool(sweep.get("valid")),
                "side": sweep.get("side"),
                "level": round(float(sweep["level"]), 8) if sweep.get("level") is not None else None,
                "stop": round(float(sweep["stop"]), 8) if sweep.get("stop") is not None else None,
                "target": round(float(sweep["target"]), 8) if sweep.get("target") is not None else None,
                "reason": sweep.get("reason", ""),
            },
        }
        with DASHBOARD_CHART_LOCK:
            DASHBOARD_CHART_CACHE[symbol] = snapshot
    except Exception as e:
        print("[DASHBOARD CHART CACHE ERROR]", symbol, e)


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
    update_dashboard_chart_cache(symbol, df, current_price, confirmed_time)
    return df, confirmed_time, current_price


def scan_symbols_for_best_buy(force_rescan=False):
    global last_candle_times
    candidates = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol in get_enabled_symbols():
        df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
        if df is None:
            continue

        new_candle = confirmed_time != last_candle_times.get(symbol)
        if not new_candle and not force_rescan:
            continue

        # En rescannage après une vente, la bougie est évaluée une seule fois
        # sans modifier artificiellement son horodatage.
        if new_candle:
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
            "| trend_score:", f"{details.get('trend_score')}/8",
            "| trend_missing:", details.get("trend_missing"),
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
            f"[SIGNAL] {symbol} {signal_text} trend={details['trend_ok']} trend_score={details.get('trend_score')}/8 "
            f"trend_missing={details.get('trend_missing')} rsi={details['rsi_ok']} "
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
            symbol_auto_learning = get_auto_learning_adjustment(
                mode=details.get("signal_mode"),
                symbol=symbol,
                pattern=details.get("mode_f_pattern") if details.get("signal_mode") == "MODE_F_CANDLE_REVERSAL" else None,
            )
            if symbol_auto_learning.get("blocked"):
                print(f"[AUTO LEARNING BLOCK] {symbol} {details.get('signal_mode')} reasons={symbol_auto_learning.get('reasons')}")
                continue
            if symbol_auto_learning.get("multiplier", 1.0) != 1.0:
                score = score * float(symbol_auto_learning.get("multiplier", 1.0))
                details["score"] = score
                details["auto_learning_multiplier"] = symbol_auto_learning.get("multiplier")
                details["auto_learning_reasons"] = ",".join(symbol_auto_learning.get("reasons", []))

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



def evaluate_smart_reentry(candidate, cooldown_until):
    """
    Décide si un signal BUY peut contourner le cooldown après une vente.
    Retourne (autorisé, motif).
    """
    if not cooldown_until:
        return True, "NO_COOLDOWN"

    confirmed_time = candidate.get("confirmed_time")
    if confirmed_time is None or confirmed_time >= cooldown_until:
        return True, "COOLDOWN_FINISHED"

    if not SMART_REENTRY_ENABLED:
        return False, "SMART_REENTRY_DISABLED"

    symbol = candidate.get("symbol")
    score = float(candidate.get("score", 0) or 0)
    details = candidate.get("details") or {}
    trend_score = int(details.get("trend_score", 0) or 0)
    mode = details.get("signal_mode")
    breakout = bool(details.get("breakout_ok"))
    green = bool(details.get("green_candle"))
    obv = bool(details.get("obv_rising"))

    # Un autre token n'est pas responsable de la sortie précédente.
    if symbol != LAST_EXIT_SYMBOL:
        allowed = (
            score >= SMART_REENTRY_OTHER_TOKEN_MIN_SCORE
            and trend_score >= SMART_REENTRY_OTHER_TOKEN_MIN_TREND_SCORE
            and green
            and (breakout or obv)
        )
        return allowed, (
            f"OTHER_TOKEN score={score:.2f} trend={trend_score} "
            f"green={green} breakout={breakout} obv={obv}"
        )

    # Après un stop de sécurité, aucune réentrée immédiate sur le même token.
    if LAST_EXIT_REASON in SMART_REENTRY_BLOCKED_EXIT_REASONS:
        return False, f"SAME_TOKEN_BLOCKED_EXIT:{LAST_EXIT_REASON}"

    changed_mode = bool(mode and LAST_EXIT_MODE and mode != LAST_EXIT_MODE)
    very_strong = score >= SMART_REENTRY_SAME_TOKEN_STRONG_SCORE

    allowed = (
        score >= SMART_REENTRY_SAME_TOKEN_MIN_SCORE
        and trend_score >= SMART_REENTRY_SAME_TOKEN_MIN_TREND_SCORE
        and breakout
        and green
        and obv
        and (changed_mode or very_strong)
    )

    return allowed, (
        f"SAME_TOKEN score={score:.2f} trend={trend_score} "
        f"mode={mode} previous_mode={LAST_EXIT_MODE} changed={changed_mode} "
        f"strong={very_strong} breakout={breakout} green={green} obv={obv}"
    )


def consume_reentry_rescan():
    global REENTRY_RESCAN_PENDING
    force = bool(REENTRY_RESCAN_PENDING)
    REENTRY_RESCAN_PENDING = False
    return force


def run():
    github_restore_data()
    migrate_legacy_data_files()
    rebuild_auto_optimizer_profiles()
    global META_BY_SYMBOL, SIZE_DECIMALS_BY_SYMBOL, trade_state

    print("BOT MULTI V6.5 ADAPTIVE INTELLIGENCE TREND SCORE")
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
        "🤖 <b>BOT MULTI V6.9.1.1 Adaptive Intelligence Auto Optimizer TP1 Fix démarré</b>\n"
        f"Coins: {', '.join(SYMBOLS)}\n"
        f"Max position: 1\n"
        f"Solde: {start_balance} USDC\n"
        f"Taille trade: {TRADE_SIZE_USD} USDC\n"
        f"Timeframe: {get_active_timeframe()}\n"
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

                if trade_state.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP":
                    manage_mode_l_position(pos, df, current_price, confirmed_time)
                else:
                    manage_open_position(pos, df, current_price, confirmed_time)
                time.sleep(CHECK_INTERVAL)
                continue

            cooldown_until = trade_state.get("cooldown_until_candle")
            force_reentry_rescan = consume_reentry_rescan()
            best = scan_symbols_for_best_buy(force_rescan=force_reentry_rescan)

            if best is None:
                time.sleep(CHECK_INTERVAL)
                continue

            symbol = best["symbol"]
            confirmed_time = best["confirmed_time"]
            current_price = best["current_price"]
            last = best["last"]
            details = best["details"]

            reentry_allowed, reentry_reason = evaluate_smart_reentry(best, cooldown_until)
            if not reentry_allowed:
                print(f"[COOLDOWN] Signal refusé après vente: {reentry_reason}")
                LOG_HISTORY.append(
                    f"[REENTRY BLOCK] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                    f"{symbol} {reentry_reason}"
                )
                if len(LOG_HISTORY) > MAX_LOG_HISTORY:
                    del LOG_HISTORY[:-MAX_LOG_HISTORY]
                time.sleep(CHECK_INTERVAL)
                continue

            if cooldown_until and confirmed_time < cooldown_until:
                print(f"[SMART REENTRY] Signal autorisé: {reentry_reason}")
                notify_telegram(
                    f"🔄 <b>Réentrée intelligente autorisée</b>\n"
                    f"Symbole: {symbol}\n"
                    f"Après sortie: {LAST_EXIT_SYMBOL} / {LAST_EXIT_REASON}\n"
                    f"Score: {best['score']:.4f}\n"
                    f"Mode: {details.get('signal_mode')}\n"
                    f"Raison: {reentry_reason}"
                )

            balance = get_balance()

            if balance <= 0:
                print("[RATE LIMIT] Solde indisponible, pause 120s.")
                notify_telegram("⚠️ API Hyperliquid indisponible (429 probable). Retry dans 120 secondes.")
                time.sleep(120)
                continue

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
                f"Trend score: {details.get('trend_score')}/8\n"
                f"Trend manquant: {details.get('trend_missing') or 'aucun'}\n"
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
                f"Mode D D1 désactivé: {not details.get('mode_d_pullback_between_emas')}\n"
                f"Mode D D2 confirmé sur bougie suivante: {details.get('mode_d_cross_both_emas')}\n"
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

            entry_side = "SELL" if details.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP" and details.get("mode_l", {}).get("side") == "SHORT" else "BUY"
            if place_order_usd(symbol, entry_side, TRADE_SIZE_USD, current_price, reduce_only=False):
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
                trade_state["mode_l_side"] = details.get("mode_l", {}).get("side") if details.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP" else None
                trade_state["mode_l_stop"] = details.get("mode_l", {}).get("stop") if details.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP" else None
                trade_state["mode_l_target"] = details.get("mode_l", {}).get("target") if details.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP" else None
                trade_state["mode_l_liquidity"] = details.get("mode_l", {}).get("level") if details.get("signal_mode") == "MODE_L_LIQUIDITY_SWEEP" else None

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
                    # Pour ces modes, le TP1 sera calculé au premier cycle
                    # de gestion de la position.
                    trade_state["tp1_price"] = None
                    trade_state["tp1_adaptive_mult"] = None
                    trade_state["tp1_adaptive_reason"] = "Calcul au premier cycle de gestion"
                    trade_state["tp1_adaptive_sample"] = 0
                else:
                    tp1_info = calculate_adaptive_tp1(
                        entry=current_price,
                        atr_value=last["atr"],
                        mode=details.get("signal_mode"),
                        symbol=symbol,
                    )
                    trade_state["tp1_price"] = round(tp1_info["price"], 8)
                    trade_state["tp1_adaptive_mult"] = tp1_info["multiplier"]
                    trade_state["tp1_adaptive_reason"] = tp1_info["reason"]
                    trade_state["tp1_adaptive_sample"] = tp1_info["sample"]
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
