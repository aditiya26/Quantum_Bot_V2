"""
QuantumBot Pro V3.0 - High-Frequency Institutional Quant Engine (Non-Blocking Backup)
"""
import sys, io, os, time, asyncio, logging, logging.handlers
from datetime import datetime, timezone
import zipfile, shutil

if sys.stdout is not None: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr is not None: sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import config.settings as cfg
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
log_h = logging.handlers.RotatingFileHandler('quant_v3.log', maxBytes=15*1024*1024, backupCount=5, encoding='utf-8')
log_h.setFormatter(log_fmt)
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(log_h)
if sys.stdout is not None:
    sh = logging.StreamHandler()
    sh.setFormatter(log_fmt)
    root.addHandler(sh)

class MultiExchangeBotV3:
    def __init__(self):
        logging.info("Menginisialisasi QuantumBot Pro V3.0 (Zero-Idle Institutional Engine)...")
        self.state = StateManager()
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

    def release_all_halts(self):
        cfg.GLOBAL_HALT = False
        try:
            if os.path.exists('HALT.flag'): os.remove('HALT.flag')
        except: pass
        if self.global_risk: self.global_risk.reset_emergency_halt()
        if self.pos_mgr: self.pos_mgr.clear_all_halted_engines()
        logging.info(" SELURUH STATUS HALT BERHASIL DILEPASKAN.")

    def get_live_status(self):
        import datetime
        now_str = datetime.datetime.now(cfg.LOCAL_TZ).strftime('%H:%M:%S')
        status = (
            f"=== LIVE BOT STATUS ({now_str} WIB) ===\n"
            f"Equity      : $\n"
            f"Status Loop : AKTIF (Scan 1.2s)\n"
            f"Market Hub  : OK\n"
            f"Risk Engine : AKTIF\n"
            f"----------------------------------------\n"
        )
        return status

    async def _perform_backup(self):
        _today = datetime.now(cfg.LOCAL_TZ).strftime('%Y-%m-%d')
        try:
            os.makedirs("backups", exist_ok=True)
            zip_name = f"backups/QuantumBot_Backup_{_today}.zip"
            if os.path.exists("quantum_state.db"):
                shutil.copy2("quantum_state.db", "temp_backup.db")
            
            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root_dir, dirs, files in os.walk("."):
                    if any(x in root_dir for x in ["backups", "__pycache__", ".git", "venv"]): continue
                    for file in files:
                        # FIX: Jangan zip database asli dua kali
                        if file.endswith(('.log', '.zip', '.db-wal', '.db-shm', 'temp_backup.db', 'quantum_state.db')): continue
                        try:
                            zipf.write(os.path.join(root_dir, file), os.path.relpath(os.path.join(root_dir, file), "."))
                        except PermissionError:
                            pass
                
                if os.path.exists("temp_backup.db"):
                    zipf.write("temp_backup.db", "quantum_state.db")
                    try: os.remove("temp_backup.db")
                    except: pass
            
            logging.info(f"Backup file ZIP sukses dibuat: {zip_name}")
            # Kirim file dengan batas waktu (timeout) 60 detik agar tidak hang selamanya
            await asyncio.wait_for(
                self.telegram.send_document(zip_name, caption=f" <b>Full Code Auto-Backup Sukses</b>\nModal Terlindungi: `${self.current_equity:,.2f}`"),
                timeout=60.0
            )
            logging.info("Backup berhasil dikirim ke Telegram.")
        except Exception as e:
            logging.error(f"Backup Error: {e}")

    async def supervise(self, coro_func, name, *args):
        backoff = 2
        while not self.stop_event.is_set():
            try:
                await coro_func(*args)
                backoff = 2
            except asyncio.CancelledError: break
            except Exception as e:
                logging.error(f"[Supervisor] Exception in {name}: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30)

    async def high_frequency_loop(self):
        while not self.stop_event.is_set():
            try:
                if os.path.exists('HALT.flag'):
                    cfg.GLOBAL_HALT = True
                    await asyncio.sleep(5)
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

                await self.pos_mgr.manage_all(equity=self.current_equity)
                await self.global_risk.manage(self.current_equity)

                # [HEARTBEAT] 4 Jam
                if now - self._last_heartbeat_time > 14400:
                    self._last_heartbeat_time = now
                    asyncio.create_task(self.telegram.send(f" <b>HEARTBEAT: BOT AKTIF & SEHAT</b>\nEquity: <b>${self.current_equity:,.2f}</b>", with_keyboard=False))

            except Exception as e:
                logging.error(f"[HF Loop Error]: {e}", exc_info=True)
            
            await asyncio.sleep(1.2)

    async def run(self):
        cfg.validate_config()
        await self.state.init_db()
        await self.client.init_connection()
        
        # KUNCI: Jalankan backup di background (Non-Blocking)
        logging.info("Menjalankan Auto-Backup di latar belakang...")
        asyncio.create_task(self._perform_backup())
        
        msg = (
            f" <b>QUANTUM BOT PRO V3.0 ONLINE</b>\n"
            f"Modal: <b>${self.current_equity:,.2f} USD</b>\n"
            f"Mode: <b>Agresif Terukur (Zero-Idle)</b>\n"
            f"Central Data Hub & Dynamic Escalator 2.0 Aktif."
        )
        await self.telegram.send(msg, with_keyboard=True)
        
        tasks = [
            asyncio.create_task(self.supervise(self.telegram.poll_commands, "TelegramBot", self.state, self.clients, self.global_risk)),
            asyncio.create_task(self.supervise(self.high_frequency_loop, "HFLoop")),
        ]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    bot = MultiExchangeBotV3()
    try: 
        asyncio.run(bot.run())
    except KeyboardInterrupt: 
        logging.info("Bot dimatikan oleh user (CTRL+C).")
    finally:
        try: asyncio.run(bot.telegram.close())
        except: pass
        try: asyncio.run(bot.client.close())
        except: pass




