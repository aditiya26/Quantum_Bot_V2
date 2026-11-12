import numpy as np
import logging

logger = logging.getLogger(__name__)

class MLPredictorEngine:
    def __init__(self, min_confidence: float = 0.65):
        self.min_confidence = min_confidence

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1: return 50.0
        diff = np.diff(closes)
        gains = np.maximum(diff, 0)
        losses = np.abs(np.minimum(diff, 0))
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, len(diff)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    @staticmethod
    def _compute_ema(series: np.ndarray, span: int) -> float:
        alpha = 2.0 / (span + 1.0)
        val = series[0]
        for s in series[1:]:
            val = alpha * s + (1.0 - alpha) * val
        return float(val)

    def extract_features(self, ohlcv: list) -> dict:
        closes = np.array([x[4] for x in ohlcv], dtype=float)
        highs = np.array([x[2] for x in ohlcv], dtype=float)
        lows = np.array([x[3] for x in ohlcv], dtype=float)
        volumes = np.array([x[5] for x in ohlcv], dtype=float)

        curr_close = closes[-1]
        
        rsi_14 = self._compute_rsi(closes, 14)
        rsi_prev = self._compute_rsi(closes[:-1], 14)
        rsi_velocity = rsi_14 - rsi_prev

        ema_9 = self._compute_ema(closes, 9)
        ema_21 = self._compute_ema(closes, 21)
        ema_spread_pct = ((ema_9 - ema_21) / ema_21) * 100.0

        ma_20 = np.mean(closes[-20:])
        std_20 = np.std(closes[-20:])
        upper_bb = ma_20 + 2.0 * std_20
        lower_bb = ma_20 - 2.0 * std_20
        bandwidth = (upper_bb - lower_bb) / ma_20 if ma_20 > 0 else 0
        pct_b = (curr_close - lower_bb) / (upper_bb - lower_bb) if (upper_bb - lower_bb) > 0 else 0.5

        candle_range = highs[-1] - lows[-1]
        if candle_range > 0:
            upper_wick = highs[-1] - max(curr_close, ohlcv[-1][1])
            lower_wick = min(curr_close, ohlcv[-1][1]) - lows[-1]
            wick_imbalance = (lower_wick - upper_wick) / candle_range
        else:
            wick_imbalance = 0.0

        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_surge_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 else 1.0

        return {'rsi': rsi_14, 'rsi_velocity': rsi_velocity, 'ema_spread_pct': ema_spread_pct,
                'pct_b': pct_b, 'bandwidth': bandwidth, 'wick_imbalance': wick_imbalance, 'vol_surge_ratio': vol_surge_ratio}

    def predict_signal(self, ohlcv: list, symbol: str) -> dict:
        feats = self.extract_features(ohlcv)
        score_bull, score_bear = 0.0, 0.0

        if feats['rsi_velocity'] > 1.5: score_bull += 0.25
        elif feats['rsi_velocity'] < -1.5: score_bear += 0.25

        if feats['ema_spread_pct'] > 0.08: score_bull += 0.25
        elif feats['ema_spread_pct'] < -0.08: score_bear += 0.25

        if feats['wick_imbalance'] > 0.35: score_bull += 0.25
        elif feats['wick_imbalance'] < -0.35: score_bear += 0.25

        if feats['vol_surge_ratio'] > 1.3:
            if score_bull > score_bear: score_bull += 0.25
            else: score_bear += 0.25

        tot = score_bull + score_bear
        if tot == 0:
            confidence, direction = 0.5, "NEUTRAL"
        elif score_bull > score_bear:
            confidence, direction = score_bull, "BUY"
        else:
            confidence, direction = score_bear, "SELL"

        is_valid = confidence >= self.min_confidence
        return {'symbol': symbol, 'direction': direction, 'confidence': round(confidence * 100.0, 1), 'is_valid_entry': is_valid, 'features': feats}
