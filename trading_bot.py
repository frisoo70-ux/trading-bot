"""
Bot de Trading - Stratégie EMA Cross + RSI
Plateforme: Hyperliquid
Auteur: Claude (Anthropic)

AVERTISSEMENT: Ce bot est fourni à titre éducatif.
Le trading comporte des risques de perte en capital.
"""

import time
import json
import hmac
import hashlib
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import os

# ============================================================
# CONFIGURATION - À MODIFIER AVEC TES PROPRES VALEURS
# ============================================================
API_KEY = os.environ.get("HL_API_KEY", "")          # Ta clé API Hyperliquid
API_SECRET = os.environ.get("HL_API_SECRET", "")    # Ton secret API Hyperliquid
WALLET_ADDRESS = os.environ.get("HL_WALLET", "")    # Ton adresse wallet

# Paramètres de trading
SYMBOL = "SOL"           # Paire à trader (BTC, ETH, SOL, etc.)
TRADE_SIZE_USD = 2      # Montant par trade en USD (commence petit !)
LEVERAGE = 1             # Levier (1 = pas de levier, RECOMMANDÉ pour débuter)
STOP_LOSS_PCT = 0.02     # Stop-loss à 2%
TAKE_PROFIT_PCT = 0.04   # Take-profit à 4%

# Paramètres de la stratégie
EMA_FAST = 9             # EMA rapide (périodes)
EMA_SLOW = 21            # EMA lente (périodes)
RSI_PERIOD = 14          # Période RSI
RSI_OVERSOLD = 30        # RSI survendu (signal achat)
RSI_OVERBOUGHT = 70      # RSI suracheté (signal vente)

# Intervalle de vérification
CHECK_INTERVAL = 60      # Vérifier toutes les 60 secondes

# ============================================================
# FONCTIONS DE CALCUL TECHNIQUE
# ============================================================

def calculate_ema(prices: list, period: int) -> float:
    """Calcule l'EMA (Moyenne Mobile Exponentielle)"""
    if len(prices) < period:
        return None
    
    series = pd.Series(prices)
    ema = series.ewm(span=period, adjust=False).mean()
    return ema.iloc[-1]

def calculate_rsi(prices: list, period: int = 14) -> float:
    """Calcule le RSI (Relative Strength Index)"""
    if len(prices) < period + 1:
        return None
    
    series = pd.Series(prices)
    delta = series.diff()
    
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi.iloc[-1]

def get_signal(prices: list) -> str:
    """
    Génère un signal de trading basé sur EMA Cross + RSI
    
    Retourne:
    - 'BUY'  : Signal d'achat
    - 'SELL' : Signal de vente
    - 'HOLD' : Pas d'action
    """
    if len(prices) < EMA_SLOW + RSI_PERIOD:
        return 'HOLD'
    
    ema_fast = calculate_ema(prices, EMA_FAST)
    ema_slow = calculate_ema(prices, EMA_SLOW)
    rsi = calculate_rsi(prices, RSI_PERIOD)
    
    if ema_fast is None or ema_slow is None or rsi is None:
        return 'HOLD'
    
    # Signal d'achat : EMA rapide croise au-dessus de l'EMA lente ET RSI survendu
    if ema_fast > ema_slow and rsi < RSI_OVERSOLD:
        return 'BUY'
    
    # Signal de vente : EMA rapide croise en-dessous de l'EMA lente ET RSI suracheté
    elif ema_fast < ema_slow and rsi > RSI_OVERBOUGHT:
        return 'SELL'
    
    return 'HOLD'

# ============================================================
# FONCTIONS API HYPERLIQUID
# ============================================================

BASE_URL = "https://api.hyperliquid.xyz"

def get_market_prices(symbol: str, limit: int = 100) -> list:
    """Récupère les prix historiques depuis Hyperliquid"""
    try:
        url = f"{BASE_URL}/info"
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": "15m",
                "startTime": int((time.time() - limit * 60) * 1000),
                "endTime": int(time.time() * 1000)
            }
        }
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        prices = [float(candle["c"]) for candle in data]
        return prices
    except Exception as e:
        print(f"[ERREUR] Impossible de récupérer les prix: {e}")
        return []

