import logging
import time
import numpy as np
import pandas as pd
import datetime as dt
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger("VWAPTrendExpansionEngine")

class VWAPTrendExpansionEngine:
    """
    Institutional VWAP Trend Expansion Engine (Magic: 2005)
    """
    MAGIC = 2005

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0
        self.cooldowns = {}

    async def _get_h1_trend_bias(self, client, symbol: str) -> str:
        try:
            h1_bars = await client.fetch_ohlcv(symbol, '1h', 200)
            if not h1_bars or len(h1_bars) < 200: return "NEUTRAL"
            closes = [b[4] for b in h1_bars]
            ema_200 = sum(closes[-200:]) / 200
            current_price = closes[-1]
            
            if current_price > ema_200 * 1.001: return "BULLISH"
            if current_price < ema_200 * 0.999: return "BEARISH"
            return "NEUTRAL"
        except: return "NEUTRAL"

    async def manage(self):
        if not getattr(cfg, 'VWAP_TREND_EXPANSION_ENABLED', True): 
            return
        
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): 
            return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(20, 15)
            self._last_news_check = time.time()
        if self._news_block: 
            return

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: 
            return

        for symbol in ["XAUUSDm", "BTCUSDm", "ETHUSDm", "GBPJPYm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): 
                continue
            if time.time() < self.cooldowns.get(symbol, 0): 
                continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): 
                continue

            bars = await client.fetch_ohlcv(symbol, '15m', 200)
            if not bars or len(bars) < 60: 
                continue

            # True Session-Anchored VWAP
            last_bar_time_ms = bars[-1][0]
            last_bar_dt = dt.datetime.fromtimestamp(last_bar_time_ms / 1000.0, tz=dt.timezone.utc)
            start_of_day_ms = int(dt.datetime(last_bar_dt.year, last_bar_dt.month, last_bar_dt.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
            
            session_bars = [b for b in bars if b[0] >= start_of_day_ms]
            if len(session_bars) < 15: 
                session_bars = bars[-100:]

            df = pd.DataFrame(session_bars, columns=['t','o','h','l','c','v'])
            tp = (df['h'] + df['l'] + df['c']) / 3.0
            vol = df['v'].replace(0, 1.0)
            
            cumulative_tp_vol = np.cumsum(tp * vol)
            cumulative_vol = np.cumsum(vol)
            vwap_series = cumulative_tp_vol / cumulative_vol
            vwap = vwap_series.iloc[-1]

            variance = np.sum(vol * np.square(tp - vwap)) / np.sum(vol)
            std = np.sqrt(variance) if variance > 0 else (vwap * 0.001)

            upper_band = vwap + (1.5 * std)
            lower_band = vwap - (1.5 * std)

            prev = df.iloc[-2]
            curr_close = prev['c']
            # [FIXED BUG] Typo indeks kolom
            prev_low, prev_high = prev['l'], prev['h']
            
            avg_vol = df['v'].iloc[-25:-1].mean()
            is_volume_spike = prev['v'] >= (avg_vol * 1.8)
            if not is_volume_spike: 
                continue

            h1_trend = await self._get_h1_trend_bias(client, symbol)

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: 
                continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)

            # BUY SETUP
            if h1_trend != "BEARISH" and curr_close > upper_band and prev['c'] > prev['o']:
                sl = round(vwap, digits)
                risk = tick['ask'] - sl
                tp = round(tick['ask'] + (risk * 3.0), digits)
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 1200
                        await self.telegram.send(
                            f" <b>VWAP TREND EXPANSION BUY</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['ask']:.3f}\n"
                            f"Breakout Upper Band + Vol Surge | H1: {h1_trend}\n"
                            f"SL: {sl} | TP: {tp} (R:R 1:3.0)", with_keyboard=False
                        )

            # SELL SETUP
            elif h1_trend != "BULLISH" and curr_close < lower_band and prev['c'] < prev['o']:
                sl = round(vwap, digits)
                risk = sl - tick['bid']
                tp = round(tick['bid'] - (risk * 3.0), digits)
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 1200
                        await self.telegram.send(
                            f" <b>VWAP TREND EXPANSION SELL</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['bid']:.3f}\n"
                            f"Breakout Lower Band + Vol Surge | H1: {h1_trend}\n"
                            f"SL: {sl} | TP: {tp} (R:R 1:3.0)", with_keyboard=False
                        )
