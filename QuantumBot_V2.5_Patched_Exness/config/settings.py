"""
QuantumBot Pro V2.5 - System Configuration
Calibrated for $2,000 USD Capital on Exness Broker
"""
import os, json
from datetime import timezone, timedelta
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError: pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOCAL_TZ = timezone(timedelta(hours=7)) # WIB

SYMBOLS = ["XAUUSDm", "BTCUSDm", "GBPJPYm", "USTECm"]
MT5_PROFIT_SCALE = 1.0
ACTIVE_SYMBOLS = {sym: True for sym in SYMBOLS}

# Pengaturan Simbol Terkalibrasi untuk Modal $2,000
SYMBOL_CONFIGS = {
    "XAUUSDm": {"int_gap": 2.5, "ext_gap": 5.0, "lot_size": 0.03, "profit_target": 15.0, "pip_size": 0.1},
    "BTCUSDm": {"int_gap": 40.0, "ext_gap": 80.0, "lot_size": 0.02, "profit_target": 25.0, "pip_size": 1.0},
    "GBPJPYm": {"int_gap": 0.12, "ext_gap": 0.30, "lot_size": 0.03, "profit_target": 12.0, "pip_size": 0.01},
    "USTECm": {"int_gap": 10.0, "ext_gap": 25.0, "lot_size": 0.02, "profit_target": 15.0, "pip_size": 1.0}
}

# Master Strategy Toggles
SMC_ENABLED = True
SWEEP_ENABLED = True
LONDON_BREAKOUT_ENABLED = True
NY_REVERSAL_ENABLED = True
SQUEEZE_ENABLED = True
FIBONACCI_ENABLED = True
DIVERGENCE_ENABLED = True
CRASH_SNIPER_ENABLED = True
TREND_ENABLED = True
EDGE_ENABLED = True
PA_ENABLED = True
PURE_HEDGE_ENABLED = False
GRID_ENABLED = False
HYBRID_ENABLED = True
STRADDLE_ENABLED = True
ICT_ENABLED = True
POC_ENABLED = True
ZSCORE_ENABLED = True
SESSION_SWEEP_ENABLED = True
VWAP_BOUNCE_ENABLED = True
ZSCORE_STAT_ENABLED = True
VWAP_MICRO_M5_ENABLED = True
SAFE_GRID_ENABLED = True
MICRO_FVG_ENABLED = True

# Simbol Assignment
SMC_SYMBOLS = SYMBOLS
TREND_SYMBOLS = SYMBOLS
EDGE_SYMBOLS = SYMBOLS
PA_SYMBOLS = SYMBOLS
HYBRID_SYMBOLS = SYMBOLS
SQUEEZE_SYMBOLS = SYMBOLS
CRASH_SNIPER_SYMBOLS = ["XAUUSDm", "BTCUSDm"]
PURE_HEDGE_SYMBOLS = ["XAUUSDm", "BTCUSDm"]
GRID_SYMBOLS = SYMBOLS
LONDON_BREAKOUT_SYMBOLS = ["XAUUSDm", "GBPJPYm"]

# Magic Numbers
GRID_MAGIC = 111; HYBRID_MAGIC = 222; SMC_MAGIC = 333; PURE_HEDGE_MAGIC = 444
TREND_MAGIC = 555; EDGE_MAGIC = 666; PA_MAGIC = 777; CRASH_SNIPER_MAGIC = 888
LONDON_BREAKOUT_MAGIC = 999; SQUEEZE_MAGIC = 1001; NY_REVERSAL_MAGIC = 1002
FIBONACCI_MAGIC = 1003; DIVERGENCE_MAGIC = 1004; SWEEP_MAGIC = 1005; STRADDLE_MAGIC = 1006
ICT_MAGIC = 1007; POC_MAGIC = 1008; ZSCORE_MAGIC = 1009; SESSION_SWEEP_MAGIC = 1010
VWAP_BOUNCE_MAGIC = 1011; VWAP_MICRO_M5_MAGIC = 2001; ZSCORE_STAT_MAGIC = 2002
MICRO_FVG_MAGIC = 2003; SAFE_GRID_MAGIC = 2004; MANUAL_MAGIC = 234000

# Manajemen Risiko Terukur untuk Modal $2,000 USD
GLOBAL_RISK_ENABLED = True
BASE_EQUITY = 2000.0
DAILY_PROFIT_TARGET_USD = 100.0  # Target profit realistis 5%
DAILY_LOSS_LIMIT_USD = 80.0     # Maksimal rugi 4% ($80)
GLOBAL_HARD_STOP_USD = 100.0     # Floating stop 5%
MAX_TOTAL_OPEN_LOTS = 0.20      # Total exposure maksimal terkalibrasi
MIN_MARGIN_LEVEL_PCT = 350.0

# Trailing & Weekend Protection
WEEKEND_CLOSE_ENABLED = True
WEEKEND_CLOSE_HOUR = 22 # 22:00 WIB Jumat
WEEKEND_ACTIVE_SYMBOLS = ["BTCUSDm", "BTCUSD"]
GLOBAL_MAX_SPREAD_PCT = 0.08 # Spread filter

def get_contract_size(symbol: str) -> float:
    s = symbol.upper()
    if "XAU" in s: return 100.0
    if "BTC" in s or "USTEC" in s: return 1.0
    if "EUR" in s or "GBP" in s or "JPY" in s: return 100000.0
    return 100.0

def validate_config():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[PERINGATAN] TELEGRAM_TOKEN atau TELEGRAM_CHAT_ID belum diatur di file .env!")
