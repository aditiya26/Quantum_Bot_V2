import asyncio

class OrderLockManager:
    """Mengelola lock per-simbol untuk mencegah race condition order."""
    def __init__(self):
        self._locks = {}
    def get(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]
