import asyncio
import logging, time, pandas as pd
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger(__name__)

class NewsStraddleEngine:
    """
    Institutional News Straddle Engine (Magic: 1006):
    Memasang Pending Order Breakout simetris menjelang rilis berita high-impact dengan OCO management.
    """
    MAGIC = 1006

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = 'exness_mt5'
        self.news = NewsFilter()
        self.pending_trades = {}
        self._cleanup_done = False

    async def _cleanup_straddle(self):
        await asyncio.sleep(3)
        try:
            import MetaTrader5 as mt5
            orders = mt5.orders_get()
            if orders:
                for o in orders:
                    if o.magic == self.MAGIC:
                        mt5.order_send({'action': mt5.TRADE_ACTION_REMOVE, 'order': o.ticket})
            logger.info("[News Straddle] Pembersihan order tertinggal berhasil.")
        except Exception as e: 
            logger.error(f"[News Straddle] Gagal cleanup: {e}")

    async def manage(self):
        if not getattr(cfg, 'STRADDLE_ENABLED', False): 
            return
        
        if not self._cleanup_done:
            self._cleanup_done = True
            asyncio.create_task(self._cleanup_straddle())
            
        client = self.clients.get(self.exchange)
        if not client or getattr(cfg, 'GLOBAL_HALT', False): 
            return

        is_news, news_msg = await self.news.is_high_impact_news_near(pre_min=10, post_min=0)
        
        target_symbols = ["XAUUSDm", "BTCUSDm"]
        
        for symbol in target_symbols:
            if not cfg.ACTIVE_SYMBOLS.get(symbol, True): 
                continue

            mt5_pos = await client.fetch_positions()
            if mt5_pos is None: 
                continue

            # --- OCO MANAGEMENT (One Cancels The Other) ---
            if symbol in self.pending_trades:
                p = self.pending_trades[symbol]
                age = time.time() - p['time']
                active_pos = [pos for pos in mt5_pos if pos['symbol'] == symbol and pos['magic'] == self.MAGIC]
                
                if len(active_pos) > 0:
                    if p['buy'] and not p.get('sell_cancelled', False):
                        if any(pos['side'] == 'BUY' for pos in active_pos):
                            await client.cancel_pending_order(p['sell'])
                            p['sell_cancelled'] = True
                    if p['sell'] and not p.get('buy_cancelled', False):
                        if any(pos['side'] == 'SELL' for pos in active_pos):
                            await client.cancel_pending_order(p['buy'])
                            p['buy_cancelled'] = True
                
                if age > 600:
                    if not p.get('buy_cancelled', False): 
                        await client.cancel_pending_order(p['buy'])
                    if not p.get('sell_cancelled', False): 
                        await client.cancel_pending_order(p['sell'])
                    del self.pending_trades[symbol]
                continue

            # --- PLACING NEW STRADDLE ---
            if is_news:
                m5 = await client.fetch_ohlcv(symbol, '5m', 20)
                if not m5: 
                    continue
                
                df = pd.DataFrame(m5, columns=['t','o','h','l','c','v'])
                range_high = df['h'].iloc[-6:].max()
                range_low = df['l'].iloc[-6:].min()
                box_range = range_high - range_low
                
                tick = await client.fetch_ticker(symbol)
                if not tick or tick['ask'] <= 0: 
                    continue

                # Buffer dinamis 0.05% dari harga
                buffer = (tick['ask'] * 0.0005)
                buy_price = round(range_high + buffer, 2)
                sell_price = round(range_low - buffer, 2)
                
                # [FIX] SL lebih ketat (50% dari lebar box)
                sl_dist = box_range * 0.5
                buy_sl = round(buy_price - sl_dist, 2)
                sell_sl = round(sell_price + sl_dist, 2)
                
                # TP RRR 1:2
                buy_tp = round(buy_price + (sl_dist * 2.0), 2)
                sell_tp = round(sell_price - (sl_dist * 2.0), 2)
                
                lot = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('lot_size', 0.02)

                async with self.order_lock.get(symbol):
                    buy_o = await client.create_pending_order(symbol, 'buy', lot, buy_price, magic=self.MAGIC, sl=buy_sl, tp=buy_tp)
                    sell_o = await client.create_pending_order(symbol, 'sell', lot, sell_price, magic=self.MAGIC, sl=sell_sl, tp=sell_tp)
                
                if buy_o and sell_o:
                    self.pending_trades[symbol] = {
                        'buy': buy_o['ticket'], 
                        'sell': sell_o['ticket'],
                        'buy_cancelled': False, 
                        'sell_cancelled': False, 
                        'time': time.time()
                    }
                    await self.telegram.send(
                        f" <b>NEWS STRADDLE PLACED ({symbol})</b>\n"
                        f"Info: {news_msg}\n"
                        f"Buy Stop: {buy_price} (SL: {buy_sl})\n"
                        f"Sell Stop: {sell_price} (SL: {sell_sl})", 
                        with_keyboard=False
                    )
