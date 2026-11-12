import logging, time, pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter
from datetime import datetime

logger = logging.getLogger(__name__)

class LondonBreakoutEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = "exness_mt5"; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    async def manage(self):
        if not getattr(cfg, 'LONDON_BREAKOUT_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return
        
        now = datetime.now(cfg.LOCAL_TZ)
        if not (14 <= now.hour < 19): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in getattr(cfg, 'LONDON_BREAKOUT_SYMBOLS', []):
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            try:
                mt5_pos = await client.fetch_positions()
                if mt5_pos is None: continue
                positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.LONDON_BREAKOUT_MAGIC]
                if len(positions) > 0: continue

                tick = await client.exchange.fetch_ticker(symbol)
                if not tick or tick['ask'] <= 0: continue
                if (tick['ask'] - tick['bid']) / tick['ask'] * 100 > getattr(cfg, 'GLOBAL_MAX_SPREAD_PCT', 0.09): continue

                h1 = await client.fetch_ohlcv(symbol, '1h', 24)
                if not h1 or len(h1) < 12: continue
                
                df = pd.DataFrame(h1, columns=['t','o','h','l','c','v'])
                df['dt'] = pd.to_datetime(df['t'], unit='ms')
                
                asia = df[(df['dt'].dt.hour >= 0) & (df['dt'].dt.hour <= 7)]
                if len(asia) < 5: continue

                asia_high = asia['h'].max()
                asia_low = asia['l'].min()
                asia_range = asia_high - asia_low

                if (asia_range / tick['ask']) > 0.015: continue
                current_price = tick['ask']
                lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

                if current_price > asia_high and tick['bid'] < (asia_high + asia_range * 0.3):
                    sl = max(asia_low, current_price - (asia_range * 0.5))
                    risk = abs(current_price - sl)
                    tp = current_price + (risk * 3.0)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=cfg.LONDON_BREAKOUT_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f"🇬🇧 <b>LONDON BREAKOUT BUY</b> {symbol} @ {current_price:.2f}\nSL: {sl:.2f} | TP: {tp:.2f} (R:R 1:3.0)", with_keyboard=False)

                elif current_price < asia_low and tick['ask'] > (asia_low - asia_range * 0.3):
                    sl = min(asia_high, current_price + (asia_range * 0.5))
                    risk = abs(sl - current_price)
                    tp = current_price - (risk * 3.0)
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=cfg.LONDON_BREAKOUT_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f"🇬🇧 <b>LONDON BREAKOUT SELL</b> {symbol} @ {current_price:.2f}\nSL: {sl:.2f} | TP: {tp:.2f} (R:R 1:3.0)", with_keyboard=False)
            except Exception as e:
                logger.error(f"London Breakout {symbol}: {e}")
