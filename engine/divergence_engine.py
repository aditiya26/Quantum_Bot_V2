import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class DivergenceEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = 'exness_mt5'; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    def _rsi(self, c, p=14):
        d = np.diff(c); u = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
        ru = pd.Series(u).ewm(alpha=1/p, adjust=False).mean().values
        rd = pd.Series(dn).ewm(alpha=1/p, adjust=False).mean().values
        rs = np.divide(ru, rd, out=np.zeros_like(ru), where=rd != 0)
        return np.concatenate([[np.nan], 100 - 100 / (1 + rs)])

    async def manage(self):
        if not getattr(cfg, 'DIVERGENCE_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in ["XAUUSDm", "BTCUSDm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: continue
            existing = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.DIVERGENCE_MAGIC]
            if existing: continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            m15 = await client.fetch_ohlcv(symbol, '15m', 50)
            if not m15 or len(m15) < 30: continue
            df = pd.DataFrame(m15, columns=['t','o','h','l','c','v'])
            c = df['c'].values
            rsi = self._rsi(c, 14)
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # Hidden Bullish: Harga Higher Low, RSI Lower Low
            if df['l'].iloc[-2] > df['l'].iloc[-11] and rsi[-2] < rsi[-11] and rsi[-2] < 42:
                sl = df['l'].iloc[-2] - (tick['ask'] * 0.0015)
                risk = abs(tick['ask'] - sl)
                tp = tick['ask'] + (risk * 2.5)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=cfg.DIVERGENCE_MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(f"🔄 <b>HIDDEN DIVERGENCE BUY</b> {symbol}\nRSI Oversold + Higher Low\nR:R 1:2.5", with_keyboard=False)

            # Hidden Bearish: Harga Lower High, RSI Higher High
            elif df['h'].iloc[-2] < df['h'].iloc[-11] and rsi[-2] > rsi[-11] and rsi[-2] > 58:
                sl = df['h'].iloc[-2] + (tick['ask'] * 0.0015)
                risk = abs(sl - tick['bid'])
                tp = tick['bid'] - (risk * 2.5)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=cfg.DIVERGENCE_MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(f"🔄 <b>HIDDEN DIVERGENCE SELL</b> {symbol}\nRSI Overbought + Lower High\nR:R 1:2.5", with_keyboard=False)
