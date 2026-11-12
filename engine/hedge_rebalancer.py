import asyncio, logging, time
import config.settings as cfg

logger = logging.getLogger("HedgeRebalancer")

class HedgeRebalancerEngine:
    def __init__(self, state, clients, telegram, order_lock):
        self.clients = clients
        self.telegram = telegram
        self.order_lock = order_lock
        self.exchange = "exness_mt5"
        self.cooldowns = {}

    async def manage(self):
        if not getattr(cfg, 'HEDGE_REBALANCE_ENABLED', True): return
        client = self.clients.get(self.exchange)
        if not client: return

        mt5_pos = await client.fetch_positions()
        if not mt5_pos: return

        # Group posisi per simbol
        symbol_groups = {}
        for p in mt5_pos:
            sym = p['symbol']
            if sym not in symbol_groups: symbol_groups[sym] = []
            symbol_groups[sym].append(p)

        for sym, pos_list in symbol_groups.items():
            if time.time() < self.cooldowns.get(sym, 0): continue
            
            # Syarat: Total grup sedang rugi parah (misal < -$50)
            total_net = sum(p.get('profit', 0) + p.get('commission', 0) + p.get('swap', 0) for p in pos_list)
            if total_net > -50.0: continue

            profits = [p for p in pos_list if p.get('profit', 0) > 0]
            losses = [p for p in pos_list if p.get('profit', 0) < 0]

            if len(profits) >= 2 and len(losses) >= 1:
                logger.info(f"[REBALANCE] {sym} Net ${total_net:.2f}. Mengeksekusi Close 2 Profit, 1 Loss, Split Hedge...")
                
                profits.sort(key=lambda x: x.get('profit', 0), reverse=True)
                losses.sort(key=lambda x: x.get('profit', 0)) 

                pos_profit_1 = profits[0]
                pos_profit_2 = profits[1]
                pos_loss_1 = losses[0]
                
                # Sisi hedge yang baru adalah kebalikan dari posisi yatim (loss) yang ditutup
                side_new_hedge = 'sell' if pos_loss_1['side'].upper() == 'BUY' else 'buy'

                async with self.order_lock.get(sym):
                    # 1. Close 2 Profit
                    await client.close_position(sym, pos_profit_1['contracts'], pos_profit_1['side'], str(pos_profit_1['ticket']))
                    await client.close_position(sym, pos_profit_2['contracts'], pos_profit_2['side'], str(pos_profit_2['ticket']))
                    
                    # 2. Close 1 Loss (Yatim pertama)
                    await client.close_position(sym, pos_loss_1['contracts'], pos_loss_1['side'], str(pos_loss_1['ticket']))

                    # 3. Hitung Total Lot Yatim yang TERSISA
                    # (Losses dikurangi 1 yang baru saja di-close)
                    sisa_losses = losses[1:] 
                    total_lot_yatim = sum(p['contracts'] for p in sisa_losses)
                    
                    if total_lot_yatim <= 0:
                        logger.warning(f"[REBALANCE] Tidak ada sisa yatim di {sym} untuk di-hedge.")
                        continue

                    # 4. Pecah Lot (Split) untuk 2 Entry
                    # Misal yatim 0.03 -> 0.01 dan 0.02
                    lot_1 = round(total_lot_yatim / 3, 2)
                    if lot_1 < 0.01: lot_1 = 0.01
                    lot_2 = round(total_lot_yatim - lot_1, 2)
                    if lot_2 < 0.01: lot_2 = 0.01
                    
                    # Pastikan total tidak melebihi (jika pembulatan aneh)
                    if (lot_1 + lot_2) > total_lot_yatim:
                        lot_2 = round(total_lot_yatim - lot_1, 2)

                    logger.info(f"[REBALANCE] Sisa Yatim: {total_lot_yatim} Lot. Membuka 2 Hedge: {lot_1} Lot & {lot_2} Lot.")

                    # 5. Open 2 New Hedges
                    order1 = await client.create_order(sym, side_new_hedge, lot_1, magic=9999, sl=0.0, tp=0.0)
                    order2 = await client.create_order(sym, side_new_hedge, lot_2, magic=9999, sl=0.0, tp=0.0)

                if order1 and order2:
                    self.cooldowns[sym] = time.time() + 1800
                    await self.telegram.send(
                        f" <b>HEDGE REBALANCING SUKSES</b> {sym}\n"
                        f"Menutup 2 Profit & 1 Loss.\n"
                        f"Sisa Yatim: {total_lot_yatim} Lot.\n"
                        f"Membuka 2 Hedge Baru ({side_new_hedge.upper()}): {lot_1} Lot & {lot_2} Lot.",
                        with_keyboard=False
                    )
