"""
QuantumBot Pro V4.9 - Master Quant Settings
Fokus: Agresif Terukur, Bebas Hambatan Entry, Manajemen Terintegrasi.
"""
import os
from datetime import timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOCAL_TZ = timezone(timedelta(hours=7))
DB_PATH = os.getenv("DB_PATH", "quantum_state.db")

SYMBOLS = ["XAUUSDm", "BTCUSDm", "USTECm", "ETHUSDm"]
ACTIVE_SYMBOLS = {"XAUUSDm": True, "BTCUSDm": True, "USTECm": True, "ETHUSDm": True, "GBPJPYm": False, "ETHUSDm_old": False}
BASE_EQUITY = 2000.0
MT5_PROFIT_SCALE = 1.0

# LOT DINAMIS (Dihitung oleh LotSizer 1.5%)
SYMBOL_CONFIGS = {
    "XAUUSDm": {"pip_size": 0.1, "contract_size": 100.0, "lot_size": 0.02, "max_lot": 0.03, "profit_target": 35.0, "int_gap": 2.5, "ext_gap": 5.0},
    "BTCUSDm": {"pip_size": 1.0, "contract_size": 1.0, "lot_size": 0.01, "max_lot": 0.02, "profit_target": 60.0, "int_gap": 150.0, "ext_gap": 300.0},
    "ETHUSDm": {"pip_size": 0.1, "contract_size": 1.0, "lot_size": 0.03, "max_lot": 0.03, "profit_target": 30.0, "int_gap": 15.0, "ext_gap": 30.0},
    "USTECm": {"pip_size": 1.0, "contract_size": 1.0, "lot_size": 0.02, "max_lot": 0.03, "profit_target": 40.0, "int_gap": 25.0, "ext_gap": 50.0},
    "GBPJPYm": {"pip_size": 0.01, "contract_size": 100000.0, "lot_size": 0.02, "max_lot": 0.03, "profit_target": 25.0, "int_gap": 0.35, "ext_gap": 0.70}
}

# MANAJEMEN RISIKO & CIRCUIT BREAKER
GLOBAL_RISK_ENABLED = True
RISK_PER_TRADE_PCT = 1.5
MAX_DAILY_DRAWDOWN_PCT = 4.0
DAILY_PROFIT_TARGET_USD = 180.0
DAILY_LOSS_LIMIT_USD = 85.0
GLOBAL_HARD_STOP_USD = 120.0
MAX_TOTAL_OPEN_LOTS = 0.50
MAX_OPEN_POSITIONS = 20
MIN_MARGIN_LEVEL_PCT = 0.0
GLOBAL_MAX_SPREAD_PCT = 0.25
GLOBAL_HALT = False

# TRAILING & PROFIT MANAGEMENT (Pure Trailing)
BEP_TRIGGER_PIPS = 9999.0
PARTIAL_SCALE_PIPS = 9999.0
TRAILING_MILESTONE_RR = 0.5
TRAILING_ATR_MULT = 1.0
RUNNER_TRAIL_PIPS = 35.0

WEEKEND_CLOSE_ENABLED = True
WEEKEND_CLOSE_HOUR = 22
WEEKEND_ACTIVE_SYMBOLS = ["XAUUSDm", "BTCUSDm", "USTECm", "ETHUSDm"]

# MAGIC NUMBERS
HYBRID_MAGIC = 222; SMC_MAGIC = 333
TREND_MAGIC = 555; EDGE_MAGIC = 666
CRASH_SNIPER_MAGIC = 888; LONDON_BREAKOUT_MAGIC = 999; SQUEEZE_MAGIC = 1001; NY_REVERSAL_MAGIC = 1002
FIBONACCI_MAGIC = 1003; DIVERGENCE_MAGIC = 1004; SWEEP_MAGIC = 1005; STRADDLE_MAGIC = 1006
ICT_MAGIC = 1007; POC_MAGIC = 1008; ZSCORE_MAGIC = 1009; SESSION_SWEEP_MAGIC = 1010
VWAP_BOUNCE_MAGIC = 1011; VWAP_MICRO_MAGIC = 2001; ZSCORE_STAT_MAGIC = 2002
MICRO_FVG_MAGIC = 2003; DONCHIAN_MAGIC = 3001
SUPERTREND_MAGIC = 3002; PIVOT_BOUNCE_MAGIC = 3003; ASIAN_BOX_MAGIC = 3004
CLIMAX_FADE_MAGIC = 3006
VWAP_TREND_EXPANSION_MAGIC = 2005; VW_ORB_MAGIC = 2006; VWAP_TREND_MAGIC = 4001
MANUAL_MAGIC = 234000

