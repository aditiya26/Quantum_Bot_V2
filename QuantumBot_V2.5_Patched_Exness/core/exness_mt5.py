"""
QuantumBot Pro - Exness MT5 High-Speed Driver (Isolated Single-Worker)
Eliminates MT5 C-API thread contention, reduces latency to < 50ms, and prevents freeze.
"""
import asyncio, logging, time, os, json, concurrent.futures
from datetime import datetime, timedelta, timezone
import config.settings as cfg
from core.exchange_base import ExchangeBase

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 C-extension tidak terdeteksi. Driver menggunakan mode Mock/Bridge.")

class MT5ExchangeWrapper:
    def __init__(self, client):
        self.client = client

    async def fetch_ticker(self, symbol):
        return await self.client.fetch_ticker(symbol)

class ExnessMT5Client(ExchangeBase):
    def __init__(self):
        # Dedicated 1-worker threadpool menjamin keamanan MT5 C-API yang not thread-safe
        self._ex = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='mt5_isolated')
        self.exchange = MT5ExchangeWrapper(self)
        self._pos_cache = None
        self._pos_cache_ts = 0.0
        self._pos_cache_ttl = 1.0 # Cache posisi pendek (1 detik)
        self._ohlcv_cache = {}
        self._tick_cache = {}
        self.ai_allowed = {}
        
        if MT5_AVAILABLE:
            self._connect_mt5()

    def _connect_mt5(self):
        for attempt in range(5):
            if mt5.initialize():
                acc = mt5.account_info()
                if acc:
                    logger.info(f"✅ MT5 Terhubung! Login: {acc.login} | Server: {acc.server} | Equity: ${acc.equity:,.2f}")
                    return True
            time.sleep(1.0)
        logger.error(f"❌ Gagal inisialisasi MT5: {mt5.last_error() if mt5 else 'N/A'}")
        return False

    def _ensure_connection(self):
        if not MT5_AVAILABLE: return
        try:
            info = mt5.terminal_info()
            if info is None or not info.connected:
                logger.warning("Koneksi MT5 terputus, mencoba auto-reconnect...")
                mt5.shutdown()
                time.sleep(0.5)
                mt5.initialize()
        except: pass

    def clear_caches(self):
        """Dipanggil oleh maintenance loop untuk membebaskan memory."""
        self._ohlcv_cache.clear()
        self._tick_cache.clear()
        self._pos_cache = None

    async def fetch_ticker(self, symbol: str) -> dict:
        if not MT5_AVAILABLE:
            return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}
            
        now = time.time()
        # Cache tick 300ms untuk mencegah spam IPC ratusan kali per detik
        if symbol in self._tick_cache:
            ts, data = self._tick_cache[symbol]
            if now - ts < 0.35:
                return data

        def _get():
            self._ensure_connection()
            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.bid <= 0 or tick.ask <= 0:
                return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}
            last = tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2.0
            spread = round((tick.ask - tick.bid), 5)
            return {'last': last, 'bid': tick.bid, 'ask': tick.ask, 'spread': spread}

        loop = asyncio.get_running_loop()
        try:
            res = await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=3.5)
            self._tick_cache[symbol] = (now, res)
            return res
        except:
            return {'last': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0}

    async def fetch_balance(self) -> float:
        if not MT5_AVAILABLE: return getattr(cfg, 'BASE_EQUITY', 2000.0)
        def _get():
            self._ensure_connection()
            acc = mt5.account_info()
            return float(acc.equity) if acc else 0.0
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=4.0)
        except: return 0.0

    async def fetch_margin_level(self) -> float:
        if not MT5_AVAILABLE: return 9999.0
        def _get():
            self._ensure_connection()
            acc = mt5.account_info()
            return float(acc.margin_level) if acc and acc.margin > 0 else 9999.0
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=4.0)
        except: return 9999.0

    async def fetch_positions(self, force=False):
        if not MT5_AVAILABLE: return []
        now = time.time()
        if not force and self._pos_cache is not None and (now - self._pos_cache_ts) < self._pos_cache_ttl:
            return self._pos_cache

        def _get():
            self._ensure_connection()
            pos = mt5.positions_get()
            if pos is None: return []
            return [{
                'symbol': p.symbol,
                'side': 'BUY' if p.type == mt5.POSITION_TYPE_BUY else 'SELL',
                'contracts': float(p.volume),
                'entryPrice': float(p.price_open),
                'ticket': p.ticket,
                'magic': int(p.magic),
                'profit': float(p.profit),
                'sl': float(p.sl),
                'tp': float(p.tp),
                'time': p.time
            } for p in pos]

        loop = asyncio.get_running_loop()
        try:
            res = await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=4.5)
            self._pos_cache = res
            self._pos_cache_ts = now
            return res
        except:
            return self._pos_cache or []

    async def fetch_today_deals(self):
        if not MT5_AVAILABLE: return []
        def _get():
            self._ensure_connection()
            now_utc = datetime.now(timezone.utc)
            start_of_day = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
            deals = mt5.history_deals_get(start_of_day, now_utc + timedelta(days=1))
            if deals is None: return []
            res = []
            for d in deals:
                if d.entry in (1, 2, 3):
                    res.append({
                        'ticket': d.ticket,
                        'position_id': d.position_id,
                        'symbol': d.symbol,
                        'magic': d.magic,
                        'profit': float(d.profit) + float(d.commission) + float(d.swap),
                        'time': d.time
                    })
            return res
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=6.0)
        except: return []

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 50):
        if not MT5_AVAILABLE: return []
        cache_key = f"{symbol}_{timeframe}_{limit}"
        now = time.time()
        
        if cache_key in self._ohlcv_cache and (now - self._ohlcv_cache[cache_key]['ts'] < 15):
            return self._ohlcv_cache[cache_key]['data']

        tf_map = {
            '1m': mt5.TIMEFRAME_M1, '5m': mt5.TIMEFRAME_M5,
            '15m': mt5.TIMEFRAME_M15, '1h': mt5.TIMEFRAME_H1,
            '4h': mt5.TIMEFRAME_H4, '1d': mt5.TIMEFRAME_D1
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M15)

        def _get():
            self._ensure_connection()
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, min(limit, 60))
            if rates is None: return []
            return [[int(r['time'])*1000, float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['tick_volume'])] for r in rates]

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(loop.run_in_executor(self._ex, _get), timeout=5.0)
            self._ohlcv_cache[cache_key] = {'ts': now, 'data': result}
            return result
        except: return []

    def _get_filling(self, symbol):
        if not MT5_AVAILABLE: return 0
        si = mt5.symbol_info(symbol)
        if si:
            modes = si.filling_mode
            if modes & 2: return mt5.ORDER_FILLING_IOC
            if modes & 1: return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    async def create_order(self, symbol, side, amount, price=None, magic=234000, sl=None, tp=None):
        if not MT5_AVAILABLE: return None

        # Filter AI Gate: Hanya membatasi jika strategi bertentangan langsung dengan tren kuat
        TREND_MAGICS = [333, 555, 222, 999]
        if magic in TREND_MAGICS and self.ai_allowed and symbol in self.ai_allowed:
            allowed_dir = self.ai_allowed[symbol]
            if allowed_dir != 'NEUTRAL' and side.upper() != allowed_dir:
                logger.warning(f"AI Matrix Filter: {symbol} {side} ditolak (Tren Dominan: {allowed_dir}).")
                return None

        def _create():
            self._ensure_connection()
            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.ask <= 0 or tick.bid <= 0: return None
            
            # Universal Spread Filter
            spread_pct = (tick.ask - tick.bid) / tick.ask * 100
            max_spread = getattr(cfg, 'GLOBAL_MAX_SPREAD_PCT', 0.08)
            if spread_pct > max_spread:
                logger.warning(f"Spread Block {symbol}: {spread_pct:.3f}% > {max_spread}%")
                return None

            si = mt5.symbol_info(symbol)
            volume = float(round(amount, 2))
            if si:
                step = si.volume_step or 0.01
                volume = round(round(volume / step) * step, 2)
                volume = max(si.volume_min, min(si.volume_max, volume))

            order_type = mt5.ORDER_TYPE_BUY if side.lower() == 'buy' else mt5.ORDER_TYPE_SELL
            exec_price = tick.ask if side.lower() == 'buy' else tick.bid

            # Deviation 60 points untuk eksekusi cepat tanpa requote
            req = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': symbol,
                'volume': volume,
                'type': order_type,
                'price': exec_price,
                'deviation': 60,
                'magic': magic,
                'comment': f'QBot_{magic}',
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': self._get_filling(symbol)
            }
            digits = si.digits if si else 4
            if sl and sl > 0: req['sl'] = float(round(sl, digits))
            if tp and tp > 0: req['tp'] = float(round(tp, digits))

            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return {'id': res.order, 'ticket': res.order, 'average': res.price, 'volume': res.volume}
            else:
                comment = res.comment if res else "Unknown Error"
                logger.warning(f"Order Gagal [{symbol} {side}]: {comment}")
                return None

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _create), timeout=6.0)
        except Exception as e:
            logger.error(f"Error order {symbol}: {e}")
            return None

    async def safe_update_sl(self, symbol, side, amount, new_sl_price, old_sl_id=None):
        if not MT5_AVAILABLE: return str(old_sl_id), 0.0
        def _update():
            self._ensure_connection()
            positions = mt5.positions_get(symbol=symbol)
            if not positions: return str(old_sl_id), 0.0
            target = next((p for p in positions if str(p.ticket) == str(old_sl_id)), None)
            if not target:
                ttype = mt5.POSITION_TYPE_BUY if side in ('BUY', 'LONG') else mt5.POSITION_TYPE_SELL
                target = next((p for p in positions if p.type == ttype), None)
            if not target: return str(old_sl_id), 0.0

            si = mt5.symbol_info(symbol)
            digits = si.digits if si else 4
            req = {
                'action': mt5.TRADE_ACTION_SLTP,
                'symbol': symbol,
                'position': target.ticket,
                'sl': float(round(new_sl_price, digits)),
                'tp': target.tp
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return str(target.ticket), float(new_sl_price)
            return str(old_sl_id), 0.0

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _update), timeout=5.0)
        except: return str(old_sl_id), 0.0

    async def close_position(self, symbol, amount, current_side, old_sl_id=None, magic=234000):
        if not MT5_AVAILABLE: return None
        def _close():
            self._ensure_connection()
            positions = mt5.positions_get(symbol=symbol)
            if not positions: return None
            target = next((p for p in positions if str(p.ticket) == str(old_sl_id)), None)
            if not target:
                ttype = mt5.POSITION_TYPE_BUY if current_side in ('BUY', 'LONG') else mt5.POSITION_TYPE_SELL
                target = next((p for p in positions if p.type == ttype), None)
            if not target: return None

            tick = mt5.symbol_info_tick(symbol)
            req = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': symbol,
                'volume': float(target.volume),
                'type': mt5.ORDER_TYPE_SELL if target.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                'position': target.ticket,
                'price': tick.bid if target.type == mt5.POSITION_TYPE_BUY else tick.ask,
                'deviation': 60,
                'magic': target.magic,
                'comment': 'QBot_Close',
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': self._get_filling(symbol)
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return {'id': res.order}
            return None

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _close), timeout=6.0)
        except: return None

    async def close_partial(self, symbol, ticket, amount, current_side):
        def _close():
            self._ensure_connection()
            positions = mt5.positions_get(ticket=int(ticket))
            if not positions: return False
            target = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            req = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': symbol,
                'volume': float(amount),
                'type': mt5.ORDER_TYPE_SELL if target.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                'position': target.ticket,
                'price': tick.bid if target.type == mt5.POSITION_TYPE_BUY else tick.ask,
                'deviation': 60,
                'magic': target.magic,
                'comment': 'QBot_Partial',
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': self._get_filling(symbol)
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return True
            return False

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _close), timeout=6.0)
        except: return False

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
                'comment': 'QBot_CloseBy'
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._pos_cache = None
                return {'id': res.order}
            return None

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._ex, _cb), timeout=6.0)
        except: return None

    async def close(self):
        if MT5_AVAILABLE:
            try: mt5.shutdown()
            except: pass
        self._ex.shutdown(wait=False)
