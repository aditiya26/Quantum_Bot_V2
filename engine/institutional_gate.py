import logging
import config.settings as cfg
from engine.institutional_orchestrator import InstitutionalPreventiveShield
from monitor.news_filter import NewsFilter

logger = logging.getLogger("InstitutionalGate")

class InstitutionalOrchestratorGate:
    def __init__(self, client, risk_manager):
        self.client = client
        self.risk_mgr = risk_manager
        self.spread_shield = InstitutionalPreventiveShield(client)
        self.news_filter = NewsFilter()

    async def validate_institutional_gate(self, symbol: str) -> bool:
        try:
            # 1. Cek Global Halt / Daily DD Breaker
            if getattr(cfg, 'GLOBAL_HALT', False):
                return False

            # 2. Cek Berita Makro (30 Menit sebelum/sesudah)
            news_block, _ = await self.news_filter.is_high_impact_news_near(30, 30)
            if news_block:
                return False

            # 3. Cek Spread & Volatilitas (Shield)
            if not await self.spread_shield.validate_market_safety(symbol):
                return False

            # Lolos semua ujian
            return True 

        except Exception as e:
            logger.error(f"Error pada Gate Institusional {symbol}: {e}")
            return False # Default: Tutup gerbang jika error
