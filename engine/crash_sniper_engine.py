import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger("CrashSniperEngine")

class CrashSniperEngine:
    MAGIC = 888

    def __init__(self, state, clients, telegram, order_lock, news_filter=None):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = news_filter if news_filter else NewsFilter()
        self._news_block = False
        self._last_news_check = 0
        self.cooldowns = {}

    async def manage(self):
        if not getattr(cfg, 'CRASH_SNIPER_ENABLED', False): return
        
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(15, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        symbols = getattr(cfg, 'CRASH_SNIPER_SYMBOLS', ["XAUUSDm", "BTCUSDm", "ETHUSDm"])

        for symbol in symbols:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if time.time() < self.cooldowns.get(symbol, 0): continue # Cegah spam

            try:
                mt5_pos = await client.fetch_positions()
                if mt5_pos is None: continue
                
                positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == self.MAGIC]
                max_layers = 2
                if len(positions) >= max_layers: continue

                tick = await client.exchange.fetch_ticker(symbol)
                if not tick or tick['ask'] <= 0 or tick['bid'] <= 0: continue
                
                # Filter spread dilonggarkan untuk Crash
                spread_pct = (tick['ask'] - tick['bid']) / tick['ask'] * 100
                if spread_pct > 0.35: continue

                bars = await client.fetch_ohlcv(symbol, '5m', 30)
                if not bars or len(bars) < 20: continue

                df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
                
                # Perhitungan ATR
                tr = pd.concat([
                    df['h'] - df['l'],
                    (df['h'] - df['c'].shift()).abs(),
                    (df['l'] - df['c'].shift()).abs()
                ], axis=1).max(axis=1)
                atr14 = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]

                last_bar = df.iloc[-2]
                prev_bar = df.iloc[-3]

                # [PELONGGARAN 1] Penurunan ATR dari 2.0x ke 1.5x
                price_drop = prev_bar['c'] - last_bar['l']
                is_crash_drop = price_drop >= (atr14 * 1.5)

                # [PELONGGARAN 2] Ekor penolakan (Wick) dari 1.5x ke 1.2x
                body = abs(last_bar['c'] - last_bar['o'])
                lower_wick = min(last_bar['o'], last_bar['c']) - last_bar['l']
                has_rejection = lower_wick >= (body * 1.2) and body > 0

                # [PELONGGARAN 3] Volume dari 1.5x ke 1.2x
                avg_vol = df['v'].iloc[-20:-1].mean()
                is_volume_spike = last_bar['v'] >= (avg_vol * 1.2)

                if is_crash_drop and has_rejection and is_volume_spike:
                    lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
                    curr_price = tick['ask']
                    
                    digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)
                    
                    # SL Khusus Crash Sniper: Beri jarak lebih lebar (0.8x ATR)
                    sl = round(last_bar['l'] - (atr14 * 0.8), digits)
                    risk = curr_price - sl
                    tp = 0.0 # [FIX] Tidak ada TP, murni Trailing

                    if risk > 0:
                        async with self.order_lock.get(symbol):
                            order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                        if order:
                            self.cooldowns[symbol] = time.time() + 300 # Jeda 5 menit
                            layer_num = len(positions) + 1
                            await self.telegram.send(
                                f" <b>CRASH SNIPER REBOUND (Layer {layer_num})</b> #{order.get('ticket')}\n"
                                f"Simbol: <b>{symbol}</b> @ {curr_price:.3f}\n"
                                f"Volatility-Drop 1.5x | SL: {sl}", 
                                with_keyboard=False
                            )

            except Exception as e:
                logger.error(f"CrashSniperEngine Error {symbol}: {e}")
