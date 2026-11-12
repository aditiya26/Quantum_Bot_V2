import logging, time, pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class SMCEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.news = NewsFilter()
        self._news_block = False
        self._last_news_check = 0

    async def manage(self):
        if not getattr(cfg, 'SMC_ENABLED', False): return
        client = self.clients.get(self.exchange)
        if not client: return

        if time.time() - self._last_news_check > 300:
            self._news_block, _ = await self.news.is_high_impact_news_near(20, 10)
            self._last_news_check = time.time()
        if self._news_block: return

        for symbol in cfg.SMC_SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): continue
            
            # AMBIL TICK DI AWAL SEBELUM DIGUNAKAN! (Bugfix UnboundLocalError)
            tick = await client.exchange.fetch_ticker(symbol)
            if not tick or tick['ask'] <= 0 or tick['bid'] <= 0: continue

            spread_pct = (tick['ask'] - tick['bid']) / tick['ask'] * 100
            if spread_pct > getattr(cfg, 'GLOBAL_MAX_SPREAD_PCT', 0.09): continue

            mt5_pos = await client.fetch_positions()
            positions = [p for p in mt5_pos if p['symbol'] == symbol and p['magic'] == cfg.SMC_MAGIC] if mt5_pos else []

            max_layers = 3
            if len(positions) >= max_layers: continue

            existing_side = None
            if len(positions) > 0:
                existing_side = positions[0]['side']
                total_profit_pct = sum(
                    ((tick['bid'] - p['entryPrice']) / p['entryPrice'] * 100) if existing_side == 'BUY'
                    else ((p['entryPrice'] - tick['ask']) / p['entryPrice'] * 100)
                    for p in positions
                )
                if total_profit_pct < 0.4: continue

            h1 = await client.fetch_ohlcv(symbol, '1h', 60)
            m15 = await client.fetch_ohlcv(symbol, '15m', 60)
            if not h1 or not m15 or len(m15) < 30: continue

            df_h1 = pd.DataFrame(h1, columns=['t','o','h','l','c','v'])
            df_m15 = pd.DataFrame(m15, columns=['t','o','h','l','c','v'])
            
            ema50_h1 = df_h1['c'].ewm(span=50, adjust=False).mean().iloc[-1]
            ema200_h1 = df_h1['c'].ewm(span=200, adjust=False).mean().iloc[-1]
            h1_uptrend = ema50_h1 > ema200_h1
            h1_downtrend = ema50_h1 < ema200_h1

            swing_low = df_m15['l'].iloc[-25:-2].min()
            swing_high = df_m15['h'].iloc[-25:-2].max()
            last = df_m15.iloc[-2]
            prev = df_m15.iloc[-3]

            price = tick['ask']
            lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.03)

            # SMC BUY: Sweep di bawah swing low + close kembali ke atas + Uptrend H1
            if h1_uptrend and prev['l'] < swing_low and last['c'] > swing_low and existing_side != 'SELL':
                risk_dist = abs(last['c'] - swing_low) + (price * 0.001)
                sl = price - risk_dist
                tp = price + (risk_dist * 3.0) # R:R 1:3.0
                
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'buy', lot, magic=cfg.SMC_MAGIC, sl=sl, tp=tp)
                if order:
                    layer_num = len(positions) + 1
                    await self.telegram.send(
                        f"🏛️ <b>SMC ORDER BLOCK BUY</b> (Layer {layer_num})\n"
                        f"Pair: <b>{symbol}</b> @ {price:.3f}\n"
                        f"SL: {sl:.3f} | TP: {tp:.3f} (R:R 1:3.0)\n"
                        f"Alasan: Liquidity Sweep Swing Low + Institutional Demand",
                        with_keyboard=False
                    )

            # SMC SELL: Sweep di atas swing high + close kembali ke bawah + Downtrend H1
            elif h1_downtrend and prev['h'] > swing_high and last['c'] < swing_high and existing_side != 'BUY':
                risk_dist = abs(swing_high - last['c']) + (price * 0.001)
                sl = price + risk_dist
                tp = price - (risk_dist * 3.0) # R:R 1:3.0
                
                async with self.order_lock.get(symbol):
                    order = await client.create_order(symbol, 'sell', lot, magic=cfg.SMC_MAGIC, sl=sl, tp=tp)
                if order:
                    layer_num = len(positions) + 1
                    await self.telegram.send(
                        f"🏛️ <b>SMC ORDER BLOCK SELL</b> (Layer {layer_num})\n"
                        f"Pair: <b>{symbol}</b> @ {price:.3f}\n"
                        f"SL: {sl:.3f} | TP: {tp:.3f} (R:R 1:3.0)\n"
                        f"Alasan: Liquidity Sweep Swing High + Institutional Supply",
                        with_keyboard=False
                    )
