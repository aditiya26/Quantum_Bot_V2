"""
QuantumBot Pro - Telegram Bot & Real-Time Alert Center V2.5
Features: Async Message Priority Queue, Zero-Delay Callback ACK, Total Halt Release.
"""
import aiohttp, html, asyncio, logging, json, os, time, psutil
import config.settings as cfg

logger = logging.getLogger(__name__)

STRATEGY_NAMES = {
    111: 'Grid Scalper', 222: 'Hybrid Trend', 333: 'SMC Institutional',
    444: 'Pure Hedge', 555: 'Trend Rider', 666: 'Edge Scalper',
    777: 'PA VWAP', 888: 'Crash Sniper', 999: 'London Breakout',
    1001: 'Squeeze Breakout', 1002: 'NY Reversal', 1003: 'Fibo Golden Zone',
    1004: 'Divergence', 1005: 'Liquidity Sweep', 1006: 'News Straddle',
    1007: 'ICT Silver Bullet', 1008: 'Volume POC', 1009: 'Z-Score M15', 
    1010: 'Session Sweep', 1011: 'VWAP Bounce',
    2001: 'VWAP Micro M5', 2002: 'Z-Score Stat', 2003: 'Micro FVG Retest', 2004: 'Safe Grid',
    234000: 'Manual Order'
}

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self.is_paused = False
        self.session = None
        self.last_update_id = 0
        self.active_symbol = cfg.SYMBOLS[0]
        self.bot_instance = None
        self.clients = None
        self.pos_mgr = None
        self.risk_mgr = None
        self.order_lock = None
        
        # Async Message Queue untuk pengiriman anti-delay & anti-429
        self.queue = asyncio.Queue()
        self._worker_task = None

    async def _get_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        return self.session

    async def _queue_worker(self):
        """Worker background yang memproses antrean pesan secara teratur."""
        while True:
            try:
                item = await self.queue.get()
                payload = item['payload']
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                session = await self._get_session()
                
                for attempt in range(3):
                    try:
                        async with session.post(url, json=payload, timeout=8.0) as resp:
                            if resp.status == 429:
                                retry_after = int(resp.headers.get("Retry-After", 2))
                                await asyncio.sleep(retry_after)
                                continue
                            break
                    except Exception:
                        await asyncio.sleep(1.0)
                        
                self.queue.task_done()
                await asyncio.sleep(0.4) # Rate limit aman (~2.5 msg/sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram Queue Worker error: {e}")

    async def close(self):
        if self._worker_task:
            self._worker_task.cancel()
        if self.session and not self.session.closed:
            await self.session.close()

    def _get_keyboard(self, menu='main'):
        status_btn = '▶ RESUME' if self.is_paused else '⏸ PAUSE'
        if menu == 'main':
            return {'inline_keyboard': [
                [{'text': '📊 Dashboard', 'callback_data': 'menu_dash'}, {'text': '📈 Analytics', 'callback_data': 'menu_ana'}],
                [{'text': '💼 Posisi Aktif', 'callback_data': 'menu_pos'}, {'text': '⚡ 23 Strategi', 'callback_data': 'menu_strat'}],
                [{'text': '⚙️ Settings', 'callback_data': 'menu_set'}, {'text': '🌐 Pair Trading', 'callback_data': 'menu_assets'}],
                [{'text': '🛠️ Tools & VPS', 'callback_data': 'menu_tools'}, {'text': '🟢 LEPAS HALT', 'callback_data': 'cmd_release_halt'}],
                [{'text': '🚨 DARURAT (KILL SWITCH)', 'callback_data': 'prompt_kill_confirm'}]
            ]}
        elif menu == 'dash':
            return {'inline_keyboard': [
                [{'text': '🔄 Refresh Status', 'callback_data': 'cmd_status'}, {'text': '💵 Cek Saldo Exness', 'callback_data': 'cmd_balance'}],
                [{'text': '🛡️ Cek Margin Health', 'callback_data': 'cmd_margin_health'}, {'text': '🟢 LEPAS HALT', 'callback_data': 'cmd_release_halt'}],
                [{'text': '⬅️ Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'ana':
            return {'inline_keyboard': [
                [{'text': '📅 Hari Ini', 'callback_data': 'perf_1'}, {'text': '🗓️ 7 Hari Terakhir', 'callback_data': 'perf_7'}],
                [{'text': '📊 Semua Waktu', 'callback_data': 'perf_30'}, {'text': '🔄 Reset Laporan', 'callback_data': 'prompt_reset_perf'}],
                [{'text': '⬅️ Kembali', 'callback_data': 'main'}]
            ]}
        elif menu == 'assets':
            kb = []
            for sym, active in cfg.ACTIVE_SYMBOLS.items():
                mark = '✅ ON' if active else '❌ OFF'
                kb.append([{'text': f'{sym}: {mark}', 'callback_data': f'toggle_asset_{sym}'}])
            kb.append([{'text': '⬅️ Kembali', 'callback_data': 'main'}])
            return {'inline_keyboard': kb}
        elif menu == 'tools':
            return {'inline_keyboard': [
                [{'text': '💻 VPS Health & RAM', 'callback_data': 'cmd_health'}, {'text': '🧹 Clean Cache Now', 'callback_data': 'cmd_clean_cache'}],
                [{'text': '💾 Trigger Backup', 'callback_data': 'btn_backup'}, {'text': '📜 Lihat Log', 'callback_data': 'cmd_logs'}],
                [{'text': '⬅️ Kembali', 'callback_data': 'main'}]
            ]}
        return {'inline_keyboard': [[{'text': '⬅️ Menu Utama', 'callback_data': 'main'}]]}

    async def send(self, text, with_keyboard=True, menu='main', priority=False):
        if not self.enabled: return
        clean_text = text[:4000].replace('< ', '&lt; ').replace(' >', ' &gt;')
        payload = {'chat_id': self.chat_id, 'text': clean_text, 'parse_mode': 'HTML'}
        if isinstance(with_keyboard, dict):
            payload['reply_markup'] = with_keyboard
        elif with_keyboard:
            payload['reply_markup'] = self._get_keyboard(menu)

        if not self._worker_task or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._queue_worker())

        await self.queue.put({'payload': payload, 'priority': priority})

    async def edit_message(self, msg_id, text, menu='main'):
        if not self.enabled: return
        clean_text = text[:4000].replace('< ', '&lt; ').replace(' >', ' &gt;')
        payload = {'chat_id': self.chat_id, 'message_id': msg_id, 'text': clean_text, 'parse_mode': 'HTML'}
        if isinstance(menu, dict): payload['reply_markup'] = menu
        elif menu: payload['reply_markup'] = self._get_keyboard(menu)
        try:
            session = await self._get_session()
            async with session.post(f'https://api.telegram.org/bot{self.token}/editMessageText', json=payload, timeout=6.0):
                pass
        except: pass

    async def send_document(self, filepath, caption=""):
        if not self.enabled or not os.path.exists(filepath): return
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            session = await self._get_session()
            with open(filepath, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(self.chat_id))
                data.add_field('document', f, filename=os.path.basename(filepath))
                if caption: data.add_field('caption', caption)
                async with session.post(url, data=data, timeout=30): pass
        except Exception as e:
            logger.error(f"Send document error: {e}")

    async def _handle_callback(self, cb_id, msg_id, data_str, state_manager, clients, risk_mgr):
        # 1. IMMEDIATE ACK: Memberitahu Telegram tombol berhasil dipencet (Anti-spinner)
        try:
            async with (await self._get_session()).post(
                f'https://api.telegram.org/bot{self.token}/answerCallbackQuery',
                json={'callback_query_id': cb_id}, timeout=3.0
            ): pass
        except: pass

        cli = clients.get("exness_mt5")
        
        # 2. PROSES DISPATCH CALLBACK
        if data_str == 'main':
            await self.edit_message(msg_id, "🤖 <b>QUANTUM BOT PRO V2.5: MENU UTAMA</b>\nSilakan pilih opsi manajemen di bawah:", 'main')
            
        elif data_str == 'cmd_release_halt':
            # OBJECTIVE 5: RELEASE ALL HALT TOTAL & CLEAN
            if self.bot_instance:
                self.bot_instance.release_all_halts()
            else:
                cfg.GLOBAL_HALT = False
                try:
                    if os.path.exists('HALT.flag'): os.remove('HALT.flag')
                except: pass
                if risk_mgr: risk_mgr.reset_emergency_halt()
                if self.pos_mgr: self.pos_mgr.clear_all_halted_engines()
                
            txt = (
                "🟢 <b>STATUS JEDA BERHASIL DILEPASKAN</b>\n\n"
                "• File <code>HALT.flag</code> dihapus.\n"
                "• Cooldown circuit breaker di-reset ke 0.\n"
                "• Mesin trading aktif kembali memindai sinyal."
            )
            await self.edit_message(msg_id, txt, 'main')

        elif data_str == 'menu_dash':
            eq = await cli.fetch_balance() if cli else 2000.0
            margin_lvl = await cli.fetch_margin_level() if cli else 9999.0
            pos = await cli.fetch_positions() if cli else []
            total_pnl = sum(p.get('profit', 0.0) for p in pos)
            is_halted = getattr(cfg, 'GLOBAL_HALT', False) or (risk_mgr and risk_mgr.pause_until > time.time())
            
            txt = (
                f"📊 <b>DASHBOARD PORTOFOLIO EXNESS</b>\n\n"
                f"💰 Equity Akun: <b>${eq:,.2f} USD</b>\n"
                f"📈 Floating PnL: <b>{'+' if total_pnl>=0 else ''}${total_pnl:.2f}</b>\n"
                f"🛡️ Margin Level: <b>{margin_lvl:.0f}%</b>\n"
                f"💼 Posisi Berjalan: <b>{len(pos)} Posisi</b>\n"
                f"⚡ Status Trading: <b>{'⏸ JEDA / HALTED' if is_halted else '🟢 AKTIF BERJALAN'}</b>"
            )
            await self.edit_message(msg_id, txt, 'dash')

        elif data_str == 'cmd_clean_cache':
            import gc
            if state_manager:
                await state_manager.checkpoint_wal()
            if cli:
                cli.clear_caches()
            if self.pos_mgr:
                self.pos_mgr.purge_expired_cooldowns()
            gc.collect()
            await self.edit_message(msg_id, "🧹 <b>CACHE SISTEM BERHASIL DIBERSIHKAN!</b>\nSQLite WAL checkpoint selesai, RAM & Ticks di-reset.", 'tools')

        elif data_str == 'cmd_health':
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            await self.edit_message(msg_id, f"💻 <b>VPS PERFORMANCE HEALTH</b>\n\nCPU Usage: <b>{cpu:.1f}%</b>\nRAM Usage: <b>{ram:.1f}%</b>\nStatus: Normal & Responsif.", 'tools')

        elif data_str == 'menu_pos':
            pos = await cli.fetch_positions(force=True) if cli else []
            if not pos:
                await self.edit_message(msg_id, "💼 <b>POSISI AKTIF</b>\n\n<i>Tidak ada posisi berjalan saat ini.</i>", 'main')
            else:
                txt = f"💼 <b>POSISI AKTIF ({len(pos)})</b>\n\n"
                for p in pos[:8]:
                    m_name = STRATEGY_NAMES.get(p.get('magic', 0), f"Strat {p.get('magic')}")
                    pnl = p.get('profit', 0.0)
                    icon = "🟢" if pnl >= 0 else "🔴"
                    txt += f"{icon} <b>{p['symbol']}</b> [{p['side']}] {p['contracts']} Lot\n   {m_name} | PnL: <b>${pnl:.2f}</b>\n\n"
                await self.edit_message(msg_id, txt, 'main')

        elif data_str == 'cmd_logs':
            try:
                import collections
                with open('quantum_bot.log', 'r', encoding='utf-8') as f:
                    logs = ''.join(collections.deque(f, maxlen=15))
                logs_clean = html.escape(logs[-2500:])
            except: logs_clean = "Log file belum tersedia."
            await self.edit_message(msg_id, f'📜 <b>LOG TERAKHIR BOT</b>\n<pre>{logs_clean}</pre>', 'tools')

        elif data_str == 'btn_backup':
            if self.bot_instance:
                await self.bot_instance._perform_backup()
                await self.edit_message(msg_id, "💾 <b>Auto-Backup Terkirim ke Telegram!</b>", 'tools')

    async def poll_commands(self, state_manager, clients, risk_mgr):
        if not self.enabled: return
        logger.info("Telegram Polling Command Listener aktif.")
        while True:
            try:
                params = {'offset': self.last_update_id + 1, 'timeout': 10}
                async with (await self._get_session()).get(
                    f'https://api.telegram.org/bot{self.token}/getUpdates',
                    params=params, timeout=18.0
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('ok'):
                            for r in data.get('result', []):
                                self.last_update_id = r['update_id']
                                
                                # Verifikasi Chat ID Pengirim
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
                                    asyncio.create_task(
                                        self._handle_callback(
                                            cb['id'],
                                            cb['message']['message_id'],
                                            cb.get('data', ''),
                                            state_manager, clients, risk_mgr
                                        )
                                    )
                                elif 'message' in r:
                                    text = r['message'].get('text', '').lower()
                                    if text in ('/start', '/menu', 'menu', 'help'):
                                        await self.send("🤖 <b>QUANTUM BOT PRO V2.5</b>", True, 'main')
            except Exception as e:
                await asyncio.sleep(2.0)
            await asyncio.sleep(0.5)
