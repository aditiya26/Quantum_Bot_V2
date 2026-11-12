import logging, config.settings as cfg
logger = logging.getLogger("BasketNetting")
class BasketNettingEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.clients, self.telegram, self.order_lock = clients, telegram, order_lock
    async def manage(self):
        if not getattr(cfg, 'BASKET_NETTING_ENABLED', True): return
        client = self.clients.get("exness_mt5")
        if not client: return
        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return
        by_symbol = {}
        for p in mt5_pos: by_symbol.setdefault(p['symbol'], []).append(p)
        for sym, positions in by_symbol.items():
            if sum(p.get('profit', 0) for p in positions) >= cfg.SYMBOL_BASKET_PROFIT_TARGET:
                async with self.order_lock.get(sym):
                    for p in positions:
                        await client.close_position(sym, p['contracts'], p['side'], str(p['ticket']))
                await self.telegram.send(f" <b>BASKET NETTING</b> {sym} ditutup!", with_keyboard=False)
