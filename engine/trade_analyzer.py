import pandas as pd
import numpy as np
import os

class TradeHistoryAuditor:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def audit_performance(self) -> dict:
        if not os.path.exists(self.file_path):
            return {"error": "File CSV tidak ditemukan."}
            
        try:
            # Baca CSV dari MT5 (delimiter titik koma)
            df = pd.read_csv(self.file_path, delimiter=';')
            
            # Hapus baris TOTAL dan baris kosong di bagian bawah
            df = df.dropna(subset=['Net Profit (USD)'])
            df = df[df['Ticket'] != 'TOTAL']
            
            if len(df) == 0:
                return {"error": "Belum ada trade di dalam file."}

            # Konversi ke numerik
            pnl = pd.to_numeric(df['Net Profit (USD)']).values
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]

            total_trades = len(pnl)
            win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
            
            gross_profit = wins.sum() if len(wins) > 0 else 0
            gross_loss = abs(losses.sum()) if len(losses) > 0 else 0
            
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
            expectancy = pnl.mean() if total_trades > 0 else 0

            # Max Drawdown
            cumulative = np.cumsum(pnl)
            peak = np.maximum.accumulate(cumulative)
            drawdown = peak - cumulative
            max_dd = drawdown.max() if len(drawdown) > 0 else 0

            return {
                "total_trades": int(total_trades),
                "win_rate": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 2),
                "max_dd": round(max_dd, 2),
                "net_profit": round(pnl.sum(), 2)
            }
        except Exception as e:
            return {"error": f"Gagal menganalisa: {str(e)}"}
