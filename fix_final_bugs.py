import os
import re

def patch_file(filepath, pattern, replacement):
    if not os.path.exists(filepath): return
    with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
    new_content = content.replace(pattern, replacement)
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f: f.write(new_content)
        print(f"[PASS] {filepath} berhasil dipatch.")

# 1. Hard Cap Lot
old_vol = """            si = mt5.symbol_info(symbol)
            vol = round(round(amount / (si.volume_step if si else 0.01)) * (si.volume_step if si else 0.01), 2)
            if si: vol = max(si.volume_min, min(si.volume_max, vol))"""
new_vol = """            si = mt5.symbol_info(symbol)
            safe_amount = amount
            if "BTC" in symbol: safe_amount = min(safe_amount, 0.02)
            elif "ETH" in symbol: safe_amount = min(safe_amount, 0.10)
            elif "XAU" in symbol: safe_amount = min(safe_amount, 0.05)
            vol = round(round(safe_amount / (si.volume_step if si else 0.01)) * (si.volume_step if si else 0.01), 2)
            if si: vol = max(si.volume_min, min(si.volume_max, vol))"""
patch_file('core/exness_mt5.py', old_vol, new_vol)

# 2. Risk Manager Reset & Flow
old_risk_init = """        self.escalator = DynamicEscalatorV3(clients.get('exness_mt5'), state, telegram)
        self._last_hist_check = 0"""
new_risk_init = """        self.escalator = DynamicEscalatorV3(clients.get('exness_mt5'), state, telegram)
        self._realized_halt_today = False
        self._last_hist_check = 0"""
patch_file('engine/risk_manager.py', old_risk_init, new_risk_init)

old_risk_mng = """        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return

        # 1. Cek Loss Streak Harian"""
new_risk_mng = """        # 1. Cek Loss Streak Harian"""
patch_file('engine/risk_manager.py', old_risk_mng, new_risk_mng)

old_realized = """        except Exception as e:
            logger.error(f"Realized PnL Check error: {e}")
            
        # 2. Emergency Protocol (Margin Critical / Loss Limit)"""
new_realized = """        except Exception as e:
            logger.error(f"Realized PnL Check error: {e}")
            
        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return

        # 2. Emergency Protocol (Margin Critical / Loss Limit)"""
patch_file('engine/risk_manager.py', old_realized, new_realized)

old_risk_end = """    async def _manage_position_trailing(self, client, p):"""
new_risk_end = """    def reset_emergency_halt(self):
        self.pause_until = 0
        self._emergency_notified = False
        self._realized_halt_today = False
        self.pair_cooldown_until.clear()

    async def _manage_position_trailing(self, client, p):"""
patch_file('engine/risk_manager.py', old_risk_end, new_risk_end)

# 3. Position Manager Override
old_pm = """        # Simpan state asli user hanya sekali agar tidak ditimpa
        if not hasattr(self, '_user_active_symbols'):
            self._user_active_symbols = cfg.ACTIVE_SYMBOLS.copy()

        for sym in cfg.SYMBOLS:
            user_wants_active = self._user_active_symbols.get(sym, True)
            if is_asia:
                cfg.ACTIVE_SYMBOLS[sym] = user_wants_active and ("BTC" in sym or "ETH" in sym)
            else:
                cfg.ACTIVE_SYMBOLS[sym] = user_wants_active"""
new_pm = """        # Biarkan Telegram yang mengatur ACTIVE_SYMBOLS"""
patch_file('engine/position_manager.py', old_pm, new_pm)

old_pm_clear = """    async def sync_and_adopt(self):"""
new_pm_clear = """    def clear_all_halted_engines(self):
        if hasattr(self.safe_grid, 'halted_symbols'): self.safe_grid.halted_symbols.clear()
        if hasattr(self.hedge, 'halted_symbols'): self.hedge.halted_symbols.clear()

    async def sync_and_adopt(self):"""
patch_file('engine/position_manager.py', old_pm_clear, new_pm_clear)

# 4. Escalator Floor
old_esc = """            trail_distance = getattr(cfg, 'RUNNER_TRAIL_PIPS', 18.0) * pip_unit
            current_sl = float(pos.get('current_sl', 0.0))
            bep_floor = entry + (1.5 * pip_unit) if side == 'BUY' else entry - (1.5 * pip_unit)"""
new_esc = """            trail_distance = getattr(cfg, 'RUNNER_TRAIL_PIPS', 18.0) * pip_unit
            current_sl = float(pos.get('current_sl', 0.0))
            current_spread = current_ask - current_bid
            buffer = max(0.5 * pip_unit, current_spread * 0.1)
            bep_floor = entry + current_spread + buffer if side == 'BUY' else entry - current_spread - buffer"""
patch_file('core/dynamic_escalator.py', old_esc, new_esc)

# 5. Main Heartbeat & Status
old_main_ls = """            f"Equity      : $\\n" """
new_main_ls = """            f"Equity      : ${self.current_equity:,.2f}\\n" """
patch_file('main.py', old_main_ls, new_main_ls)

old_main_hb = """                await self.pos_mgr.manage_all(equity=self.current_equity)
                await self.global_risk.manage(self.current_equity)"""
new_main_hb = """                await self.pos_mgr.manage_all(equity=self.current_equity)
                await self.global_risk.manage(self.current_equity)

                if now - self._last_heartbeat_time > 14400:
                    self._last_heartbeat_time = now
                    asyncio.create_task(self.telegram.send(f" <b>HEARTBEAT: BOT AKTIF & SEHAT</b>\\nEquity: ${self.current_equity:,.2f}", with_keyboard=False))"""
patch_file('main.py', old_main_hb, new_main_hb)

# 6. Telegram Reset Halt
old_tg = """                except: pass
                await self.edit_message(msg_id, " <b>MODE DARURAT DILEPAS</b>\\nFile HALT.flag dihapus. Bot aktif kembali.", 'main')"""
new_tg = """                except: pass
                if hasattr(self, 'bot_instance') and self.bot_instance:
                    if self.bot_instance.global_risk: self.bot_instance.global_risk.reset_emergency_halt()
                    if self.bot_instance.pos_mgr: self.bot_instance.pos_mgr.clear_all_halted_engines()
                await self.edit_message(msg_id, " <b>MODE DARURAT DILEPAS</b>\\nFile HALT.flag dihapus. Bot aktif kembali.", 'main')"""
patch_file('monitor/telegram.py', old_tg, new_tg)

print("SEMUA PATCH BERHASIL DITERAPKAN!")
