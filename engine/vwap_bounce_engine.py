import logging
import numpy as np
import config.settings as cfg
import datetime as dt

logger = logging.getLogger("VWAP_Optimized")

class VWAPDynamicBounceEngine:
    MAGIC = 1011

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"

    async def get_h1_trend(self, client, symbol):
        """[POIN 3] Multi-Timeframe Confluence (EMA 200 H1)"""
        try:
            h1_bars = await client.fetch_ohlcv(symbol, '1h', 200)
            if not h1_bars or len(h1_bars) < 200: return "NEUTRAL"
            closes = [b[4] for b in h1_bars]
            ema_200 = sum(closes[-200:]) / 200
            current_price = closes[-1]
            
            if current_price > ema_200 * 1.001: return "BULLISH"
            if current_price < ema_200 * 0.999: return "BEARISH"
            return "NEUTRAL"
        except:
            return "NEUTRAL"

    async def manage(self):
        if not getattr(cfg, 'VWAP_BOUNCE_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos): continue

            # Ambil data M5 (300 bar untuk memastikan cukup data sesi harian)
            bars = await client.fetch_ohlcv(symbol, '5m', 300)
            if not bars or len(bars) < 60: continue

            # [POIN 1] TRUE SESSION-ANCHORED VWAP (Berbasis Waktu Server MT5)
            last_bar_time_ms = bars[-1][0]
            last_bar_dt = dt.datetime.fromtimestamp(last_bar_time_ms / 1000.0, tz=dt.timezone.utc)
            
            # Warm-up Period: Jangan trading jika sesi baru berjalan kurang dari 1 jam
            now_server_dt = dt.datetime.now(dt.timezone.utc)
            if now_server_dt.hour < 1:
                continue
                
            start_of_day_ms = int(dt.datetime(last_bar_dt.year, last_bar_dt.month, last_bar_dt.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
            
            session_bars = [b for b in bars if b[0] >= start_of_day_ms]
            if len(session_bars) < 10: 
                session_bars = bars[-100:] # Fallback jika data sesi belum cukup

            tp_arr = np.array([(b[2] + b[3] + b[4]) / 3.0 for b in session_bars])
            vols = np.array([max(b[5], 1.0) for b in session_bars])
            
            cumulative_tp_vol = np.cumsum(tp_arr * vols)
            cumulative_vol = np.cumsum(vols)
            vwap_series = cumulative_tp_vol / cumulative_vol
            vwap = vwap_series[-1]

            # Standar Deviasi untuk Band Institusional
            variance = np.sum(vols * np.square(tp_arr - vwap)) / np.sum(vols)
            std = np.sqrt(variance) if variance > 0 else (vwap * 0.0015)

            upper_band = vwap + (1.0 * std)
            lower_band = vwap - (1.0 * std)

            # Evaluasi bar tertutup sepenuhnya
            prev = bars[-2]
            curr_close = prev[4]
            prev_low, prev_high = prev[3], prev[2]
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            digits = 2 if 'XAU' in symbol or 'BTC' in symbol else (3 if 'JPY' in symbol else 4)

            # [POIN 2] FILTER VOLUME SURGE (1.5x rata-rata 20 bar)
            avg_vol = np.mean(vols[-25:-1])
            is_volume_spike = prev[5] >= (avg_vol * 1.5)
            if not is_volume_spike: continue

            # [POIN 3] CEK TREN H1
            h1_trend = await self.get_h1_trend(client, symbol)
            
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            # [POIN 4] WIDER STOP LOSS + RRR 1:2.2
            # --- SKENARIO BUY BOUNCE ---
            if h1_trend != "BEARISH" and prev_low <= lower_band and curr_close > lower_band:
                sl = round(prev_low - (std * 0.4), digits) 
                tp = round(curr_close + ((curr_close - sl) * 2.2), digits)
                
                risk = tick['ask'] - sl
                reward = tp - tick['ask']
                
                if risk > 0 and (reward / risk) >= 2.0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(
                            f" <b>VWAP OPTIMIZED BUY</b>\n"
                            f"{symbol} | VWAP: {vwap:.2f}\n"
                            f"Vol Spike + H1 {h1_trend}\n"
                            f"RRR 1:{(reward/risk):.1f}", with_keyboard=False
                        )

            # --- SKENARIO SELL BOUNCE ---
            elif h1_trend != "BULLISH" and prev_high >= upper_band and curr_close < upper_band:
                sl = round(prev_high + (std * 0.4), digits)
                tp = round(curr_close - ((sl - curr_close) * 2.2), digits)
                
                risk = sl - tick['bid']
                reward = tick['bid'] - tp
                
                if risk > 0 and (reward / risk) >= 2.0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        await self.telegram.send(
                            f" <b>VWAP OPTIMIZED SELL</b>\n"
                            f"{symbol} | VWAP: {vwap:.2f}\n"
                            f"Vol Spike + H1 {h1_trend}\n"
                            f"RRR 1:{(reward/risk):.1f}", with_keyboard=False
                        )
