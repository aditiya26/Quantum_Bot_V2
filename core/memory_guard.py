"""
QuantumBot Pro V4.9 - Institutional Memory & RAM Guard
"""
import os, sys, gc, time, ctypes, logging
from typing import Dict, Any, Optional

logger = logging.getLogger("MemoryGuard")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

class MemoryGuard:
    def __init__(self, ram_threshold_pct: float = 85.0):
        self.ram_threshold_pct = ram_threshold_pct
        self.last_trim_time = time.time()
        self.total_trims_performed = 0
        self._libc = None

        if sys.platform.startswith("linux"):
            try: self._libc = ctypes.CDLL("libc.so.6")
            except Exception as e: logger.warning(f"[MemoryGuard] Gagal memuat libc.so.6: {e}")

    def get_system_memory_stats(self) -> Dict[str, Any]:
        if not PSUTIL_AVAILABLE or psutil is None:
            return {"available": False, "vps_ram_percent": 0.0, "bot_rss_mb": 0.0}

        try:
            vm = psutil.virtual_memory()
            process = psutil.Process(os.getpid())
            bot_rss_mb = process.memory_info().rss / (1024 * 1024)

            mt5_rss_mb = 0.0
            for p in psutil.process_iter(['name', 'memory_info']):
                try:
                    pname = (p.info['name'] or '').lower()
                    if 'terminal' in pname or 'metatrader' in pname:
                        mt5_rss_mb += p.info['memory_info'].rss / (1024 * 1024)
                except Exception: pass

            return {
                "available": True,
                "vps_ram_percent": round(vm.percent, 1),
                "vps_total_mb": round(vm.total / (1024 * 1024), 1),
                "vps_used_mb": round(vm.used / (1024 * 1024), 1),
                "vps_free_mb": round(vm.available / (1024 * 1024), 1),
                "bot_rss_mb": round(bot_rss_mb, 1),
                "mt5_rss_mb": round(mt5_rss_mb, 1),
                "is_critical": vm.percent >= self.ram_threshold_pct
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def trim_os_heap(self) -> bool:
        success = False
        if sys.platform.startswith("linux") and self._libc is not None:
            try: success = (self._libc.malloc_trim(0) == 1)
            except Exception: pass
        elif sys.platform == "win32":
            try:
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                success = (ctypes.windll.psapi.EmptyWorkingSet(handle) != 0)
            except Exception: pass
        return success

    def perform_deep_trim(self, data_hub=None, mt5_client=None, state_manager=None) -> Dict[str, Any]:
        before_stats = self.get_system_memory_stats()
        before_bot_rss = before_stats.get("bot_rss_mb", 0.0)
        before_vps_ram = before_stats.get("vps_ram_percent", 0.0)

        if data_hub and hasattr(data_hub, "evict_stale_data"):
            try: data_hub.evict_stale_data()
            except Exception: pass

        if mt5_client and hasattr(mt5_client, "clean_transient_buffers"):
            try: mt5_client.clean_transient_buffers()
            except Exception: pass

        collected = gc.collect(2)
        os_trimmed = self.trim_os_heap()

        self.last_trim_time = time.time()
        self.total_trims_performed += 1

        after_stats = self.get_system_memory_stats()
        after_bot_rss = after_stats.get("bot_rss_mb", 0.0)
        after_vps_ram = after_stats.get("vps_ram_percent", 0.0)
        freed_bot_mb = max(0.0, before_bot_rss - after_bot_rss)

        return {
            "before_vps_ram": before_vps_ram,
            "after_vps_ram": after_vps_ram,
            "before_bot_rss": before_bot_rss,
            "after_bot_rss": after_bot_rss,
            "freed_bot_mb": round(freed_bot_mb, 1),
            "gc_collected": collected,
            "os_trimmed": os_trimmed
        }

    def check_and_auto_trim(self, data_hub=None, mt5_client=None, interval_seconds: int = 600) -> Optional[Dict[str, Any]]:
        now = time.time()
        stats = self.get_system_memory_stats()
        if stats.get("is_critical", False) or (now - self.last_trim_time) >= interval_seconds:
            return self.perform_deep_trim(data_hub, mt5_client)
        return None
