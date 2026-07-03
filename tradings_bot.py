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
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ============================================================
# BOT MULTI-COINS V3
# XPL / XRP / kSHIB
# Timeframe : 15 min
# Long only
# Une seule position ouverte à la fois
#
# Indicateurs :
# EMA20 / EMA50 / RSI14 / ATR14 / OBV_MA5 / VWAP / Breakout
#
# Entrée :
# Le bot scanne XPL, XRP, kSHIB.
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

SYMBOLS = ["XPL", "XRP", "kSHIB"]
TRADE_SIZE_USD = 30
CHECK_INTERVAL = 60

TRADES_CSV_PATH = Path("trades.csv")
POST_TRADE_PENDING_PATH = Path("post_trade_pending.json")
POST_TRADE_ANALYSIS_PATH = Path("post_trade_analysis.csv")
LAST_MONTHLY_REPORT_FILE = Path("last_monthly_report.txt")
BOT_CONFIG_PATH = Path("bot_config.json")
DASHBOARD_PORT = int(os.environ.get("PORT", "8080"))
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
POST_TRADE_HORIZONS = [
    ("15m", 15 * 60 * 1000),
    ("30m", 30 * 60 * 1000),
    ("1h", 60 * 60 * 1000),
    ("2h", 2 * 60 * 60 * 1000),
    ("4h", 4 * 60 * 60 * 1000),
]
POST_TRADE_FINAL_MS = 4 * 60 * 60 * 1000

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

# MODE C - Capitulation / Reversal après forte baisse
MODE_C_RSI_MAX = 35
MODE_C_DROP_LOOKBACK = 8
MODE_C_MIN_DROP_PCT = 0.035
MODE_C_VOLUME_MULT = 1.2
MODE_C_WICK_ATR_MULT = 0.5

# MODE B assoupli : accepte une anticipation si EMA20 proche EMA50
MODE_B_MAX_EMA_GAP_PCT = 0.012

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

    # Infos pour résumé final Telegram
    "entry_balance": None,
    "signal_mode": None,
    "score": None,
    "pattern_score": None,
    "bull_flag": None,
    "ascending_triangle": None,
    "compression_breakout": None,
    "higher_lows": None,
    "tp_structure": None,
    "mode_c_reversal": None,
    "mode_c_drop_pct": None,
    "mode_b_early_ok": None,
    "ema_gap_pct": None,
    "tp1_price": None,
    "tp1_exec_price": None,
    "tp1_qty": None,
}

META_BY_SYMBOL = {}
SIZE_DECIMALS_BY_SYMBOL = {symbol: 2 for symbol in SYMBOLS}
LAST_ENTRY_PRICE_BY_SYMBOL = {symbol: None for symbol in SYMBOLS}
LAST_SELL_REASON = None
TELEGRAM_LAST_UPDATE_ID = None
LOG_HISTORY = []
ANALYSIS_HISTORY = []


def notify_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("[ERREUR TELEGRAM]", e)



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
        "tp_structure",
        "mode_c_reversal",
        "mode_c_drop_pct",
        "mode_b_early_ok",
        "ema_gap_pct",
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
        trade_state.get("tp_structure"),
        trade_state.get("mode_c_reversal"),
        trade_state.get("mode_c_drop_pct"),
        trade_state.get("mode_b_early_ok"),
        trade_state.get("ema_gap_pct"),
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





def ensure_post_trade_files():
    if not POST_TRADE_PENDING_PATH.exists():
        POST_TRADE_PENDING_PATH.write_text("[]", encoding="utf-8")
    if not POST_TRADE_ANALYSIS_PATH.exists():
        headers = ["date","symbol","mode","entry","exit","exit_reason","pnl_at_exit_pct","after_15m_pct","after_30m_pct","after_1h_pct","after_2h_pct","after_4h_pct","max_after_pct","min_after_pct","verdict"]
        with open(POST_TRADE_ANALYSIS_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)


def load_pending_post_trades():
    ensure_post_trade_files()
    try:
        return json.loads(POST_TRADE_PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_pending_post_trades(items):
    ensure_post_trade_files()
    POST_TRADE_PENDING_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def add_post_trade_tracking(symbol, mode, entry_price, exit_price, exit_reason):
    try:
        exit_time_ms = int(time.time() * 1000)
        pnl_at_exit = ((float(exit_price) - float(entry_price)) / float(entry_price)) * 100 if entry_price else 0
        item = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "mode": mode,
            "entry": float(entry_price),
            "exit": float(exit_price),
            "exit_reason": exit_reason,
            "exit_time_ms": exit_time_ms,
            "pnl_at_exit_pct": round(pnl_at_exit, 4),
            "horizons": {},
            "max_price": float(exit_price),
            "min_price": float(exit_price),
            "status": "PENDING",
        }
        pending = load_pending_post_trades()
        pending.append(item)
        save_pending_post_trades(pending)
        print("[POST TRADE] pending added", symbol)
    except Exception as e:
        print("[POST TRADE ADD ERROR]", e)


