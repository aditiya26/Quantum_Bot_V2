"""
QuantumBot Pro - Dynamic Safe Lot Sizer V2.5
Terkalibrasi Modal $2,000 USD | Max Risk 1.0% per Posisi
"""
import logging
import config.settings as cfg

logger = logging.getLogger(__name__)

class LotSizer:
    def __init__(self, base_equity: float = 2000.0):
        self.current_equity = max(100.0, base_equity)

    def update_equity(self, equity: float):
        if equity > 0:
            self.current_equity = equity

    def calc_lot(self, symbol: str, stop_loss_pips: float, risk_pct: float = 1.0) -> float:
        """
        Menghitung lot secara presisi berdasarkan risiko dolar dan pip value nyata.
        Formula: Lot = Risk_USD / (SL_Pips * Pip_Value_USD)
        """
        if stop_loss_pips <= 0:
            stop_loss_pips = 30.0
            
        risk_usd = self.current_equity * (risk_pct / 100.0) # Modal $2,000 -> Max $20 risk
        contract_size = cfg.get_contract_size(symbol)
        cfg_sym = cfg.SYMBOL_CONFIGS.get(symbol, {'pip_size': 0.1})
        pip_size = cfg_sym.get('pip_size', 0.1)

        # Konversi quote currency ke USD (misal JPY pair)
        quote_to_usd = 1.0
        if "JPY" in symbol:
            quote_to_usd = 1.0 / 155.0 # Estimasi kurs USDJPY

        pip_value_usd = contract_size * pip_size * quote_to_usd
        if pip_value_usd <= 0:
            return 0.02

        raw_lot = risk_usd / (stop_loss_pips * pip_value_usd)
        
        # Batasan Lot Khusus Modal $2,000 demi Low Drawdown
        if "BTC" in symbol:
            max_allowed = 0.03
        elif "XAU" in symbol:
            max_allowed = 0.06
        else:
            max_allowed = 0.06

        safe_lot = max(0.01, min(raw_lot, max_allowed))
        return round(safe_lot, 2)
