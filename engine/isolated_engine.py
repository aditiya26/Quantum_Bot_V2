import asyncio, logging, json, os, time
import config.settings as cfg

logger = logging.getLogger("IsolatedEngine")

class IsolatedEngine:
    MAGIC = getattr(cfg, 'ISOLATED_MAGIC', 9999)
    STATE_FILE = "isolated_state.json"

    def __init__(self, state, clients, telegram, order_lock):
        self.state = state
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.isolated_pairs = self._load_state()
        self.processing_lock = asyncio.Lock()

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, 'r') as f:
                    return json.load(f).get('pairs', {})
            except: return {}
        return {}

    def _save_state(self):
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump({'pairs': self.isolated_pairs}, f, indent=2)
        except: pass

    async def manage(self):
        if not getattr(cfg, 'ISOLATED_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client: return

        async with self.processing_lock:
            mt5_pos = await client.fetch_positions(force=True)
            if not mt5_pos: return
            active_tickets = {str(p['ticket']): p for p in mt5_pos}

            # 1. Rekonsiliasi Orphan (Hapus memori jika posisi sudah tidak ada)
            for orig_ticket, hedge_ticket in list(self.isolated_pairs.items()):
                if orig_ticket not in active_tickets:
                    if hedge_ticket in active_tickets:
                        hp = active_tickets[hedge_ticket]
                        logger.warning(f"[ORPHAN] Posisi asli #{orig_ticket} mati. Menutup hedge #{hedge_ticket}...")
                        async with self.order_lock.get(hp['symbol']):
                            await client.close_position(hp['symbol'], hp['contracts'], hp['side'], hedge_ticket)
                    del self.isolated_pairs[orig_ticket]
                    self._save_state()
                elif hedge_ticket not in active_tickets:
                    del self.isolated_pairs[orig_ticket]
                    self._save_state()

            # 2. Pindai posisi baru yang butuh Hedge (Murni 1:1)
            threshold = getattr(cfg, 'ISOLATED_LOSS_THRESHOLD', -10.0)
            for p in mt5_pos:
                if p['magic'] == self.MAGIC: continue
                ticket = str(p['ticket'])
                profit = p.get('profit', 0.0)

                if profit <= threshold and ticket not in self.isolated_pairs:
                    hedge_side = 'sell' if p['side'].upper() == 'BUY' else 'buy'
                    exact_lot = round(float(p['contracts']), 2)

                    logger.info(f"[ISOLATED TRIGGER] #{ticket} ({p['symbol']}) rugi ${profit:.2f}. Membuka hedge {exact_lot} Lot...")
                    async with self.order_lock.get(p['symbol']):
                        hedge_order = await client.create_order(p['symbol'], hedge_side, exact_lot, magic=self.MAGIC, sl=0.0, tp=0.0)

                    if hedge_order:
                        hedge_ticket = str(hedge_order.get('ticket'))
                        await client.safe_update_sl(p['symbol'], p['side'], exact_lot, 0.0, ticket)
                        self.isolated_pairs[ticket] = hedge_ticket
                        self._save_state()
                        if self.telegram:
                            await self.telegram.send(f" <b>ISOLATED HEDGE</b> {p['symbol']} #{ticket} -> Hedge #{hedge_ticket}", with_keyboard=False)

            # 3. Evaluasi Target CloseBy (BEP)
            target_profit = getattr(cfg, 'ISOLATED_TARGET_PROFIT', 0.01)
            for orig_ticket, hedge_ticket in list(self.isolated_pairs.items()):
                if orig_ticket not in active_tickets or hedge_ticket not in active_tickets: continue

                pos1 = active_tickets[orig_ticket]
                pos2 = active_tickets[hedge_ticket]

                total_basket_net = (pos1.get('profit', 0) + pos1.get('commission', 0) + pos1.get('swap', 0) +
                                    pos2.get('profit', 0) + pos2.get('commission', 0) + pos2.get('swap', 0))

                if total_basket_net >= target_profit:
                    logger.info(f"[CLOSEBY] #{orig_ticket} & #{hedge_ticket} BEP (+${total_basket_net:.2f}). Menutup...")
                    async with self.order_lock.get(pos1['symbol']):
                        res = await client.close_by(pos1['symbol'], int(orig_ticket), int(hedge_ticket))
                    
                    if res:
                        del self.isolated_pairs[orig_ticket]
                        self._save_state()
                        if self.telegram:
                            await self.telegram.send(f" <b>ISOLATED CLOSEBY</b> {pos1['symbol']} ditutup +${total_basket_net:.2f}", with_keyboard=False)
                    else:
                        async with self.order_lock.get(pos1['symbol']):
                            await client.close_position(pos1['symbol'], pos1['contracts'], pos1['side'], orig_ticket)
                            await client.close_position(pos2['symbol'], pos2['contracts'], pos2['side'], hedge_ticket)
                        del self.isolated_pairs[orig_ticket]
                        self._save_state()

            # 4. Basket Netting (Target $10)
            basket_target = getattr(cfg, 'SYMBOL_BASKET_PROFIT_TARGET', 10.0)
            symbol_groups = {}
            for p in mt5_pos:
                sym = p['symbol']
                if sym not in symbol_groups: symbol_groups[sym] = []
                symbol_groups[sym].append(p)
                
            for sym, pos_list in symbol_groups.items():
                if len(pos_list) > 1:
                    total_group_net = sum(p.get('profit', 0) + p.get('commission', 0) + p.get('swap', 0) for p in pos_list)
                    if total_group_net >= basket_target:
                        logger.info(f"[BASKET NETTING] Total grup {sym} profit (${total_group_net:.2f}). Menutup semua...")
                        async with self.order_lock.get(sym):
                            for p in pos_list:
                                await client.close_position(sym, p['contracts'], p['side'], str(p['ticket']))
                                if str(p['ticket']) in self.isolated_pairs: del self.isolated_pairs[str(p['ticket'])]
                                if str(p['ticket']) in self.isolated_pairs.values():
                                    for k, v in list(self.isolated_pairs.items()):
                                        if v == str(p['ticket']): del self.isolated_pairs[k]
                        self._save_state()
                        if self.telegram:
                            await self.telegram.send(f" <b>BASKET NETTING SUKSES</b> {sym}\nTotal Profit: <b>+${total_group_net:.2f}</b>", with_keyboard=False)