def finalize_post_trade_item(item):
    try:
        exit_price = float(item.get("exit", 0) or 0)
        max_price = float(item.get("max_price", exit_price) or exit_price)
        min_price = float(item.get("min_price", exit_price) or exit_price)
        max_after_pct = round(((max_price - exit_price) / exit_price) * 100, 4) if exit_price else 0
        min_after_pct = round(((min_price - exit_price) / exit_price) * 100, 4) if exit_price else 0
        pnl_exit = float(item.get("pnl_at_exit_pct", 0) or 0)
        if max_after_pct >= 2.0 and max_after_pct > max(pnl_exit + 1.0, 1.0):
            verdict = "SORTIE_TROP_TOT"
        elif max_after_pct <= 0.4 and min_after_pct < 0:
            verdict = "BONNE_SORTIE"
        else:
            verdict = "NEUTRE"
        row = [
            item.get("date"), item.get("symbol"), item.get("mode"), item.get("entry"), item.get("exit"),
            item.get("exit_reason"), item.get("pnl_at_exit_pct"),
            item.get("horizons", {}).get("15m"), item.get("horizons", {}).get("30m"),
            item.get("horizons", {}).get("1h"), item.get("horizons", {}).get("2h"),
            item.get("horizons", {}).get("4h"), max_after_pct, min_after_pct, verdict
        ]
        ensure_post_trade_files()
        with open(POST_TRADE_ANALYSIS_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
    except Exception as e:
        print("[POST TRADE FINALIZE ERROR]", e)


def update_post_trade_tracking(symbol, current_price):
    try:
        pending = load_pending_post_trades()
        if not pending:
            return
        now_ms = int(time.time() * 1000)
        remaining = []
        for item in pending:
            if item.get("symbol") != symbol:
                remaining.append(item)
                continue
            price = float(current_price)
            item["max_price"] = max(float(item.get("max_price", price)), price)
            item["min_price"] = min(float(item.get("min_price", price)), price)
            elapsed = now_ms - int(item.get("exit_time_ms", now_ms))
            item.setdefault("horizons", {})
            for label, horizon_ms in POST_TRADE_HORIZONS:
                if elapsed >= horizon_ms and label not in item["horizons"]:
                    exit_price = float(item.get("exit", price))
                    item["horizons"][label] = round(((price - exit_price) / exit_price) * 100, 4) if exit_price else None
            if elapsed >= POST_TRADE_FINAL_MS:
                finalize_post_trade_item(item)
            else:
                remaining.append(item)
        save_pending_post_trades(remaining)
    except Exception as e:
        print("[POST TRADE UPDATE ERROR]", e)


def load_post_trade_dataframe():
    ensure_post_trade_files()
    try:
        df = pd.read_csv(POST_TRADE_ANALYSIS_PATH)
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception:
        return pd.DataFrame()


def build_posttrade_message():
    try:
        pending = load_pending_post_trades()
        done = load_post_trade_dataframe()
        msg = "🧪 <b>POST TRADE ANALYZER</b>\n"
        msg += f"En attente: {len(pending)}\n"
        msg += f"Terminées: {0 if done.empty else len(done)}\n"
        if pending:
            msg += "\n<b>Pending</b>\n"
            for p in pending[-5:]:
                msg += f"- {p.get('symbol')} {p.get('mode')} PnL sortie {p.get('pnl_at_exit_pct')}%\n"
        if not done.empty:
            msg += "\n<b>Dernières terminées</b>\n"
            for _, r in done.tail(5).iterrows():
                msg += f"- {r.get('symbol')} {r.get('mode')} verdict {r.get('verdict')} max {r.get('max_after_pct')}%\n"
        return msg
    except Exception as e:
        return f"Erreur posttrade: {e}"



def json_safe(v):
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v


def default_bot_config():
    return {
        "modes": {
            "MODE_A_CLASSIQUE": True,
            "MODE_B_PATTERN": True,
            "MODE_C_CAPITULATION": True,
        },
        "trade_size_usd": TRADE_SIZE_USD,
    }


def load_bot_config():
    try:
        if BOT_CONFIG_PATH.exists():
            with open(BOT_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = default_bot_config()
            if isinstance(data, dict):
                cfg.update(data)
                cfg["modes"] = {**default_bot_config()["modes"], **data.get("modes", {})}
            return cfg
    except Exception as e:
        print("[CONFIG] load error", e)
    return default_bot_config()


def save_bot_config(cfg):
    try:
        with open(BOT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[CONFIG] save error", e)


def is_mode_enabled(mode):
    try:
        return bool(load_bot_config().get("modes", {}).get(mode, True))
    except Exception:
        return True


def dashboard_ai_recommendations():
    recs = []
    try:
        df = load_trades_dataframe()
        if df.empty:
            return ["Aucun trade enregistré pour le moment."]
        df = df.copy()
        df["real_pnl_usdc"] = pd.to_numeric(df.get("real_pnl_usdc"), errors="coerce").fillna(0)
        recs.append(f"Analyse basée sur {len(df)} trade(s).")
        if "mode" in df.columns:
            by_mode = df.groupby("mode")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            best = by_mode.sort_values("sum", ascending=False).head(1)
            worst = by_mode.sort_values("sum", ascending=True).head(1)
            for mode, r in best.iterrows():
                recs.append(f"Mode le plus rentable: {mode} avec {r['sum']:.3f} USDC sur {int(r['count'])} trade(s).")
            for mode, r in worst.iterrows():
                if r["sum"] < 0:
                    recs.append(f"Mode à surveiller: {mode} perd {abs(r['sum']):.3f} USDC.")
        if "symbol" in df.columns:
            by_sym = df.groupby("symbol")["real_pnl_usdc"].agg(["count", "sum", "mean"])
            worst = by_sym.sort_values("sum", ascending=True).head(1)
            for sym, r in worst.iterrows():
                if r["sum"] < 0:
                    recs.append(f"Token à surveiller: {sym} est négatif de {r['sum']:.3f} USDC.")
        return recs[:8]
    except Exception as e:
        return [f"Erreur IA Coach: {e}"]



def get_live_trade_snapshot():
    """
    Snapshot live de la position ouverte pour le dashboard.
    Met à jour le PnL en direct en utilisant get_open_position().
    """
    try:
        pos = get_open_position()
        if not pos:
            return {
                "in_position": False,
                "symbol": None,
                "entry": None,
                "current_price": None,
                "size": None,
                "pnl_pct": None,
                "pnl_usdc_est": None,
                "mode": trade_state.get("signal_mode"),
                "tp1_done": trade_state.get("tp1_done"),
                "tp1_price": trade_state.get("tp1_price"),
                "trailing_stop": trade_state.get("trailing_stop"),
                "highest_price": trade_state.get("highest_price"),
                "entry_balance": trade_state.get("entry_balance"),
            }

        symbol = pos.get("symbol")
        current_price = None
        df = None
        confirmed_time = None

        try:
            df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
        except Exception:
            current_price = None

        entry = trade_state.get("entry_price") or pos.get("entry")
        size = abs(float(pos.get("size", 0) or 0))

        if current_price is None:
            current_price = pos.get("mark_price") or pos.get("price") or entry

        current_price = float(current_price)
        entry = float(entry) if entry else 0

        pnl_pct = ((current_price - entry) / entry) * 100 if entry else 0
        pnl_usdc_est = (current_price - entry) * size if entry and size else 0

        atr_value = trade_state.get("entry_atr")
        tp1_price = trade_state.get("tp1_price")
        hard_stop = None
        if atr_value and entry:
            hard_stop = entry - ATR_STOP_MULT * float(atr_value)

        return {
            "in_position": True,
            "symbol": symbol,
            "entry": round(entry, 8),
            "current_price": round(current_price, 8),
            "size": round(size, 8),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usdc_est": round(pnl_usdc_est, 6),
            "mode": trade_state.get("signal_mode"),
            "score": trade_state.get("score"),
            "tp1_done": trade_state.get("tp1_done"),
            "tp1_price": tp1_price,
            "trailing_stop": trade_state.get("trailing_stop"),
            "highest_price": trade_state.get("highest_price"),
            "hard_stop": round(hard_stop, 8) if hard_stop else None,
            "entry_balance": trade_state.get("entry_balance"),
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    except Exception as e:
        return {"in_position": False, "error": str(e)}


def get_dashboard_stats():
    df = load_trades_dataframe()
    balance = round(get_balance(), 2)

    result = {
        "balance": balance,
        "open_position": get_open_position(),
        "live_trade": get_live_trade_snapshot(),
        "config": load_bot_config(),
        "summary": {
            "trades": 0, "wins": 0, "losses": 0, "winrate": 0,
            "pnl_total": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": None,
        },
        "by_mode": [],
        "by_symbol": [],
        "by_exit_reason": [],
        "recent_trades": [],
        "analysis_history": ANALYSIS_HISTORY[-80:],
        "log_history": LOG_HISTORY[-120:],
        "ai_recommendations": dashboard_ai_recommendations(),
        "post_trade_pending": load_pending_post_trades(),
        "post_trade_analysis": [] if load_post_trade_dataframe().empty else load_post_trade_dataframe().tail(80).fillna("").to_dict(orient="records"),
        "dashboard_name": "V3.10 Rebase Dashboard Pro Live Pro",
    }

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
                "name": str(key), "trades": int(len(g)), "wins": gwins, "losses": glosses,
                "winrate": round((gwins / len(g)) * 100, 2) if len(g) else 0,
                "pnl": round(float(gpnl.sum()), 4),
                "avg_pnl": round(float(gpnl.mean()), 4) if len(g) else 0,
                "profit_factor": round(ggross_profit / ggross_loss, 3) if ggross_loss else None,
            })
        return sorted(rows, key=lambda x: x["pnl"], reverse=True)

    result["by_mode"] = grouped_stats("mode")
    result["by_symbol"] = grouped_stats("symbol")
    result["by_exit_reason"] = grouped_stats("exit_reason")
    recent = df.tail(60).fillna("").to_dict(orient="records")
    result["recent_trades"] = [{k: json_safe(v) for k, v in row.items()} for row in recent]
    return result


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"/>
<title>Bot Trading Dashboard</title>
<style>
:root{--bg:#0f172a;--card:#111827;--card2:#172033;--text:#e5e7eb;--muted:#94a3b8;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--border:#263244}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;padding:12px}
h1{font-size:22px;margin:8px 0 12px} h3{margin:0 0 10px;font-size:16px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:12px;box-shadow:0 6px 18px rgba(0,0,0,.18)}
.k{color:var(--muted);font-size:12px}.v{font-size:20px;font-weight:700;margin-top:4px}.green{color:var(--green)}.red{color:var(--red)}.yellow{color:var(--yellow)}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}th{color:var(--muted);font-weight:600}.scroll{overflow:auto}
button{border:0;border-radius:12px;padding:10px 12px;background:#2563eb;color:white;font-weight:700;min-height:42px}button.off{background:#475569}.modeRow{display:flex;gap:8px;flex-wrap:wrap}
pre{white-space:pre-wrap;background:#07111f;border-radius:12px;padding:10px;max-height:340px;overflow:auto;font-size:12px;color:#cbd5e1}
.aiBox{background:#071827;border:1px solid #164e63;border-radius:12px;padding:12px;white-space:pre-wrap;line-height:1.35}
tr.clickable{cursor:pointer}tr.clickable:hover{background:#1f2a3c}.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:99;padding:16px;overflow:auto}.modalBox{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px;max-width:760px;margin:40px auto}.modalClose{float:right;background:#334155}.detailGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.detailItem{background:var(--card2);padding:10px;border-radius:12px;border:1px solid #2b3950}
@media(max-width:760px){body{padding:10px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.card{padding:10px;border-radius:14px}.v{font-size:18px}table{font-size:12px}th,td{padding:7px}.detailGrid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>📊 Bot Trading Dashboard <span class="muted" id="dashName"></span></h1>

<div class="grid">
 <div class="card"><div class="k">Solde</div><div class="v" id="balance">-</div></div>
 <div class="card"><div class="k">Trades</div><div class="v" id="trades">-</div></div>
 <div class="card"><div class="k">Winrate</div><div class="v" id="winrate">-</div></div>
 <div class="card"><div class="k">PnL total</div><div class="v" id="pnl">-</div></div>
</div>

<div class="card" style="margin-top:12px">
 <h3>⚙️ Modes actifs V3.10</h3>
 <div class="modeRow" id="modes"></div>
 <div class="muted" style="margin-top:8px">Modes conservés : A classique, B pattern early, C capitulation soft.</div>
</div>

<div class="card" style="margin-top:12px">
 <h3>🔥 Live Trading en cours</h3>
 <div class="grid">
  <div class="card"><div class="k">Coin</div><div class="v" id="liveSymbol">-</div></div>
  <div class="card"><div class="k">Mode</div><div class="v" id="liveMode">-</div></div>
  <div class="card"><div class="k">PnL live</div><div class="v" id="livePnlPct">-</div></div>
  <div class="card"><div class="k">PnL USDC estimé</div><div class="v" id="livePnlUsdc">-</div></div>
 </div>
 <div class="scroll" style="margin-top:10px">
  <table id="liveTradeTable"></table>
 </div>
</div>

<div class="card" style="margin-top:12px">
 <h3>📍 Position ouverte brute</h3>
 <pre id="pos">-</pre>
</div>

<div class="card" style="margin-top:12px"><h3>📈 Performance par mode</h3><div class="scroll"><table id="byMode"></table></div></div>
<div class="card" style="margin-top:12px"><h3>🪙 Performance par token</h3><div class="scroll"><table id="bySymbol"></table></div></div>
<div class="card" style="margin-top:12px"><h3>📋 Trades récents</h3><div class="scroll"><table id="recentTrades"></table></div></div>

<div class="card" style="margin-top:12px"><h3>🧪 Post Trade Analyzer - en attente</h3><div class="scroll"><table id="postPending"></table></div></div>
<div class="card" style="margin-top:12px"><h3>✅ Post Trade Analyzer - terminées</h3><div class="scroll"><table id="postDone"></table></div></div>

<div class="card" style="margin-top:12px"><h3>🧠 IA Coach</h3><div id="aiCoach" class="aiBox">-</div></div>
<div class="card" style="margin-top:12px"><h3>🕯️ Derniers signaux</h3><div class="scroll"><table id="signals"></table></div></div>
<div class="card" style="margin-top:12px"><h3>📋 Logs récents</h3><pre id="logs"></pre></div>

<div id="tradeModal" class="modal">
 <div class="modalBox">
  <button class="modalClose" onclick="closeTradeModal()">Fermer</button>
  <h3>🔎 Analyse détaillée du trade</h3>
  <div id="tradeDetailContent"></div>
 </div>
</div>

<script>
let lastTrades=[];
function cls(v){let n=Number(v); if(isNaN(n))return ''; return n>0?'green':(n<0?'red':'')}
function fmt(v,d=2){let n=Number(v); return isNaN(n)?(v??'-'):n.toFixed(d)}
function esc(x){return String(x??'-').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function table(el,cols,rows,clickable=false){const head='<tr>'+cols.map(c=>`<th>${c.label}</th>`).join('')+'</tr>';const body=(rows||[]).map((r,i)=>{const click=clickable?` class="clickable" onclick="openTradeModal(${i})"`:'';return'<tr'+click+'>'+cols.map(c=>{let val=c.get?c.get(r):r[c.key];let klass=c.cls?c.cls(val,r):'';return`<td class="${klass}">${val??'-'}</td>`}).join('')+'</tr>'}).join('');document.getElementById(el).innerHTML=head+body}
function closeTradeModal(){document.getElementById('tradeModal').style.display='none'}
function openTradeModal(i){const r=lastTrades[i];if(!r)return;document.getElementById('tradeDetailContent').innerHTML=`<div class="detailGrid">
<div class="detailItem"><b>Date</b><br>${esc(r.date)}</div><div class="detailItem"><b>Coin</b><br>${esc(r.symbol)}</div>
<div class="detailItem"><b>Mode</b><br>${esc(r.mode)}</div><div class="detailItem"><b>Sortie</b><br>${esc(r.exit_reason)}</div>
<div class="detailItem"><b>Entry</b><br>${esc(r.entry)}</div><div class="detailItem"><b>Exit</b><br>${esc(r.exit)}</div>
<div class="detailItem"><b>PnL %</b><br>${esc(r.pnl_price_pct)}</div><div class="detailItem"><b>PnL USDC</b><br>${esc(r.real_pnl_usdc)}</div>
</div><h3 style="margin-top:16px">🧠 Lecture rapide</h3><div class="aiBox">Mode ${esc(r.mode)} sur ${esc(r.symbol)}. Sortie ${esc(r.exit_reason)}. PnL réel ${esc(r.real_pnl_usdc)} USDC.</div>`;document.getElementById('tradeModal').style.display='block'}
async function toggleMode(m){await fetch('/api/toggle_mode?mode='+encodeURIComponent(m),{method:'POST'});load()}
function renderLiveTrade(live){
 if(!live || !live.in_position){
   document.getElementById('liveSymbol').textContent='Aucune';
   document.getElementById('liveMode').textContent='-';
   document.getElementById('livePnlPct').textContent='-';
   document.getElementById('livePnlPct').className='v';
   document.getElementById('livePnlUsdc').textContent='-';
   document.getElementById('livePnlUsdc').className='v';
   table('liveTradeTable',[
    {label:'Entry',get:r=>'-'},{label:'Prix actuel',get:r=>'-'},{label:'Qty',get:r=>'-'},{label:'TP1',get:r=>'-'},{label:'Stop',get:r=>'-'},{label:'Trail',get:r=>'-'},{label:'Update',get:r=>'-'}
   ],[{}]);
   return;
 }
 document.getElementById('liveSymbol').textContent=live.symbol||'-';
 document.getElementById('liveMode').textContent=(live.mode||'-').replace('MODE_','');
 document.getElementById('livePnlPct').textContent=fmt(live.pnl_pct,3)+'%';
 document.getElementById('livePnlPct').className='v '+cls(live.pnl_pct);
 document.getElementById('livePnlUsdc').textContent=fmt(live.pnl_usdc_est,4)+' USDC';
 document.getElementById('livePnlUsdc').className='v '+cls(live.pnl_usdc_est);
 table('liveTradeTable',[
  {label:'Entry',get:r=>fmt(r.entry,6)},
  {label:'Prix actuel',get:r=>fmt(r.current_price,6)},
  {label:'Qty',get:r=>fmt(r.size,6)},
  {label:'Score',get:r=>r.score??'-'},
  {label:'TP1',get:r=>r.tp1_price??'-'},
  {label:'Stop',get:r=>r.hard_stop??'-'},
  {label:'Trail',get:r=>r.trailing_stop??'-'},
  {label:'Plus haut',get:r=>r.highest_price??'-'},
  {label:'Update',get:r=>r.last_update??'-'}
 ],[live]);
}
function renderModes(cfg){const modes=cfg.modes||{};document.getElementById('modes').innerHTML=Object.entries(modes).map(([m,on])=>`<button class="${on?'':'off'}" onclick="toggleMode('${m}')">${on?'✅':'❌'} ${m.replace('MODE_','')}</button>`).join('')}
async function load(){
 const d=await (await fetch('/api/stats')).json();
 document.getElementById('dashName').textContent='· '+(d.dashboard_name||'');
 document.getElementById('balance').textContent=fmt(d.balance,2)+' USDC';
 document.getElementById('trades').textContent=d.summary.trades;
 document.getElementById('winrate').textContent=fmt(d.summary.winrate,1)+'%';
 document.getElementById('pnl').textContent=fmt(d.summary.pnl_total,3)+' USDC'; document.getElementById('pnl').className='v '+cls(d.summary.pnl_total);
 renderModes(d.config||{});
 renderLiveTrade(d.live_trade);
 document.getElementById('pos').textContent=JSON.stringify(d.open_position,null,2);
 table('byMode',[{label:'Mode',key:'name'},{label:'Trades',key:'trades'},{label:'WR',get:r=>fmt(r.winrate,1)+'%'},{label:'PnL',get:r=>fmt(r.pnl,4),cls:v=>cls(v)},{label:'Avg',get:r=>fmt(r.avg_pnl,4),cls:v=>cls(v)}],d.by_mode);
 table('bySymbol',[{label:'Coin',key:'name'},{label:'Trades',key:'trades'},{label:'WR',get:r=>fmt(r.winrate,1)+'%'},{label:'PnL',get:r=>fmt(r.pnl,4),cls:v=>cls(v)}],d.by_symbol);
 lastTrades=(d.recent_trades||[]).slice().reverse();
 table('recentTrades',[{label:'Date',key:'date'},{label:'Coin',key:'symbol'},{label:'Mode',key:'mode'},{label:'Entry',key:'entry'},{label:'Exit',key:'exit'},{label:'Sortie',key:'exit_reason'},{label:'PnL %',get:r=>fmt(r.pnl_price_pct,2),cls:v=>cls(v)},{label:'USDC',get:r=>fmt(r.real_pnl_usdc,4),cls:v=>cls(v)}],lastTrades,true);
 table('postPending',[{label:'Date',key:'date'},{label:'Coin',key:'symbol'},{label:'Mode',key:'mode'},{label:'Exit',key:'exit'},{label:'PnL sortie',get:r=>fmt(r.pnl_at_exit_pct,2),cls:v=>cls(v)},{label:'Max',get:r=>fmt(((Number(r.max_price)-Number(r.exit))/Number(r.exit))*100,2),cls:v=>cls(v)},{label:'Min',get:r=>fmt(((Number(r.min_price)-Number(r.exit))/Number(r.exit))*100,2),cls:v=>cls(v)},{label:'Status',key:'status'}],(d.post_trade_pending||[]).slice().reverse());
 table('postDone',[{label:'Date',key:'date'},{label:'Coin',key:'symbol'},{label:'Mode',key:'mode'},{label:'+15m',get:r=>fmt(r.after_15m_pct,2),cls:v=>cls(v)},{label:'+1h',get:r=>fmt(r.after_1h_pct,2),cls:v=>cls(v)},{label:'+4h',get:r=>fmt(r.after_4h_pct,2),cls:v=>cls(v)},{label:'Max',get:r=>fmt(r.max_after_pct,2),cls:v=>cls(v)},{label:'Verdict',key:'verdict'}],(d.post_trade_analysis||[]).slice().reverse());
 table('signals',[{label:'Heure',key:'time'},{label:'Coin',key:'symbol'},{label:'Signal',key:'signal'},{label:'Mode',key:'mode'},{label:'Score',get:r=>fmt(r.score,4)},{label:'A/B/C',get:r=>`${r.mode_a}/${r.mode_b}/${r.mode_c}`}],(d.analysis_history||[]).slice().reverse());
 document.getElementById('aiCoach').textContent=(d.ai_recommendations||[]).map(x=>'• '+x).join('\n')||'-';
 document.getElementById('logs').textContent=(d.log_history||[]).join('\n');
}
load();setInterval(load,5000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def _auth_ok(self):
        if not DASHBOARD_TOKEN:
            return True
        q = parse_qs(urlparse(self.path).query)
        return q.get("token", [""])[0] == DASHBOARD_TOKEN

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return
        if path == "/api/stats":
            self._send_json(get_dashboard_stats())
            return
        if path == "/trades.csv":
            ensure_trades_csv()
            body = TRADES_CSV_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=trades.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return
        if path == "/api/toggle_mode":
            q = parse_qs(urlparse(self.path).query)
            mode = q.get("mode", [""])[0]
            cfg = load_bot_config()
            if mode in cfg.get("modes", {}):
                cfg["modes"][mode] = not bool(cfg["modes"][mode])
                save_bot_config(cfg)
                self._send_json({"ok": True, "mode": mode, "enabled": cfg["modes"][mode]})
                return
            self._send_json({"ok": False, "error": "unknown mode"}, 400)
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, 404)

    def log_message(self, *args):
        return


def start_dashboard():
    try:
        server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[DASHBOARD] http://0.0.0.0:{DASHBOARD_PORT}")
    except Exception as e:
        print("[DASHBOARD ERROR]", e)


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

            if cmd == "/posttrade":
                notify_telegram(build_posttrade_message())
                continue

            if cmd == "/dashboard":
                notify_telegram(f"🌐 <b>Dashboard</b>\nPort: {DASHBOARD_PORT}\nDomaine Railway + token si configuré.")
                continue

            if cmd == "/help":
                notify_telegram(
                    "🤖 <b>Commandes disponibles</b>\n"
                    "/report ou report - Rapport actuel\n"
                    "/stats ou stats - Rapport actuel\n"
                    "/csv ou csv - Recevoir trades.csv\n"
                    "/help ou help - Afficher cette aide\n/dashboard - Infos dashboard"
                )
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

        "entry_balance": None,
        "signal_mode": None,
        "score": None,
        "pattern_score": None,
        "bull_flag": None,
        "ascending_triangle": None,
        "compression_breakout": None,
        "higher_lows": None,
        "tp_structure": None,
        "mode_c_reversal": None,
        "mode_c_drop_pct": None,
        "mode_b_early_ok": None,
        "ema_gap_pct": None,
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




def estimate_tp_structure(df, breakout_level):
    try:
        recent = df.iloc[-12:]
        structure_low = recent["low"].min()
        height = breakout_level - structure_low
        if height <= 0:
            return None
        return round(breakout_level + height, 5)
    except Exception:
        return None


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

    # V3.10 : plus réactif.
    # Volume fort OU mèche basse forte, au lieu des deux obligatoires.
    ok = (
        drop_pct >= MODE_C_MIN_DROP_PCT
        and rsi_ok
        and (volume_ok or wick_ok)
        and green_ok
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
    tp_structure = estimate_tp_structure(df, breakout_level)

    breakout_strength = max(0, (last["close"] / breakout_level) - 1) if breakout_level > 0 else 0
    vwap_strength = max(0, (last["close"] / last["vwap"]) - 1) if last["vwap"] > 0 else 0

    # Correction bug OBV négatif.
    if obv_rising and last["obv_ma5"] != 0:
        obv_strength = max(0, (last["obv"] - last["obv_ma5"]) / abs(last["obv_ma5"]))
        obv_strength = min(obv_strength, 2.0)
    else:
        obv_strength = 0

    rsi_score = max(0, 1 - abs(last["rsi"] - 62) / 10)

    mode_a_ok = (
        trend_ok
        and rsi_ok
        and vwap_ok
        and obv_rising
        and breakout_ok
        and green_candle
    )

    mode_b_strict_ok = (
        patt_details["pattern_score"] >= 2.0
        and breakout_ok
        and obv_rising
        and vwap_ok
        and green_candle
        and 45 < last["rsi"] < 85
    )

    ema_gap_pct = abs(last["ema20"] - last["ema50"]) / last["close"] if last["close"] > 0 else 1

    mode_b_early_ok = (
        patt_details["pattern_score"] >= 3.0
        and patt_details["higher_lows"]
        and obv_rising
        and green_candle
        and ema_gap_pct <= MODE_B_MAX_EMA_GAP_PCT
        and 40 < last["rsi"] < 85
    )

    mode_b_ok = mode_b_strict_ok or mode_b_early_ok

    # Dashboard toggles
    mode_a_ok = mode_a_ok and is_mode_enabled("MODE_A_CLASSIQUE")
    mode_b_ok = mode_b_ok and is_mode_enabled("MODE_B_PATTERN")
    mode_c_ok = mode_c_ok and is_mode_enabled("MODE_C_CAPITULATION")

    signal_mode = None
    if mode_a_ok:
        signal_mode = "MODE_A_CLASSIQUE"
    elif mode_b_ok:
        signal_mode = "MODE_B_PATTERN"
    elif mode_c_ok:
        signal_mode = "MODE_C_CAPITULATION"

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

    ok = mode_a_ok or mode_b_ok or mode_c_ok

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
        "mode_b_strict_ok": mode_b_strict_ok,
        "mode_b_early_ok": mode_b_early_ok,
        "ema_gap_pct": round(ema_gap_pct * 100, 3),
        "mode_c_ok": mode_c_ok,
        "pattern_score": patt_details["pattern_score"],
        "bull_flag": patt_details["bull_flag"],
        "ascending_triangle": patt_details["ascending_triangle"],
        "compression_breakout": patt_details["compression_breakout"],
        "higher_lows": patt_details["higher_lows"],
        "tp_structure": tp_structure,
        "mode_c_reversal": mode_c_ok,
        "mode_c_drop_pct": mode_c_details["mode_c_drop_pct"],
        "mode_c_volume_ok": mode_c_details["mode_c_volume_ok"],
        "mode_c_wick_ok": mode_c_details["mode_c_wick_ok"],
        "mode_c_green_ok": mode_c_details["mode_c_green_ok"],
        "mode_c_rsi_ok": mode_c_details["mode_c_rsi_ok"],
    }




def send_trade_summary(symbol, exit_price, exit_reason, final_balance):
    """
    Envoie le résumé complet du trade, ajoute la ligne dans trades.csv,
    envoie le rapport actuel et le fichier CSV sur Telegram.
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
        f"TP Structure indicatif: {trade_state.get('tp_structure')}\n"
        f"Mode C Reversal: {trade_state.get('mode_c_reversal')}\n"
        f"Mode C Drop: {trade_state.get('mode_c_drop_pct')}%\n"
        f"Mode B early: {trade_state.get('mode_b_early_ok')}\n"
        f"EMA gap: {trade_state.get('ema_gap_pct')}%\n"
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

    notify_telegram(msg)

    add_post_trade_tracking(
        symbol=symbol,
        mode=trade_state.get("signal_mode"),
        entry_price=entry,
        exit_price=exit_price,
        exit_reason=exit_reason,
    )

    append_trade_to_csv(symbol, exit_price, exit_reason, final_balance)
    notify_telegram(build_report_message(title="📊 RAPPORT ACTUEL"))
    send_telegram_document(TRADES_CSV_PATH, caption="📎 trades.csv mis à jour")


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
    ema_break = (
        last["close"] < last["ema20"]
        and prev["close"] < prev["ema20"]
    )
    hard_stop_hit = current_price <= hard_stop

    print(
        f"[POSITION] {symbol} Entry={entry:.5f} Prix={current_price:.5f} "
        f"PnL={pnl_pct:.2f}% ATR={atr_value:.5f} TP1={tp1_price:.5f} "
        f"Trail={trailing_display} Stop={hard_stop:.5f} RSI={last['rsi']:.2f}"
    )

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

    for symbol in SYMBOLS:
        df, confirmed_time, current_price = prepare_df_for_symbol(symbol)
        if df is None:
            continue

        new_candle = confirmed_time != last_candle_times.get(symbol)
        if not new_candle:
            continue

        last_candle_times[symbol] = confirmed_time
        last = df.iloc[-1]
        update_post_trade_tracking(symbol, current_price)
        ok, score, details = buy_signal(df)

        print(f"\n[CANDLE CLOSE] {now} {symbol}")
        print(
            f"Close={last['close']:.5f} Current={current_price:.5f} "
            f"EMA20={last['ema20']:.5f} EMA50={last['ema50']:.5f} "
            f"RSI={last['rsi']:.2f} ATR={last['atr']:.5f} "
            f"VWAP={last['vwap']:.5f} OBV={last['obv']:.2f} OBV_MA5={last['obv_ma5']:.2f}"
        )
        LOG_HISTORY.append(f"[CANDLE CLOSE] {now} {symbol} close={last['close']:.5f} mode={details.get('signal_mode')} score={round(details['score'],4)}")
        if len(LOG_HISTORY) > 500:
            del LOG_HISTORY[:-500]

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
            "| mode_b_early:", details.get("mode_b_early_ok"),
            "| ema_gap%:", details.get("ema_gap_pct"),
            "| pattern_score:", round(details["pattern_score"], 4),
            "| score:", round(details["score"], 4),
        )

        ANALYSIS_HISTORY.append({
            "time": now,
            "symbol": symbol,
            "signal": "BUY" if ok else "HOLD",
            "mode": details.get("signal_mode"),
            "score": round(details.get("score", 0), 4),
            "mode_a": bool(details.get("mode_a_ok")),
            "mode_b": bool(details.get("mode_b_ok")),
            "mode_c": bool(details.get("mode_c_ok")),
        })
        if len(ANALYSIS_HISTORY) > 300:
            del ANALYSIS_HISTORY[:-300]

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

    print("BOT MULTI V3.10 REBASE DASHBOARD PRO LIVE")
    print("Long only | 15m | Une seule position à la fois")
    print("Indicateurs : EMA20/EMA50 + RSI + ATR + OBV_MA5 + VWAP + Breakout + Patterns + Mode A/B/C")
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
    start_dashboard()
    initialize_telegram_offset()

    notify_telegram(
        "🤖 <b>BOT MULTI V3.10 Rebase Dashboard Pro Live Pro démarré</b>\n"
        f"Coins: {', '.join(SYMBOLS)}\n"
        f"Max position: 1\n"
        f"Solde: {start_balance} USDC\n"
        f"Taille trade: {TRADE_SIZE_USD} USDC\n"
        f"Commandes: /report /csv /posttrade /dashboard /help"
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

                update_post_trade_tracking(symbol, current_price)

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
                f"TP Structure indicatif: {details.get('tp_structure')}\n"
                f"Mode B early: {details.get('mode_b_early_ok')}\n"
                f"EMA gap: {details.get('ema_gap_pct')}%\n"
                f"Mode C Reversal: {details.get('mode_c_reversal')}\n"
                f"Mode C Drop: {details.get('mode_c_drop_pct')}%\n"
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

                # Sauvegarde infos signal pour résumé final Telegram
                trade_state["entry_balance"] = round(balance, 2)
                trade_state["signal_mode"] = details.get("signal_mode")
                trade_state["score"] = round(best["score"], 4)
                trade_state["pattern_score"] = round(details.get("pattern_score", 0), 4)
                trade_state["bull_flag"] = details.get("bull_flag")
                trade_state["ascending_triangle"] = details.get("ascending_triangle")
                trade_state["compression_breakout"] = details.get("compression_breakout")
                trade_state["higher_lows"] = details.get("higher_lows")
                trade_state["tp_structure"] = details.get("tp_structure")
                trade_state["mode_c_reversal"] = details.get("mode_c_reversal")
                trade_state["mode_c_drop_pct"] = details.get("mode_c_drop_pct")
                trade_state["mode_b_early_ok"] = details.get("mode_b_early_ok")
                trade_state["ema_gap_pct"] = details.get("ema_gap_pct")
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
