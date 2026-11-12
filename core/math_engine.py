import math
import numpy as np
import pandas as pd
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Union, Any

class QuantMathEngine:
    @staticmethod
    def round_to_step(value: float, step: float = 0.01) -> float:
        d_val = Decimal(str(value))
        d_step = Decimal(str(step))
        return float(((d_val // d_step) * d_step).quantize(d_step, rounding=ROUND_DOWN))

    @staticmethod
    def calculate_zscore(prices: List[float], period: int = 20) -> Dict[str, float]:
        if len(prices) < period:
            return {"z_score": 0.0, "mean": 0.0, "std": 0.0, "is_oversold": False, "is_overbought": False}
        arr = np.array(prices[-period:], dtype=np.float64)
        mean = float(np.mean(arr))
        std = max(float(np.std(arr, ddof=1)), 1e-4)
        z = (float(prices[-1]) - mean) / std
        return {
            "z_score": round(z, 4),
            "mean": round(mean, 5),
            "std": round(std, 5),
            "is_oversold": z <= -2.2,
            "is_overbought": z >= 2.2
        }

    @staticmethod
    def calculate_anchored_vwap(ohlcv_bars: List[List[float]]) -> Dict[str, float]:
        if not ohlcv_bars or len(ohlcv_bars) < 5:
            return {"vwap": 0.0, "upper_1": 0.0, "lower_1": 0.0, "upper_2": 0.0, "lower_2": 0.0}
        highs = np.array([b[2] for b in ohlcv_bars])
        lows = np.array([b[3] for b in ohlcv_bars])
        closes = np.array([b[4] for b in ohlcv_bars])
        volumes = np.array([max(b[5], 1.0) for b in ohlcv_bars])

        tp = (highs + lows + closes) / 3.0
        cum_tp_vol = np.sum(tp * volumes)
        cum_vol = np.sum(volumes)
        vwap = cum_tp_vol / cum_vol
        var = np.sum(volumes * np.square(tp - vwap)) / cum_vol
        std = math.sqrt(max(0.0, var))

        return {
            "vwap": round(vwap, 4),
            "upper_1": round(vwap + (1.5 * std), 4),
            "lower_1": round(vwap - (1.5 * std), 4),
            "upper_2": round(vwap + (2.0 * std), 4),
            "lower_2": round(vwap - (2.0 * std), 4)
        }

    @staticmethod
    def calculate_atr(ohlcv_bars: List[List[float]], period: int = 14) -> float:
        if len(ohlcv_bars) < period + 1: return 0.0
        highs = np.array([b[2] for b in ohlcv_bars])
        lows = np.array([b[3] for b in ohlcv_bars])
        closes = np.array([b[4] for b in ohlcv_bars])
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        return round(float(pd.Series(tr).ewm(alpha=1.0/period, adjust=False).mean().iloc[-1]), 4)

    @staticmethod
    def classify_market_regime(ohlcv_bars: List[List[float]]) -> Dict[str, Any]:
        if not ohlcv_bars or len(ohlcv_bars) < 30:
            return {"regime": "NEUTRAL", "adx": 0.0, "is_shock": False}
        highs = np.array([b[2] for b in ohlcv_bars])
        lows = np.array([b[3] for b in ohlcv_bars])
        closes = np.array([b[4] for b in ohlcv_bars])
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        tr_sum = np.sum(tr[-14:])
        up_moves = highs[1:] - highs[:-1]
        down_moves = lows[:-1] - lows[1:]
        pos_dm = np.where((up_moves > down_moves) & (up_moves > 0), up_moves, 0.0)
        neg_dm = np.where((down_moves > up_moves) & (down_moves > 0), down_moves, 0.0)
        
        di_plus = (np.sum(pos_dm[-14:]) / tr_sum) * 100.0 if tr_sum > 0 else 0
        di_minus = (np.sum(neg_dm[-14:]) / tr_sum) * 100.0 if tr_sum > 0 else 0
        adx = (abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9)) * 100.0
        
        baseline_atr = np.mean(tr[-28:-14]) if len(tr) >= 28 else np.mean(tr[-14:])
        current_atr = np.mean(tr[-5:])
        is_shock = current_atr > (baseline_atr * 2.5) if baseline_atr > 0 else False

        if is_shock: regime = "SHOCK"
        elif adx >= 25.0: regime = "TRENDING"
        elif adx <= 20.0: regime = "RANGING"
        else: regime = "NEUTRAL"

        return {"regime": regime, "adx": round(float(adx), 2), "is_shock": is_shock}
