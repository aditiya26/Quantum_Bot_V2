import logging
logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.prices = {}
    async def connect(self): pass
    def get_price(self, symbol): return self.prices.get(symbol, 0.0)
    async def close(self): pass
