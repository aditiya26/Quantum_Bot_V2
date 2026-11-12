import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class EdgeEngine:
    """
    Institutional Micro-Scalper Engine:
    Mengkombinasikan RSI Extreme Rejection + Volume Surge + H1 Trend Alignment + ATR Dynamic SL/TP.
    """
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = "exness_mt5"; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    def _calculate_atr(self, df, period=14):
        tr = np.maximum(df['h'] - df['l'], np.maximum(np.abs(df['h'] - df['c'].shift()), np.abs(df['l'] - df['c'].shift())))
        return tr.ewm(span=period, adjust=False).mean()

    def _rsi(self, c, p=14):
        d = np.diff(c); u = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
        ru = pd.Series(u).ewm(alpha=1/p, adjust=False).mean().values
        rd = pd.Series(dn).ewm(alpha=1/p, adjust=False).mean().values
        rs = np.divide(ru, rd, out=np.zeros_like(ru), where=rd != 0)
        return np.concatenate([[np.nan], 100 - 100 / (1 + rs)])

    async def _check_h1_trend(self, client, symbol):
        h1 = await client.fetch_ohlcv(symbol, '1h', 210)
        if not h1 or len(h1) < 200: return True, True
        closes = np.array([c[4] for c in h1])
        ema200 = pd.Series(closes).ewm(span=200, adjust=False).mean().iloc[-1]
        return closes[-1] > ema200, closes[-1] < ema200

    async def manage(self):
        if not getattr(cfg, 'EDGE_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in getattr(cfg, 'EDGE_SYMBOLS', cfg.SYMBOLS):
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == cfg.EDGE_MAGIC for p in mt5_pos or []): continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            # Guard 1: Filter Spread Maksimal (0.05% dari harga)
            spread = tick['ask'] - tick['bid']
            if spread > (tick['ask'] * 0.0005): continue

            ohlcv = await client.fetch_ohlcv(symbol, '5m', 60)
            if not ohlcv or len(ohlcv) < 40: continue
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            
            df['atr'] = self._calculate_atr(df, 14)
            current_atr = df['atr'].iloc[-1]

            c, o, h, l, v = df['c'].values, df['o'].values, df['h'].values, df['l'].values, df['v'].values
            rsi = self._rsi(c, 14)[-2]
            
            body = abs(c[-2] - o[-2])
            avg_vol = np.mean(v[-21:-1])
            vol_surge = v[-2] >= (avg_vol * 1.5)
            
            # Guard 2: MTF Alignment (H1 Trend)
            h1_bull, h1_bear = await self._check_h1_trend(client, symbol)
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            
            # Perbaikan Desimal Presisi
            if 'JPY' in symbol: digits = 3
            elif 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol or 'ETH' in symbol: digits = 2
            else: digits = 5

            # BUY TRIGGER: RSI Oversold (< 28) + Lower Wick Rejection + Volume Surge + H1 Bullish
            if rsi < 28 and (min(o[-2], c[-2]) - l[-2]) > (body * 1.3) and vol_surge and h1_bull:
                sl = round(l[-2] - (current_atr * 0.5), digits)
                risk = tick['ask'] - sl
                tp = round(tick['ask'] + (risk * 2.2), digits) # RRR 1:2.2
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=cfg.EDGE_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>QUANT EDGE BUY</b> {symbol} @ {tick['ask']:.2f}\nR:R 1:2.2 | ATR SL: {sl:.2f}", with_keyboard=False)

            # SELL TRIGGER: RSI Overbought (> 72) + Upper Wick Rejection + Volume Surge + H1 Bearish
            elif rsi > 72 and (h[-2] - max(o[-2], c[-2])) > (body * 1.3) and vol_surge and h1_bear:
                sl = round(h[-2] + (current_atr * 0.5), digits)
                risk = sl - tick['bid']
                tp = round(tick['bid'] - (risk * 2.2), digits)
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=cfg.EDGE_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>QUANT EDGE SELL</b> {symbol} @ {tick['bid']:.2f}\nR:R 1:2.2 | ATR SL: {sl:.2f}", with_keyboard=False)
