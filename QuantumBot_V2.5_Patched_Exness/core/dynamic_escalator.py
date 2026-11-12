"""
QuantumBot Pro - Dynamic Escalator V2.5
3-Stage Institutional Trade Management: BEP Lock (+30p) -> Scale Out 50% (+45p) -> Parabolic Trail (+55p).
"""
import asyncio, logging
logger = logging.getLogger(__name__)

class DynamicEscalator:
    def __init__(self, mt5_client, state_manager, tg_notifier=None):
        self.client = mt5_client
        self.state_mgr = state_manager
        self.tg = tg_notifier
        self.pip_scales = {
            "XAUUSDm": 0.1,
            "BTCUSDm": 1.0,
            "GBPJPYm": 0.01,
            "USTECm": 1.0
        }

    async def evaluate_position(self, pos: dict, current_bid: float, current_ask: float):
        ticket = int(pos['ticket'])
        symbol = pos['symbol']
        side = pos['side'].upper()
        entry = pos['entry_price']
        lot = pos['amount']
        stage = pos.get('stage', 0)
        pip_unit = self.pip_scales.get(symbol, 0.1)

        # Hitung Pips Profit Bersih
        if side == 'BUY':
            profit_pips = (current_bid - entry) / pip_unit
        else:
            profit_pips = (entry - current_ask) / pip_unit

        # STAGE 1: BREAKEVEN LOCK (+30 Pips)
        if stage == 0 and profit_pips >= 30.0:
            bep_price = entry + (2.0 * pip_unit) if side == 'BUY' else entry - (2.0 * pip_unit)
            res = await self.client.safe_update_sl(symbol, side, lot, bep_price, str(ticket))
            
            # FIX BUG 5: Validasi bahwa harga SL baru > 0 dan bukan tuple gagal
            if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                await self.state_mgr.save_position(ticket, {**pos, 'stage': 1, 'current_sl': bep_price})
                logger.info(f"[Escalator] Ticket #{ticket} ({symbol}) STAGE 1: BEP Locked @ {bep_price}")
                if self.tg:
                    await self.tg.send(f"🛡️ <b>BEP LOCKED</b>\n#{ticket} {symbol}\nProfit: +{profit_pips:.1f} pips\nSL Baru: {bep_price:.3f}")

        # STAGE 2: PARTIAL SCALE-OUT 50% (+45 Pips)
        elif stage == 1 and profit_pips >= 45.0 and lot >= 0.02:
            close_lot = round(lot / 2.0, 2)
            rem_lot = round(lot - close_lot, 2)
            
            success = await self.client.close_partial(symbol, ticket, close_lot, side)
            if success:
                await self.state_mgr.save_position(ticket, {**pos, 'stage': 2, 'amount': rem_lot})
                logger.info(f"[Escalator] Ticket #{ticket} ({symbol}) STAGE 2: Closed 50% ({close_lot} lot).")
                if self.tg:
                    await self.tg.send(f"💰 <b>PARTIAL SCALE-OUT 50%</b>\n#{ticket} {symbol}\nSecured: +{profit_pips:.1f} pips\nSisa Lot: {rem_lot}")

        # STAGE 3: PARABOLIC TRAILING (Jarak 20 Pips)
        elif stage >= 1 and profit_pips >= 55.0:
            trail_distance = 20.0 * pip_unit
            current_sl = float(pos.get('current_sl', 0.0))
            
            if side == 'BUY':
                new_sl = current_bid - trail_distance
                if new_sl > current_sl + (3.0 * pip_unit):
                    res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                    if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                        await self.state_mgr.save_position(ticket, {**pos, 'stage': 3, 'current_sl': new_sl})
                        logger.info(f"[Escalator] Ticket #{ticket} ({symbol}) STAGE 3: Trailing SL -> {new_sl:.3f}")
            else:
                new_sl = current_ask + trail_distance
                if current_sl == 0.0 or new_sl < current_sl - (3.0 * pip_unit):
                    res = await self.client.safe_update_sl(symbol, side, lot, new_sl, str(ticket))
                    if res and isinstance(res, (tuple, list)) and len(res) >= 2 and float(res[1]) > 0.0:
                        await self.state_mgr.save_position(ticket, {**pos, 'stage': 3, 'current_sl': new_sl})
                        logger.info(f"[Escalator] Ticket #{ticket} ({symbol}) STAGE 3: Trailing SL -> {new_sl:.3f}")
