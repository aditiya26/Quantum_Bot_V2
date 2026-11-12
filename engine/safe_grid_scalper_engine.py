"""
Safe Grid Scalper V4.4 - Fixed Dynamic Hard Stop & Unified Core
"""
import asyncio, logging, time
import config.settings as cfg
import numpy as np

logger = logging.getLogger("Safe_Grid_Scalper_V4")

class SafeGridScalperEngine:
    MAGIC = 2004

    def __init__(self, state, clients, telegram, order_lock, quant_core):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.quant_core = quant_core
        self.exchange = "exness_mt5"
        self.max_pairs = 5
        self.halt_cooldowns = {}
        self.basket_peak_profit = {}

    async def close_all_grid_positions(self, client, symbol, positions):
        for p in positions:
            async with self.order_lock.get(symbol):
                await client.close_position(symbol, p['contracts'], p['side'], str(p['ticket']), magic=self.MAGIC)

    async def get_dynamic_grid_step(self, client, symbol: str) -> float:
        if "BTC" in symbol: default_step = 50.0
        elif "ETH" in symbol: default_step = 10.0
        elif "XAU" in symbol: default_step = 2.0
        else: default_step = 1.0
        try:
            ohlcv = await client.fetch_ohlcv(symbol, '15m', 15)
            if ohlcv and len(ohlcv) >= 14:
                tr_list = [max(ohlcv[i][2] - ohlcv[i][3], abs(ohlcv[i][2] - ohlcv[i-1][4]), abs(ohlcv[i][3] - ohlcv[i-1][4])) for i in range(1, len(ohlcv))]
                atr = sum(tr_list[-14:]) / 14
                return round(max(atr * 0.5, default_step), 2)
        except: pass
        return default_step

    async def check_trend_block(self, client, symbol: str, side: str) -> bool:
        try:
            ohlcv = await client.fetch_ohlcv(symbol, '1h', 200)
            if not ohlcv or len(ohlcv) < 200: return True
            closes = [c[4] for c in ohlcv]
            ema_200 = sum(closes[-200:]) / 200
            tick = await client.fetch_ticker(symbol)
            price = tick['last']
            if price > ema_200 * 1.01 and side == 'SELL': return False
            if price < ema_200 * 0.99 and side == 'BUY': return False
        except: pass
        return True

    async def manage(self):
        if not getattr(cfg, 'SAFE_GRID_ENABLED', True) or getattr(cfg, 'GLOBAL_HALT', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        now = time.time()
        for symbol in ["XAUUSDm", "BTCUSDm", "ETHUSDm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if now < self.halt_cooldowns.get(symbol, 0): continue

            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: continue
            grid_positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == self.MAGIC]

            if len(grid_positions) > 0:
                total_pnl = sum(p.get('profit', 0.0) for p in grid_positions)

                # Hitung Dynamic Hard Stop di sini, sebelum IF
                current_atr = 0.0
                try:
                    ohlcv = await client.fetch_ohlcv(symbol, '15m', 15)
                    tr_list = [max(ohlcv[i][2] - ohlcv[i][3], abs(ohlcv[i][2] - ohlcv[i-1][4]), abs(ohlcv[i][3] - ohlcv[i-1][4])) for i in range(1, len(ohlcv))]
                    current_atr = sum(tr_list[-14:]) / 14
                except: pass
                dynamic_hs = self.quant_core.calculate_dynamic_basket_stop(symbol, getattr(cfg, 'BASE_EQUITY', 2000.0), current_atr)

                buys_count = len([p for p in grid_positions if p['side'] == 'BUY'])
                sells_count = len([p for p in grid_positions if p['side'] == 'SELL'])
                pairs_count = max(buys_count, sells_count)
                base_tp = 6.0 + (pairs_count * 2.0)
                
                current_peak = self.basket_peak_profit.get(symbol, 0.0)
                if total_pnl > current_peak:
                    self.basket_peak_profit[symbol] = total_pnl
                    current_peak = total_pnl

                trail_drop = 3.0

                if total_pnl >= base_tp and total_pnl <= (current_peak - trail_drop):
                    await self.close_all_grid_positions(client, symbol, grid_positions)
                    self.basket_peak_profit[symbol] = 0.0
                    await self.telegram.send(f" <b>HYBRID TRAILING TP HIT</b>\n{symbol} ditutup di +${total_pnl:.2f}", with_keyboard=False)
                    continue
                elif total_pnl >= (base_tp + 15.0):
                    await self.close_all_grid_positions(client, symbol, grid_positions)
                    self.basket_peak_profit[symbol] = 0.0
                    await self.telegram.send(f" <b>HYBRID MAXIMUM TP HIT</b>\n{symbol} ditutup di +${total_pnl:.2f}!", with_keyboard=False)
                    continue
                elif total_pnl <= -dynamic_hs:
                    await self.close_all_grid_positions(client, symbol, grid_positions)
                    self.halt_cooldowns[symbol] = now + 1800
                    await self.telegram.send(f" <b>HYBRID HARD STOP (-${abs(total_pnl):.2f})</b> {symbol}. Jeda 30m.", with_keyboard=False)
                    continue
                elif 2.0 <= total_pnl < base_tp:
                    losing_legs = [p for p in grid_positions if p.get('profit', 0.0) < -1.0]
                    if losing_legs:
                        for p in losing_legs:
                            async with self.order_lock.get(symbol):
                                await client.close_position(symbol, p['contracts'], p['side'], str(p['ticket']), magic=self.MAGIC)
                        await self.telegram.send(f" <b>DECOUPLING</b> {symbol}: Potong {len(losing_legs)} baris rugi.", with_keyboard=False)
                        continue

            # ENTRY LOGIC
            tick = await client.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0: continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            if "ETH" in symbol: lot = max(lot, 0.03)
            elif "BTC" in symbol: lot = max(lot, 0.01)

            buys = [p for p in grid_positions if p['side'] == 'BUY']
            sells = [p for p in grid_positions if p['side'] == 'SELL']
            current_pairs = max(len(buys), len(sells))

            if len(grid_positions) == 0:
                allow_buy = await self.check_trend_block(client, symbol, 'BUY')
                allow_sell = await self.check_trend_block(client, symbol, 'SELL')
                if not allow_buy or not allow_sell: continue
                async with self.order_lock.get(symbol):
                    await client.create_order(symbol, 'buy', lot, magic=self.MAGIC)
                    await client.create_order(symbol, 'sell', lot, magic=self.MAGIC)
                continue

            if current_pairs < self.max_pairs:
                dynamic_step = await self.get_dynamic_grid_step(client, symbol)
                last_buy_price = min([b['entryPrice'] for b in buys]) if buys else tick['ask']
                last_sell_price = max([s['entryPrice'] for s in sells]) if sells else tick['bid']
                
                price_moved_up = (tick['ask'] - last_sell_price) >= dynamic_step
                price_moved_down = (last_buy_price - tick['ask']) >= dynamic_step

                if price_moved_up or price_moved_down:
                    allow_buy = await self.check_trend_block(client, symbol, 'BUY')
                    allow_sell = await self.check_trend_block(client, symbol, 'SELL')
                    async with self.order_lock.get(symbol):
                        if allow_buy: await client.create_order(symbol, 'buy', lot, magic=self.MAGIC)
                        if allow_sell: await client.create_order(symbol, 'sell', lot, magic=self.MAGIC)
