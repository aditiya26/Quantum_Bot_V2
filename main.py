"""
QuantumBot Pro V4.5 - High-Frequency Institutional Quant Engine (Fixed & Non-Blocking)
Optimized for: Active Measured Entries, No Premature BEP, Adaptive ATR Trailing
"""
import sys, io, os, time, asyncio, logging, logging.handlers, gc, zipfile, shutil
from datetime import datetime, timezone

if sys.stdout is not None: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr is not None: sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import config.settings as cfg
from core.memory_guard import MemoryGuard
from core.github_backup import run_github_backup
from core.exness_mt5 import ExnessMT5Client
from core.state_manager import StateManager
from core.order_lock import OrderLockManager
from core.smart_market_data_hub import SmartMarketDataHub
from core.unified_quant_core import UnifiedQuantCore
from engine.risk_manager import RiskManager
from engine.position_manager import PositionManager
from engine.lot_sizer import LotSizer
from monitor.telegram import TelegramNotifier

log_fmt = logging.Formatter('%(asctime)s | %(levelname)-7s | %(name)s: %(message)s')
log_h = logging.handlers.RotatingFileHandler('quant_v4.log', maxBytes=15*1024*1024, backupCount=5, encoding='utf-8')
log_h.setFormatter(log_fmt)
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(log_h)
if sys.stdout is not None:
    sh = logging.StreamHandler()
    sh.setFormatter(log_fmt)
    root.addHandler(sh)

