import logging, time, pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter
from datetime import datetime

logger = logging.getLogger(__name__)

class NYReversalEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = 'exness_mt5'; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    async def manage(self):
        if not getattr(cfg, 'NY_REVERSAL_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        now = datetime.now(cfg.LOCAL_TZ)
        if not (now.hour == 20 and now.minute >= 30) and not (now.hour == 21): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in ["XAUUSDm", "GBPJPYm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: continue
            existing = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.NY_REVERSAL_MAGIC]
            if existing: continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            m5 = await client.fetch_ohlcv(symbol, '5m', 25)
            if not m5 or len(m5) < 20: continue
            df = pd.DataFrame(m5, columns=['t','o','h','l','c','v'])
            last = df.iloc[-2]
            recent_high = df['h'].iloc[-12:-1].max()
            recent_low = df['l'].iloc[-12:-1].min()
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # Fake breakdown -> BUY
            if last['l'] < recent_low and last['c'] > recent_low and last['c'] > last['o']:
                sl = last['l'] - (tick['ask'] * 0.003)
                risk = abs(tick['ask'] - sl)
                tp = tick['ask'] + (risk * 2.8)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=cfg.NY_REVERSAL_MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(f"🗽 <b>NY REVERSAL BUY</b> {symbol} (Fake Breakdown) @ {tick['ask']:.2f}\nR:R 1:2.8", with_keyboard=False)

            # Fake breakout -> SELL
            elif last['h'] > recent_high and last['c'] < recent_high and last['c'] < last['o']:
                sl = last['h'] + (tick['ask'] * 0.003)
                risk = abs(sl - tick['bid'])
                tp = tick['bid'] - (risk * 2.8)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=cfg.NY_REVERSAL_MAGIC, sl=sl, tp=tp)
                if order:
                    await self.telegram.send(f"🗽 <b>NY REVERSAL SELL</b> {symbol} (Fake Breakout) @ {tick['bid']:.2f}\nR:R 1:2.8", with_keyboard=False)
