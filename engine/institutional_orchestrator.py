"""
QuantumBot Pro V3.4 - Institutional Orchestrator
Fixed: Empty position evaluation bug, atomic stat-arb, isolated session, true daily pivot.
"""
import asyncio, logging, time
import numpy as np
import config.settings as cfg
from datetime import datetime, timezone
from monitor.news_filter import NewsFilter

logger = logging.getLogger("Institutional_Orchestrator")

class InstitutionalPreventiveShield:
    def __init__(self, client):
        self.client = client

    async def validate_market_safety(self, symbol: str) -> bool:
        try:
            bars = await self.client.fetch_ohlcv(symbol, '15m', 24)
            if not bars or len(bars) < 20: return False

            highs = np.array([b[2] for b in bars], dtype=float)
            lows = np.array([b[3] for b in bars], dtype=float)
            closes = np.array([b[4] for b in bars], dtype=float)

            tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            current_atr = np.mean(tr[-5:])
            baseline_atr = np.mean(tr[-20:])

            if current_atr > (baseline_atr * 5.0):
                return False

            tick = await self.client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0 or tick['bid'] <= 0: return False
            
            spread = tick['ask'] - tick['bid']
            mid_price = (tick['ask'] + tick['bid']) / 2.0
            spread_pct = (spread / mid_price) * 100.0

            max_spread = 0.25 if ("BTC" in symbol or "ETH" in symbol or "XAU" in symbol) else 0.10
            if spread_pct > max_spread:
                return False

        except Exception as e:
            logger.error(f"Error Preventive Shield {symbol}: {e}")
            return False

        return True

class MarketRegimeRouter:
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.cache_ttl = 300

    async def evaluate_regime(self, symbol: str) -> str:
        import time
        import numpy as np
        now = time.time()
        
        if symbol in self.cache and now - self.cache[symbol]['ts'] < self.cache_ttl:
            return self.cache[symbol]['regime']

        try:
            # Ambil data H1 untuk tren global
            bars = await self.client.fetch_ohlcv(symbol, '1h', 50)
            if not bars or len(bars) < 30: return "RANGING"
            
            closes = np.array([b[4] for b in bars], dtype=float)
            highs = np.array([b[2] for b in bars], dtype=float)
            lows = np.array([b[3] for b in bars], dtype=float)

            # 1. Deteksi SHOCK (Volatilitas Ekstrem)
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            current_atr = np.mean(tr[-5:])
            baseline_atr = np.mean(tr[-20:])
            if baseline_atr > 0 and current_atr > (baseline_atr * 3.5):
                regime = "SHOCK"
                self.cache[symbol] = {'ts': now, 'regime': regime}
                return regime

            # 2. Tren Global (SMA 20)
            sma20 = np.mean(closes[-20:])
            curr_close = closes[-1]
            is_trend_up = curr_close > sma20 * 1.002
            is_trend_down = curr_close < sma20 * 0.998

            # 3. Volatilitas Global (Donchian Expansion)
            upper_donchian = max(highs[-25:-1])
            lower_donchian = min(lows[-25:-1])
            is_donchian_breakout = curr_close > upper_donchian or curr_close < lower_donchian

            # Keputusan Regime Institusional
            if is_donchian_breakout or is_trend_up or is_trend_down:
                regime = "TRENDING"
            else:
                regime = "RANGING" # Diubah dari SIDEWAYS agar sinkron dengan position_manager
                
            self.cache[symbol] = {'ts': now, 'regime': regime}
            return regime
        except Exception as e:
            import logging
            logging.error(f"Error Global Regime Router {symbol}: {e}")
            return "RANGING"