class MultiExchangeBotV4:
    def __init__(self):
        logging.info("Menginisialisasi QuantumBot Pro V4.5 (Active Measured Institutional Engine)...")
        self.state = StateManager()
        self.memory_guard = MemoryGuard()
        self.order_lock = OrderLockManager()
        self.client = ExnessMT5Client()
        self.clients = {"exness_mt5": self.client}
        self.lot_sizer = LotSizer(base_equity=getattr(cfg, 'BASE_EQUITY', 2000.0))
        self.telegram = TelegramNotifier(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID)
        
        self.data_hub = SmartMarketDataHub(self.client, cfg.SYMBOLS)
        self.quant_core = UnifiedQuantCore(self.client)
        self.global_risk = RiskManager(self.state, self.clients, self.telegram, self.order_lock)
        self.pos_mgr = PositionManager(
            state=self.state, clients=self.clients, telegram=self.telegram,
            lot_sizer=self.lot_sizer, risk_mgr=self.global_risk,
            order_lock=self.order_lock, data_hub=self.data_hub, quant_core=self.quant_core
        )
        
        self.telegram.bot_instance = self
        self.telegram.order_lock = self.order_lock
        self.telegram.clients = self.clients
        self.telegram.pos_mgr = self.pos_mgr
        self.telegram.risk_mgr = self.global_risk

        self.stop_event = asyncio.Event()
        self.current_equity = 2000.0
        self._last_equity_ts = 0.0
        self._last_sync_time = 0.0
        self._last_heartbeat_time = time.time()
        self._last_gc_time = time.time()
        self._last_github_backup = time.time()

    def release_all_halts(self):
        cfg.GLOBAL_HALT = False
        try:
            if os.path.exists('HALT.flag'): os.remove('HALT.flag')
        except: pass
        if self.global_risk: self.global_risk.reset_emergency_halt()
        if self.pos_mgr: self.pos_mgr.clear_all_halted_engines()
        logging.info(" SELURUH STATUS HALT BERHASIL DILEPASKAN.")

    async def supervise(self, coro_func, name, *args):
        backoff = 2
        while not self.stop_event.is_set():
            try:
                await coro_func(*args)
                backoff = 2
                if not self.stop_event.is_set():
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError: break
            except Exception as e:
                logging.error(f"[Supervisor] Exception in {name}: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30)


    async def generate_excel_report(self):
        import csv
        from datetime import datetime
        try:
            logging.info('[ALL-TIME] Menarik history 90 hari dari MT5...')
            history = await self.client.fetch_history(days=90)
            logging.info(f'[ALL-TIME] Data ditarik: {len(history) if history else 0} transaksi.')
            if not history:
                return None

            STRATEGY_NAMES = {
                0: 'CloseBy/Unknown', 111: 'Grid', 222: 'Hybrid', 333: 'SMC Institutional',
                444: 'Pure Hedge', 555: 'Trend Rider', 666: 'Edge Scalper',
                777: 'PA VWAP', 888: 'Crash Sniper', 999: 'London Breakout',
                1001: 'Squeeze Breakout', 1002: 'NY Reversal', 1003: 'Fibo Golden Zone',
                1004: 'Divergence', 1005: 'Liquidity Sweep', 1006: 'News Straddle',
                1007: 'ICT Bullet', 1008: 'Vol POC', 1009: 'Z-Score M15',
                1010: 'Session Sweep', 1011: 'VWAP Bounce',
                2001: 'VWAP Micro M5', 2002: 'Z-Score Stat', 2004: 'Safe Grid',
                2005: 'VWAP Trend Exp', 2006: 'VW-ORB', 4001: 'VWAP Trend Riding',
                3001: 'Donchian Breakout', 3002: 'Supertrend', 3003: 'Pivot Bounce',
                3004: 'Asian Box', 3005: 'Round Number', 3006: 'Climax Fade', 3007: 'Stat-Arb',
                9999: 'Isolated Hedge',
                234000: 'Manual Order'
            }

            def get_reason(rc):
                if rc == 3: return "HIT SL"
                if rc == 4: return "HIT TP"
                if rc == 0: return "MANUAL"
                return "OTHER"

            filename = "Trade_History_Report.csv"
            with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["Waktu Close", "Ticket", "Simbol", "Arah", "Volume (Lot)", "Harga Close", "Net Profit (USD)", "Magic", "Engine (Strategi)", "Alasan Close"])

                total_profit = 0
                trade_count = 0

                for d in history:
                    profit = d.get('profit', 0) + d.get('commission', 0) + d.get('swap', 0)
                    total_profit += profit
                    trade_count += 1

                    magic = d.get('magic', 0)
                    engine_name = STRATEGY_NAMES.get(magic, f"Unknown ({magic})")
                    reason_str = get_reason(d.get('reason', -1))
                    
                    side = "SELL" if d.get('type') == 0 else "BUY"
                    
                    time_str = datetime.fromtimestamp(d.get('time', 0)).strftime('%Y-%m-%d %H:%M:%S')

                    writer.writerow([
                        time_str, d.get('ticket', 0), d.get('symbol', ''), side, d.get('volume', 0.01),
                        d.get('price', 0.0), round(profit, 2), magic, engine_name, reason_str
                    ])

                writer.writerow([])
                writer.writerow(["TOTAL", "", "", "", "", "", round(total_profit, 2), "", "", ""])

            return filename
        except Exception as e:
            logging.error(f"Generate excel report error: {e}")
            return None


    async def analyze_performance(self):
        from engine.trade_analyzer import TradeHistoryAuditor
        filepath = await self.generate_excel_report()
        if not filepath:
            return "Gagal membuat CSV."
        auditor = TradeHistoryAuditor(filepath)
        res = auditor.audit_performance()
        
        if "error" in res:
            return res["error"]
            
        msg = (
            f" <b>INSTITUTIONAL AUDIT REPORT</b>\n"
            f"----------------------------------------\n"
            f"Total Trades : <b>{res['total_trades']}</b>\n"
            f"Net Profit   : <b>${res['net_profit']:,.2f}</b>\n"
            f"Win Rate     : <b>{res['win_rate']}%</b>\n"
            f"Profit Factor: <b>{res['profit_factor']}</b>\n"
            f"Expectancy   : <b>${res['expectancy']}/trade</b>\n"
            f"Max Drawdown : <b>${res['max_dd']:,.2f}</b>\n"
            f"----------------------------------------\n"
        )
        if res['profit_factor'] >= 1.5 and res['expectancy'] > 0:
            msg += " <b>VERDICT: PROFITABLE (Sehat)</b>"
        else:
            msg += " <b>VERDICT: CRITICAL (Perlu Evaluasi)</b>"
            
        return msg



    async def generate_all_time_engine_report(self):
        try:
            history = await self.client.fetch_history(days=30)
            IGNORE_MAGICS = [111, 2004, 111, 444, 777, 3007, 234000, 2003] # Grid, Pure Hedge, PA VWAP, Stat-Arb, Manual
            if not history:
                return " <b>LAPORAN ENGINE</b>\n\nTidak ada data history dalam 30 hari terakhir."

            STRATEGY_NAMES = {
                0: 'CloseBy/Unknown', 111: 'Grid', 222: 'Hybrid', 333: 'SMC Institutional',
                444: 'Pure Hedge', 555: 'Trend Rider', 666: 'Edge Scalper',
                777: 'PA VWAP', 888: 'Crash Sniper', 999: 'London Breakout',
                1001: 'Squeeze Breakout', 1002: 'NY Reversal', 1003: 'Fibo Golden Zone',
                1004: 'Divergence', 1005: 'Liquidity Sweep', 1006: 'News Straddle',
                1007: 'ICT Bullet', 1008: 'Vol POC', 1009: 'Z-Score M15',
                1010: 'Session Sweep', 1011: 'VWAP Bounce',
                2001: 'VWAP Micro M5', 2002: 'Z-Score Stat', 2004: 'Safe Grid',
                2005: 'VWAP Trend Exp', 2006: 'VW-ORB', 4001: 'VWAP Trend Riding',
                3001: 'Donchian Breakout', 3002: 'Supertrend', 3003: 'Pivot Bounce',
                3004: 'Asian Box', 3005: 'Round Number', 3006: 'Climax Fade', 3007: 'Stat-Arb',
                9999: 'Isolated Hedge',
                234000: 'Manual Order'
            }

            stats = {}
            for d in history:
                magic = d.get('magic', 0)
                if magic in IGNORE_MAGICS:
                    continue
                profit = d.get('profit', 0) + d.get('commission', 0) + d.get('swap', 0)
                
                if magic not in stats:
                    stats[magic] = {'net': 0.0, 'trades': 0, 'wins': 0}
                
                stats[magic]['net'] += profit
                stats[magic]['trades'] += 1
                if profit > 0:
                    stats[magic]['wins'] += 1

            if not stats:
                return " <b>LAPORAN ENGINE</b>\n\nBelum ada history trade tertutup."

            # Urutkan dari profit tertinggi ke terendah
            sorted_stats = sorted(stats.items(), key=lambda x: x[1]['net'], reverse=True)
            
            txt = " <b>PERFORMA SEMUA ENGINE (30 HARI)</b>\n"
            txt += "\n"
            
            # Tampilkan SEMUA engine dengan format 1 baris
            for m, s in sorted_stats:
                strat_name = STRATEGY_NAMES.get(m, f"Magic {m}")
                s_wr = (s['wins'] / s['trades'] * 100) if s['trades'] > 0 else 0
                em = '' if s['net'] > 0 else ''
                
                # Format ringkas:  Nama: +$10.00 | WR: 50% (10x)
                txt += f"{em} <b>{strat_name}</b>: {'+' if s['net']>=0 else ''}${s['net']:.2f} | WR: {s_wr:.0f}% ({s['trades']}x)\n"

            return txt
            
        except Exception as e:
            logging.error(f"All time report error: {e}")
            return f" <b>Gagal menarik data:</b> {str(e)[:100]}"

    async def high_frequency_loop(self):
        while not self.stop_event.is_set():
            try:
                if os.path.exists('HALT.flag') or getattr(cfg, 'GLOBAL_HALT', False):
                    cfg.GLOBAL_HALT = True
                    logging.warning("GLOBAL HALT AKTIF: Bot beristirahat total (Menunggu instruksi).")
                    await asyncio.sleep(10)
                    continue

                now = time.time()
                await self.data_hub.refresh_snapshot()

                if now - self._last_equity_ts > 10.0:
                    eq = await self.client.fetch_balance()
                    if eq > 0:
                        self.current_equity = eq
                        self.lot_sizer.update_equity(eq)
                    self._last_equity_ts = now

                if now - self._last_sync_time > 2.0:
                    await self.pos_mgr.sync_and_adopt()
                    self._last_sync_time = now


                # [FITUR BARU] Auto Garbage Collection (Membersihkan RAM setiap 15 menit)
                if now - self._last_gc_time > 300:
                    self.memory_guard.check_and_auto_trim(data_hub=self.data_hub, mt5_client=self.client)
                    self._last_gc_time = now
                    logging.info("[SYSTEM] Garbage Collector berjalan. RAM dibersihkan.")


                # [FITUR BARU] Auto-Backup ke GitHub setiap 6 Jam
                if now - self._last_github_backup > 21600:
                    logging.info("[SYSTEM] Memulai Auto-Backup ke GitHub...")
                    asyncio.get_running_loop().run_in_executor(None, run_github_backup)
                    self._last_github_backup = now


                # [OPTIMASI] Sinkronisasi Cache Isolated ke exness_mt5
                if hasattr(self.pos_mgr.isolated, 'isolated_pairs'):
                    pairs = self.pos_mgr.isolated.isolated_pairs
                    iso_set = set(str(k) for k in pairs.keys()) | set(str(v) for v in pairs.values())
                    self.client._isolated_tickets_cache = iso_set

                await self.pos_mgr.manage_all(equity=self.current_equity)
                isolated_pairs = getattr(self.pos_mgr.isolated, 'isolated_pairs', {})
                await self.global_risk.manage(self.current_equity, isolated_pairs=isolated_pairs)

                if now - self._last_heartbeat_time > 14400:
                    self._last_heartbeat_time = now
                    asyncio.create_task(self.telegram.send(f" <b>HEARTBEAT: BOT AKTIF & SEHAT</b>\nEquity: <b>${self.current_equity:,.2f}</b>", with_keyboard=False))

            except Exception as e:
                logging.error(f"[HF Loop Error]: {e}", exc_info=True)
            
            await asyncio.sleep(1.2)

    async def _perform_backup(self):
        _today = datetime.now(cfg.LOCAL_TZ).strftime('%Y-%m-%d_%H-%M')
        try:
            logging.info("[BACKUP] Memulai proses backup...")
            os.makedirs("backups", exist_ok=True)
            zip_name = f"backups/QuantumBot_Backup_{_today}.zip"
            
            temp_db = None
            if os.path.exists("quantum_state.db"):
                try:
                    temp_db = f"temp_backup_{int(time.time())}.db"
                    shutil.copy2("quantum_state.db", temp_db)
                except PermissionError:
                    logging.warning("[BACKUP] Database dikunci, lanjut backup kode tanpa DB.")
                    temp_db = None

            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root_dir, dirs, files in os.walk("."):
                    if any(x in root_dir for x in ["backups", "__pycache__", ".git", "venv", "temp_backup"]): continue
                    for file in files:
                        if file.endswith(('.log', '.zip', '.db-wal', '.db-shm', 'quantum_state.db')): continue
                        try:
                            zipf.write(os.path.join(root_dir, file), os.path.relpath(os.path.join(root_dir, file), "."))
                        except PermissionError:
                            pass
                
                if temp_db and os.path.exists(temp_db):
                    zipf.write(temp_db, "quantum_state.db")
                    try: os.remove(temp_db)
                    except: pass

            logging.info(f"[BACKUP] File ZIP berhasil dibuat: {zip_name}")
            
            await asyncio.sleep(2)
            
            if os.path.exists(zip_name):
                logging.info("[BACKUP] Mengirim ke Telegram...")
                await asyncio.wait_for(
                    self.telegram.send_document(zip_name, caption=f" <b>Full Code Auto-Backup Sukses</b>\nModal Terlindungi: `${self.current_equity:,.2f}`"),
                    timeout=60.0
                )
                logging.info("[BACKUP] SUKSES! Backup terkirim ke Telegram.")
        except Exception as e:
            logging.error(f"[BACKUP GAGAL] {e}")


    async def run(self):
        cfg.validate_config()
        await self.state.init_db()
        await self.client.init_connection()
        await self._perform_backup() # Jalankan backup saat start
        real_eq = await self.client.fetch_balance()
        if real_eq > 0:
            self.current_equity = real_eq
            self.lot_sizer.update_equity(real_eq)
        self._last_equity_ts = time.time()
        
        msg = (
            f" <b>QUANTUM BOT PRO V4.5 ONLINE</b>\n"
            f"Modal: <b>${self.current_equity:,.2f} USD</b>\n"
            f"Mode: <b>Measured Active (Zero Idle)</b>\n"
            f"Trailing: <b>Adaptive 2.0x ATR (Stiff BEP Disabled)</b>"
        )
        await self.telegram.send(msg, with_keyboard=True)
        
        tasks = [
            asyncio.create_task(self.supervise(self.telegram.poll_commands, "TelegramBot", self.state, self.clients, self.global_risk)),
            asyncio.create_task(self.supervise(self.high_frequency_loop, "HFLoop")),
        ]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    gc.freeze() # [OPTIMASI] Mencegah GC memindai objek permanen
    bot = MultiExchangeBotV4()
    try: 
        asyncio.run(bot.run())
    except KeyboardInterrupt: 
        logging.info("Bot dimatikan oleh user (CTRL+C).")
    finally:
        try: asyncio.run(bot.telegram.close())
        except: pass
        try: asyncio.run(bot.client.close())
        except: pass
