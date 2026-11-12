import logging
import numpy as np
import config.settings as cfg
import datetime as dt

logger = logging.getLogger("VWAP_Trend_Riding")

class VWAPTrendRidingEngine:
    MAGIC = 4001 # Magic baru khusus Trend Riding

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"

    async def manage(self):
        if not getattr(cfg, 'VWAP_TREND_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue

            # Ambil data M15
            bars = await client.fetch_ohlcv(symbol, '15m', 96)
            if not bars or len(bars) < 50: continue

            # Hitung Anchored VWAP M15
            now_utc = dt.datetime.now(dt.timezone.utc)
            start_of_day_ms = int(dt.datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
            session_bars = [b for b in bars if b[0] >= start_of_day_ms]
            if len(session_bars) < 10: session_bars = bars[-50:]

            tp_arr = np.array([(b[2] + b[3] + b[4]) / 3.0 for b in session_bars])
            vols = np.array([max(b[5], 1.0) for b in session_bars])
            
            vwap = np.sum(tp_arr * vols) / np.sum(vols)
            std = np.sqrt(np.sum(vols * np.square(tp_arr - vwap)) / np.sum(vols))
            
            upper_band = vwap + (2.0 * std)
            lower_band = vwap - (2.0 * std)

            prev, older = bars[-2], bars[-3]
            curr_close = prev[4]
            lot = min(cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02), 0.03)
            digits = 2 if 'XAU' in symbol or 'BTC' in symbol else (3 if 'JPY' in symbol else 4)

            # [FILTER] Volume Spike 1.5x
            avg_vol = np.mean(vols[-20:])
            vol_spike = prev[5] >= (avg_vol * 1.5)
            if not vol_spike: continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            # BREAKOUT BUY (Harga tembus Upper Band dengan volume)
            if curr_close > upper_band and older[4] < upper_band:
                sl = round(vwap, digits) # SL kembali ke VWAP
                risk = tick['ask'] - sl
                tp = round(tick['ask'] + (risk * 2.5), digits)
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>VWAP TREND RIDE BUY</b>\\n{symbol} | Breakout Upper Band", with_keyboard=False)

            # BREAKOUT SELL (Harga tembus Lower Band dengan volume)
            elif curr_close < lower_band and older[4] > lower_band:
                sl = round(vwap, digits)
                risk = sl - tick['bid']
                tp = round(tick['bid'] - (risk * 2.5), digits)
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(f" <b>VWAP TREND RIDE SELL</b>\\n{symbol} | Breakout Lower Band", with_keyboard=False)
