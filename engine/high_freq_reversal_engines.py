import asyncio, logging, time
import numpy as np
import config.settings as cfg

logger = logging.getLogger("ReversalEngines_HighFreq_Trailing")

class HighFreqReversalEngines:
    MAGIC_ROUND = 3005
    MAGIC_CLIMAX = 3006

    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.active_trails = {}
        self.cooldowns = {}

    def _format_price(self, symbol: str, price: float) -> float:
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol else (3 if 'JPY' in symbol else 5)
        return round(float(price), digits)

    async def manage_trailing_stops(self, client):
        if not self.active_trails: return
        positions = await client.fetch_positions()
        if not positions: return

        active_tickets = {p['ticket'] for p in positions if p['magic'] in [self.MAGIC_ROUND, self.MAGIC_CLIMAX]}

        for ticket, data in list(self.active_trails.items()):
            if ticket not in active_tickets:
                del self.active_trails[ticket]
                continue

            pos = next((p for p in positions if p['ticket'] == ticket), None)
            if not pos: continue

            symbol, side, atr = data['symbol'], data['side'], data['atr']
            
            # [FIX] Ambil harga ticker real-time
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0: continue
            
            current_price = tick['bid'] if side == 'buy' else tick['ask']
            open_price, current_sl = pos['entryPrice'], pos['sl']

            # Pure ATR Trailing
            if side == 'buy':
                new_sl = self._format_price(symbol, current_price - (1.5 * atr))
                if new_sl > current_sl and current_price > open_price:
                    await client.safe_update_sl(symbol, side, pos['contracts'], new_sl, str(ticket))
                    pos['sl'] = new_sl
            elif side == 'sell':
                new_sl = self._format_price(symbol, current_price + (1.5 * atr))
                if (current_sl == 0 or new_sl < current_sl) and current_price < open_price:
                    await client.safe_update_sl(symbol, side, pos['contracts'], new_sl, str(ticket))
                    pos['sl'] = new_sl

    async def manage(self):
        if getattr(cfg, 'GLOBAL_HALT', False): return
        client = self.clients.get("exness_mt5")
        if not client: return

        try:
            await self.manage_trailing_stops(client)
        except Exception as e:
            logger.error(f"Error Trailing Reversal: {e}")

        mt5_pos = await client.fetch_positions()
        if mt5_pos is None: return

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if time.time() < self.cooldowns.get(symbol, 0): continue

            bars = await client.fetch_ohlcv(symbol, '5m', 20)
            if not bars or len(bars) < 15: continue

            highs = np.array([b[2] for b in bars], dtype=float)
            lows = np.array([b[3] for b in bars], dtype=float)
            closes = np.array([b[4] for b in bars], dtype=float)
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            atr = np.mean(tr[-14:])

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0: continue
            price = tick['last']

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            has_pos = any(p['symbol'] == symbol and p['magic'] in [self.MAGIC_ROUND, self.MAGIC_CLIMAX] for p in mt5_pos)
            if has_pos: continue

            # Deteksi Round Number
            step = 500.0 if "BTC" in symbol else (50.0 if "XAU" in symbol else 100.0)
            nearest = round(price / step) * step

            if abs(price - nearest) / nearest <= 0.0012:
                if bars[-2][3] <= nearest and bars[-1][4] > nearest:
                    sl = self._format_price(symbol, nearest - (1.5 * atr))
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC_ROUND, sl=sl, tp=0.0)
                    if order:
                        ticket = order.get('ticket')
                        if ticket: self.active_trails[ticket] = {'symbol': symbol, 'side': 'buy', 'atr': atr}
                        self.cooldowns[symbol] = time.time() + 240
                        await self.telegram.send(f" <b>HIGH-FREQ ROUND BUY</b> | {symbol} (Trailing)", with_keyboard=False)

                elif bars[-2][2] >= nearest and bars[-1][4] < nearest:
                    sl = self._format_price(symbol, nearest + (1.5 * atr))
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC_ROUND, sl=sl, tp=0.0)
                    if order:
                        ticket = order.get('ticket')
                        if ticket: self.active_trails[ticket] = {'symbol': symbol, 'side': 'sell', 'atr': atr}
                        self.cooldowns[symbol] = time.time() + 240
                        await self.telegram.send(f" <b>HIGH-FREQ ROUND SELL</b> | {symbol} (Trailing)", with_keyboard=False)
