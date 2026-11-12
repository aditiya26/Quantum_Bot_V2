import logging
import time
import pandas as pd
import datetime as dt
import config.settings as cfg
from monitor.news_filter import NewsFilter
from engine.institutional_orchestrator import InstitutionalPreventiveShield

logger = logging.getLogger("VWORBEngine")

class VWORBEngine:
    """
    Institutional VW-ORB Engine (Magic: 2006):
    Volume-Weighted Opening Range Breakout pada pembukaan sesi London & NY.
    """
    MAGIC = 2006

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0
        self.executed_today = {}

    async def manage(self):
        if not getattr(cfg, 'VW_ORB_ENABLED', True):
            return

        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False):
            return

        # 1. Filter Jam Sesi (London Opening: 14:00 WIB / NY Opening: 20:30 WIB)
        now_local = dt.datetime.now(cfg.LOCAL_TZ)
        hour, minute = now_local.hour, now_local.minute
        
        # Jendela waktu pemindaian ORB
        is_london_orb = (hour == 14 and minute >= 15) or (hour == 15 and minute <= 30)
        is_ny_orb = (hour == 20 and minute >= 45) or (hour == 21)
        
        if not (is_london_orb or is_ny_orb):
            return

        # 2. News Filter Guard
        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(20, 15)
            self._last_news_check = time.time()
        if self._news_block:
            return

        # 3. Inisialisasi Preventive Shield
        shield = InstitutionalPreventiveShield(client)

        for symbol in ["XAUUSDm", "GBPJPYm", "BTCUSDm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True):
                continue

            date_key = f"{symbol}_{now_local.strftime('%Y%m%d')}_{'LDN' if is_london_orb else 'NY'}"
            if self.executed_today.get(date_key, False):
                continue

            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []):
                continue
                
            # 4. Validasi Shield (Volatilitas & Spread)
            if not await shield.validate_market_safety(symbol):
                continue

            # Ambil data M5
            bars = await client.fetch_ohlcv(symbol, '5m', 40)
            if not bars or len(bars) < 20:
                continue

            df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
            
            # [FIX] Ambil 3 candle M5 sebelum candle trigger (-2)
            or_candles = df.iloc[-5:-2] 
            or_high = or_candles['h'].max()
            or_low = or_candles['l'].min()
            or_range = or_high - or_low

            last_bar = df.iloc[-2] # Candle M5 yang baru saja tertutup (Trigger)
            
            # [FIX] Rata-rata volume 20 candle sebelum trigger
            avg_vol = df['v'].iloc[-22:-2].mean()
            
            # Volume Surge Validation (> 2.0x Volume Rata-rata)
            is_vol_surge = last_bar['v'] >= (avg_vol * 2.0)
            if not is_vol_surge:
                continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0:
                continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)
            digits = 2 if 'XAU' in symbol or 'BTC' in symbol else (3 if 'JPY' in symbol else 5)

            # --- BULLISH VW-ORB BREAKOUT ---
            if last_bar['c'] > or_high and last_bar['c'] > last_bar['o']:
                sl = round(or_low, digits)
                risk = tick['ask'] - sl
                tp = round(tick['ask'] + (risk * 2.5), digits)

                if risk > 0 and (tp - tick['ask']) / risk >= 2.0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.executed_today[date_key] = True
                        await self.telegram.send(
                            f" <b>VW-ORB BULLISH BREAKOUT</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['ask']:.3f}\n"
                            f"OR High: {or_high:.2f} | Vol Surge: {last_bar['v']/avg_vol:.1f}x\n"
                            f"SL: {sl} | TP: {tp} (R:R 1:2.5)",
                            with_keyboard=False
                        )

            # --- BEARISH VW-ORB BREAKOUT ---
            elif last_bar['c'] < or_low and last_bar['c'] < last_bar['o']:
                sl = round(or_high, digits)
                risk = sl - tick['bid']
                tp = round(tick['bid'] - (risk * 2.5), digits)

                if risk > 0 and (tick['bid'] - tp) / risk >= 2.0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.executed_today[date_key] = True
                        await self.telegram.send(
                            f" <b>VW-ORB BEARISH BREAKOUT</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['bid']:.3f}\n"
                            f"OR Low: {or_low:.2f} | Vol Surge: {last_bar['v']/avg_vol:.1f}x\n"
                            f"SL: {sl} | TP: {tp} (R:R 1:2.5)",
                            with_keyboard=False
                        )
