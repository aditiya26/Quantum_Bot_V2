import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter
from datetime import datetime

logger = logging.getLogger(__name__)

class LiquiditySweepEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = 'exness_mt5'; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    def _stoch_rsi(self, c, p=14, k=3, d=3):
        delta = pd.Series(c).diff()
        up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
        ema_up = up.ewm(alpha=1/p, adjust=False).mean()
        ema_down = down.ewm(alpha=1/p, adjust=False).mean()
        rs = ema_up / ema_down
        rsi = 100 - (100 / (1 + rs))
        stoch = (rsi - rsi.rolling(p).min()) / (rsi.rolling(p).max() - rsi.rolling(p).min())
        stoch_k = stoch.rolling(k).mean() * 100
        stoch_d = stoch_k.rolling(d).mean()
        return stoch_k.values, stoch_d.values

    async def manage(self):
        if not getattr(cfg, 'SWEEP_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in ["XAUUSDm", "GBPJPYm"]:
            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: continue
            existing = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.SWEEP_MAGIC]
            if existing: continue

            h1 = await client.fetch_ohlcv(symbol, '1h', 150)
            m15 = await client.fetch_ohlcv(symbol, '15m', 80)
            if not h1 or not m15 or len(m15) < 30: continue

            df_h1 = pd.DataFrame(h1, columns=['t','o','h','l','c','v'])
            df_m15 = pd.DataFrame(m15, columns=['t','o','h','l','c','v'])
            
            ema50 = df_h1['c'].ewm(span=50, adjust=False).mean().iloc[-1]
            ema200 = df_h1['c'].ewm(span=200, adjust=False).mean().iloc[-1]
            is_uptrend = df_h1['c'].iloc[-1] > ema50 and ema50 > ema200
            is_downtrend = df_h1['c'].iloc[-1] < ema50 and ema50 < ema200

            df_m15['dt'] = pd.to_datetime(df_m15['t'], unit='ms')
            asia = df_m15[(df_m15['dt'].dt.hour >= 7) & (df_m15['dt'].dt.hour <= 13)]
            if len(asia) < 4: continue
            asia_high = asia['h'].max()
            asia_low = asia['l'].min()

            last = df_m15.iloc[-1]
            c_closes = df_m15['c'].values
            stoch_k, stoch_d = self._stoch_rsi(c_closes)
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # Skenario BUY: Sweep Asia Low di Uptrend
            if is_uptrend and last['l'] < asia_low and last['c'] > asia_low:
                if stoch_k[-1] > 20 and stoch_d[-1] > 20:
                    sl = last['l'] - (last['c'] * 0.0015)
                    risk = abs(last['c'] - sl)
                    tp = last['c'] + (risk * 3.5) # RRR 1:3.5
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=cfg.SWEEP_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f"🌊 <b>LIQUIDITY SWEEP BUY</b> {symbol}\nSapu Asia Low @ {last['c']:.3f}\nSL: {sl:.3f} | TP: {tp:.3f} (R:R 1:3.5)", with_keyboard=False)

            # Skenario SELL: Sweep Asia High di Downtrend
            elif is_downtrend and last['h'] > asia_high and last['c'] < asia_high:
                if stoch_k[-1] < 80 and stoch_d[-1] < 80:
                    sl = last['h'] + (last['c'] * 0.0015)
                    risk = abs(sl - last['c'])
                    tp = last['c'] - (risk * 3.5) # RRR 1:3.5
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=cfg.SWEEP_MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f"🌊 <b>LIQUIDITY SWEEP SELL</b> {symbol}\nSapu Asia High @ {last['c']:.3f}\nSL: {sl:.3f} | TP: {tp:.3f} (R:R 1:3.5)", with_keyboard=False)
