import logging, time, pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class HybridEngine:
    MAGIC = 222

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0
        self.cooldowns = {} # [FITUR BARU] Mencegah spam entry

    async def manage(self):
        if not getattr(cfg, 'HYBRID_ENABLED', True): return
        
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(25, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in getattr(cfg, 'HYBRID_SYMBOLS', cfg.SYMBOLS):
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue

            # [FITUR BARU] Cek Cooldown (Jeda 15 menit setelah entry)
            if time.time() < self.cooldowns.get(symbol, 0): continue

            try:
                mt5_pos = await client.fetch_positions()
                if mt5_pos is None: continue
                positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == self.MAGIC]
                if len(positions) > 0: continue

                tick = await client.exchange.fetch_ticker(symbol)
                if not tick or tick['ask'] <= 0 or tick['bid'] <= 0: continue
                
                spread_pct = (tick['ask'] - tick['bid']) / tick['ask'] * 100
                if spread_pct > getattr(cfg, 'GLOBAL_MAX_SPREAD_PCT', 0.09): continue

                h1_data = await client.fetch_ohlcv(symbol, '1h', 100)
                m15_data = await client.fetch_ohlcv(symbol, '15m', 60)
                if not h1_data or not m15_data or len(h1_data) < 50 or len(m15_data) < 30: continue

                df_h1 = pd.DataFrame(h1_data, columns=['t','o','h','l','c','v'])
                df_m15 = pd.DataFrame(m15_data, columns=['t','o','h','l','c','v'])

                ema50_h1 = df_h1['c'].ewm(span=50, adjust=False).mean().iloc[-1]
                ema200_h1 = df_h1['c'].ewm(span=200, adjust=False).mean().iloc[-1]
                h1_bullish = ema50_h1 > ema200_h1
                h1_bearish = ema50_h1 < ema200_h1

                ema21_m15 = df_m15['c'].ewm(span=21, adjust=False).mean().iloc[-1]
                
                tr = pd.concat([
                    df_m15['h'] - df_m15['l'], 
                    (df_m15['h'] - df_m15['c'].shift()).abs(), 
                    (df_m15['l'] - df_m15['c'].shift()).abs()
                ], axis=1).max(axis=1)
                atr14 = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
                buffer = 0.2 * atr14

                last_bar = df_m15.iloc[-2]
                curr_price = tick['ask'] if h1_bullish else tick['bid']
                lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)

                digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)

                # --- HYBRID BUY SETUP ---
                if h1_bullish and last_bar['l'] <= (ema21_m15 + buffer) and last_bar['c'] > last_bar['o']:
                    # [FITUR BARU] Paksa jarak SL minimal (Mencegah kena spread)
                    min_sl_dist = 2.0 if 'XAU' in symbol else (200.0 if 'BTC' in symbol else 0.005)
                    calc_sl = last_bar['l'] - (atr14 * 0.8)
                    sl = round(min(calc_sl, curr_price - min_sl_dist), digits) 
                    
                    risk = curr_price - sl
                    tp = round(curr_price + (risk * 2.8), digits)

                    if risk > 0:
                        async with self.order_lock.get(symbol):
                            order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                        if order:
                            self.cooldowns[symbol] = time.time() + 900 # Jeda 15 menit
                            await self.telegram.send(
                                f" <b>HYBRID TREND BUY</b> #{order.get('ticket')}\n"
                                f"Simbol: <b>{symbol}</b> @ {curr_price:.3f}\n"
                                f"SL: {sl} | TP: {tp} (R:R 1:2.8)", 
                                with_keyboard=False
                            )

                # --- HYBRID SELL SETUP ---
                elif h1_bearish and last_bar['h'] >= (ema21_m15 - buffer) and last_bar['c'] < last_bar['o']:
                    min_sl_dist = 2.0 if 'XAU' in symbol else (200.0 if 'BTC' in symbol else 0.005)
                    calc_sl = last_bar['h'] + (atr14 * 0.8)
                    sl = round(max(calc_sl, curr_price + min_sl_dist), digits)
                    
                    risk = sl - curr_price
                    tp = round(curr_price - (risk * 2.8), digits)

                    if risk > 0:
                        async with self.order_lock.get(symbol):
                            order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                        if order:
                            self.cooldowns[symbol] = time.time() + 900 # Jeda 15 menit
                            await self.telegram.send(
                                f" <b>HYBRID TREND SELL</b> #{order.get('ticket')}\n"
                                f"Simbol: <b>{symbol}</b> @ {curr_price:.3f}\n"
                                f"SL: {sl} | TP: {tp} (R:R 1:2.8)", 
                                with_keyboard=False
                            )

            except Exception as e:
                logger.error(f"HybridEngine Error pada {symbol}: {e}")
