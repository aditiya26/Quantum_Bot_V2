import asyncio, logging
import config.settings as cfg
from monitor.news_filter import NewsFilter

logger = logging.getLogger("PositionManager")

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

        from engine.smc_engine import SMCEngine
        from engine.trend_engine import TrendEngine
        from engine.vwap_micro_m5_engine import VWAPMicroBounceM5Engine
        from engine.vwap_bounce_engine import VWAPDynamicBounceEngine
        from engine.vwap_trend_expansion_engine import VWAPTrendExpansionEngine
        from engine.vwap_trend_riding_engine import VWAPTrendRidingEngine
        from engine.vworb_engine import VWORBEngine
        from engine.zscore_stat_reversion_engine import ZScoreStatisticalReversionEngine
        from engine.zscore_engine import ZScoreMeanReversionEngine
        from engine.volume_profile_poc_engine import VolumeProfilePOCEngine
        from engine.crash_sniper_engine import CrashSniperEngine
        from engine.liquidity_sweep_engine import LiquiditySweepEngine
        from engine.session_sweep_engine import SessionSweepReversalEngine
        from engine.london_breakout_engine import LondonBreakoutEngine
        from engine.fibonacci_engine import FibonacciEngine
        from engine.divergence_engine import DivergenceEngine
        from engine.ict_silver_bullet_engine import ICTSilverBulletEngine
        from engine.news_straddle_engine import NewsStraddleEngine
        from engine.isolated_engine import IsolatedEngine
        from engine.institutional_orchestrator import (
            DonchianBreakoutEngine, SupertrendEngine, PivotBounceEngine,
            AsianRangeEngine, RoundNumberEngine, ClimaxFadeEngine
        )

        self.smc = SMCEngine(state, clients, telegram, order_lock)
        self.trend = TrendEngine(state, clients, telegram, order_lock)
        self.vwap_m5 = VWAPMicroBounceM5Engine(state, clients, telegram, order_lock)
        self.vwap = VWAPDynamicBounceEngine(state, clients, telegram, order_lock)
        self.vworb = VWORBEngine(state, clients, telegram, order_lock)
        self.vwap_trend_exp = VWAPTrendExpansionEngine(state, clients, telegram, order_lock)
        self.vwap_trend = VWAPTrendRidingEngine(state, clients, telegram, order_lock)
        self.zscore_stat = ZScoreStatisticalReversionEngine(state, clients, telegram, order_lock)
        self.zscore = ZScoreMeanReversionEngine(state, clients, telegram, order_lock)
        self.poc = VolumeProfilePOCEngine(state, clients, telegram, order_lock)
        self.crash = CrashSniperEngine(state, clients, telegram, order_lock, self.news_filter)
        self.sweep = LiquiditySweepEngine(state, clients, telegram, order_lock)
        self.session_sweep = SessionSweepReversalEngine(state, clients, telegram, order_lock)
        self.london = LondonBreakoutEngine(state, clients, telegram, order_lock)
        self.fibo = FibonacciEngine(state, clients, telegram, order_lock)
        self.div = DivergenceEngine(state, clients, telegram, order_lock)
        self.ict = ICTSilverBulletEngine(state, clients, telegram, order_lock)
        self.straddle = NewsStraddleEngine(state, clients, telegram, order_lock)

        self.donchian = DonchianBreakoutEngine(state, clients, telegram, order_lock, quant_core)
        self.supertrend = SupertrendEngine(state, clients, telegram, order_lock, quant_core)
        self.pivot = PivotBounceEngine(state, clients, telegram, order_lock, quant_core)
        self.asian_box = AsianRangeEngine(state, clients, telegram, order_lock)
        self.round_number = RoundNumberEngine(state, clients, telegram, order_lock)
        self.climax_fade = ClimaxFadeEngine(state, clients, telegram, order_lock)

        self.isolated = IsolatedEngine(state, clients, telegram, order_lock)

    def clear_all_halted_engines(self):
        logger.info("PositionManager: Seluruh status halted engine dibersihkan.")

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

    async def manage_basket_netting(self, client):
        if not getattr(cfg, 'BASKET_NETTING_ENABLED', True): return
        positions = await client.fetch_positions()
        if not positions: return
        target_usd = getattr(cfg, 'SYMBOL_BASKET_PROFIT_TARGET', 10.0)
        grouped_by_symbol = {}
        for p in positions:
            sym = p['symbol']
            if sym not in grouped_by_symbol: grouped_by_symbol[sym] = []
            grouped_by_symbol[sym].append(p)

        for sym, pos_list in grouped_by_symbol.items():
            symbol_net_pnl = sum(p.get('profit', 0.0) + p.get('swap', 0.0) + p.get('commission', 0.0) for p in pos_list)
            if symbol_net_pnl >= target_usd and len(pos_list) >= 1:
                async with self.order_lock.get(sym):
                    closed_count = 0
                    for p in pos_list:
                        res = await client.close_position(sym, p['contracts'], p['side'], str(p['ticket']))
                        if res: closed_count += 1

                if hasattr(self.isolated, 'isolated_pairs'):
                    cleaned_pairs = {k: v for k, v in self.isolated.isolated_pairs.items() if v.get('symbol') != sym}
                    self.isolated.isolated_pairs = cleaned_pairs
                    self.isolated._save_state()

                if self.telegram:
                    await self.telegram.send(f" <b>PANEN RAYA (BASKET NETTING)</b>\nSimbol: <b>{sym}</b>\nTotal Profit Bersih: <b>+${symbol_net_pnl:.2f}</b>\nJumlah Posisi Disapu: <b>{closed_count} Posisi</b>", with_keyboard=False)

    async def manage_all(self, equity: float = 2000.0):
        if getattr(cfg, 'GLOBAL_HALT', False) or getattr(self.telegram, 'is_paused', False): return
        client = self.clients.get('exness_mt5')
        if client: await client.fetch_positions(force=True)

        await self.manage_basket_netting(client)

        for sym in cfg.SYMBOLS:
            dyn_lot = self.lot_sizer.calc_lot(sym, risk_in_pips=25.0, risk_pct=1.5)
            if sym in cfg.SYMBOL_CONFIGS:
                cfg.SYMBOL_CONFIGS[sym]['lot_size'] = dyn_lot

        regime = await self.quant_core.get_unified_regime("XAUUSDm")

        if regime == "TRENDING":
            engines = [
                self.smc, self.trend, self.london, self.crash, 
                self.session_sweep, self.straddle, self.donchian,
                self.supertrend, self.asian_box, self.ict,
                self.vwap_trend, self.vwap_trend_exp, self.vworb, self.isolated
            ]
        elif regime == "RANGING":
            engines = [
                self.sweep, self.vwap, self.vwap_m5,
                self.zscore, self.zscore_stat, self.poc, self.crash, self.straddle,
                self.pivot, self.round_number, self.climax_fade,
                self.fibo, self.div, self.isolated
            ]
        else:
            engines = [
                self.smc, self.trend, self.vwap_m5,
                self.crash, self.straddle, self.donchian, self.supertrend, self.pivot,
                self.ict, self.vwap_trend_exp, self.round_number,
                self.climax_fade, self.isolated
            ]

        sem = asyncio.Semaphore(6)
        async def run_worker(eng):
            async with sem:
                try: await eng.manage()
                except Exception as e: logger.error(f"Engine {eng.__class__.__name__} err: {e}")

        self.quant_core.active_exposures.clear()
        await asyncio.gather(*(run_worker(eng) for eng in engines))