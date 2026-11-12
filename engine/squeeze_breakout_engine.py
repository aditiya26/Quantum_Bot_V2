import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class SqueezeBreakoutEngine:
    MAGIC = 1001

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0

    async def manage(self):
        if not getattr(cfg, 'SQUEEZE_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client: return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(15, 10)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in getattr(cfg, 'SQUEEZE_SYMBOLS', cfg.SYMBOLS):
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            
            mt5_pos = await client.fetch_positions()
            positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == self.MAGIC] if mt5_pos else []
            if len(positions) > 0: continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            m15 = await client.fetch_ohlcv(symbol, '15m', 50)
            if not m15 or len(m15) < 30: continue

            df = pd.DataFrame(m15, columns=['t','o','h','l','c','v'])
            c = df['c']
            
            sma20 = c.rolling(20).mean()
            std20 = c.rolling(20).std()
            bb_upper = sma20 + (2.0 * std20)
            bb_lower = sma20 - (2.0 * std20)

            tr = np.maximum(df['h'] - df['l'], np.maximum(abs(df['h'] - c.shift()), abs(df['l'] - c.shift())))
            atr = tr.rolling(20).mean()
            kc_upper = sma20 + (1.5 * atr)
            kc_lower = sma20 - (1.5 * atr)

            is_squeezed = (bb_lower.iloc[-2] > kc_lower.iloc[-2]) and (bb_upper.iloc[-2] < kc_upper.iloc[-2])
            momentum = c.iloc[-2] - sma20.iloc[-2]
            last_close = c.iloc[-2]
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            if is_squeezed and last_close > bb_upper.iloc[-2] and momentum > 0:
                sl = sma20.iloc[-1]
                risk = abs(last_close - sl)
                tp = last_close + (risk * 3.0)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(
                        f"💥 <b>SQUEEZE BREAKOUT BUY</b> {symbol} @ {last_close:.2f}\n"
                        f"SL: {sl:.2f} | TP: {tp:.2f} (R:R 1:3.0)", with_keyboard=False
                    )

            elif is_squeezed and last_close < bb_lower.iloc[-2] and momentum < 0:
                sl = sma20.iloc[-1]
                risk = abs(sl - last_close)
                tp = last_close - (risk * 3.0)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(
                        f"💥 <b>SQUEEZE BREAKOUT SELL</b> {symbol} @ {last_close:.2f}\n"
                        f"SL: {sl:.2f} | TP: {tp:.2f} (R:R 1:3.0)", with_keyboard=False
                    )