def get_account_balance() -> float:
    """Récupère le solde du compte"""
    try:
        url = f"{BASE_URL}/info"
        payload = {
            "type": "clearinghouseState",
            "user": WALLET_ADDRESS
        }
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        balance = float(data.get("marginSummary", {}).get("accountValue", 0))
        return balance
    except Exception as e:
        print(f"[ERREUR] Impossible de récupérer le solde: {e}")
        return 0

def place_order(symbol: str, side: str, size_usd: float, price: float) -> dict:
    """
    Place un ordre sur Hyperliquid
    
    IMPORTANT: Cette fonction nécessite une intégration complète
    avec la signature cryptographique Hyperliquid.
    En mode simulation, elle affiche seulement l'ordre.
    """
    
    # ⚠️ MODE SIMULATION - Affiche l'ordre sans l'exécuter
    print(f"\n[SIMULATION] Ordre {side}")
    print(f"  Symbole  : {symbol}")
    print(f"  Montant  : ${size_usd}")
    print(f"  Prix     : ${price:.2f}")
    print(f"  Stop-loss: ${price * (1 - STOP_LOSS_PCT):.2f}" if side == "BUY" else f"  Stop-loss: ${price * (1 + STOP_LOSS_PCT):.2f}")
    
    return {"status": "simulated", "side": side, "price": price}

# ============================================================
# BOUCLE PRINCIPALE DU BOT
# ============================================================

def run_bot():
    """Boucle principale du bot de trading"""
    
    print("=" * 50)
    print("  BOT DE TRADING - EMA Cross + RSI")
    print("  Plateforme: Hyperliquid")
    print("  Mode: SIMULATION")
    print("=" * 50)
    print(f"  Paire      : {SYMBOL}/USD")
    print(f"  Taille     : ${TRADE_SIZE_USD} par trade")
    print(f"  Stop-loss  : {STOP_LOSS_PCT * 100}%")
    print(f"  Take-profit: {TAKE_PROFIT_PCT * 100}%")
    print("=" * 50)
    print()
    
    last_signal = 'HOLD'
    trade_count = 0
    
    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            
            # 1. Récupérer les prix
            prices = get_market_prices(SYMBOL, limit=100)
            
            if not prices:
                print(f"[{now}] ⚠️  Impossible de récupérer les prix, nouvelle tentative...")
                time.sleep(CHECK_INTERVAL)
                continue
            
            current_price = prices[-1]
            
            # 2. Calculer les indicateurs
            ema_fast = calculate_ema(prices, EMA_FAST)
            ema_slow = calculate_ema(prices, EMA_SLOW)
            rsi = calculate_rsi(prices, RSI_PERIOD)
            
            # 3. Générer le signal
            signal = get_signal(prices)
            
            # 4. Afficher l'état
            ema_fast_str = f"${ema_fast:,.2f}" if ema_fast else "N/A"
ema_slow_str = f"${ema_slow:,.2f}" if ema_slow else "N/A"
rsi_str = f"{rsi:.1f}" if rsi else "N/A"
print(f"[{now}] Prix: ${current_price:,.2f} | EMA9: {ema_fast_str} | EMA21: {ema_slow_str} | RSI: {rsi_str} | Signal: {signal}")

            
            # 5. Agir sur le signal (seulement si nouveau signal)
            if signal != last_signal and signal != 'HOLD':
                place_order(SYMBOL, signal, TRADE_SIZE_USD, current_price)
                last_signal = signal
                trade_count += 1
                print(f"  → Trade #{trade_count} exécuté")
            
            # 6. Attendre avant la prochaine vérification
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n[INFO] Bot arrêté par l'utilisateur")
            print(f"[INFO] Total trades: {trade_count}")
            break
        except Exception as e:
            print(f"[ERREUR] {e}")
            time.sleep(CHECK_INTERVAL)

# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    run_bot()
