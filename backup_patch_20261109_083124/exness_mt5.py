"""
Exness MT5 Low-Latency Async Wrapper V4.0 (Forensics Edition)
"""
import asyncio, logging, time, concurrent.futures, os
from datetime import datetime, timedelta, timezone
import config.settings as cfg
from core.exchange_base import ExchangeBase
from core.ml_confidence_gate import MLConfidenceGate

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 C-extension tidak terdeteksi.")

class ExnessMT5Client(ExchangeBase):
    def __init__(self):
        self._ex = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='mt5_pool')
        self._pos_cache = None
        self._pos_cache_ts = 0.0
        self._pos_cache_ttl = 1.0
        self._ohlcv_cache = {}
        self._tick_cache = {}
        self.ai_allowed = {}
        self._magic_cache = None
        self._magic_lock = asyncio.Lock() # [FIX] Lock untuk mencegah korupsi JSON
        self.banned_strategies = {} 
        self.connected = False
        
        class ExchangeMock:
            def __init__(self, client): self.client = client
            async def fetch_ticker(self, symbol): return await self.client.fetch_ticker(symbol)
        self.exchange = ExchangeMock(self)

    async def init_connection(self):
        if not MT5_AVAILABLE: return False
        def _connect():
            if mt5.initialize():
                acc = mt5.account_info()
                logger.info(f" MT5 Terhubung! Login: {acc.login if acc else 'N/A'} | Equity: ${acc.equity:,.2f}")
                return True
            return False
        loop = asyncio.get_running_loop()
        self.connected = await loop.run_in_executor(self._ex, _connect)
        return self.connected

    def _ensure_connection(self):
        if not MT5_AVAILABLE: return
        try:
            info = mt5.terminal_info()
            if info is None or not info.connected:
                mt5.shutdown()
                time.sleep(0.3)
                mt5.initialize()
        except Exception: pass

    def clear_caches(self):
        self._ohlcv_cache.clear()
        self._tick_cache.clear()
        self._pos_cache = None

    async def fetch_balance(self) -> float:
        if not MT5_AVAILABLE: return getattr(cfg, 'BASE_EQUITY', 2000.0)
        def _get():
            self._ensure_connection()
            acc = mt5.account_info()
            return float(acc.equity) if acc else 0.0
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=4.0)
        except: return 0.0

    async def fetch_margin_level(self) -> float:
        if not MT5_AVAILABLE: return 9999.0
        def _get():
            self._ensure_connection()
            acc = mt5.account_info()
            return float(acc.margin_level) if acc and acc.margin > 0 else 9999.0
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=4.0)
        except: return 9999.0

    async def fetch_ticker(self, symbol: str) -> dict:
        if not MT5_AVAILABLE: return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}
        now = time.time()
        if symbol in self._tick_cache:
            ts, data = self._tick_cache[symbol]
            if now - ts < 0.35: return data

        def _get():
            self._ensure_connection()
            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.bid <= 0 or tick.ask <= 0:
                return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}
                
            # [FIX] Validasi usia tick - toleran time_msc=0, threshold 5s
            current_time_ms = int(time.time() * 1000)
            if hasattr(tick, 'time_msc') and tick.time_msc > 0:
                age_ms = current_time_ms - tick.time_msc
                if age_ms > 5000:
                    logger.warning(f"Stale tick {symbol} (age {age_ms}ms). Harga dibatalkan.")
                    return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}
                
            last = tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2.0
            return {'last': last, 'bid': tick.bid, 'ask': tick.ask, 'spread': round(tick.ask - tick.bid, 5)}
        
        try:
            res = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=2.5)
            self._tick_cache[symbol] = (now, res)
            return res
        except: return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}

    async def fetch_positions(self, force=False):
        if not MT5_AVAILABLE: return []
        now = time.time()
        if not force and self._pos_cache is not None and (now - self._pos_cache_ts) < self._pos_cache_ttl:
            return self._pos_cache
        def _get():
            self._ensure_connection()
            pos = mt5.positions_get()
            if pos is None: return []
            return [{'symbol': p.symbol, 'side': 'BUY' if p.type == mt5.POSITION_TYPE_BUY else 'SELL',
                     'contracts': float(p.volume), 'entryPrice': float(p.price_open), 'ticket': p.ticket,
                     'magic': int(p.magic), 'profit': float(p.profit), 'sl': float(p.sl), 'tp': float(p.tp), 'time': p.time} for p in pos]
        try:
            res = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=3.5)
            self._pos_cache = res
            self._pos_cache_ts = now
            return res
        except: return self._pos_cache or []

    async def fetch_today_deals(self):
        if not MT5_AVAILABLE: return []
        def _get():
            self._ensure_connection()
            now_utc = datetime.now(timezone.utc)
            start_of_day = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
            deals = mt5.history_deals_get(start_of_day, now_utc + timedelta(days=1))
            if deals is None: return []
            
            # [FIX] Filter Swap & Komisi agar tidak memicu false trigger di RiskManager
            res = []
            for d in deals:
                if d.entry in (1, 2, 3):
                    net_profit = float(d.profit) + float(d.commission) + float(d.swap)
                    res.append({'ticket': d.ticket, 'position_id': d.position_id, 'symbol': d.symbol, 'magic': d.magic,
                                'profit': net_profit, 'time': d.time})
            return res
            
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=6.0)
        except: return []

    async def fetch_history(self, days=30):
        if not MT5_AVAILABLE: return []
        def _get():
            self._ensure_connection()
            now_utc = datetime.now(timezone.utc)
            from_date = now_utc - timedelta(days=max(days, 1)) if days > 0 else datetime(2020, 1, 1, tzinfo=timezone.utc)
            deals = mt5.history_deals_get(from_date, now_utc + timedelta(days=1))
            if deals is None: return []
            return [{'magic': d.magic, 'ticket': d.position_id, 'symbol': d.symbol, 'profit': float(d.profit),
                     'commission': float(d.commission), 'swap': float(d.swap), 'time': d.time} for d in deals if d.entry in (1, 2, 3)]
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=10.0)
        except: return []

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 50):
        if not MT5_AVAILABLE: return []
        cache_key = f"{symbol}_{timeframe}_{limit}"
        now = time.time()
        if len(self._ohlcv_cache) > 100: self._ohlcv_cache.clear()
        ttl_map = {'1m': 5, '5m': 15, '15m': 30, '1h': 60}
        ttl = ttl_map.get(timeframe, 20)
        if cache_key in self._ohlcv_cache and (now - self._ohlcv_cache[cache_key]['ts'] < ttl):
            return self._ohlcv_cache[cache_key]['data']

        tf_map = {'1m': mt5.TIMEFRAME_M1, '5m': mt5.TIMEFRAME_M5, '15m': mt5.TIMEFRAME_M15, '1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4, '1d': mt5.TIMEFRAME_D1}
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M15)

        def _get():
            self._ensure_connection()
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, min(limit, 500))
            if rates is None: return []
            return [[int(r['time'])*1000, float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['tick_volume'])] for r in rates]
        try:
            result = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _get), timeout=5.0)
            self._ohlcv_cache[cache_key] = {'ts': now, 'data': result}
            return result
        except: return []

    def _get_filling(self, symbol):
        if not MT5_AVAILABLE: return 0
        si = mt5.symbol_info(symbol)
        if si:
            if si.filling_mode & 2: return mt5.ORDER_FILLING_IOC
            if si.filling_mode & 1: return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    async def create_order(self, symbol: str, side: str, amount: float, price: float = None, magic: int = 234000, sl: float = None, tp: float = None) -> dict:
        if not MT5_AVAILABLE: return None
        
        # [INSTITUTIONAL PREVENTIVE SHIELD]
        from engine.institutional_orchestrator import InstitutionalPreventiveShield
        shield = InstitutionalPreventiveShield(self)
        is_safe = await shield.validate_market_safety(symbol)
        if not is_safe:
            logger.warning(f"ORDER DIBLOKIR SHIELD [{symbol}]: Volatilitas ekstrem atau Spread melebar.")
            return None

        # [ML CONFIDENCE GATE] Hanya untuk Strategi Tren
        if magic in [333, 555, 222, 999, 1007, 4001, 2006]:
            ml_gate = MLConfidenceGate(self, min_confidence=getattr(cfg, 'ML_MIN_CONFIDENCE', 70.0))
            is_ml_approved = await ml_gate.validate_signal(symbol, side)
            if not is_ml_approved:
                return None

        # [CORRELATION GUARD] Cegah order berlawanan arah di simbol yang sama (Opsional, demi keamanan ekstra)
        # Jika Anda ingin bot bisa Hedge, hapus blok ini. Tapi untuk mencegah Naked Exposure:
        positions = await self.fetch_positions()
        if positions:
            for p in positions:
                if p['symbol'] == symbol and p['side'] != side.upper() and p['magic'] != magic:
                    # Jika sudah ada posisi berlawanan dari strategi LAIN, blokir order ini
                    logger.warning(f"CORRELATION GUARD [{symbol}]: Ditolak {side.upper()} karena ada posisi {p['side']} aktif dari Magic {p['magic']}.")
                    return None
                    
        # PENGAMAN TERAKHIR: Tolak order jika lot sudah penuh (kecuali untuk tutup posisi)
        positions = await self.fetch_positions()
        total_lots = sum(p['contracts'] for p in positions) if positions else 0
        if total_lots >= getattr(cfg, 'MAX_TOTAL_OPEN_LOTS', 0.50):
            logger.warning(f"ORDER DITOLAK [{symbol}]: Batas maksimal lot ({getattr(cfg, 'MAX_TOTAL_OPEN_LOTS', 0.50)}) tercapai.")
            return None

        TREND_MAGICS = [333, 555, 222, 999]
        if magic in TREND_MAGICS and self.ai_allowed and symbol in self.ai_allowed:
            allowed_dir = self.ai_allowed[symbol]
            if allowed_dir != 'NEUTRAL' and side.upper() != allowed_dir:
                return None

        def _send():
            self._ensure_connection()
            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.ask <= 0: return None
            
            spread_pct = (tick.ask - tick.bid) / tick.ask * 100
            if spread_pct > getattr(cfg, 'GLOBAL_MAX_SPREAD_PCT', 0.20):
                return None

            si = mt5.symbol_info(symbol)
            
            # [FIX] Gunakan batas dari settings.py (Single Source of Truth)
            sym_conf = cfg.SYMBOL_CONFIGS.get(symbol, {})
            max_allowed_lot = sym_conf.get('max_lot', 0.05)
            safe_amount = min(amount, max_allowed_lot)
            
            vol = round(round(safe_amount / (si.volume_step if si else 0.01)) * (si.volume_step if si else 0.01), 2)
            if si: vol = max(si.volume_min, min(si.volume_max, vol))
            digits = si.digits if si else 4

            req = {
                'action': mt5.TRADE_ACTION_DEAL, 'symbol': symbol, 'volume': vol,
                'type': mt5.ORDER_TYPE_BUY if side.lower() == 'buy' else mt5.ORDER_TYPE_SELL,
                'price': tick.ask if side.lower() == 'buy' else tick.bid, 'deviation': 35, 'magic': magic,
                'comment': f'Q3_{magic}', 'type_time': mt5.ORDER_TIME_GTC, 'type_filling': self._get_filling(symbol)
            }
            if sl and sl > 0: req['sl'] = float(round(sl, digits))
            if tp and tp > 0: req['tp'] = float(round(tp, digits))

            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                try:
                    import json
                    if self._magic_cache is None:
                        if os.path.exists('magic_tracker.json'):
                            with open('magic_tracker.json', 'r') as tf: self._magic_cache = json.load(tf)
                        else:
                            self._magic_cache = {}
                    
                    self._magic_cache[str(res.order)] = magic
                    if len(self._magic_cache) > 3000:
                        self._magic_cache = dict(list(self._magic_cache.items())[-3000:])
                    
                    import asyncio
                    asyncio.create_task(self._save_magic_tracker())
                except: pass
                return {'id': res.order, 'ticket': res.order, 'price': res.price, 'volume': res.volume, 'average': res.price}
            return None
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _send), timeout=6.0)
        except: return None

    async def safe_update_sl(self, symbol, side, amount, new_sl_price, old_sl_id=None):
        if not MT5_AVAILABLE: return str(old_sl_id), 0.0
        def _update():
            self._ensure_connection()
            positions = mt5.positions_get(symbol=symbol)
            if not positions: return str(old_sl_id), 0.0
            target = next((p for p in positions if str(p.ticket) == str(old_sl_id)), None)
            if not target: return str(old_sl_id), 0.0
            si = mt5.symbol_info(symbol)
            req = {'action': mt5.TRADE_ACTION_SLTP, 'symbol': symbol, 'position': target.ticket,
                   'sl': float(round(new_sl_price, si.digits if si else 4)), 'tp': target.tp}
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return str(target.ticket), float(new_sl_price)
            return str(old_sl_id), 0.0
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _update), timeout=5.0)
        except: return str(old_sl_id), 0.0

    async def close_position(self, symbol, amount, current_side, old_sl_id=None, magic=234000):
        if not MT5_AVAILABLE: return None
        def _close():
            self._ensure_connection()
            positions = mt5.positions_get(symbol=symbol)
            if not positions: return None
            target = next((p for p in positions if str(p.ticket) == str(old_sl_id)), None)
            if not target: return None
            tick = mt5.symbol_info_tick(symbol)
            req = {'action': mt5.TRADE_ACTION_DEAL, 'symbol': symbol, 'volume': float(target.volume),
                   'type': mt5.ORDER_TYPE_SELL if target.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                   'position': target.ticket, 'price': tick.bid if target.type == mt5.POSITION_TYPE_BUY else tick.ask,
                   'deviation': 40, 'magic': target.magic, 'comment': 'Q3_Close',
                   'type_time': mt5.ORDER_TIME_GTC, 'type_filling': self._get_filling(symbol)}
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return {'id': res.order}
            return None
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _close), timeout=6.0)
        except: return None

    async def close_by(self, symbol, ticket1, ticket2, magic=234000):
        if not MT5_AVAILABLE: return None
        def _cb():
            self._ensure_connection()
            pos1 = mt5.positions_get(ticket=int(ticket1))
            actual_magic = pos1[0].magic if pos1 else magic
            req = {
                'action': mt5.TRADE_ACTION_CLOSE_BY,
                'symbol': symbol,
                'position': int(ticket1),
                'position_by': int(ticket2),
                'magic': actual_magic,
                'comment': 'Q3_CloseBy'
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return {'id': res.order}
            return None
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _cb), timeout=6.0)
        except: return None

    async def close_partial_and_resolve(self, symbol: str, ticket: int, amount: float, current_side: str) -> int:
        if not MT5_AVAILABLE: return ticket
        def _run():
            self._ensure_connection()
            positions = mt5.positions_get(ticket=int(ticket))
            if not positions: return 0
            pos = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            req = {'action': mt5.TRADE_ACTION_DEAL, 'symbol': symbol, 'volume': float(amount),
                   'type': mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                   'position': pos.ticket, 'price': tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask,
                   'deviation': 40, 'magic': pos.magic, 'comment': 'Q3_Partial',
                   'type_time': mt5.ORDER_TIME_GTC, 'type_filling': self._get_filling(symbol)}
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                time.sleep(0.15)
                new_positions = mt5.positions_get(symbol=symbol)
                if new_positions:
                    for np in new_positions:
                        if np.magic == pos.magic and np.volume < pos.volume:
                            return np.ticket
                return pos.ticket
            return 0
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _run), timeout=8.0)
        except: return ticket

    async def create_pending_order(self, symbol, side, amount, price, magic=234000, sl=None, tp=None):
        if not MT5_AVAILABLE: return None
        def _create():
            self._ensure_connection()
            si = mt5.symbol_info(symbol)
            req = {
                'action': mt5.TRADE_ACTION_PENDING,
                'symbol': symbol,
                'volume': float(amount),
                'type': mt5.ORDER_TYPE_BUY_STOP if side.lower() == 'buy' else mt5.ORDER_TYPE_SELL_STOP,
                'price': float(price),
                'deviation': 40,
                'magic': magic,
                'comment': 'Q3_Straddle',
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': self._get_filling(symbol)
            }
            digits = si.digits if si else 4
            if sl and sl > 0: req['sl'] = float(round(sl, digits))
            if tp and tp > 0: req['tp'] = float(round(tp, digits))
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return {'id': res.order, 'ticket': res.order}
            return None
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _create), timeout=10.0)
        except: return None

    async def cancel_pending_order(self, ticket):
        if not MT5_AVAILABLE: return False
        def _cancel():
            self._ensure_connection()
            req = {'action': mt5.TRADE_ACTION_REMOVE, 'order': int(ticket)}
            res = mt5.order_send(req)
            return True if res and res.retcode == mt5.TRADE_RETCODE_DONE else False
        try: return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._ex, _cancel), timeout=10.0)
        except: return False

    async def get_indicator_feature(self, symbol: str, feature_name: str) -> float:
        if not hasattr(self, '_l3_cache'): self._l3_cache = {}
        key = f"{symbol}_{feature_name}"
        now = time.time()
        if key in self._l3_cache and (now - self._l3_cache[key]['ts'] < 25):
            return self._l3_cache[key]['val']
            
        val = 0.0
        if feature_name == 'EMA50_H1':
            bars = await self.fetch_ohlcv(symbol, '1h', 60)
            if bars and len(bars) >= 50:
                val = float(sum(b[4] for b in bars[-50:]) / 50)
        elif feature_name == 'ATR14_M15':
            bars = await self.fetch_ohlcv(symbol, '15m', 20)
            if bars and len(bars) >= 15:
                tr = [max(bars[i][2] - bars[i][3], abs(bars[i][2] - bars[i-1][4]), abs(bars[i][3] - bars[i-1][4])) for i in range(1, len(bars))]
                val = float(sum(tr[-14:]) / 14)
        
        self._l3_cache[key] = {'val': val, 'ts': now}
        return val

    async def _save_magic_tracker(self):
        async with self._magic_lock:
            try:
                import json
                await asyncio.sleep(0.5)
                if self._magic_cache:
                    temp_file = 'magic_tracker.json.tmp'
                    with open(temp_file, 'w') as tf: json.dump(self._magic_cache, tf)
                    os.replace(temp_file, 'magic_tracker.json')
            except Exception as e:
                logger.error(f"Gagal menyimpan magic tracker: {e}")

    async def close(self):
        if MT5_AVAILABLE:
            try: mt5.shutdown()
            except: pass
        self._ex.shutdown(wait=False)


