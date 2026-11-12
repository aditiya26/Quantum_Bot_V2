import asyncio, logging, time
import config.settings as cfg

logger = logging.getLogger("DynamicEscalator")

class DynamicEscalatorV3:
    def __init__(self, mt5_client, state_manager, tg_notifier=None):
        self.client = mt5_client
        self.state_mgr = state_manager
        self.tg = tg_notifier
        self.pip_scales = {"XAUUSDm": 0.1, "BTCUSDm": 1.0, "GBPJPYm": 0.01, "USTECm": 1.0, "ETHUSDm": 0.1}
        self.trailing_notified = set() # [ANTI-SPAM] Mencegah notifikasi berulang

    async def evaluate_position(self, pos: dict, current_bid: float, current_ask: float, isolated_pairs: dict = None):
        ticket = int(pos['ticket'])
        magic = pos.get('magic_number', pos.get('magic', 0))

        # 1. PENGECUALIAN ISOLATED ENGINE (MUTLAK)
        if magic == getattr(cfg, 'ISOLATED_MAGIC', 9999):
            return 
            
        # 2. BYPASS JIKA POSISI SEDANG DI-HEDGE
        ticket_str = str(ticket)
        if isolated_pairs:
            if ticket_str in isolated_pairs or ticket_str in isolated_pairs.values():
                return 

        symbol = pos['symbol']
        side = pos['side'].upper()
        entry = float(pos['entry_price'])
        lot = float(pos['amount'])
        current_sl = float(pos.get('current_sl', 0.0))
        pip_unit = self.pip_scales.get(symbol, 0.1)

        current_price = current_bid if side == 'BUY' else current_ask
        profit_diff = (current_price - entry) if side == 'BUY' else (entry - current_price)
        
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'ETH' in symbol else (3 if 'JPY' in symbol else 5)

        # 3. LOGIKA XAUUSDm: BEP DULU, LALU TRAILING KETAT
        if "XAU" in symbol:
            profit_usd = profit_diff * lot * 100.0
            if profit_usd < 2.0:
                return

            bep_dist = 1.00 # Kunci BEP + $1
            if side == 'BUY':
                if current_sl < entry + bep_dist:
                    new_sl = round(entry + bep_dist, digits)
                    res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                    if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                        await self.state_mgr.save_position(ticket, {**pos, 'current_sl': new_sl})
                    return
            else:
                if current_sl == 0.0 or current_sl > entry - bep_dist:
                    new_sl = round(entry - bep_dist, digits)
                    res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                    if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                        await self.state_mgr.save_position(ticket, {**pos, 'current_sl': new_sl})
                    return

            trail_dist = 1.0 # Jarak Trailing $1

        # 4. LOGIKA CRYPTO & INDICES (BTC, ETH, USTEC)
        else:
            if "BTC" in symbol:
                trigger_dist = entry * 0.0005   # 0.05%
                trail_dist = entry * 0.0010     # 0.1%
            else:
                trigger_dist = entry * 0.0015   # 0.15%
                trail_dist = entry * 0.0020     # 0.20%
                
            if profit_diff < trigger_dist:
                return

        # 5. EKSEKUSI TRAILING & NOTIFIKASI ANTI-SPAM
        if side == 'BUY':
            calc_trail = current_price - trail_dist
            new_sl = round(calc_trail, digits)

            if new_sl > current_sl + (1.0 * pip_unit):
                res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                    await self.state_mgr.save_position(ticket, {**pos, 'current_sl': new_sl})
                    
                    # [ANTI-SPAM] Hanya kirim 1 kali
                    if self.tg:
                        
                        await self.tg.send(f" <b>TRAILING ACTIVE</b> {symbol} #{ticket} | SL: {new_sl:.3f}", with_keyboard=False)

        else: # SELL
            calc_trail = current_price + trail_dist
            new_sl = round(calc_trail, digits)

            if current_sl == 0.0 or new_sl < current_sl - (1.0 * pip_unit):
                res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                    await self.state_mgr.save_position(ticket, {**pos, 'current_sl': new_sl})
                    
                    # [ANTI-SPAM] Hanya kirim 1 kali
                    if self.tg:
                        
                        await self.tg.send(f" <b>TRAILING ACTIVE</b> {symbol} #{ticket} | SL: {new_sl:.3f}", with_keyboard=False)
