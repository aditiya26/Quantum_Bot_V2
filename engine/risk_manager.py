import logging, time, os, json, asyncio
from datetime import datetime as _dt
from decimal import Decimal
import config.settings as cfg
from core.dynamic_escalator import DynamicEscalatorV3

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        
        self.peak_equity = Decimal(str(getattr(cfg, 'BASE_EQUITY', 2000.0)))
        self.start_of_day_equity = Decimal("0.0")
        self.last_reset_date = None
        self._daily_dd_notified = False
        
        self._emergency_notified = False
        self.pause_until = 0
        self.escalator = DynamicEscalatorV3(clients.get('exness_mt5'), state, telegram)
        self._realized_halt_today = False
        self._last_hist_check = 0
        
        # Memori Cooldown
        self.pair_cooldown_until = {}
        self.cooldown_file = "cooldown_state.json"
        self._load_cooldowns()
        
        self._weekend_closed_date = None
        self.halt_reset_time = time.time()

    def _load_cooldowns(self):
        if os.path.exists(self.cooldown_file):
            try:
                with open(self.cooldown_file, 'r') as f:
                    self.pair_cooldown_until = json.load(f)
            except: pass

    def _save_cooldowns(self):
        try:
            with open(self.cooldown_file, 'w') as f:
                json.dump(self.pair_cooldown_until, f)
        except: pass

    async def manage(self, equity: float, isolated_pairs: dict = None):
        if not getattr(cfg, 'GLOBAL_RISK_ENABLED', True) or getattr(cfg, 'GLOBAL_HALT', False): return
        if equity <= 0.0: return
        
        current_eq = Decimal(str(equity))
        if current_eq > self.peak_equity:
            self.peak_equity = current_eq

        # Daily Circuit Breaker
        now_local = _dt.now(cfg.LOCAL_TZ)
        current_date = now_local.date()
        
        if self.last_reset_date != current_date:
            self.start_of_day_equity = current_eq
            self.last_reset_date = current_date
            self._realized_halt_today = False 
            self._daily_dd_notified = False
            logger.info(f"[CIRCUIT BREAKER] Hari baru dimulai. Equity awal: ${self.start_of_day_equity}")

        if self.start_of_day_equity > 0:
            dd_pct = ((self.start_of_day_equity - current_eq) / self.start_of_day_equity) * 100
            max_dd_pct = Decimal(str(getattr(cfg, 'MAX_DAILY_DRAWDOWN_PCT', 4.0)))
            
            if False: # [FIX] Daily DD Breaker DINONAKTIFKAN
                self._daily_dd_notified = True
                cfg.GLOBAL_HALT = True
                self.pause_until = time.time() + (24 * 3600)
                
                try:
                    with open('HALT.flag', 'w') as f: f.write('DAILY_DD_BREACH')
                except: pass
                
                if self.telegram:
                    asyncio.create_task(self.telegram.send(
                        f" <b>CIRCUIT BREAKER TERPICU!</b>\n"
                        f"Drawdown Harian mencapai <b>{dd_pct:.2f}%</b>\n"
                        f"Batas Maksimal: {max_dd_pct}%\n"
                        f"Bot DIMATIKAN sampai besok jam 00:00 WIB.", 
                        with_keyboard=True
                    ))
                return

        if self.pause_until > 0:
            if time.time() < self.pause_until:
                return
            else:
                self.pause_until = 0
                self._emergency_notified = False
                await self.telegram.send(" <b>MASA JEDA SELESAI</b>\nBot bangun dan siap mencari peluang kembali.", with_keyboard=False)

        client = self.clients.get(self.exchange)
        if not client: return
        
        # PAIR COOLDOWN
        now_ts = time.time()
        if now_ts - self._last_hist_check > 60:
            self._last_hist_check = now_ts
            try:
                history = await client.fetch_today_deals()
                if history:
                    history.sort(key=lambda x: x['time'], reverse=True)
                    pair_losses = {}
                    for h in history:
                        sym = h.get('symbol', 'UNKNOWN')
                        if not getattr(cfg, 'ACTIVE_SYMBOLS', {}).get(sym, False):
                            continue
                            
                        if h['profit'] < 0: 
                            pair_losses[sym] = pair_losses.get(sym, 0) + 1
                        else: 
                            pair_losses[sym] = 0
                            
                    for sym, count in pair_losses.items():
                        if count >= 3:
                            if sym not in self.pair_cooldown_until or now_ts > self.pair_cooldown_until[sym]:
                                self.pair_cooldown_until[sym] = now_ts + 3600
                                await self.telegram.send(
                                    f" <b>PAIR COOLDOWN</b>: {sym} 3x Loss beruntun. Jeda 60 menit.",
                                    with_keyboard=False
                                )
                                self._save_cooldowns() # Simpan ke memori
            except Exception as e:
                logger.error(f"Deal history check error: {e}")

        # REALIZED PNL
        try:
            today_deals = await client.fetch_today_deals()
            if today_deals:
                valid_deals = [d for d in today_deals if d.get('time', 0) >= self.halt_reset_time]
                realized_pnl = sum(d.get('profit', 0) for d in valid_deals)
                
                daily_limit = getattr(cfg, 'DAILY_LOSS_LIMIT_USD', 100.0)
                if False: # [FIX] Realized Daily Loss DINONAKTIFKAN
                    if not getattr(self, '_realized_halt_today', False):
                        self._realized_halt_today = True
                        cfg.GLOBAL_HALT = True
                        self.pause_until = time.time() + (8 * 3600)
                        await self.telegram.send(
                            f" <b>DAILY REALIZED HARD STOP</b> \n"
                            f"Total Rugi Hari Ini (Realized): <b>-${abs(realized_pnl):.2f}</b>\n"
                            f"Batas: -${daily_limit:.2f}\n"
                            f"Bot akan beristirahat sampai besok.", with_keyboard=True)
                        return
        except Exception as e:
            logger.error(f"Realized PnL Check error: {e}")
            
        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return

        # EMERGENCY
        margin_level = await client.fetch_margin_level()
        min_margin = getattr(cfg, 'MIN_MARGIN_LEVEL_PCT', 350.0)
        total_unrealized = sum(p.get('profit', 0.0) for p in mt5_pos) * getattr(cfg, "MT5_PROFIT_SCALE", 1.0)
        global_stop = getattr(cfg, 'GLOBAL_HARD_STOP_USD', 120.0)

        is_margin_critical = False # [FIX] Full Margin
        is_hard_loss = (total_unrealized <= -global_stop)
        
        drawdown_from_peak = self.peak_equity - current_eq
        is_daily_breach = (drawdown_from_peak >= Decimal(str(getattr(cfg, 'DAILY_LOSS_LIMIT_USD', 85.0))))

        # [FIX V5] Nyalakan kembali Emergency Protocol (Airbag Level Akun)
        if is_hard_loss or is_daily_breach or is_margin_critical:
            self._emergency_notified = True
            reason = "Margin Level Rendah" if is_margin_critical else ("Hard Stop Terlampaui" if is_hard_loss else "Daily Drawdown Breach")
            msg = (
                f" <b>EMERGENCY PROTOCOL ACTIVATED</b> \n\n"
                f"Pemicu: <b>{reason}</b>\n"
                f"Margin Level: <b>{margin_level:.1f}%</b>\n"
                f"Total Floating: <b>${total_unrealized:.2f}</b>\n"
                f"Tindakan: Menutup semua posisi demi proteksi modal ${equity:.2f}."
            )
            await self.telegram.send(msg, with_keyboard=True)
            for p in mt5_pos:
                try:
                    async with self.order_lock.get(p['symbol']):
                        await client.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                except: pass
            return

        # WEEKEND
        is_friday_night = (now_local.weekday() == 4 and now_local.hour >= getattr(cfg, 'WEEKEND_CLOSE_HOUR', 22))
        _today = now_local.strftime('%Y-%m-%d')
        if getattr(cfg, 'WEEKEND_CLOSE_ENABLED', True) and is_friday_night:
            if self._weekend_closed_date != _today:
                self._weekend_closed_date = _today
                closed_count = 0
                for p in mt5_pos:
                    if p['symbol'] in getattr(cfg, 'WEEKEND_ACTIVE_SYMBOLS', ['BTCUSDm', 'BTCUSD']): continue
                    try:
                        async with self.order_lock.get(p['symbol']):
                            await client.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                            closed_count += 1
                    except: pass
                if closed_count > 0:
                    await self.telegram.send(
                        f" <b>Weekend Protection</b>: {closed_count} posisi Forex/Emas ditutup. Crypto (BTC) tetap berjalan.",
                        with_keyboard=False
                    )

        # ESCALATOR
        db_positions = {p['ticket']: p for p in await self.state.get_open_positions()}
        for p in mt5_pos:
            try:
                tick = await client.exchange.fetch_ticker(p['symbol'])
                if tick:
                    db_pos = db_positions.get(p['ticket'], {})
                    pos_data = {
                        'ticket': p['ticket'], 'symbol': p['symbol'], 'side': p['side'],
                        'entry_price': p['entryPrice'], 'amount': p['contracts'],
                        'stage': db_pos.get('stage', 0), 'current_sl': db_pos.get('current_sl', p.get('sl', 0.0)),
                        'status': 'OPEN', 'exchange': 'EXNESS_MT5',
                        'magic_number': p.get('magic', 0), 'initial_amount': p['contracts']
                    }
                    await self.escalator.evaluate_position(pos_data, tick['bid'], tick['ask'], isolated_pairs=isolated_pairs)
            except Exception as e:
                logger.error(f"Escalator error {p.get('symbol')}: {e}")

    def reset_emergency_halt(self):
        self.pause_until = 0
        self._emergency_notified = False
        self._realized_halt_today = False
        self._daily_dd_notified = False
        self.pair_cooldown_until.clear()
        self.halt_reset_time = time.time()
        self._save_cooldowns() 
        
        # [FIX FATAL] Netralkan sirkuit breaker agar tidak langsung HALT lagi
        self.start_of_day_equity = Decimal("0.0")
        self.peak_equity = Decimal("0.0")
        
        logger.info("RiskManager: Status HALT direset. Circuit breaker dinetralkan.")
