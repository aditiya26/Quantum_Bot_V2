import logging, time
import numpy as np
import pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class ZScoreMeanReversionEngine:
    """
    Institutional Z-Score Mean Reversion Engine:
    Mendeteksi titik jenuh beli (Overbought Z >= +2.2) dan jenuh jual (Oversold Z <= -2.2) 
    secara statistik HANYA saat kondisi pasar Sideways (ADX < 25).
    """
    MAGIC = 1009

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0

    def _calculate_adx(self, df, period=14):
        high, low, close = df['h'].values, df['l'].values, df['c'].values
        tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]
        
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0) # FIXED TYPO
        
        tr_smooth = pd.Series(tr).ewm(span=period, adjust=False).mean().values
        pos_di = 100 * (pd.Series(pos_dm).ewm(span=period, adjust=False).mean().values / (tr_smooth + 1e-9))
        neg_di = 100 * (pd.Series(neg_dm).ewm(span=period, adjust=False).mean().values / (tr_smooth + 1e-9))
        
        dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9))
        adx = pd.Series(dx).ewm(span=period, adjust=False).mean().iloc[-1]
        return adx

    async def manage(self):
        if not getattr(cfg, 'ZSCORE_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue

            bars = await client.fetch_ohlcv(symbol, '15m', 50)
            if not bars or len(bars) < 35: continue

            df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
            
            # Guard 1: Filter ADX (Wajib SIDEWAYS, ADX < 25)
            adx_val = self._calculate_adx(df, 14)
            if adx_val > 25.0:
                continue

            closes = df['c'].values
            mean = float(np.mean(closes[-20:]))
            std = float(np.std(closes[-20:]))
            if std == 0: continue

            last_bar = df.iloc[-2]
            curr_p = last_bar['c']
            z_score = (curr_p - mean) / std
            
            body = abs(last_bar['c'] - last_bar['o'])
            lower_wick = min(last_bar['o'], last_bar['c']) - last_bar['l']
            upper_wick = last_bar['h'] - max(last_bar['o'], last_bar['c'])
            
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            
            # Perbaikan Desimal Presisi
            if 'JPY' in symbol: digits = 3
            elif 'XAU' in symbol or 'BTC' in symbol or 'ETH' in symbol: digits = 2
            elif 'USTEC' in symbol: digits = 1
            else: digits = 5

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            # SKENARIO OVERSOLD BUY (Z <= -2.2 + Rejection Wick + ADX Sideways)
            if z_score <= -2.2 and lower_wick > (body * 1.2):
                sl = round(last_bar['l'] - (std * 0.5), digits)
                tp = round(mean, digits) # Target ke Rata-rata Statistik
                
                risk = tick['ask'] - sl
                reward = tp - tick['ask']
                if risk > 0 and (reward / risk) >= 1.8:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>QUANT Z-SCORE BUY</b> {symbol}\nZ-Score: {z_score:.2f} | ADX: {adx_val:.1f}", with_keyboard=False)

            # SKENARIO OVERBOUGHT SELL (Z >= +2.2 + Rejection Wick + ADX Sideways)
            elif z_score >= 2.2 and upper_wick > (body * 1.2):
                sl = round(last_bar['h'] + (std * 0.5), digits)
                tp = round(mean, digits)
                
                risk = sl - tick['bid']
                reward = tick['bid'] - tp
                if risk > 0 and (reward / risk) >= 1.8:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>QUANT Z-SCORE SELL</b> {symbol}\nZ-Score: {z_score:.2f} | ADX: {adx_val:.1f}", with_keyboard=False)
