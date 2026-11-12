import asyncio, logging, time, numpy as np, pandas as pd
import config.settings as cfg
from engine.institutional_orchestrator import InstitutionalPreventiveShield, MarketRegimeRouter

logger = logging.getLogger("ZScore_Stat_Reversion_Institutional")

class ZScoreStatisticalReversionEngine:
    MAGIC = 2002

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.lookback = 25
        self.z_threshold = 1.8
        self.cooldowns = {}

    def _format_price(self, symbol: str, price: float) -> float:
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)
        return round(float(price), digits)

    def calculate_zscore(self, closes: np.ndarray):
        if len(closes) < self.lookback:
            return 0.0, 0.0, 0.0
        window = closes[-self.lookback:]
        mean = float(np.mean(window))
        std = float(np.std(window))
        if std <= 0.00001: 
            std = 0.0001
        z_score = (float(closes[-1]) - mean) / std
        return z_score, mean, std

    async def manage(self):
        if not getattr(cfg, 'ZSCORE_STAT_ENABLED', True): 
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

            # 1. Validasi Rezim: Z-Score Reversion HARUS SIDEWAYS
            regime = await regime_router.evaluate_regime(symbol)
            if regime != "SIDEWAYS":
                continue

            # 2. Validasi Perisai Keamanan Pasar
            if not await shield.validate_market_safety(symbol):
                continue

            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []):
                continue

            bars = await client.fetch_ohlcv(symbol, '5m', 50)
            if not bars or len(bars) < 30: 
                continue

            closed_bars = bars[:-1]
            closes = np.array([b[4] for b in closed_bars], dtype=float)
            z_score, mean, std = self.calculate_zscore(closes)

            last_confirmed = closed_bars[-1]
            o, h, l, c = last_confirmed[1], last_confirmed[2], last_confirmed[3], last_confirmed[4]
            body = abs(c - o)
            lower_wick = min(o, c) - l
            upper_wick = h - max(o, c)

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['bid'] <= 0 or tick['ask'] <= 0: 
                continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)

            # --- OVERSOLD REVERSION (Z <= -2.2) ---
            if z_score <= -self.z_threshold and lower_wick >= (body * 0.6):
                sl = self._format_price(symbol, l - (std * 0.5))
                tp = self._format_price(symbol, mean)

                risk = tick['ask'] - sl
                reward = tp - tick['ask']
                if risk > 0 and (reward / risk) >= 1.8: # R:R minimal dinaikkan ke 1.8
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 300
                        await self.telegram.send(
                            f" <b>Z-SCORE STATISTICAL BUY (Institutional)</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['ask']:.3f}\n"
                            f"Z-Score: <b>{z_score:.2f}σ</b> | Regime: SIDEWAYS\n"
                            f"Target Mean: {tp:.3f} | SL: {sl:.3f}",
                            with_keyboard=False
                        )

            # --- OVERBOUGHT REVERSION (Z >= +2.2) ---
            elif z_score >= self.z_threshold and upper_wick >= (body * 0.6):
                sl = self._format_price(symbol, h + (std * 0.5))
                tp = self._format_price(symbol, mean)

                risk = sl - tick['bid']
                reward = tick['bid'] - tp
                if risk > 0 and (reward / risk) >= 1.8: # R:R minimal dinaikkan ke 1.8
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 300
                        await self.telegram.send(
                            f" <b>Z-SCORE STATISTICAL SELL (Institutional)</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['bid']:.3f}\n"
                            f"Z-Score: <b>+{z_score:.2f}σ</b> | Regime: SIDEWAYS\n"
                            f"Target Mean: {tp:.3f} | SL: {sl:.3f}",
                            with_keyboard=False
                        )

