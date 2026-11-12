import time
import logging
import numpy as np
import config.settings as cfg

logger = logging.getLogger(__name__)

class VolumeProfilePOCEngine:
    MAGIC = 1008

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.cooldowns = {}

    async def calculate_atr(self, bars, period=14) -> float:
        if len(bars) < period + 1:
            return 0.0
        tr_list = []
        for i in range(1, len(bars)):
            high = bars[i][2]
            low = bars[i][3]
            prev_close = bars[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        return float(np.mean(tr_list[-period:]))

    async def check_market_regime(self, bars) -> bool:
        try:
            closes = [b[4] for b in bars]
            sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else np.mean(closes)
            current_price = closes[-1]
            deviation = abs(current_price - sma_50) / sma_50
            if deviation > 0.012:
                return False
        except Exception:
            pass
        return True

    async def manage(self):
        if not getattr(cfg, 'POC_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return

        for symbol in cfg.SYMBOLS:
            if time.time() < self.cooldowns.get(symbol, 0): continue
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos):
                continue

            bars = await client.fetch_ohlcv(symbol, '15m', 96)
            if not bars or len(bars) < 50: continue

            is_sideways = await self.check_market_regime(bars)
            if not is_sideways:
                continue

            highs = [b[2] for b in bars]
            lows = [b[3] for b in bars]
            vols = [b[5] for b in bars]
            min_p, max_p = min(lows), max(highs)
            p_range = max_p - min_p
            if p_range <= 0: continue

            buckets = 50
            bucket_size = p_range / buckets
            vol_dist = np.zeros(buckets)
            
            for b in bars:
                mid = (b[2] + b[3]) / 2.0
                idx = int(np.clip(int((mid - min_p) / bucket_size), 0, buckets - 1))
                vol_dist[idx] += max(b[5], 1.0)

            total_vol = np.sum(vol_dist)
            if total_vol <= 0: continue

            poc_idx = int(np.argmax(vol_dist))
            poc_price = min_p + (poc_idx + 0.5) * bucket_size

            target_va_vol = total_vol * 0.70
            current_va_vol = vol_dist[poc_idx]
            lower_idx = poc_idx
            upper_idx = poc_idx

            while current_va_vol < target_va_vol and (lower_idx > 0 or upper_idx < buckets - 1):
                vol_below = vol_dist[lower_idx - 1] if lower_idx > 0 else -1
                vol_above = vol_dist[upper_idx + 1] if upper_idx < buckets - 1 else -1

                if vol_below >= vol_above and lower_idx > 0:
                    lower_idx -= 1
                    current_va_vol += vol_dist[lower_idx]
                elif upper_idx < buckets - 1:
                    upper_idx += 1
                    current_va_vol += vol_dist[upper_idx]
                else:
                    break

            val_price = min_p + lower_idx * bucket_size
            vah_price = min_p + (upper_idx + 1) * bucket_size

            prev_bar = bars[-2]
            last_bar = bars[-1]
            curr_p = last_bar[4]
            atr = await self.calculate_atr(bars)
            if atr <= 0: continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.01)

            # [FIX V4.8] H1 Over-Extension Guard
            h1_bars = await client.fetch_ohlcv(symbol, '1h', 50)
            if h1_bars and len(h1_bars) >= 50:
                h1_closes = [b[4] for b in h1_bars]
                ema_50 = sum(h1_closes[-50:]) / 50
                if abs(curr_p - ema_50) / ema_50 > 0.015: 
                    continue

            # --- SKenario A: BULLISH REVERSION ---
            if prev_bar[3] < val_price and prev_bar[4] > val_price:
                entry_price = curr_p
                tp_price = poc_price
                sl_price = min(prev_bar[3], last_bar[3]) - (atr * 0.5)
                
                risk = entry_price - sl_price
                reward = tp_price - entry_price
                
                if reward > 0 and risk > 0 and (reward / risk) >= 1.5:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(
                            symbol, 'buy', lot, magic=self.MAGIC, 
                            sl=float(sl_price), tp=float(tp_price)
                        )
                    if order:
                        self.cooldowns[symbol] = time.time() + 900
                        await self.telegram.send(
                            f" <b>INSTITUTIONAL POC REVERSION BUY</b>\n"
                            f"Simbol: <b>{symbol}</b>\n"
                            f"VAL Rejection -> Target POC: {poc_price:.2f}\n"
                            f"RRR: 1:{(reward/risk):.2f}",
                            with_keyboard=False
                        )

            # --- SKENARIO B: BEARISH REVERSION ---
            elif prev_bar[2] > vah_price and prev_bar[4] < vah_price:
                entry_price = curr_p
                tp_price = poc_price
                sl_price = max(prev_bar[2], last_bar[2]) + (atr * 0.5)
                
                risk = sl_price - entry_price
                reward = entry_price - tp_price
                
                if reward > 0 and risk > 0 and (reward / risk) >= 1.5:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(
                            symbol, 'sell', lot, magic=self.MAGIC, 
                            sl=float(sl_price), tp=float(tp_price)
                        )
                    if order:
                        self.cooldowns[symbol] = time.time() + 900
                        await self.telegram.send(
                            f" <b>INSTITUTIONAL POC REVERSION SELL</b>\n"
                            f"Simbol: <b>{symbol}</b>\n"
                            f"VAH Rejection -> Target POC: {poc_price:.2f}\n"
                            f"RRR: 1:{(reward/risk):.2f}",
                            with_keyboard=False
                        )
