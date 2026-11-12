import MetaTrader5 as mt5
import csv
from datetime import datetime, timedelta

# Daftar Magic Number & Nama Strategi (Sesuai bot Anda)
STRATEGY_NAMES = {
    0: 'CloseBy/Unknown', 111: 'Grid', 222: 'Hybrid', 333: 'SMC Institutional',
    444: 'Pure Hedge', 555: 'Trend Rider', 666: 'Edge Scalper',
    777: 'PA VWAP', 888: 'Crash Sniper', 999: 'London Breakout',
    1001: 'Squeeze Breakout', 1002: 'NY Reversal', 1003: 'Fibo Golden Zone',
    1004: 'Divergence', 1005: 'Liquidity Sweep', 1006: 'News Straddle',
    1007: 'ICT Bullet', 1008: 'Vol POC', 1009: 'Z-Score M15', 
    1010: 'Session Sweep', 1011: 'VWAP Bounce',
    2001: 'VWAP Micro M5', 2002: 'Z-Score Stat', 2003: 'Micro FVG', 2004: 'Safe Grid',
    2005: 'VWAP Trend Exp', 2006: 'VW-ORB', 4001: 'VWAP Trend Riding',
    3001: 'Donchian Breakout', 3002: 'Supertrend', 3003: 'Pivot Bounce', 
    3004: 'Asian Box', 3005: 'Round Number', 3006: 'Climax Fade', 3007: 'Stat-Arb',
    234000: 'Manual Order'
}

def get_reason(reason_code):
    if reason_code == mt5.DEAL_REASON_SL: return "HIT SL"
    if reason_code == mt5.DEAL_REASON_TP: return "HIT TP"
    if reason_code == mt5.DEAL_REASON_CLIENT: return "MANUAL"
    return "OTHER"

def export_excel():
    print("[INFO] Menghubungkan ke MT5...")
    if not mt5.initialize():
        print("[FAIL] Gagal konek ke MT5. Pastikan terminal MT5 terbuka.")
        return

    # Tarik data 30 hari terakhir
    now = datetime.now()
    from_date = now - timedelta(days=30)
    to_date = now + timedelta(days=1)
    
    print(f"[INFO] Menarik data dari {from_date.strftime('%Y-%m-%d')} sampai {now.strftime('%Y-%m-%d')}...")
    deals = mt5.history_deals_get(from_date, to_date)
    
    if deals is None or len(deals) == 0:
        print("[WARN] Tidak ada history trade dalam 30 hari terakhir.")
        mt5.shutdown()
        return

    filename = "Trade_History_Report.csv"
    
    # Tulis ke CSV
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';') # Gunakan titik koma agar rapi di Excel Indonesia
        
        # Header
        writer.writerow([
            "Waktu Close", "Ticket", "Simbol", "Arah", "Volume (Lot)", 
            "Harga Close", "Net Profit (USD)", "Magic", "Engine (Strategi)", "Alasan Close"
        ])
        
        total_profit = 0
        trade_count = 0
        
        # Filter hanya transaksi yang menutup posisi (DEAL_ENTRY_OUT)
        for d in deals:
            if d.entry in (1, 2): 
                profit = d.profit + d.commission + d.swap
                total_profit += profit
                trade_count += 1
                
                magic = d.magic
                engine_name = STRATEGY_NAMES.get(magic, f"Unknown ({magic})")
                reason_str = get_reason(d.reason)
                
                # Arah (Jika Deal Out adalah BUY, berarti posisi aslinya adalah SELL)
                side = "SELL" if d.type == mt5.DEAL_TYPE_BUY else "BUY"
                
                time_str = datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M:%S')
                
                writer.writerow([
                    time_str, d.ticket, d.symbol, side, d.volume,
                    d.price, round(profit, 2), magic, engine_name, reason_str
                ])
                
        # Baris Total
        writer.writerow([])
        writer.writerow(["TOTAL", "", "", "", "", "", round(total_profit, 2), "", "", ""])

    print(f"\n[PASS] Laporan berhasil dibuat: {filename}")
    print(f"[INFO] Total Trades: {trade_count} | Net Profit: ${total_profit:.2f}")
    print("[INFO] Silakan buka file tersebut menggunakan Excel atau Notepad.")
    mt5.shutdown()

if __name__ == "__main__":
    export_excel()
