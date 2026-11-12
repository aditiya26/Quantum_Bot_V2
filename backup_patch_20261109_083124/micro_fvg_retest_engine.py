import asyncio, logging, time
import numpy as np
import pandas as pd
import config.settings as cfg
from engine.institutional_orchestrator import InstitutionalPreventiveShield, MarketRegimeRouter

logger = logging.getLogger("Micro_FVG_Scalper_Institutional")

class MicroFVGRetestScalper:
    MAGIC = 2003

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.active_fvgs = {} 
        self.cooldowns = {}

    def _format_price(self, symbol: str, price: float) -> float:
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)
        return round(float(price), digits)

    def _calculate_atr(self, bars, period=14):
        if len(bars) < period + 1: return 0.0
        tr_list = []
        for i in range(1, len(bars)):
            h, l, prev_c = bars[i][2], bars[i][3], bars[i-1][4]
            tr_list.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        return float(np.mean(tr_list[-period:]))

    async def scan_and_trade(self, symbol: str, client, shield, regime_router):
        regime = await regime_router.evaluate_regime(symbol)
        if regime == "SHOCK":
            return

        if not await shield.validate_market_safety(symbol):
            return

        bars = await client.fetch_ohlcv(symbol, '5m', 40)
        if not bars or len(bars) < 20: 
            return

        pip_unit = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('pip_size', 0.1)
        min_gap = 3.5 * pip_unit
        atr = self._calculate_atr(bars, 14)

        c1, c2, c3 = bars[-4], bars[-3], bars[-2]
        
        tick = await client.exchange.fetch_ticker(symbol)
        if not tick or tick['bid'] <= 0 or tick['ask'] <= 0: 
            return

        if c3[3] > c1[2] + min_gap and c2[4] > c2[1]: 
            self.active_fvgs[symbol] = {
                'type': 'BULLISH', 'high': c3[3], 'low': c1[2],
                'invalidation': c1[3], 'time': time.time()
            }
        elif c1[3] > c3[2] + min_gap and c2[4] < c2[1]: 
            self.active_fvgs[symbol] = {
                'type': 'BEARISH', 'high': c1[3], 'low': c3[2],
                'invalidation': c1[2], 'time': time.time()
            }

        if symbol in self.active_fvgs:
            fvg = self.active_fvgs[symbol]
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)
            curr_ask, curr_bid = tick['ask'], tick['bid']

            if fvg['type'] == 'BULLISH' and (curr_ask <= fvg['high']) and (curr_bid >= fvg['low']):
                buffer_sl = max(1.5 * pip_unit, atr * 0.5)
                sl = self._format_price(symbol, fvg['invalidation'] - buffer_sl)
                
                risk = curr_ask - sl
                tp = self._format_price(symbol, curr_ask + (risk * 2.2))
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        del self.active_fvgs[symbol]
                        self.cooldowns[symbol] = time.time() + 600
                        await self.telegram.send(
                            f" <b>MICRO-FVG RETEST BUY (Inst. Guard)</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {curr_ask:.3f}\n"
                            f"SL: {sl:.3f} | TP: {tp:.3f} (R:R 1:2.2)",
                            with_keyboard=False
                        )

            elif fvg['type'] == 'BEARISH' and (curr_bid >= fvg['low']) and (curr_ask <= fvg['high']):
                buffer_sl = max(1.5 * pip_unit, atr * 0.5)
                sl = self._format_price(symbol, fvg['invalidation'] + buffer_sl)
                
                risk = sl - curr_bid
                tp = self._format_price(symbol, curr_bid - (risk * 2.2)) 
                
                if risk > 0:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        del self.active_fvgs[symbol]
                        self.cooldowns[symbol] = time.time() + 600
                        await self.telegram.send(
                            f" <b>MICRO-FVG RETEST SELL (Inst. Guard)</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {curr_bid:.3f}\n"
                            f"SL: {sl:.3f} | TP: {tp:.3f} (R:R 1:2.2)",
                            with_keyboard=False
                        )

    async def manage(self):
        if not getattr(cfg, 'MICRO_FVG_ENABLED', True): 
            return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): 
            return
        
        shield = InstitutionalPreventiveShield(client)
        regime_router = MarketRegimeRouter(client)

        for symbol in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): 
                continue
            if time.time() < self.cooldowns.get(symbol, 0): 
                continue
            
            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []):
                continue
            try:
                await self.scan_and_trade(symbol, client, shield, regime_router)
            except Exception as e:
                logger.error(f"Error Institutional Micro-FVG {symbol}: {e}")
