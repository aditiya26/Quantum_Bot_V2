import logging
import time
from datetime import datetime
import config.settings as cfg

logger = logging.getLogger(__name__)

class SessionSweepReversalEngine:
    MAGIC = 1010
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.cooldowns = {} # ANTI-SPAM: Menyimpan waktu cooldown per simbol

    async def manage(self):
        if not getattr(cfg, 'SESSION_SWEEP_ENABLED', True): return
        client = self.clients.get('exness_mt5')
        if not client: return
        
        now = datetime.now(cfg.LOCAL_TZ)
        h, m = now.hour, now.minute
        # London (14:00-15:30) atau NY (20:30-22:00)
        if not ((h == 14) or (h == 15 and m <= 30) or (h == 20 and m >= 30) or (h == 21)): return

        # FIX 1: Kunci HANYA untuk Forex (Jangan gunakan untuk Crypto yang 24/7)
        for symbol in ["XAUUSDm", "GBPJPYm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            
            # FIX 2: Cek Cooldown (Jeda 15 menit setelah entry)
            if time.time() < self.cooldowns.get(symbol, 0): continue

            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []): continue

            bars = await client.fetch_ohlcv(symbol, '15m', 50)
            if not bars or len(bars) < 25: continue

            asian_high = max(b[2] for b in bars[-28:-8])
            asian_low = min(b[3] for b in bars[-28:-8])
            asian_range = asian_high - asian_low
            if asian_range <= 0: continue
            
            last_bar, prev_bar = bars[-2], bars[-3]
            curr_p = last_bar[4]
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # SWEEP LOW -> BUY
            if (prev_bar[3] < asian_low or last_bar[3] < asian_low) and last_bar[4] > asian_low:
                sl = min(last_bar[3], prev_bar[3]) - (asian_range * 0.05)
                tp = asian_high - (asian_range * 0.1)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order:
                    self.cooldowns[symbol] = time.time() + 900 # Jeda 15 menit
                    await self.telegram.send(f" <b>SESSION SWEEP BUY</b> {symbol}\nSwept Asian Low", with_keyboard=False)
            
            # SWEEP HIGH -> SELL
            elif (prev_bar[2] > asian_high or last_bar[2] > asian_high) and last_bar[4] < asian_high:
                sl = max(last_bar[2], prev_bar[2]) + (asian_range * 0.05)
                tp = asian_low + (asian_range * 0.1)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order:
                    self.cooldowns[symbol] = time.time() + 900 # Jeda 15 menit
                    await self.telegram.send(f" <b>SESSION SWEEP SELL</b> {symbol}\nSwept Asian High", with_keyboard=False)
