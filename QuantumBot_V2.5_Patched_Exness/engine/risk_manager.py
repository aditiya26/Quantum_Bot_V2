"""
QuantumBot Pro - Real-Time Risk Manager V2.5
Terkalibrasi Modal $2,000 USD | Proteksi Drawdown Bertingkat
"""
import logging, time, os, json
from datetime import datetime as _dt
import config.settings as cfg
from core.dynamic_escalator import DynamicEscalator

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self._emergency_notified = False
        self._realized_halt_today = False
        self.pause_until = 0
        self.escalator = DynamicEscalator(clients.get('exness_mt5'), state, telegram)
        self._last_hist_check = 0
        self.pair_cooldown_until = {}
        self._weekend_closed_date = None

    def reset_emergency_halt(self):
        """Me-reset total status pause sehingga bot bisa langsung trading kembali."""
        self.pause_until = 0
        self._emergency_notified = False
        self._realized_halt_today = False
        self.pair_cooldown_until.clear()
        logger.info("RiskManager: Status HALT & jeda darurat berhasil direset.")

    async def manage(self, equity: float):
        if not getattr(cfg, 'GLOBAL_RISK_ENABLED', True): return
        if equity <= 0.0: return
        
        # Cek apakah sedang dalam masa pause
        if self.pause_until > 0:
            if time.time() < self.pause_until:
                return
            else:
                self.reset_emergency_halt()
                await self.telegram.send("🟢 <b>MASA JEDA PROTEKSI SELESAI</b>\nBot aktif dan siap mencari peluang baru.", with_keyboard=True)

        client = self.clients.get(self.exchange)
        if not client: return

        # 1. CEK REALIZED DAILY PNL (Kerugian yang sudah ditutup hari ini)
        now_ts = time.time()
        if now_ts - self._last_hist_check > 45:
            self._last_hist_check = now_ts
            try:
                today_deals = await client.fetch_today_deals()
                if today_deals:
                    realized_pnl = sum(d.get('profit', 0) for d in today_deals)
                    daily_limit = getattr(cfg, 'DAILY_LOSS_LIMIT_USD', 80.0) # 4% dari modal $2000
                    
                    if realized_pnl <= -daily_limit:
                        if not self._realized_halt_today:
                            self._realized_halt_today = True
                            cfg.GLOBAL_HALT = True
                            self.pause_until = time.time() + (6 * 3600) # Jeda 6 jam
                            try: open('HALT.flag', 'w').write('1')
                            except: pass
                            await self.telegram.send(
                                f"🚨 <b>DAILY REALIZED HARD STOP HIT</b>\n\n"
                                f"Total Realized Loss Hari Ini: <b>-${abs(realized_pnl):.2f} USD</b>\n"
                                f"Batas Proteksi: -${daily_limit:.2f}\n"
                                f"Bot dijeda otomatis demi melindungi sisa modal ${equity:,.2f}.\n"
                                f"Gunakan tombol <b>LEPAS HALT</b> di Telegram jika ingin mengaktifkan ulang lebih awal.",
                                with_keyboard=True
                            )
                            return
            except Exception as e:
                logger.error(f"Realized PnL Check error: {e}")

        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return

        # 2. EMERGENCY PROTOCOL UNREALIZED (Margin Critical / Max Floating Loss)
        margin_level = await client.fetch_margin_level()
        min_margin = getattr(cfg, 'MIN_MARGIN_LEVEL_PCT', 350.0)
        total_unrealized = sum(p.get('profit', 0.0) for p in mt5_pos) * getattr(cfg, 'MT5_PROFIT_SCALE', 1.0)
        global_stop = getattr(cfg, 'GLOBAL_HARD_STOP_USD', 100.0) # 5% floating stop

        is_margin_critical = (margin_level < min_margin)
        is_hard_loss = (total_unrealized <= -global_stop)

        if (is_margin_critical or is_hard_loss) and not self._emergency_notified:
            self._emergency_notified = True
            cfg.GLOBAL_HALT = True
            try: open('HALT.flag', 'w').write('1')
            except: pass
            
            reason = f"Margin Level Rendah ({margin_level:.1f}%)" if is_margin_critical else f"Floating Loss Terlampaui (-${abs(total_unrealized):.2f})"
            msg = (
                f"🚨 <b>CIRCUIT BREAKER: POSISI DITUTUP OTOMATIS</b> 🚨\n\n"
                f"Pemicu: <b>{reason}</b>\n"
                f"Tindakan: Menutup seluruh posisi terbuka demi mengamankan modal ${equity:,.2f}.\n"
                f"Bot masuk mode JEDA. Tekan <b>LEPAS HALT</b> di menu Dashboard untuk mengaktifkan kembali."
            )
            await self.telegram.send(msg, with_keyboard=True)
            for p in mt5_pos:
                try:
                    async with self.order_lock.get(p['symbol']):
                        await client.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                except: pass
            return

        # 3. WEEKEND PROTECTION (Jumat Malam: Auto-flat TradFi, Simpan Crypto BTC)
        now_local = _dt.now(cfg.LOCAL_TZ)
        is_friday_night = (now_local.weekday() == 4 and now_local.hour >= getattr(cfg, 'WEEKEND_CLOSE_HOUR', 22))
        _today = now_local.strftime('%Y-%m-%d')
        if getattr(cfg, 'WEEKEND_CLOSE_ENABLED', True) and is_friday_night:
            if self._weekend_closed_date != _today:
                self._weekend_closed_date = _today
                closed_count = 0
                for p in mt5_pos:
                    if p['symbol'] in getattr(cfg, 'WEEKEND_ACTIVE_SYMBOLS', ['BTCUSDm', 'BTCUSD']):
                        continue
                    try:
                        async with self.order_lock.get(p['symbol']):
                            await client.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                            closed_count += 1
                    except: pass
                if closed_count > 0:
                    await self.telegram.send(
                        f"🛡️ <b>Weekend Protection Aktif</b>: {closed_count} posisi Forex/Emas ditutup untuk menghindari gap akhir pekan. Crypto (BTC) tetap berjalan.",
                        with_keyboard=False
                    )

        # 4. TRAILING STOP & DYNAMIC ESCALATOR
        db_positions = {p['ticket']: p for p in await self.state.get_open_positions()}
        for p in mt5_pos:
            try:
                tick = await client.exchange.fetch_ticker(p['symbol'])
                if tick and tick['bid'] > 0:
                    db_pos = db_positions.get(p['ticket'], {})
                    pos_data = {
                        'ticket': p['ticket'],
                        'symbol': p['symbol'],
                        'side': p['side'],
                        'entry_price': p['entryPrice'],
                        'amount': p['contracts'],
                        'stage': db_pos.get('stage', 0),
                        'current_sl': db_pos.get('current_sl', p.get('sl', 0.0)),
                        'status': 'OPEN',
                        'exchange': 'EXNESS_MT5',
                        'magic_number': p.get('magic', 0),
                        'initial_amount': p['contracts']
                    }
                    await self.escalator.evaluate_position(pos_data, tick['bid'], tick['ask'])
            except Exception as e:
                logger.error(f"Escalator error {p.get('symbol')}: {e}")
