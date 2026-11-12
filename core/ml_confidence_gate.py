import logging
import config.settings as cfg
from engine.ml_predictor_engine import MLPredictorEngine

logger = logging.getLogger("MLConfidenceGate")

class MLConfidenceGate:
    """
    ML-Ensemble Confidence Gate Enhancement:
    Gerbang pengaman kuantitatif berbasis ML untuk memfilter sinyal strategi yang skor keyakinannya < 70%.
    """
    def __init__(self, client, min_confidence: float = 70.0):
        self.client = client
        self.predictor = MLPredictorEngine(min_confidence=min_confidence / 100.0)
        self.min_confidence = min_confidence

    async def validate_signal(self, symbol: str, proposed_side: str) -> bool:
        if not getattr(cfg, 'ML_GATE_ENABLED', True):
            return True

        try:
            ohlcv = await self.client.fetch_ohlcv(symbol, '15m', 50)
            if not ohlcv or len(ohlcv) < 30:
                return True

            prediction = self.predictor.predict_signal(ohlcv, symbol)
            
            direction = prediction.get('direction', 'NEUTRAL')
            confidence = prediction.get('confidence', 0.0)
            is_valid = prediction.get('is_valid_entry', False)

            if is_valid and direction == proposed_side.upper() and confidence >= self.min_confidence:
                logger.info(f"ML GATE APPROVED [{symbol} {proposed_side}]: Confidence {confidence}%")
                return True
            else:
                logger.warning(
                    f"ML GATE REJECTED [{symbol} {proposed_side}]: "
                    f"Predicted={direction} ({confidence}%), Threshold={self.min_confidence}%"
                )
                return False

        except Exception as e:
            logger.error(f"Error pada ML Confidence Gate [{symbol}]: {e}")
            return True
