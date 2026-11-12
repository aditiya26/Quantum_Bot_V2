"""
Central Market Data Hub V3.0
"""
import time, asyncio, numpy as np

class MarketDataHub:
    def __init__(self, mt5_client, symbols):
        self.client = mt5_client
        self.symbols = symbols
        self.snapshot = {}
        self.spread_history = {s: [] for s in symbols}
        self.last_refresh = 0.0

    async def refresh_snapshot(self):
        now = time.time()
        # Gunakan asyncio.gather untuk mengambil semua harga secara PARALEL (Non-Blocking)
        tasks = [self.client.fetch_ticker(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks)
        
        for sym, tick in zip(self.symbols, results):
            if tick and tick['bid'] > 0:
                self.snapshot[sym] = {
                    'bid': tick['bid'],
                    'ask': tick['ask'],
                    'last': tick['last'],
                    'spread': tick['spread'],
                    'time': now
                }
                hist = self.spread_history[sym]
                hist.append((now, tick['spread']))
                if len(hist) > 120: hist.pop(0)

    def get_ticker(self, symbol: str) -> dict:
        return self.snapshot.get(symbol, {'bid': 0.0, 'ask': 0.0, 'last': 0.0, 'spread': 0.0})

    def get_median_spread(self, symbol: str) -> float:
        hist = [s[1] for s in self.spread_history.get(symbol, [])]
        return float(np.median(hist)) if hist else 0.001

    async def get_market_regime(self, symbol: str) -> str:
        ohlcv = await self.client.fetch_ohlcv(symbol, '15m', 30)
        if not ohlcv or len(ohlcv) < 20: return "NEUTRAL"
        closes = [b[4] for b in ohlcv]
        highs = [b[2] for b in ohlcv]
        lows = [b[3] for b in ohlcv]
        tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1])) for i in range(1, len(closes))]
        atr = float(np.mean(tr[-14:]))
        atr_pct = (atr / closes[-1]) * 100
        
        if atr_pct > 0.35: return "TRENDING"
        if atr_pct < 0.15: return "RANGING"
        return "NEUTRAL"

