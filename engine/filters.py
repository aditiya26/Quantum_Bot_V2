import logging
from datetime import datetime, timezone
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class MarketFilters:
    def check_dead_market(self, df, is_tradfi=False):
        if df is None or len(df) < 14: return False
        try:
            h, l, c = df['h'].values, df['l'].values, df['c'].values
            tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
            atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values[-1]
            atr_pct = (atr / c[-1]) * 100
            min_atr = 0.01 if is_tradfi else 0.05
            return atr_pct >= min_atr
        except: return True

    def check_shock(self, df):
        if df is None or len(df) < 5: return False
        c, h, l = df['c'].values, df['h'].values, df['l'].values
        tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
        atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values[-1]
        if atr <= 0: return False
        return abs(c[-1] - c[-3]) > (atr * 2.5)

    def check_market_holiday(self):
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5: return False
        return True

    def check_adx(self, df, threshold=22):
        if df is None or len(df) < 20: return True
        try:
            h, l, c = df['h'].values, df['l'].values, df['c'].values
            plus_dm = np.maximum(h[1:] - h[:-1], 0)
            minus_dm = np.maximum(l[:-1] - l[1:], 0)
            tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
            atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean()
            plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/14, adjust=False).mean() / atr
            minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/14, adjust=False).mean() / atr
            dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = pd.Series(dx).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
            return adx < threshold
        except: return True
