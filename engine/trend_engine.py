import asyncio, logging, time
import numpy as np
import config.settings as cfg
from engine.institutional_orchestrator import InstitutionalPreventiveShield, MarketRegimeRouter

logger = logging.getLogger("TrendRider_HighFreq_Trailing")

class TrendEngine:
    # [FIX] Gunakan Magic 555 (Trend Rider), bukan 3001 (Donchian)
    MAGIC = 555 

    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.cooldowns = {}
        self.active_trails = {}

    def _format_price(self, symbol: str, price: float) -> float:
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol else (3 if 'JPY' in symbol else 5)
        return round(float(price), digits)

    async def scan_and_trade(self, symbol: str, client, shield, regime_router):
        # 1. Validasi Rezim: Trend Rider HANYA aktif saat TRENDING
        regime = await regime_router.evaluate_regime(symbol)
        if regime != "TRENDING":
            return

        # 2. Validasi Perisai Keamanan
        if not await shield.validate_market_safety(symbol):
            return

        bars = await client.fetch_ohlcv(symbol, '1h', 25)
        if not bars or len(bars) < 15: return

        closes = np.array([b[4] for b in bars], dtype=float)
        highs = np.array([b[2] for b in bars], dtype=float)
        lows = np.array([b[3] for b in bars], dtype=float)
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr14 = np.mean(tr[-14:])

        # Breakout Donchian H1
        upper = max(highs[-20:-1])
        lower = min(lows[-20:-1])
        curr_close = closes[-1]

        tick = await client.exchange.fetch_ticker(symbol)
        if not tick or tick['ask'] <= 0: return

        lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.01)

        # --- DONCHIAN BREAKOUT BUY ---
        if curr_close > upper:
            risk_gap = 2.0 * atr14
            sl = self._format_price(symbol, tick['ask'] - risk_gap)
            
            async with self.order_lock.get(symbol):
                order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=0.0)
            if order:
                ticket = order.get('ticket')
                if ticket: self.active_trails[ticket] = {'symbol': symbol, 'side': 'buy', 'atr': atr14, 'entry': tick['ask']}
                self.cooldowns[symbol] = time.time() + 600
                await self.telegram.send(f" <b>HIGH-FREQ TREND BUY</b> | {symbol} (Pure Trailing)", with_keyboard=False)

        # --- DONCHIAN BREAKOUT SELL ---
        elif curr_close < lower:
            risk_gap = 2.0 * atr14
            sl = self._format_price(symbol, tick['bid'] + risk_gap)
            
            async with self.order_lock.get(symbol):
                order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=0.0)
            if order:
                ticket = order.get('ticket')
                if ticket: self.active_trails[ticket] = {'symbol': symbol, 'side': 'sell', 'atr': atr14, 'entry': tick['bid']}
                self.cooldowns[symbol] = time.time() + 600
                await self.telegram.send(f" <b>HIGH-FREQ TREND SELL</b> | {symbol} (Pure Trailing)", with_keyboard=False)

    async def manage_trailing_stops(self, client):
        if not self.active_trails: return
        positions = await client.fetch_positions()
        if not positions: return

        active_tickets = {p['ticket'] for p in positions if p['magic'] == self.MAGIC}

        for ticket, data in list(self.active_trails.items()):
            if ticket not in active_tickets:
                del self.active_trails[ticket]
                continue

            pos = next((p for p in positions if p['ticket'] == ticket), None)
            if not pos: continue

            symbol, side, atr, entry_price = data['symbol'], data['side'], data['atr'], data['entry']
            
            # [FIX] Ambil harga real-time dan kunci data MT5 yang benar
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0: continue
            
            current_price = tick['bid'] if side == 'buy' else tick['ask']
            current_sl = pos['sl']

            # ATR Trailing Stop dinamis: let winners run.
            if side == 'buy':
                new_sl = self._format_price(symbol, current_price - (2.0 * atr))
                if new_sl > current_sl and current_price > entry_price + (2.0 * atr):
                    res = await client.safe_update_sl(symbol, side, pos['contracts'], new_sl, str(ticket))
                    if res: pos['sl'] = new_sl
            elif side == 'sell':
                new_sl = self._format_price(symbol, current_price + (2.0 * atr))
                if (current_sl == 0 or new_sl < current_sl) and current_price < entry_price - (2.0 * atr):
                    res = await client.safe_update_sl(symbol, side, pos['contracts'], new_sl, str(ticket))
                    if res: pos['sl'] = new_sl

    async def manage(self):
        # [FIX] Gunakan variabel cfg yang benar (TREND_ENABLED)
        if not getattr(cfg, 'TREND_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): return
        
        shield = InstitutionalPreventiveShield(client)
        regime_router = MarketRegimeRouter(client)

        try:
            await self.manage_trailing_stops(client)
        except Exception as e: 
            logger.error(f"Error Trailing Trend Rider: {e}")

        mt5_pos = await client.fetch_positions()
        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            if time.time() < self.cooldowns.get(symbol, 0): continue
            
            if mt5_pos and any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos):
                continue
            try:
                await self.scan_and_trade(symbol, client, shield, regime_router)
            except Exception as e: 
                logger.error(f"Error Scan Trend Rider {symbol}: {e}")
