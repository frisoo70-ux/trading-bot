import time
import json
import requests
import pandas as pd
import eth_account
from eth_account.messages import encode_defunct
from datetime import datetime
import os

PRIVATE_KEY = os.environ.get(“HL_API_SECRET”, “”)
WALLET_ADDRESS = os.environ.get(“HL_WALLET”, “”)
SYMBOL = “SOL”
TRADE_SIZE_USD = 2
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
CHECK_INTERVAL = 60 * 15
BASE_URL = “https://api.hyperliquid.xyz”

def get_prices():
try:
url = BASE_URL + “/info”
payload = {“type”: “candleSnapshot”, “req”: {“coin”: SYMBOL, “interval”: “15m”, “startTime”: int((time.time() - 100 * 60 * 15) * 1000), “endTime”: int(time.time() * 1000)}}
r = requests.post(url, json=payload, timeout=10)
return [float(c[“c”]) for c in r.json()]
except Exception as e:
print(”[ERREUR] Prix: “ + str(e))
return []

def get_balance():
try:
url = BASE_URL + “/info”
r = requests.post(url, json={“type”: “clearinghouseState”, “user”: WALLET_ADDRESS}, timeout=10)
return float(r.json().get(“marginSummary”, {}).get(“accountValue”, 0))
except Exception as e:
print(”[ERREUR] Solde: “ + str(e))
return 0

def get_positions():
try:
url = BASE_URL + “/info”
r = requests.post(url, json={“type”: “clearinghouseState”, “user”: WALLET_ADDRESS}, timeout=10)
positions = []
for pos in r.json().get(“assetPositions”, []):
p = pos.get(“position”, {})
if float(p.get(“szi”, 0)) != 0:
positions.append({“symbol”: p.get(“coin”), “size”: float(p.get(“szi”, 0)), “entry”: float(p.get(“entryPx”, 0))})
return positions
except Exception as e:
print(”[ERREUR] Positions: “ + str(e))
return []

def get_asset_index():
try:
r = requests.post(BASE_URL + “/info”, json={“type”: “meta”}, timeout=10)
for i, a in enumerate(r.json().get(“universe”, [])):
if a.get(“name”) == SYMBOL:
return i
return -1
except:
return -1

def place_order(side, size_usd, price):
try:
account = eth_account.Account.from_key(PRIVATE_KEY)
nonce = int(time.time() * 1000)
idx = get_asset_index()
if idx == -1:
print(”[ERREUR] Asset introuvable”)
return False
size = round(size_usd / price, 4)
is_buy = side == “BUY”
lp = round(price * 1.002, 2) if is_buy else round(price * 0.998, 2)
action = {“type”: “order”, “orders”: [{“a”: idx, “b”: is_buy, “p”: str(lp), “s”: str(size), “r”: False, “t”: {“limit”: {“tif”: “Ioc”}}}], “grouping”: “na”}
msg = encode_defunct(text=str(hash(json.dumps(action) + str(nonce))))
signed = account.sign_message(msg)
payload = {“action”: action, “nonce”: nonce, “signature”: {“r”: hex(signed.r), “s”: hex(signed.s), “v”: signed.v}, “vaultAddress”: None}
r = requests.post(BASE_URL + “/exchange”, json=payload, timeout=10)
result = r.json()
if result.get(“status”) == “ok”:
print(”[OK] “ + side + “ “ + str(size) + “ “ + SYMBOL + “ @ $” + str(lp))
return True
else:
print(”[ECHEC] “ + str(result))
return False
except Exception as e:
print(”[ERREUR] Ordre: “ + str(e))
return False

def ema(prices, period):
if len(prices) < period:
return None
return pd.Series(prices).ewm(span=period, adjust=False).mean().iloc[-1]

def rsi(prices, period=14):
if len(prices) < period + 1:
return None
s = pd.Series(prices)
d = s.diff()
g = d.where(d > 0, 0).rolling(period).mean()
l = -d.where(d < 0, 0).rolling(period).mean()
return (100 - 100 / (1 + g / l)).iloc[-1]

def signal(prices):
if len(prices) < EMA_SLOW + RSI_PERIOD:
return “HOLD”
ef = ema(prices, EMA_FAST)
es = ema(prices, EMA_SLOW)
r = rsi(prices, RSI_PERIOD)
if ef and es and r:
if ef > es and r < RSI_OVERSOLD:
return “BUY”
if ef < es and r > RSI_OVERBOUGHT:
return “SELL”
return “HOLD”

def check_risk(positions, price):
for pos in positions:
if pos[“symbol”] != SYMBOL:
continue
pnl = (price - pos[“entry”]) / pos[“entry”]
if pos[“size”] > 0:
if pnl <= -STOP_LOSS_PCT:
print(”[STOP-LOSS] “ + str(round(pnl*100, 2)) + “%”)
place_order(“SELL”, abs(pos[“size”]) * price, price)
return True
if pnl >= TAKE_PROFIT_PCT:
print(”[TAKE-PROFIT] “ + str(round(pnl*100, 2)) + “%”)
place_order(“SELL”, abs(pos[“size”]) * price, price)
return True
return False

def run():
print(“BOT REEL - “ + SYMBOL + “ | $” + str(TRADE_SIZE_USD) + “ par trade”)
print(“Solde: $” + str(round(get_balance(), 2)))
last = “HOLD”
trades = 0
while True:
try:
now = datetime.now().strftime(”%H:%M:%S”)
prices = get_prices()
if not prices:
time.sleep(CHECK_INTERVAL)
continue
price = prices[-1]
positions = get_positions()
if positions:
if check_risk(positions, price):
last = “HOLD”
time.sleep(CHECK_INTERVAL)
continue
sig = signal(prices)
ef = ema(prices, EMA_FAST)
r = rsi(prices, RSI_PERIOD)
ef_str = str(round(ef, 2)) if ef else “N/A”
r_str = str(round(r, 1)) if r else “N/A”
print(”[” + now + “] $” + str(price) + “ | EMA9: “ + ef_str + “ | RSI: “ + r_str + “ | “ + sig)
if sig != last and sig != “HOLD” and not positions:
if place_order(sig, TRADE_SIZE_USD, price):
last = sig
trades += 1
print(“Trade #” + str(trades) + “ | Solde: $” + str(round(get_balance(), 2)))
time.sleep(CHECK_INTERVAL)
except KeyboardInterrupt:
print(“Bot arrete. Trades: “ + str(trades))
break
except Exception as e:
print(”[ERREUR] “ + str(e))
time.sleep(CHECK_INTERVAL)

if **name** == “**main**”:
run()
