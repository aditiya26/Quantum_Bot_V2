import asyncio, logging, time, numpy as np, pandas as pd, datetime as dt
import config.settings as cfg
from engine.institutional_orchestrator import InstitutionalPreventiveShield, MarketRegimeRouter

logger = logging.getLogger("VWAP_Micro_M5_Institutional")

# [FIX] Nama class dikembalikan ke VWAPMicroBounceM5Engine agar sesuai dengan position_manager.py
class VWAPMicroBounceM5Engine:
    MAGIC = 2001

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.cooldowns = {}

    def _format_price(self, symbol: str, price: float) -> float:
        digits = 2 if 'XAU' in symbol or 'BTC' in symbol or 'USTEC' in symbol else (3 if 'JPY' in symbol else 5)
        return round(float(price), digits)

    async def manage(self):
        if not getattr(cfg, 'VWAP_MICRO_M5_ENABLED', True): 
            return
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): 
            return

        shield = InstitutionalPreventiveShield(client)
        regime_router = MarketRegimeRouter(client)

        for symbol in ["XAUUSDm", "BTCUSDm", "GBPJPYm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): 
                continue
            if time.time() < self.cooldowns.get(symbol, 0): 
                continue

            regime = await regime_router.evaluate_regime(symbol)
            if regime == "TRENDING":
                continue

            if not await shield.validate_market_safety(symbol):
                continue

            mt5_pos = await client.fetch_positions()
            if any(p['symbol'] == symbol and p['magic'] == self.MAGIC for p in mt5_pos or []):
                continue

            bars = await client.fetch_ohlcv(symbol, '5m', 60)
            if not bars or len(bars) < 30: 
                continue

            df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
            tp = (df['h'] + df['l'] + df['c']) / 3.0
            vol = df['v'].replace(0, 1.0)
            
            vwap = np.cumsum(tp * vol) / np.cumsum(vol)
            current_vwap = vwap.iloc[-2]

            variance = np.cumsum(vol * np.square(tp - vwap)) / np.cumsum(vol)
            std = np.sqrt(variance).iloc[-2] if variance.iloc[-2] > 0 else (current_vwap * 0.001)

            upper_band = current_vwap + (2.0 * std)
            lower_band = current_vwap - (2.0 * std)

            prev = df.iloc[-2]
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: 
                continue

            avg_vol = df['v'].iloc[-20:-1].mean()
            is_vol_surge = prev['v'] >= (avg_vol * 1.5)
            if not is_vol_surge:
                continue

            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)

            if prev['h'] >= upper_band and prev['c'] < prev['o']:
                sl = self._format_price(symbol, prev['h'] + (std * 0.5))
                tp = self._format_price(symbol, current_vwap)
                risk = sl - tick['bid']
                reward = tick['bid'] - tp

                if risk > 0 and (reward / risk) >= 1.5:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'sell', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 600
                        await self.telegram.send(
                            f" <b>VWAP MICRO M5 SELL FADE</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['bid']:.3f}\n"
                            f"Band Sentuh + Vol Surge | SL: {sl} | TP: {tp}",
                            with_keyboard=False
                        )

            elif prev['l'] <= lower_band and prev['c'] > prev['o']:
                sl = self._format_price(symbol, prev['l'] - (std * 0.5))
                tp = self._format_price(symbol, current_vwap)
                risk = tick['ask'] - sl
                reward = tp - tick['ask']

                if risk > 0 and (reward / risk) >= 1.5:
                    async with self.order_lock.get(symbol):
                        order = await client.create_order(symbol, 'buy', lot, magic=self.MAGIC, sl=sl, tp=tp)
                    if order:
                        self.cooldowns[symbol] = time.time() + 600
                        await self.telegram.send(
                            f" <b>VWAP MICRO M5 BUY FADE</b> #{order.get('ticket')}\n"
                            f"Simbol: <b>{symbol}</b> @ {tick['ask']:.3f}\n"
                            f"Band Sentuh + Vol Surge | SL: {sl} | TP: {tp}",
                            with_keyboard=False
                        )