# ENGINE ALLOCATION (Bersih & Fokus)
SMC_ENABLED = True; TREND_ENABLED = True; CRASH_SNIPER_ENABLED = True
SQUEEZE_ENABLED = True; NY_REVERSAL_ENABLED = False; FIBONACCI_ENABLED = True
DIVERGENCE_ENABLED = True; SWEEP_ENABLED = True; STRADDLE_ENABLED = True
ICT_ENABLED = True; POC_ENABLED = False; ZSCORE_ENABLED = True
SESSION_SWEEP_ENABLED = True; VWAP_BOUNCE_ENABLED = True; VWAP_MICRO_M5_ENABLED = True
ZSCORE_STAT_ENABLED = True; MICRO_FVG_ENABLED = False
DONCHIAN_ENABLED = True; SUPERTREND_ENABLED = True; PIVOT_BOUNCE_ENABLED = True
ASIAN_BOX_ENABLED = True; CLIMAX_FADE_ENABLED = True
VWAP_TREND_EXPANSION_ENABLED = True; VW_ORB_ENABLED = True
VWAP_TREND_ENABLED = True; HYBRID_ENABLED = False; LONDON_BREAKOUT_ENABLED = True
EDGE_ENABLED = False

# PENGHALANG DIMATIKAN
PURE_HEDGE_ENABLED = False; PA_ENABLED = False; STAT_ARB_ENABLED = False
SAFE_ROUND_NUMBER_ENABLED = False
ML_GATE_ENABLED = False
ML_MIN_CONFIDENCE = 60.0

SMC_SYMBOLS = SYMBOLS; TREND_SYMBOLS = SYMBOLS; EDGE_SYMBOLS = SYMBOLS
HYBRID_SYMBOLS = SYMBOLS; SQUEEZE_SYMBOLS = SYMBOLS
LONDON_BREAKOUT_SYMBOLS = ["XAUUSDm"]
CRASH_SNIPER_SYMBOLS = ["XAUUSDm", "BTCUSDm", "USTECm", "ETHUSDm"]

def get_contract_size(symbol: str) -> float:
    s = symbol.upper()
    if "XAU" in s: return 100.0
    if "BTC" in s or "USTEC" in s or "ETH" in s: return 1.0
    if "EUR" in s or "GBP" in s or "JPY" in s: return 100000.0
    return 100.0

def validate_config():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Telegram Token / Chat ID belum diset di .env")

ISOLATED_ENABLED = True
ISOLATED_LOSS_THRESHOLD = -7.0
ISOLATED_TARGET_PROFIT = 0.01
ISOLATED_MAGIC = 9999

# FAIL-SAFE SL DARURAT (Jika Isolated Gagal/Internet Putus)
ISOLATED_FAILSAFE_SL_USD = 15.0 # SL akan terpasang di -$15 jika Isolated gagal menyelamatkan

# BASKET NETTING (FASE 4)
BASKET_NETTING_ENABLED = True
# BASKET TAKE PROFIT (Tutup semua posisi 1 simbol jika total profit menyentuh target)
SYMBOL_BASKET_PROFIT_TARGET = 10.0


HEDGE_REBALANCE_ENABLED = False


# DYNAMIC ENGINE QUOTA (Maksimal posisi aktif per strategi)
MAX_ENTRY_PER_ENGINE = 3

