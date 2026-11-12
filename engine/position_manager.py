"""
Position Manager V4.9 - Institutional Engine Orchestration (Clean & Fixed)
Bersih dari Micro FVG, Grid, Pure Hedge, dan Stat-Arb.
"""
import asyncio, logging, time
import config.settings as cfg
from monitor.news_filter import NewsFilter
from engine.institutional_gate import InstitutionalOrchestratorGate
from engine.isolated_engine import IsolatedEngine

logger = logging.getLogger(__name__)

class PositionManager:
    def __init__(self, state, clients, telegram, lot_sizer, risk_mgr, order_lock, data_hub, quant_core):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.lot_sizer = lot_sizer
        self.risk_mgr = risk_mgr
        self.order_lock = order_lock
        self.data_hub = data_hub
        self.quant_core = quant_core
        self.news_filter = NewsFilter()

        # Imports
        from engine.smc_engine import SMCEngine
        from engine.trend_engine import TrendEngine
        from engine.vwap_micro_m5_engine import VWAPMicroBounceM5Engine
        from engine.vwap_bounce_engine import VWAPDynamicBounceEngine
        from engine.vwap_trend_expansion_engine import VWAPTrendExpansionEngine
        from engine.vwap_trend_riding_engine import VWAPTrendRidingEngine
        from engine.vworb_engine import VWORBEngine
        from engine.zscore_stat_reversion_engine import ZScoreStatisticalReversionEngine
        from engine.zscore_engine import ZScoreMeanReversionEngine
        from engine.safe_grid_scalper_engine import SafeGridScalperEngine
        from engine.volume_profile_poc_engine import VolumeProfilePOCEngine
        from engine.crash_sniper_engine import CrashSniperEngine
        from engine.liquidity_sweep_engine import LiquiditySweepEngine
        from engine.session_sweep_engine import SessionSweepReversalEngine
        from engine.hybrid_engine import HybridEngine
        from engine.london_breakout_engine import LondonBreakoutEngine
        from engine.ny_reversal_engine import NYReversalEngine
        from engine.fibonacci_engine import FibonacciEngine
        from engine.divergence_engine import DivergenceEngine
        from engine.edge_engine import EdgeEngine
        from engine.ict_silver_bullet_engine import ICTSilverBulletEngine
        from engine.news_straddle_engine import NewsStraddleEngine
        
        from engine.institutional_orchestrator import (
            DonchianBreakoutEngine, SupertrendEngine, PivotBounceEngine,
            AsianRangeEngine, RoundNumberEngine, ClimaxFadeEngine
        )

        # Inisialisasi
        self.smc = SMCEngine(state, clients, telegram, order_lock)
        self.trend = TrendEngine(state, clients, telegram, order_lock)
        self.vwap_m5 = VWAPMicroBounceM5Engine(state, clients, telegram, order_lock)
        self.vwap = VWAPDynamicBounceEngine(state, clients, telegram, order_lock)
        self.vworb = VWORBEngine(state, clients, telegram, order_lock)
        self.vwap_trend_exp = VWAPTrendExpansionEngine(state, clients, telegram, order_lock)
        self.vwap_trend = VWAPTrendRidingEngine(state, clients, telegram, order_lock)
        self.zscore_stat = ZScoreStatisticalReversionEngine(state, clients, telegram, order_lock)
        self.zscore = ZScoreMeanReversionEngine(state, clients, telegram, order_lock)
        self.safe_grid = SafeGridScalperEngine(state, clients, telegram, order_lock, quant_core)
        self.poc = VolumeProfilePOCEngine(state, clients, telegram, order_lock)
        self.crash = CrashSniperEngine(state, clients, telegram, order_lock, self.news_filter)
        self.sweep = LiquiditySweepEngine(state, clients, telegram, order_lock)
        self.session_sweep = SessionSweepReversalEngine(state, clients, telegram, order_lock)
        self.hybrid = HybridEngine(state, clients, telegram, order_lock)
        self.london = LondonBreakoutEngine(state, clients, telegram, order_lock)
        self.ny_rev = NYReversalEngine(state, clients, telegram, order_lock)
        self.fibo = FibonacciEngine(state, clients, telegram, order_lock)
        self.div = DivergenceEngine(state, clients, telegram, order_lock)
        self.edge = EdgeEngine(state, clients, telegram, order_lock)
        self.ict = ICTSilverBulletEngine(state, clients, telegram, order_lock)
        self.straddle = NewsStraddleEngine(state, clients, telegram, order_lock)

        self.donchian = DonchianBreakoutEngine(state, clients, telegram, order_lock, quant_core)
        self.supertrend = SupertrendEngine(state, clients, telegram, order_lock, quant_core)
        self.pivot = PivotBounceEngine(state, clients, telegram, order_lock, quant_core)
        self.asian_box = AsianRangeEngine(state, clients, telegram, order_lock)
        self.round_number = RoundNumberEngine(state, clients, telegram, order_lock)
        self.climax_fade = ClimaxFadeEngine(state, clients, telegram, order_lock)

        # Gate & Isolated
        self.gate = InstitutionalOrchestratorGate(clients.get('exness_mt5'), risk_mgr)
        self.isolated = IsolatedEngine(state, clients, telegram, order_lock)

    def clear_all_halted_engines(self):
        if hasattr(self.safe_grid, 'halted_symbols'): self.safe_grid.halted_symbols.clear()
        logger.info("PositionManager: Semua status Halted dibersihkan.")

    async def sync_and_adopt(self):
        client = self.clients.get("exness_mt5")
        if not client: return
        try:
            positions = await client.fetch_positions()
            if positions is None: return
            db_positions = await self.state.get_open_positions()
            db_tickets = {str(p.get('ticket')) for p in db_positions}
            mt5_tickets = {str(p['ticket']) for p in positions}
            
            for db_p in db_positions:
                if str(db_p.get('ticket')) not in mt5_tickets:
                    await self.state.close_position(db_p['ticket'], exit_price=0.0, reason='SYNC_CLOSE')
            
            for p in positions:
                ticket = int(p['ticket'])
                if str(ticket) not in db_tickets:
                    await self.state.save_position(ticket, {
                        'symbol': p['symbol'], 'side': p['side'], 'amount': p['contracts'],
                        'initial_amount': p['contracts'], 'entry_price': p['entryPrice'],
                        'current_sl': p.get('sl', 0.0), 'magic_number': p['magic'],
                        'status': 'OPEN', 'exchange': 'EXNESS_MT5'
                    })
        except Exception as e:
            logger.error(f"Sync MT5 Error: {e}")

    async def manage_all(self, equity: float = 2000.0):
        if getattr(cfg, 'GLOBAL_HALT', False) or getattr(self.telegram, 'is_paused', False):
            return

        client = self.clients.get('exness_mt5')
        if client: await client.fetch_positions(force=True)

        # Update Dynamic Lot Size
        for sym in cfg.SYMBOLS:
            if not cfg.ACTIVE_SYMBOLS.get(sym, True): continue
            dyn_lot = self.lot_sizer.calc_lot(sym, risk_in_pips=25.0, risk_pct=1.5)
            cfg.SYMBOL_CONFIGS[sym]['lot_size'] = dyn_lot

        regime = await self.quant_core.get_unified_regime("XAUUSDm")

        # Routing Engine (TANPA MICRO FVG)
        if regime == "TRENDING":
            engines = [
                self.smc, self.trend, self.hybrid, self.london, 
                self.crash, self.session_sweep, self.straddle, self.donchian,
                self.supertrend, self.asian_box, self.edge, self.ict,
                self.vwap_trend, self.vwap_trend_exp, self.vworb, self.isolated
            ]
        elif regime == "RANGING":
            engines = [
                self.safe_grid, self.sweep, self.vwap, self.vwap_m5,
                self.zscore, self.zscore_stat, self.poc, self.crash, self.straddle,
                self.pivot, self.round_number, self.climax_fade,
                self.ny_rev, self.fibo, self.div, self.isolated
            ]
        else: # NEUTRAL
            engines = [
                self.smc, self.trend, self.safe_grid, self.vwap_m5,
                self.crash, self.straddle, self.donchian, self.supertrend, self.pivot,
                self.edge, self.ict, self.vwap_trend_exp, self.round_number,
                self.climax_fade, self.isolated
            ]

        sem = asyncio.Semaphore(6)
        async def run_worker(eng):
            async with sem:
                try: 
                    await eng.manage()
                except Exception as e: 
                    logger.error(f"Engine {eng.__class__.__name__} err: {e}")

        self.quant_core.active_exposures.clear()
        await asyncio.gather(*(run_worker(eng) for eng in engines))


