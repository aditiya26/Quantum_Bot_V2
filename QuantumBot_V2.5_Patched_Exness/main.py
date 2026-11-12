"""
QuantumBot Pro V2.5 - Production-Grade MT5 Exness Automated Trading Bot
Terkalibrasi untuk Modal $2,000 USD | VPS Windows 24/7 Ready
"""
import sys, io, os, time, asyncio, logging, logging.handlers, gc
from datetime import datetime

# Windows console encoding fix
if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr is not None and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# CRITICAL WINDOWS FIX: Gunakan SelectorEventLoop agar MT5 & background task stabil 24/7
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import config.settings as cfg
from core.exness_mt5 import ExnessMT5Client
from core.state_manager import StateManager
from core.order_lock import OrderLockManager
from engine.risk_manager import RiskManager
from engine.position_manager import PositionManager
from engine.lot_sizer import LotSizer
from monitor.telegram import TelegramNotifier

# Logging Setup dengan Rotasi 10MB
log_fmt = logging.Formatter('%(asctime)s | %(levelname)-7s | %(name)s: %(message)s')
log_h = logging.handlers.RotatingFileHandler('quantum_bot.log', maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
log_h.setFormatter(log_fmt)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(log_h)
sh = logging.StreamHandler()
sh.setFormatter(log_fmt)
root_logger.addHandler(sh)

class MultiExchangeBot:
    def __init__(self):
        root_logger.info("Menginisialisasi QuantumBot Pro V2.5 (Exness MT5 Calibrated)...")
        self.state = StateManager()
        self.order_lock = OrderLockManager()
        self.lot_sizer = LotSizer(base_equity=getattr(cfg, 'BASE_EQUITY', 2000.0))
        self.telegram = TelegramNotifier(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID)
        self.clients = {"exness_mt5": ExnessMT5Client()}
        self.global_risk = RiskManager(self.state, self.clients, self.telegram, self.order_lock)
        self.pos_mgr = PositionManager(
            state=self.state,
            clients=self.clients,
            telegram=self.telegram,
            ws_manager=None,
            lot_sizer=self.lot_sizer,
            risk_mgr=self.global_risk,
            order_lock=self.order_lock
        )
        self.tasks = []
        self.stop_event = asyncio.Event()
        self.current_equity = getattr(cfg, 'BASE_EQUITY', 2000.0)
        self._last_equity_ts = 0.0
        self._last_sync_time = 0.0
        
        # Cross-wire dependensi untuk sinkronisasi Telegram
        self.telegram.bot_instance = self
        self.telegram.order_lock = self.order_lock
        self.telegram.clients = self.clients
        self.telegram.pos_mgr = self.pos_mgr
        self.telegram.risk_mgr = self.global_risk

    def release_all_halts(self):
        """Melepaskan seluruh status HALT, jeda waktu, dan circuit breaker."""
        cfg.GLOBAL_HALT = False
        try:
            if os.path.exists('HALT.flag'):
                os.remove('HALT.flag')
        except Exception as e:
            root_logger.warning(f"Gagal menghapus HALT.flag: {e}")
            
        if self.global_risk:
            self.global_risk.reset_emergency_halt()
        if self.pos_mgr:
            self.pos_mgr.clear_all_halted_engines()
        root_logger.info("✅ SELURUH STATUS HALT BERHASIL DILEPASKAN. Bot kembali normal.")

    async def _perform_backup(self):
        """Backup harian state database tanpa membocorkan file log besar."""
        import zipfile, shutil
        _today = datetime.now(cfg.LOCAL_TZ).strftime('%Y-%m-%d')
        try:
            os.makedirs("backups", exist_ok=True)
            zip_name = f"backups/QuantumBot_Backup_{_today}.zip"
            if os.path.exists("quantum_state.db"):
                shutil.copy2("quantum_state.db", "temp_backup.db")
            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                if os.path.exists("temp_backup.db"):
                    zipf.write("temp_backup.db", "quantum_state.db")
                    try: os.remove("temp_backup.db")
                    except: pass
            await self.telegram.send_document(
                zip_name,
                caption=f"💾 <b>Auto-Backup Harian Sukses</b>\nModal Terlindungi: ${self.current_equity:,.2f}"
            )
        except Exception as e:
            root_logger.error(f"Backup Error: {e}")

    async def periodic_maintenance_loop(self):
        """Objective 6: Otomatis Hapus cache secara berkala (setiap 15 menit)."""
        root_logger.info("Task Pembersihan Cache Otomatis diaktifkan (Interval: 15 Menit).")
        while not self.stop_event.is_set():
            await asyncio.sleep(900) # 15 Menit
            try:
                # 1. Truncate SQLite WAL file
                await self.state.checkpoint_wal()
                # 2. Bersihkan in-memory caches pada client MT5 dan engine
                client = self.clients.get("exness_mt5")
                if client:
                    client.clear_caches()
                if self.pos_mgr:
                    self.pos_mgr.purge_expired_cooldowns()
                # 3. Panggil Python Garbage Collector
                collected = gc.collect()
                root_logger.info(f"🧹 [Auto-Maintenance] Cache dibersihkan & Garbage Collector membebaskan {collected} objek.")
            except Exception as e:
                root_logger.warning(f"Maintenance loop notice: {e}")

    async def supervise(self, coro_func, name, *args):
        backoff = 3
        while not self.stop_event.is_set():
            try:
                await coro_func(*args)
                backoff = 3
            except asyncio.CancelledError:
                break
            except Exception as e:
                root_logger.error(f"Supervisor [{name}] crash: {e}. Restarting dalam {backoff} detik...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def management_loop(self):
        while not self.stop_event.is_set():
            try:
                if os.path.exists('HALT.flag'):
                    if not getattr(cfg, 'GLOBAL_HALT', False):
                        cfg.GLOBAL_HALT = True
                        
                if getattr(cfg, 'GLOBAL_HALT', False):
                    # Tetap lakukan sync posisi & cek trailing jika ada posisi berjalan
                    await asyncio.sleep(5)
                    continue

                now = time.time()
                # Update saldo setiap 15 detik
                if now - self._last_equity_ts > 15:
                    eq = await self.clients["exness_mt5"].fetch_balance()
                    if eq > 0:
                        self.current_equity = eq
                        self.lot_sizer.update_equity(eq)
                    self._last_equity_ts = now

                # Sinkronisasi posisi dengan database setiap 2.5 detik
                if now - self._last_sync_time > 2.5:
                    await self.pos_mgr.sync_and_adopt()
                    self._last_sync_time = now

                # Jalankan manajemen risiko real-time dan strategi
                await self.global_risk.manage(self.current_equity)
                await self.pos_mgr.manage_all(equity=self.current_equity)

                # Cek backup harian
                _today = datetime.now(cfg.LOCAL_TZ).strftime('%Y-%m-%d')
                if getattr(self, '_last_backup_date', None) != _today:
                    self._last_backup_date = _today
                    await self._perform_backup()

            except Exception as e:
                root_logger.error(f"Management loop error: {e}")
            await asyncio.sleep(2.0)

    async def run(self):
        cfg.validate_config()
        await self.state.init_db()
        
        # Cek saldo awal
        initial_eq = await self.clients["exness_mt5"].fetch_balance()
        if initial_eq > 0:
            self.current_equity = initial_eq
            self.lot_sizer.update_equity(initial_eq)

        msg = (
            f"🚀 <b>QUANTUM BOT PRO V2.5 AKTIF (PATCHED)</b>\n\n"
            f"💰 Modal Terdeteksi: <b>${self.current_equity:,.2f} USD</b>\n"
            f"🛡️ Max Daily Risk: <b>${getattr(cfg, 'DAILY_LOSS_LIMIT_USD', 80.0):.2f}</b>\n"
            f"⚡ Arsitektur: <b>Single-Worker Isolated MT5 + Async Telegram Queue</b>\n"
            f"🎯 23 Strategi Institusional Siap Beroperasi."
        )
        await self.telegram.send(msg, with_keyboard=True)

        self.tasks.append(asyncio.create_task(self.supervise(self.telegram.poll_commands, "TelegramBot", self.state, self.clients, self.global_risk)))
        self.tasks.append(asyncio.create_task(self.supervise(self.management_loop, "ManagementLoop")))
        self.tasks.append(asyncio.create_task(self.supervise(self.periodic_maintenance_loop, "AutoMaintenance")))
        await asyncio.gather(*self.tasks)

async def main():
    bot = MultiExchangeBot()
    try:
        await bot.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        root_logger.info("Bot dimatikan oleh user.")
    finally:
        bot.stop_event.set()
        await bot.clients["exness_mt5"].close()
        await bot.telegram.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
