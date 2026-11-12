
import logging
from datetime import datetime
import config.settings as cfg

logger = logging.getLogger(__name__)

class ICTSilverBulletEngine:
    MAGIC = 1007
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock

    def _is_killzone(self, now):
        h, m = now.hour, now.minute
        if 14 <= h < 15: return True, "London KZ"
        if 21 <= h < 22: return True, "NY KZ"
        return False, ""

    async def manage(self):
        if not getattr(cfg, 'ICT_ENABLED', True): return
        client = self.clients.get('exness_mt5')
        if not client: return
        now = datetime.now(cfg.LOCAL_TZ)
        in_kz, kz_name = self._is_killzone(now)
        if not in_kz: return

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []): continue

            ohlcv = await client.fetch_ohlcv(symbol, '5m', 30)
            if not ohlcv or len(ohlcv) < 10: continue

            c1, c2, c3 = ohlcv[-4], ohlcv[-3], ohlcv[-2]
            prior_high = max(c[2] for c in ohlcv[-20:-3])
            prior_low = min(c[3] for c in ohlcv[-20:-3])
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # Bullish FVG
            if c2[4] > c2[1] and c3[3] > c1[2] and c1[3] <= prior_low:
                sl = min(c1[3], c2[3]) - (c3[4] * 0.001)
                tp = c3[4] + ((c3[4] - sl) * 2.5)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>ICT SILVER BULLET BUY</b> {symbol}\n{kz_name} | FVG Retest", with_keyboard=False)

            # Bearish FVG
            elif c2[4] < c2[1] and c1[3] > c3[2] and c1[2] >= prior_high:
                sl = max(c1[2], c2[2]) + (c3[4] * 0.001)
                tp = c3[4] - ((sl - c3[4]) * 2.5)
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                if order: await self.telegram.send(f" <b>ICT SILVER BULLET SELL</b> {symbol}\n{kz_name} | FVG Retest", with_keyboard=False)
