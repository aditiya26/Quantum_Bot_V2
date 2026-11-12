import logging
import numpy as np
import time

logger = logging.getLogger("UnifiedQuantCore")

class UnifiedQuantCore:
    """
    Modul Pusat Pengendalian Kuantitatif:
    Menyatukan Market Regime, Validasi Korelasi Simbol, dan Dynamic Risk Scaling.
    """
    def __init__(self, client):
        self.client = client
        self.active_exposures = {} 
        self.cache = {}
        self.cache_ttl = 180 # Cache 3 menit agar tidak membebani MT5

    async def get_unified_regime(self, symbol: str) -> str:
        now = time.time()
        if symbol in self.cache and now - self.cache[symbol]['ts'] < self.cache_ttl:
            return self.cache[symbol]['regime']

        try:
            bars = await self.client.fetch_ohlcv(symbol, '15m', 50)
            if not bars or len(bars) < 30: return "NEUTRAL"
            
            closes = np.array([b[4] for b in bars], dtype=float)
            highs = np.array([b[2] for b in bars], dtype=float)
            lows = np.array([b[3] for b in bars], dtype=float)

            tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(closes))]
            atr_pct = (np.mean(tr[-14:]) / closes[-1]) * 100.0

            up_moves = highs[1:] - highs[:-1]
            down_moves = lows[:-1] - lows[1:]
            pos_dm = np.where((up_moves > down_moves) & (up_moves > 0), up_moves, 0.0)
            neg_dm = np.where((down_moves > up_moves) & (down_moves > 0), down_moves, 0.0)
            
            tr_sum = np.sum(tr[-14:])
            if tr_sum == 0: 
                regime = "RANGING"
            else:
                di_plus = (np.sum(pos_dm[-14:]) / tr_sum) * 100
                di_minus = (np.sum(neg_dm[-14:]) / tr_sum) * 100
                dx = (abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9)) * 100
                
                if atr_pct > 0.45: regime = "SHOCK"
                elif dx > 24.0: regime = "TRENDING"
                else: regime = "RANGING"
                
            self.cache[symbol] = {'ts': now, 'regime': regime}
            return regime
        except Exception as e:
            logger.error(f"Unified Regime Error {symbol}: {e}")
            return "NEUTRAL"

    def calculate_dynamic_basket_stop(self, symbol: str, equity: float, current_atr: float) -> float:
        """Menghitung Hard Stop berbasis volatilitas ATR dan ekuitas."""
        base_risk_pct = 1.5 
        risk_usd = equity * (base_risk_pct / 100.0)
        
        if "BTC" in symbol:
            return max(40.0, risk_usd * 1.2)
        elif "XAU" in symbol:
            atr_floor = (current_atr * 3.0) if current_atr > 0 else 0.0
            return max(25.0, risk_usd, atr_floor)
        elif "ETH" in symbol:
            atr_floor = (current_atr * 3.0) if current_atr > 0 else 0.0
            return max(20.0, risk_usd, atr_floor)
        return max(15.0, risk_usd)
        
    def check_correlation_guard(self, symbol: str, direction: str) -> bool:
        """Mencegah strategi yang sama/berlawanan menumpuk di simbol yang sama."""
        if symbol in self.active_exposures:
            # Jika sudah ada eksposur searah, tolak
            if self.active_exposures[symbol] == direction:
                return False 
        self.active_exposures[symbol] = direction
        return True

