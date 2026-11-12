import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class MarketRegime:
    @staticmethod
    def get_regime(h1_data, m15_data):
        if not h1_data or not m15_data or len(h1_data) < 30: 
            return "UNKNOWN"
        try:
            df_h1 = pd.DataFrame(h1_data, columns=['t','o','h','l','c','v'])
            df_m15 = pd.DataFrame(m15_data, columns=['t','o','h','l','c','v'])
            
            plus_dm = df_h1['h'].diff().clip(lower=0)
            minus_dm = -df_h1['l'].diff().clip(upper=0)
            tr = pd.concat([df_h1['h'] - df_h1['l'], (df_h1['h'] - df_h1['c'].shift()).abs(), (df_h1['l'] - df_h1['c'].shift()).abs()], axis=1).max(axis=1)
            atr_h1 = tr.ewm(alpha=1/14, adjust=False).mean()
            plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_h1)
            minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_h1)
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
            adx_h1 = dx.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
            
            tr_m15 = pd.concat([df_m15['h'] - df_m15['l'], (df_m15['h'] - df_m15['c'].shift()).abs(), (df_m15['l'] - df_m15['c'].shift()).abs()], axis=1).max(axis=1)
            atr_m15 = tr_m15.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
            atr_pct = (atr_m15 / df_m15['c'].iloc[-1]) * 100
            
            if atr_pct > 0.4: return "SHOCK"
            if adx_h1 >= 25: return "TRENDING"
            if adx_h1 < 18: return "RANGING"
            return "NEUTRAL"
        except Exception as e:
            logger.error(f"MarketRegime Error: {e}")
            return "UNKNOWN"
