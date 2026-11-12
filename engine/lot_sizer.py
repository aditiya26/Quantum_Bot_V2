import logging
import config.settings as cfg

logger = logging.getLogger("InstitutionalRiskManager")

class LotSizer:
    def __init__(self, base_equity: float = 2000.0):
        self.current_equity = max(100.0, base_equity)

    def update_equity(self, equity: float):
        if equity > 0:
            self.current_equity = equity

    def calc_lot(self, symbol: str, risk_in_pips: float = 25.0, risk_pct: float = 1.5, sl_price_distance: float = 0.0) -> float:
        """
        Menghitung Lot berdasarkan Risiko 1.5% dari Ekuitas.
        Jika sl_price_distance diberikan (jarak harga asli), gunakan perhitungan presisi institusional.
        """
        try:
            risk_pct_val = getattr(cfg, 'RISK_PER_TRADE_PCT', risk_pct)
            target_risk_usd = self.current_equity * (risk_pct_val / 100.0)
            
            contract_size = cfg.get_contract_size(symbol)
            pip_size = cfg.SYMBOL_CONFIGS.get(symbol, {}).get('pip_size', 0.1)

            if sl_price_distance > 0:
                # [INSTITUTIONAL MATH] Jarak harga asli (misal SL 10 dolar di XAU)
                # Lot = Risiko Dolar / (Jarak SL * Contract Size)
                raw_lot = target_risk_usd / (sl_price_distance * contract_size)
            else:
                # [FALLBACK] Menggunakan Pips
                risk_usd_per_pip = contract_size * pip_size
                raw_lot = target_risk_usd / (risk_in_pips * risk_usd_per_pip)

            # --- PENGAMAN MUTLAK (Hard Caps) ---
            # Jangan pernah biarkan kode eksternal membuat lot 1.00
            if "BTC" in symbol: max_allowed = 0.02
            elif "ETH" in symbol: max_allowed = 0.03
            elif "XAU" in symbol: max_allowed = 0.03
            else: max_allowed = 0.03

            final_lot = max(0.01, min(raw_lot, max_allowed))
            return round(final_lot, 2)

        except Exception as e:
            logger.error(f"Gagal menghitung dynamic lot {symbol}: {e}")
            return 0.01
