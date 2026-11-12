import logging, time, pandas as pd, numpy as np
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class FibonacciEngine:
    """
    Engine Fibonacci Golden Zone Institusional dengan ATR-Based Risk Management 
    dan Market Regime Filtering.
    """
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state; self.clients = clients; self.telegram = telegram; self.order_lock = order_lock
        self.exchange = 'exness_mt5'; self.news = NewsFilter()
        self._news_block = False; self._last_news_check = 0

    def _calculate_atr(self, df, period=14):
        high_low = df['h'] - df['l']
        high_close = np.abs(df['h'] - df['c'].shift())
        low_close = np.abs(df['l'] - df['c'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.ewm(span=period, adjust=False).mean()

    async def manage(self):
        if not getattr(cfg, 'FIBONACCI_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        # Berita High-Impact Filter
        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(30, 15)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in ["XAUUSDm", "BTCUSDm"]:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: continue
            existing = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.FIBONACCI_MAGIC]
            if existing: continue

            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0: continue

            # Validasi Spread Maksimal (Mencegah Slippage Ekstrem)
            spread = tick['ask'] - tick['bid']
            if spread > (tick['ask'] * 0.0005): continue

            m15 = await client.fetch_ohlcv(symbol, '15m', 80)
            if not m15 or len(m15) < 60: continue
            df = pd.DataFrame(m15, columns=['t','o','h','l','c','v'])
            
            # Dinamis ATR untuk normalisasi volatilitas
            df['atr'] = self._calculate_atr(df, 14)
            current_atr = df['atr'].iloc[-1]

            # Swing High / Swing Low Lookback (50 bar M15)
            recent_high = df['h'].iloc[-50:].max()
            recent_low = df['l'].iloc[-50:].min()
            diff = recent_high - recent_low
            if diff <= 0: continue

            price = tick['ask']
            fib_618 = recent_high - (diff * 0.618)
            fib_786 = recent_high - (diff * 0.786)
            
            last = df.iloc[-2]
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)

            # Golden Zone BUY: Pullback ke 61.8% - 78.6% dengan konfirmasi Price Action (Rejection Wick)
            body = abs(last['c'] - last['o'])
            lower_wick = min(last['o'], last['c']) - last['l']
            
            is_golden_zone = fib_786 <= price <= fib_618
            has_rejection = lower_wick >= (body * 1.5) if body > 0 else True

            if is_golden_zone and has_rejection:
                # Stop Loss ditempatkan di bawah swing atau berdasarkan ATR buffer dinamis
                sl = min(last['l'], recent_low) - (current_atr * 0.5)
                risk = abs(price - sl)
                if risk <= 0: continue
                
                # Minimum R:R 1:2.5 dengan Trailing Runner readiness
                tp = price + (risk * 2.5)
                
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=cfg.FIBONACCI_MAGIC, sl=float(sl), tp=float(tp))
                if order:
                    await self.telegram.send(f" <b>INSTITUTIONAL FIBO BUY</b> {symbol} @ {price:.2f}\nSL: {sl:.2f} | TP: {tp:.2f} (ATR: {current_atr:.2f})", with_keyboard=False)