class DonchianBreakoutEngine:
    MAGIC = 3001
    def __init__(self, state, clients, telegram, order_lock, quant_core):
        self.clients = clients; self.telegram = telegram; self.order_lock = order_lock; self.quant_core = quant_core
        self.news = NewsFilter()
    async def manage(self):
        if not getattr(cfg, 'DONCHIAN_ENABLED', True): return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        news_block, _ = await self.news.is_high_impact_news_near(15, 10)
        if news_block: return
        shield = InstitutionalPreventiveShield(client)
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            if await self.quant_core.get_unified_regime(symbol) != "TRENDING": continue
            if not await shield.validate_market_safety(symbol): continue
            bars = await client.fetch_ohlcv(symbol, '1h', 30)
            if not bars or len(bars) < 25: continue
            upper, lower = max(b[2] for b in bars[-21:-1]), min(b[3] for b in bars[-21:-1])
            curr, lot = bars[-2][4], cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            tr = [max(b[2]-b[3], abs(b[2]-bars[i-1][4]), abs(b[3]-bars[i-1][4])) for i, b in enumerate(bars[1:], 1)]
            atr = sum(tr[-14:]) / 14.0
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue
            digits = 3 if 'JPY' in symbol else (2 if 'XAU' in symbol or 'BTC' in symbol else 5)
            if curr > upper:
                sl, tp = round(tick['ask'] - (2.0*atr), digits), round(tick['ask'] + (4.0*atr), digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>DONCHIAN BUY</b> | {symbol}", with_keyboard=False)
            elif curr < lower:
                sl, tp = round(tick['bid'] + (2.0*atr), digits), round(tick['bid'] - (4.0*atr), digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>DONCHIAN SELL</b> | {symbol}", with_keyboard=False)

class SupertrendEngine:
    MAGIC = 3002
    def __init__(self, state, clients, telegram, order_lock, regime_router):
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.quant_core = regime_router
        self.news = NewsFilter()
    async def manage(self):
        if not getattr(cfg, 'SUPERTREND_ENABLED', True): return
        news_block, _ = await self.news.is_high_impact_news_near(15, 10)
        if news_block: return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            if await self.quant_core.get_unified_regime(symbol) != "TRENDING": continue
            bars = await client.fetch_ohlcv(symbol, '1h', 30)
            if not bars or len(bars) < 20: continue
            closes = np.array([b[4] for b in bars], dtype=float)
            highs = np.array([b[2] for b in bars], dtype=float)
            lows = np.array([b[3] for b in bars], dtype=float)
            hl2 = (highs + lows) / 2.0
            
            # [FIX] Perbaikan Array Length
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            atr = np.mean(tr[-14:])
            
            upper_band = hl2[-1] + (3.0 * atr)
            lower_band = hl2[-1] - (3.0 * atr)
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue
            digits = 3 if 'JPY' in symbol else (2 if 'XAU' in symbol or 'BTC' in symbol else 5)
            if closes[-2] < upper_band and closes[-1] > upper_band:
                sl, tp = round(tick['ask'] - (2.0*atr), digits), round(tick['ask'] + (4.0*atr), digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>SUPERTREND BUY</b> | {symbol}", with_keyboard=False)
            elif closes[-2] > lower_band and closes[-1] < lower_band:
                sl, tp = round(tick['bid'] + (2.0*atr), digits), round(tick['bid'] - (4.0*atr), digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>SUPERTREND SELL</b> | {symbol}", with_keyboard=False)

class PivotBounceEngine:
    MAGIC = 3003
    def __init__(self, state, clients, telegram, order_lock, quant_core):
        self.clients = clients; self.telegram = telegram; self.order_lock = order_lock; self.quant_core = quant_core
        self.news = NewsFilter()
    async def manage(self):
        if not getattr(cfg, 'PIVOT_BOUNCE_ENABLED', True): return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        news_block, _ = await self.news.is_high_impact_news_near(15, 10)
        if news_block: return
        shield = InstitutionalPreventiveShield(client)
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            if await self.quant_core.get_unified_regime(symbol) != "SIDEWAYS": continue
            if not await shield.validate_market_safety(symbol): continue
            bars = await client.fetch_ohlcv(symbol, '1h', 50)
            if not bars or len(bars) < 48: continue
            prev_24h = bars[-48:-24] 
            h, l, c = max(b[2] for b in prev_24h), min(b[3] for b in prev_24h), prev_24h[-1][4]
            pp = (h + l + c) / 3.0
            s1, r1 = (2.0 * pp) - h, (2.0 * pp) - l
            curr = bars[-1]
            lot, digits = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02), (3 if 'JPY' in symbol else (2 if 'XAU' in symbol or 'BTC' in symbol or 'ETH' in symbol else 1))
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue
            if curr[3] <= s1 and curr[4] > s1:
                sl, tp = round(s1 - (s1*0.0015), digits), round(pp, digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>PIVOT S1 BUY</b> | {symbol}", with_keyboard=False)
            elif curr[2] >= r1 and curr[4] < r1:
                sl, tp = round(r1 + (r1*0.0015), digits), round(pp, digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>PIVOT R1 SELL</b> | {symbol}", with_keyboard=False)

class AsianRangeEngine:
    MAGIC = 3004
    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.news = NewsFilter()
    async def manage(self):
        if not getattr(cfg, 'ASIAN_BOX_ENABLED', True): return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        now_utc = datetime.now(timezone.utc)
        if not (7 <= now_utc.hour < 8): return 
        news_block, _ = await self.news.is_high_impact_news_near(15, 10)
        if news_block: return
        shield = InstitutionalPreventiveShield(client)
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in ["GBPJPYm", "XAUUSDm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            if not await shield.validate_market_safety(symbol): continue
            bars = await client.fetch_ohlcv(symbol, '15m', 36)
            if not bars or len(bars) < 32: continue
            asian_bars = bars[:-4] 
            bh, bl = max(b[2] for b in asian_bars), min(b[3] for b in asian_bars)
            br = bh - bl
            curr, lot = bars[-1][4], cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            digits = 3 if 'JPY' in symbol else (2 if 'XAU' in symbol else 5)
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue
            if curr > bh:
                sl, tp = round(bh - (br*0.5), digits), round(tick['ask'] + br, digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>ASIAN BOX BUY</b> | {symbol}", with_keyboard=False)
            elif curr < bl:
                sl, tp = round(bl + (br*0.5), digits), round(tick['bid'] - br, digits)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>ASIAN BOX SELL</b> | {symbol}", with_keyboard=False)

class RoundNumberEngine:
    MAGIC = 3005
    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.news = NewsFilter()
    async def manage(self):
        if not getattr(cfg, 'ROUND_NUMBER_ENABLED', True): return
        news_block, _ = await self.news.is_high_impact_news_near(15, 10)
        if news_block: return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0: continue
            price = tick['last']
            step = 500.0 if "BTC" in symbol else (50.0 if "XAU" in symbol else 100.0)
            nearest = round(price / step) * step
            if abs(price - nearest) / nearest <= 0.0008:
                bars = await client.fetch_ohlcv(symbol, '5m', 10)
                if not bars: continue
                lot, digits = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02), (3 if 'JPY' in symbol else (2 if 'XAU' in symbol or 'BTC' in symbol else 5))
                if bars[-2][2] >= nearest and bars[-1][4] < nearest:
                    sl, tp = round(nearest + (step*0.05), digits), round(nearest - (step*0.10), digits)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order: await self.telegram.send(f" <b>ROUND SELL</b> | {symbol}", with_keyboard=False)
                elif bars[-2][3] <= nearest and bars[-1][4] > nearest:
                    sl, tp = round(nearest - (step*0.05), digits), round(nearest + (step*0.10), digits)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order: await self.telegram.send(f" <b>ROUND BUY</b> | {symbol}", with_keyboard=False)

class ClimaxFadeEngine:
    MAGIC = 3006
    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
    async def manage(self):
        if not getattr(cfg, 'CLIMAX_FADE_ENABLED', True): return
        client = self.clients.get("exness_mt5")
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue
            bars = await client.fetch_ohlcv(symbol, '5m', 25)
            if not bars or len(bars) < 22: continue
            volumes = np.array([b[5] for b in bars], dtype=float)
            if volumes[-1] >= (np.mean(volumes[-21:-1]) * 2.5):
                o, h, l, c = bars[-1][1], bars[-1][2], bars[-1][3], bars[-1][4]
                rng = h - l
                if rng == 0: continue
                lot, digits = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02), (3 if 'JPY' in symbol else (2 if 'XAU' in symbol or 'BTC' in symbol else 5))
                tick = await client.exchange.fetch_ticker(symbol)
                if (h - max(o, c)) > (rng * 0.5) and c < o:
                    sl, tp = round(h + (rng*0.2), digits), round(tick['bid'] - rng, digits)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order: await self.telegram.send(f" <b>CLIMAX SELL</b> | {symbol}", with_keyboard=False)
                elif (min(o, c) - l) > (rng * 0.5) and c > o:
                    sl, tp = round(l - (rng*0.2), digits), round(tick['ask'] + rng, digits)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order: await self.telegram.send(f" <b>CLIMAX BUY</b> | {symbol}", with_keyboard=False)

