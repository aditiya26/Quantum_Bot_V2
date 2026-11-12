"""
QuantumBot Pro V4.9 - Smart Multi-Tier In-Memory Cache Hub
Sliding Window Eviction, Memory Leaks Prevention, Low Latency.
"""
import time, asyncio, numpy as np
import logging

logger = logging.getLogger("SmartMarketDataHub")

class SmartMarketDataHub:
    def __init__(self, mt5_client, symbols):
        self.client = mt5_client
        self.symbols = symbols
        self.l1_ticker_cache = {}
        self.l2_ohlcv_cache = {}
        self.l3_indicator_cache = {}
        self.spread_history = {s: [] for s in symbols}
        self.cache_stats = {'hits': 0, 'misses': 0}

    async def get_ticker(self, symbol: str) -> dict:
        now = time.time()
        if symbol in self.l1_ticker_cache and (now - self.l1_ticker_cache[symbol]['ts'] < 0.25):
            self.cache_stats['hits'] += 1
            return self.l1_ticker_cache[symbol]['data']

        self.cache_stats['misses'] += 1
        tick = await self.client.fetch_ticker(symbol)
        if tick and tick.get('bid', 0) > 0:
            self.l1_ticker_cache[symbol] = {'data': tick, 'ts': now}
            hist = self.spread_history[symbol]
            hist.append((now, tick.get('spread', 0.0)))
            if len(hist) > 100: hist.pop(0)
            return tick
        return self.l1_ticker_cache.get(symbol, {}).get('data', {'bid': 0.0, 'ask': 0.0, 'last': 0.0, 'spread': 0.0})

    async def get_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 50) -> list:
        key = f"{symbol}_{timeframe}_{limit}"
        now = time.time()
        ttl_map = {'1m': 5, '5m': 15, '15m': 30, '1h': 60}
        ttl = ttl_map.get(timeframe, 20)

        if key in self.l2_ohlcv_cache and (now - self.l2_ohlcv_cache[key]['ts'] < ttl):
            self.cache_stats['hits'] += 1
            return self.l2_ohlcv_cache[key]['data']

        self.cache_stats['misses'] += 1

        # [FIX RAM LEAK]: Sliding window eviction alih-alih menghapus seluruh cache
        if len(self.l2_ohlcv_cache) > 80:
            oldest_keys = sorted(self.l2_ohlcv_cache.keys(), key=lambda k: self.l2_ohlcv_cache[k]['ts'])[:30]
            for ok in oldest_keys:
                del self.l2_ohlcv_cache[ok]

        data = await self.client.fetch_ohlcv(symbol, timeframe, limit)
        if data:
            self.l2_ohlcv_cache[key] = {'data': data, 'ts': now}
        return data or []

    def evict_stale_data(self):
        self.l1_ticker_cache.clear()
        self.l2_ohlcv_cache.clear()
        self.l3_indicator_cache.clear()

    async def refresh_snapshot(self):
        for sym in self.symbols:
            await self.get_ticker(sym)

    def get_cache_telemetry(self) -> dict:
        total = self.cache_stats['hits'] + self.cache_stats['misses']
        hit_rate = (self.cache_stats['hits'] / total * 100) if total > 0 else 0
        return {
            'hits': self.cache_stats['hits'],
            'misses': self.cache_stats['misses'],
            'hit_rate_pct': round(hit_rate, 2),
            'active_l2_keys': len(self.l2_ohlcv_cache)
        }
