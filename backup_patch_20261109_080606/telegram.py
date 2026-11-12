import aiohttp, time, html, asyncio, logging, json, os, time, zipfile, psutil
import config.settings as cfg

logger = logging.getLogger(__name__)

STRATEGY_NAMES = {
    0: ' CloseBy/Unknown',
    111: ' Grid', 222: ' Hybrid', 333: ' SMC Institutional',
    444: ' Pure Hedge', 555: ' Trend Rider', 666: ' Edge Scalper',
    777: ' PA VWAP', 888: ' Crash Sniper', 999: ' London Breakout',
    1001: ' Squeeze Breakout', 1002: ' NY Reversal', 1003: ' Fibo Golden Zone',
    1004: ' Divergence', 1005: ' Liquidity Sweep', 1006: ' News Straddle',
    1007: ' ICT Bullet', 1008: ' Vol POC', 1009: ' Z-Score M15', 
    1010: ' Session Sweep', 1011: ' VWAP Bounce',
    2001: ' VWAP Micro M5', 2002: ' Z-Score Stat', 2003: ' Micro FVG', 2004: ' Safe Grid',
    2005: ' VWAP Trend Exp', 2006: ' VW-ORB',
    4001: ' VWAP Trend Riding',
    3001: '  Donchian Breakout',
    3002: '  Supertrend',
    3003: '  Pivot Bounce',
    3004: '  Asian Box',
    3005: '  Round Number',
    3006: '  Climax Fade',
    3007: '  Stat-Arb (BTC/ETH)',
    234000: ' Manual Order'
}

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self._cb_lock = asyncio.Lock()
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self.is_paused = False
        self.session = None
        self.last_update_id = 0
        self.active_symbol = cfg.SYMBOLS[0]
        self.clients = None
        self.order_lock = None
        self._last_msg_time = 0

    async def _get_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _get_keyboard(self, menu='main'):
        import config.settings as cfg
        status_btn = ' RESUME' if self.is_paused else ' PAUSE'
        
        if menu == 'main':
            return {'inline_keyboard': [
                [{'text': ' Dashboard', 'callback_data': 'menu_dash'}, {'text': ' Analytics', 'callback_data': 'menu_ana'}],
                [{'text': ' Posisi Aktif', 'callback_data': 'menu_pos'}, {'text': ' 23 Strategi', 'callback_data': 'menu_strat'}],
                [{'text': ' Settings', 'callback_data': 'menu_set'}, {'text': ' Pilih Aset', 'callback_data': 'menu_assets'}],
                [{'text': ' Tools & VPS', 'callback_data': 'menu_tools'}, {'text': ' Trade Manual', 'callback_data': 'menu_trade'}],
                [{'text': ' DARURAT (KILL SWITCH)', 'callback_data': 'prompt_kill_confirm'}]
            ]}
        elif menu == 'dash':
            return {'inline_keyboard': [
                [{'text': ' Refresh Status', 'callback_data': 'cmd_status'}, {'text': ' Cek Saldo Exness', 'callback_data': 'cmd_balance'}],
                [{'text': ' Cek Margin Health', 'callback_data': 'cmd_margin_health'}, {'text': ' LEPAS HALT', 'callback_data': 'cmd_release_halt'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'ana':
            return {'inline_keyboard': [
                [{'text': ' Hari Ini', 'callback_data': 'perf_1'}, {'text': ' 7 Hari Terakhir', 'callback_data': 'perf_7'}],
                [{'text': ' Semua Waktu', 'callback_data': 'perf_30'}, {'text': ' Reset Performance', 'callback_data': 'prompt_reset_perf'}],
                [{'text': '  Performa Per Simbol', 'callback_data': 'perf_symbols'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'assets':
            kb = []
            for sym, active in cfg.ACTIVE_SYMBOLS.items():
                mark = ' ON' if active else ' OFF'
                kb.append([{'text': f'{sym}: {mark}', 'callback_data': f'toggle_asset_{sym}'}])
            kb.append([{'text': ' Kembali', 'callback_data': 'main'}])
            return {'inline_keyboard': kb}
        elif menu == 'tools':
            return {'inline_keyboard': [
                [{'text': ' Status MT5', 'callback_data': 'cmd_mt5_stat'}, {'text': ' VPS Health', 'callback_data': 'cmd_health'}],
                [{'text': ' Backup', 'callback_data': 'btn_backup'}, {'text': ' Lihat Log', 'callback_data': 'cmd_logs'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'trade':
            return {'inline_keyboard': [
                [{'text': ' BUY', 'callback_data': 'prompt_buy'}, {'text': ' SELL', 'callback_data': 'prompt_sell'}],
                [{'text': ' TUTUP 1', 'callback_data': 'prompt_close'}, {'text': ' TUTUP SEMUA', 'callback_data': 'prompt_close_all'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'set':
            sym_cfg = cfg.SYMBOL_CONFIGS.get(self.active_symbol, {})
            return {'inline_keyboard': [
                [{'text': f'Simbol: {self.active_symbol}', 'callback_data': 'menu_select_sym'}],
                [{'text': f'Lot: {sym_cfg.get("lot_size", 0.03)}', 'callback_data': 'menu_lot'}],
                [{'text': f'TP: ${sym_cfg.get("profit_target", 15.0)}', 'callback_data': 'menu_profit'}],
                [{'text': status_btn, 'callback_data': 'btn_toggle_pause'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'select_sym':
            kb = []
            for s in cfg.SYMBOLS:
                mark = ' ' if s == self.active_symbol else ''
                kb.append([{'text': f'{mark}{s}', 'callback_data': f'set_sym_{s}'}])
            kb.append([{'text': ' Kembali', 'callback_data': 'menu_set'}])
            return {'inline_keyboard': kb}
        elif menu == 'lot':
            return {'inline_keyboard': [
                [{'text': '0.01', 'callback_data': 'set_lot_0.01'}, {'text': '0.03', 'callback_data': 'set_lot_0.03'}],
                [{'text': '0.05', 'callback_data': 'set_lot_0.05'}, {'text': '0.10', 'callback_data': 'set_lot_0.10'}],
                [{'text': ' Kembali', 'callback_data': 'menu_set'}]
            ]}
        elif menu == 'strat':
            def st(flag): return " ON" if flag else " OFF"
            return {'inline_keyboard': [
                [{'text': f' SMC {st(cfg.SMC_ENABLED)}', 'callback_data': 'toggle_strat_SMC_ENABLED'}, {'text': f' Squeeze {st(cfg.SQUEEZE_ENABLED)}', 'callback_data': 'toggle_strat_SQUEEZE_ENABLED'}],
                [{'text': f' Sweep {st(cfg.SWEEP_ENABLED)}', 'callback_data': 'toggle_strat_SWEEP_ENABLED'}, {'text': f' London {st(cfg.LONDON_BREAKOUT_ENABLED)}', 'callback_data': 'toggle_strat_LONDON_BREAKOUT_ENABLED'}],
                [{'text': f' NY Rev {st(cfg.NY_REVERSAL_ENABLED)}', 'callback_data': 'toggle_strat_NY_REVERSAL_ENABLED'}, {'text': f' Fibo {st(cfg.FIBONACCI_ENABLED)}', 'callback_data': 'toggle_strat_FIBONACCI_ENABLED'}],
                [{'text': f' Div {st(cfg.DIVERGENCE_ENABLED)}', 'callback_data': 'toggle_strat_DIVERGENCE_ENABLED'}, {'text': f' Crash {st(cfg.CRASH_SNIPER_ENABLED)}', 'callback_data': 'toggle_strat_CRASH_SNIPER_ENABLED'}],
                [{'text': f' Trend {st(cfg.TREND_ENABLED)}', 'callback_data': 'toggle_strat_TREND_ENABLED'}, {'text': f' Hedge {st(cfg.PURE_HEDGE_ENABLED)}', 'callback_data': 'toggle_strat_PURE_HEDGE_ENABLED'}],
                [{'text': f' Scalper {st(cfg.EDGE_ENABLED)}', 'callback_data': 'toggle_strat_EDGE_ENABLED'}, {'text': f' PA {st(cfg.PA_ENABLED)}', 'callback_data': 'toggle_strat_PA_ENABLED'}],
                [{'text': f' Hybrid {st(cfg.HYBRID_ENABLED)}', 'callback_data': 'toggle_strat_HYBRID_ENABLED'}, {'text': f' Straddle {st(cfg.STRADDLE_ENABLED)}', 'callback_data': 'toggle_strat_STRADDLE_ENABLED'}],
                [{'text': f' ICT {st(getattr(cfg, "ICT_ENABLED", True))}', 'callback_data': 'toggle_strat_ICT_ENABLED'}, {'text': f' POC {st(getattr(cfg, "POC_ENABLED", True))}', 'callback_data': 'toggle_strat_POC_ENABLED'}],
                [{'text': f' Z-Score M15 {st(getattr(cfg, "ZSCORE_ENABLED", False))}', 'callback_data': 'toggle_strat_ZSCORE_ENABLED'}, {'text': f' Sess Swp {st(getattr(cfg, "SESSION_SWEEP_ENABLED", False))}', 'callback_data': 'toggle_strat_SESSION_SWEEP_ENABLED'}],
                [{'text': f' VWAP Bnc {st(getattr(cfg, "VWAP_BOUNCE_ENABLED", True))}', 'callback_data': 'toggle_strat_VWAP_BOUNCE_ENABLED'}, {'text': f' VWAP M5 {st(getattr(cfg, "VWAP_MICRO_M5_ENABLED", True))}', 'callback_data': 'toggle_strat_VWAP_MICRO_M5_ENABLED'}],
                [{'text': f' Z-Score M5 {st(getattr(cfg, "ZSCORE_STAT_ENABLED", True))}', 'callback_data': 'toggle_strat_ZSCORE_STAT_ENABLED'}, {'text': f' Grid {st(getattr(cfg, "SAFE_GRID_ENABLED", True))}', 'callback_data': 'toggle_strat_SAFE_GRID_ENABLED'}],
                [{'text': f' FVG {st(getattr(cfg, "MICRO_FVG_ENABLED", True))}', 'callback_data': 'toggle_strat_MICRO_FVG_ENABLED'}],
                [{'text': f' Donchian {st(getattr(cfg, "DONCHIAN_ENABLED", True))}', 'callback_data': 'toggle_strat_DONCHIAN_ENABLED'}, {'text': f' Supertrend {st(getattr(cfg, "SUPERTREND_ENABLED", True))}', 'callback_data': 'toggle_strat_SUPERTREND_ENABLED'}],
                [{'text': f' Pivot {st(getattr(cfg, "PIVOT_BOUNCE_ENABLED", True))}', 'callback_data': 'toggle_strat_PIVOT_BOUNCE_ENABLED'}, {'text': f' Asian Box {st(getattr(cfg, "ASIAN_BOX_ENABLED", True))}', 'callback_data': 'toggle_strat_ASIAN_BOX_ENABLED'}],
                [{'text': f' Round Num {st(getattr(cfg, "ROUND_NUMBER_ENABLED", True))}', 'callback_data': 'toggle_strat_ROUND_NUMBER_ENABLED'}, {'text': f' Climax {st(getattr(cfg, "CLIMAX_FADE_ENABLED", True))}', 'callback_data': 'toggle_strat_CLIMAX_FADE_ENABLED'}],
                [{'text': f' Stat-Arb {st(getattr(cfg, "STAT_ARB_ENABLED", True))}', 'callback_data': 'toggle_strat_STAT_ARB_ENABLED'}],
                [{'text': ' Kembali', 'callback_data': 'main'}]
            ]}
        return {'inline_keyboard': [[{'text': ' Menu Utama', 'callback_data': 'main'}]]}

    async def send(self, text, with_keyboard=True, menu='main'):
        if not self.enabled: return
        
        # Rate limiter sederhana (Jeda 1 detik antar pesan agar tidak di-banned Telegram)
        now = time.time()
        if hasattr(self, '_last_msg_time'):
            elapsed = now - self._last_msg_time
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
        self._last_msg_time = time.time()

        clean_text = text[:4000].replace('< ', '&lt; ').replace(' >', ' &gt;')
        payload = {'chat_id': self.chat_id, 'text': clean_text, 'parse_mode': 'HTML'}
        if isinstance(with_keyboard, dict): payload['reply_markup'] = with_keyboard
        elif with_keyboard: payload['reply_markup'] = self._get_keyboard(menu)
        
        # Direct Send (Bypass Antrean)
        try:
            async with (await self._get_session()).post(f'https://api.telegram.org/bot{self.token}/sendMessage', json=payload, timeout=10) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram API Error: {await resp.text()}")
        except Exception as e:
            logger.error(f"Gagal kirim pesan Telegram: {e}")

    async def edit_message(self, msg_id, text, menu='main'):
        if not self.enabled: return
        clean_text = text[:4000].replace('< ', '&lt; ').replace(' >', ' &gt;')
        payload = {'chat_id': self.chat_id, 'message_id': msg_id, 'text': clean_text, 'parse_mode': 'HTML'}
        if isinstance(menu, dict): payload['reply_markup'] = menu
        elif menu: payload['reply_markup'] = self._get_keyboard(menu)
        try:
            async with (await self._get_session()).post(f'https://api.telegram.org/bot{self.token}/editMessageText', json=payload, timeout=8): pass
        except: pass

    async def send_document(self, filepath, caption=""):
        if not self.enabled or not os.path.exists(filepath): return
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            session = await self._get_session()
            with open(filepath, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(self.chat_id))
                data.add_field('parse_mode', 'HTML')
                data.add_field('document', f, filename=os.path.basename(filepath))
                if caption: data.add_field('caption', caption)
                async with session.post(url, data=data, timeout=60): pass
        except: pass

    async def _handle_callback(self, cb_id, msg_id, data_str, state_manager, clients, risk_mgr):
        try:
            async with (await self._get_session()).post(f'https://api.telegram.org/bot{self.token}/answerCallbackQuery', json={'callback_query_id': cb_id}): pass
        except: pass

        try:
            cli = clients.get("exness_mt5")
            if data_str == 'main':
                await self.edit_message(msg_id, " <b>QUANTUM BOT PRO: MENU UTAMA</b>\nSilakan pilih menu di bawah:", 'main')
            elif data_str == 'menu_dash':
                eq = await cli.fetch_balance() if cli else 2000.0
                margin_lvl = await cli.fetch_margin_level() if cli else 9999.0
                pos = await cli.fetch_positions() if cli else []
                total_pnl = sum(p.get('profit', 0.0) for p in pos)
                txt = (
                    f" <b>DASHBOARD PORTOFOLIO EXNESS</b>\n"
                    f"\n"
                    f" Equity Akun: <b>${eq:,.2f}</b>\n"
                    f" Open PnL: <b>{'+' if total_pnl>=0 else ''}${total_pnl:.2f}</b>\n"
                    f" Margin Level: <b>{margin_lvl:.0f}% (Aman)</b>\n"
                    f" Posisi Berjalan: <b>{len(pos)} Posisi</b>\n"
                    f" Status Trading: <b>{' PAUSED' if self.is_paused else ' AKTIF BERJALAN'}</b>\n"
                    f""
                )
                await self.edit_message(msg_id, txt, 'dash')
            elif data_str == 'cmd_status':
                eq = await cli.fetch_balance() if cli else 0.0
                pos = await cli.fetch_positions() if cli else []
                total_pnl = sum(p.get('profit', 0.0) for p in pos)
                txt = f" <b>STATUS BOT</b>\n\nEquity: <b>${eq:,.2f}</b>\nFloating PnL: <b>${total_pnl:.2f}</b>\nPosisi: {len(pos)}"
                await self.edit_message(msg_id, txt, 'dash')
            elif data_str == 'cmd_balance':
                eq = await cli.fetch_balance() if cli else 0.0
                await self.edit_message(msg_id, f" <b>SALDO EXNESS MT5</b>\n\nSaldo/Equity Riil: <b>${eq:,.2f} USD</b>", 'dash')
            elif data_str == 'cmd_margin_health':
                margin_lvl = await cli.fetch_margin_level() if cli else 9999.0
                status_icon = "" if margin_lvl >= 500 else ("" if margin_lvl >= 350 else "")
                txt = f" <b>KESEHATAN MARGIN AKUN</b>\n\nMargin Level: <b>{margin_lvl:.1f}%</b> {status_icon}\nBatas Stop Aman: <b>350%</b>"
                await self.edit_message(msg_id, txt, 'dash')
            elif data_str == 'cmd_release_halt':
                cfg.GLOBAL_HALT = False
                try:
                    if os.path.exists('HALT.flag'): os.remove('HALT.flag')
                    if os.path.exists('daily_state.json'):
                        import json
                        with open('daily_state.json', 'r') as f: state = json.load(f)
                        state['halted'] = False
                        with open('daily_state.json', 'w') as f: json.dump(state, f)
                except: pass
                
                if hasattr(self, 'bot_instance') and self.bot_instance:
                    if self.bot_instance.global_risk: self.bot_instance.global_risk.reset_emergency_halt()
                    if self.bot_instance.pos_mgr: self.bot_instance.pos_mgr.clear_all_halted_engines()

                await self.edit_message(msg_id, " <b>MODE DARURAT DILEPAS</b>\nFile HALT.flag dihapus. Bot aktif kembali.", 'main')
            elif data_str == 'menu_pos':
                pos = await cli.fetch_positions() if cli else []
                if not pos:
                    await self.edit_message(msg_id, " <b>POSISI AKTIF</b>\n\n<i>Tidak ada posisi berjalan saat ini.</i>", 'main')
                else:
                    txt = f" <b>POSISI AKTIF ({len(pos)})</b>\n\n"
                    for p in pos[:8]:
                        m_name = STRATEGY_NAMES.get(p.get('magic', 0), 'Quantum')
                        pnl = p.get('profit', 0.0)
                        icon = "" if pnl >= 0 else ""
                        txt += f"{icon} <b>{p['symbol']}</b> [{p['side']}] {p['contracts']} Lot\n   Strat: {m_name}\n   PnL: <b>${pnl:.2f}</b>\n\n"
                    await self.edit_message(msg_id, txt, 'main')
            elif data_str == 'menu_assets':
                await self.edit_message(msg_id, " <b>PILIH ASET TRADING</b>", 'assets')
            elif data_str.startswith('toggle_asset_'):
                sym = data_str.replace('toggle_asset_', '')
                if sym in cfg.ACTIVE_SYMBOLS:
                    cfg.ACTIVE_SYMBOLS[sym] = not cfg.ACTIVE_SYMBOLS[sym]
                    st = "ON" if cfg.ACTIVE_SYMBOLS[sym] else "OFF"
                    await self.edit_message(msg_id, f"Aset <b>{sym}</b> sekarang: <b>{st}</b>", 'assets')
            elif data_str == 'menu_ana':
                await self.edit_message(msg_id, " <b>ANALISIS PERFORMA</b>\nPilih rentang waktu:", 'ana')
            elif data_str.startswith('perf_') and data_str != 'perf_symbols':
                days = int(data_str.replace('perf_', ''))
                history = await cli.fetch_history(days=days) if cli else []
                
                # MAGIC TRACKER: Koreksi magic 0 berdasarkan ticket
                try:
                    import json
                    tracker = {}
                    if os.path.exists('magic_tracker.json'):
                        with open('magic_tracker.json', 'r') as tf: tracker = json.load(tf)
                    
                    for h in history:
                        if h.get('magic', 0) == 0:
                            ticket_str = str(h.get('ticket', ''))
                            if ticket_str in tracker:
                                h['magic'] = tracker[ticket_str]
                except: pass
                
                # Filter berdasarkan waktu reset
                import json, os
                reset_ts = 0
                if os.path.exists('perf_reset.json'):
                    try:
                        with open('perf_reset.json', 'r') as f:
                            reset_ts = json.load(f).get('ts', 0)
                    except: pass
                
                if reset_ts > 0:
                    history = [h for h in history if h.get('time', 0) >= reset_ts]
                
                if not history:
                    await self.edit_message(msg_id, " <b>LAPORAN PERFORMA</b>\n\nBelum ada trade tertutup dalam periode ini.", 'ana')
                    return

                wins = sum(1 for h in history if h.get('profit', 0) > 0)
                total = len(history)
                wr = (wins / total * 100) if total > 0 else 0
                net = sum(h.get('profit', 0) + h.get('commission', 0) + h.get('swap', 0) for h in history)
                
                txt = f" <b>LAPORAN PERFORMA ({days} HARI)</b>\n"
                txt += f"\n"
                txt += f" Net: <b>{'+' if net>=0 else ''}${net:.2f}</b>\n"
                txt += f" WR: <b>{wr:.1f}%</b> ({wins} Menang / {total-wins} Kalah)\n"
                txt += f" Total: <b>{total} Trades</b>\n"
                txt += f"\n\n"
                
                # Rincian per strategi
                stats = {}
                for h in history:
                    m = h.get('magic', 0)
                    p_net = h.get('profit', 0) + h.get('commission', 0) + h.get('swap', 0)
                    if m not in stats:
                        stats[m] = {'net': 0.0, 'trades': 0, 'wins': 0}
                    stats[m]['net'] += p_net
                    stats[m]['trades'] += 1
                    if p_net > 0: stats[m]['wins'] += 1

                sorted_stats = sorted(stats.items(), key=lambda x: x[1]['net'], reverse=True)
                
                for m, s in sorted_stats:
                    strat_name = STRATEGY_NAMES.get(m, f'Magic {m}')
                    s_wr = (s['wins'] / s['trades'] * 100) if s['trades'] > 0 else 0
                    em = '' if s['net'] > 0 else ''
                    txt += f"{em} <b>{strat_name}</b>\n   Net: ${s['net']:.2f} | WR: {s_wr:.1f}% ({s['trades']}x)\n\n"

                await self.edit_message(msg_id, txt, 'ana')
                
            elif data_str == 'perf_symbols':
                history = await cli.fetch_history(days=30) if cli else []
                
                import json, os
                reset_ts = 0
                if os.path.exists('perf_reset.json'):
                    try:
                        with open('perf_reset.json', 'r') as f:
                            reset_ts = json.load(f).get('ts', 0)
                    except: pass
                
                if reset_ts > 0:
                    history = [h for h in history if h.get('time', 0) >= reset_ts]
                
                if not history:
                    msg = "  <b>PERFORMA PER SIMBOL</b>" + chr(10)*2 + "Belum ada trade tertutup dalam periode ini."
                    await self.edit_message(msg_id, msg, 'ana')
                    return

                stats = {}
                for h in history:
                    sym = h.get('symbol', 'UNKNOWN')
                    net = h.get('profit', 0) + h.get('commission', 0) + h.get('swap', 0)
                    if sym not in stats:
                        stats[sym] = {'net': 0.0, 'trades': 0, 'wins': 0}
                    stats[sym]['net'] += net
                    stats[sym]['trades'] += 1
                    if net > 0: stats[sym]['wins'] += 1

                sorted_stats = sorted(stats.items(), key=lambda x: x[1]['net'], reverse=True)
                txt = "  <b>LAPORAN PERFORMA PER SIMBOL (30 HARI)</b>" + chr(10)*2
                
                for sym, s in sorted_stats:
                    wr = (s['wins'] / s['trades'] * 100) if s['trades'] > 0 else 0
                    icon = "" if s['net'] > 0 else ""
                    txt += f"{icon} <b>{sym}</b>" + chr(10)
                    txt += f"   Net: <b>{'+' if s['net']>=0 else ''}${s['net']:.2f}</b>" + chr(10)
                    txt += f"   WR: {wr:.1f}% ({s['wins']} Menang / {s['trades']-s['wins']} Kalah)" + chr(10)*2
                
                await self.edit_message(msg_id, txt, 'ana')
                
            elif data_str == 'prompt_reset_perf':
                await self.edit_message(msg_id, " <b>RESET LAPORAN PERFORMA?</b>\n\nData performa saat ini akan diarsipkan. Laporan baru akan mulai dihitung dari detik ini.", {'inline_keyboard': [[{'text': ' YA, RESET', 'callback_data': 'reset_perf_exec'}], [{'text': 'BATAL', 'callback_data': 'menu_ana'}]]})
                
            elif data_str == 'reset_perf_exec':
                import json, time
                try:
                    with open('perf_reset.json', 'w') as f:
                        json.dump({'ts': time.time()}, f)
                    await self.edit_message(msg_id, " <b>PERFORMA DI-RESET</b>\n\nLaporan selanjutnya akan dimulai dari nol.", 'ana')
                except Exception as e:
                    await self.edit_message(msg_id, f" Gagal reset: {e}", 'ana')
                    
            elif data_str == 'prompt_kill_confirm':
                await self.edit_message(msg_id, " <b>KONFIRMASI KILL SWITCH</b>\n\nTutup SEMUA posisi dan HALT bot?", {'inline_keyboard': [[{'text': ' YA, TUTUP & HALT', 'callback_data': 'btn_kill_exec'}], [{'text': 'BATAL', 'callback_data': 'main'}]]})
            elif data_str == 'btn_kill_exec':
                cfg.GLOBAL_HALT = True
                try: open('HALT.flag', 'w').write('1')
                except: pass
                pos = await cli.fetch_positions() if cli else []
                closed = 0
                for p in pos:
                    try:
                        await cli.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                        closed += 1
                    except: pass
                await self.edit_message(msg_id, f" <b>KILL SWITCH SUKSES</b>\n{closed} Posisi ditutup.", 'main')
            elif data_str == 'menu_tools':
                await self.edit_message(msg_id, " <b>TOOLS & VPS</b>", 'tools')
            elif data_str == 'cmd_health':
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                await self.edit_message(msg_id, f" <b>VPS HEALTH</b>\n\nCPU: <b>{cpu:.1f}%</b>\nRAM: <b>{ram:.1f}%</b>", 'tools')
            elif data_str == 'cmd_logs':
                try:
                    import collections, os
                    log_file = 'quant_v3.log' if os.path.exists('quant_v3.log') else 'quantum_bot.log'
                    with open(log_file, 'r', encoding='utf-8') as f:
                        logs = ''.join(collections.deque(f, maxlen=20))
                    logs_clean = html.escape(logs[-3500:])
                    title = f'LOG TERBARU ({log_file})'
                except Exception as e:
                    logs_clean = f'Gagal membaca log: {e}'
                    title = 'LOG ERROR'
                await self.edit_message(msg_id, f' <b>{title}</b>\n<pre>{logs_clean}</pre>', 'tools')
            elif data_str == 'btn_backup':
                await self.edit_message(msg_id, "  <b>Memproses Backup...</b>\nMohon tunggu, file ZIP akan dikirim ke chat ini.", 'tools')
                if hasattr(self, 'bot_instance') and self.bot_instance:
                    try:
                        await self.bot_instance._perform_backup()
                        await self.edit_message(msg_id, "  <b>Backup Selesai!</b>\nFile ZIP telah dikirim ke chat.", 'tools')
                    except Exception as e:
                        await self.edit_message(msg_id, f"  <b>Backup Gagal:</b> {e}", 'tools')
                else:
                    await self.edit_message(msg_id, "  <b>Gagal:</b> Bot instance tidak ditemukan.", 'tools')
            elif data_str.startswith('toggle_strat_'):
                strat_key = data_str.replace('toggle_strat_', '')
                import config.settings as cfg
                if not hasattr(cfg, strat_key):
                    setattr(cfg, strat_key, True)
                current = getattr(cfg, strat_key, True)
                setattr(cfg, strat_key, not current)
                st_str = "OFF" if current else "ON"
                await self.edit_message(msg_id, f" <b>23 STRATEGI INSTITUSIONAL</b>\n\nStatus <b>{strat_key.replace('_ENABLED', '')}</b> diubah ke <b>{st_str}</b>", 'strat')
            elif data_str == 'prompt_close':
                pos = await cli.fetch_positions() if cli else []
                if not pos:
                    await self.edit_message(msg_id, " Tidak ada posisi terbuka.", 'trade')
                else:
                    kb = []
                    for p in pos[:10]:
                        kb.append([{'text': f" {p['symbol']} {p['side']} ({p['profit']:.2f})", 'callback_data': f'closepos_{p["ticket"]}'}])
                    kb.append([{'text': ' Kembali', 'callback_data': 'menu_trade'}])
                    await self.edit_message(msg_id, "Pilih posisi yang ingin ditutup:", {'inline_keyboard': kb})
                    
            elif data_str.startswith('closepos_'):
                ticket = data_str.replace('closepos_', '')
                pos = await cli.fetch_positions() if cli else []
                target = next((p for p in pos if str(p['ticket']) == ticket), None)
                if target:
                    res = await cli.close_position(target['symbol'], target['contracts'], target['side'], ticket)
                    await self.edit_message(msg_id, f" Close {target['symbol']} #{ticket}", 'trade')
                else:
                    await self.edit_message(msg_id, " Posisi tidak ditemukan.", 'trade')
                    
            elif data_str == 'menu_set':
                await self.edit_message(msg_id, " <b>PENGATURAN BOT</b>", 'set')
            elif data_str == 'menu_strat':
                await self.edit_message(msg_id, " <b>23 STRATEGI INSTITUSIONAL</b>\n\nSemua strategi berjalan otomatis dikawal oleh AI Gate.", 'strat')
            elif data_str == 'btn_toggle_pause':
                self.is_paused = not self.is_paused
                status = "DIJEDA" if self.is_paused else "DIAKTIFKAN"
                await self.edit_message(msg_id, f" Bot <b>{status}</b>.", 'set')
            elif data_str in ('prompt_buy', 'prompt_sell'):
                side = 'BUY' if data_str == 'prompt_buy' else 'SELL'
                await self.edit_message(msg_id, f" Eksekusi {side}...", 'trade')
                try:
                    tick = await cli.exchange.fetch_ticker(self.active_symbol)
                    px = tick['ask'] if side == 'BUY' else tick['bid']
                    sl = px * 0.995 if side == 'BUY' else px * 1.005
                    o = await cli.create_order(self.active_symbol, side.lower(), 0.03, magic=234000, sl=sl)
                    if o: await self.edit_message(msg_id, f' {side} {self.active_symbol} @ {o["average"]:.3f}', 'trade')
                    else: await self.edit_message(msg_id, f' Gagal.', 'trade')
                except: await self.edit_message(msg_id, ' Error.', 'trade')
            elif data_str == 'prompt_close_all':
                await self.edit_message(msg_id, ' <b>Tutup SEMUA?</b>', {'inline_keyboard': [[{'text': 'YA', 'callback_data': 'btn_close_all_exec'}, {'text': 'BATAL', 'callback_data': 'main'}]]})
            elif data_str == 'btn_close_all_exec':
                pos = await cli.fetch_positions() if cli else []
                closed = 0
                for p in pos:
                    try:
                        await cli.close_position(p['symbol'], p['contracts'], p['side'], str(p['ticket']))
                        closed += 1
                    except: pass
                await self.edit_message(msg_id, f' Closed: {closed}', 'trade')
        except Exception as e:
            logger.error(f"Callback dispatch error: {e}")

    async def poll_commands(self, state_manager, clients, risk_mgr):
        if not self.enabled: return
        while True:
            try:
                params = {'offset': self.last_update_id + 1, 'timeout': 10}
                async with (await self._get_session()).get(f'https://api.telegram.org/bot{self.token}/getUpdates', params=params, timeout=20) as resp:
                    data = await resp.json()
                    if data.get('ok'):
                        for r in data.get('result', []):
                            self.last_update_id = r['update_id']
                            
                            # VALIDASI KEAMANAN CHAT ID
                            sender_id = ""
                            if 'callback_query' in r:
                                sender_id = str(r['callback_query'].get('from', {}).get('id', ''))
                            elif 'message' in r:
                                sender_id = str(r['message'].get('from', {}).get('id', ''))
                                
                            if sender_id != str(self.chat_id):
                                logger.warning(f"BLOCKED UNAUTHORIZED USER: {sender_id}")
                                continue

                            if 'callback_query' in r:
                                cb = r['callback_query']
                                async def _run_cb(cb=cb):
                                    async with self._cb_lock:
                                        try:
                                            # TIMEOUT 10 DETIK: Mencegah bot macet jika MT5 freeze
                                            await asyncio.wait_for(
                                                self._handle_callback(cb['id'], cb['message']['message_id'], cb.get('data', ''), state_manager, clients, risk_mgr),
                                                timeout=10.0
                                            )
                                        except asyncio.TimeoutError:
                                            logger.warning("Telegram Callback Timeout: MT5 tidak merespon dalam 10 detik.")
                                        except Exception as e:
                                            logger.error(f"Callback Error: {e}")
                                asyncio.create_task(_run_cb())
                            elif 'message' in r:
                                if r['message'].get('text', '') in ('/start', '/menu', 'menu'):
                                    await self.send(" <b>QUANTUM BOT PRO</b>", True, 'main')
            except Exception as e:
                logger.error(f"[TELEGRAM POLLING ERROR] {e}")
                await asyncio.sleep(2)
            await asyncio.sleep(0.5)








