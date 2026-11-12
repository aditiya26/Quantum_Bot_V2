"""
QuantumBot Pro V3.4 - High-Speed SQLite WAL Engine & In-Memory State Manager
Fixed: Database connection churn & disk I/O bottleneck.
"""
import aiosqlite, asyncio, time, logging
import config.settings as cfg

logger = logging.getLogger("StateManager")

class StateManager:
    def __init__(self, db_path=getattr(cfg, "DB_PATH", "quantum_state.db")):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self.memory_positions = {}  # In-Memory Cache (0ms latency)
        self._cache_lock = asyncio.Lock()
        self._db_conn = None

    async def init_db(self):
        self._db_conn = await aiosqlite.connect(self.db_path, timeout=10.0)
        # Optimasi Performa Maksimal SQLite
        await self._db_conn.execute("PRAGMA journal_mode=WAL;")
        await self._db_conn.execute("PRAGMA synchronous=NORMAL;")
        await self._db_conn.execute("PRAGMA cache_size=-16000;") # 64MB Cache
        await self._db_conn.execute("PRAGMA busy_timeout=5000;")
        
        await self._db_conn.execute("""
        CREATE TABLE IF NOT EXISTS positions_v2 (
            ticket INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            side TEXT NOT NULL,
            amount REAL NOT NULL,
            initial_amount REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_sl REAL DEFAULT 0.0,
            target_tp REAL DEFAULT 0.0,
            magic_number INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            stage INTEGER DEFAULT 0,
            opened_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            strategy_type TEXT DEFAULT 'QUANTUM'
        )
        """)
        await self._db_conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_status ON positions_v2(symbol, status);")
        await self._db_conn.commit()
        await self._load_to_memory()
        logger.info("[Database] StateManager v3.4 persistent WAL Engine initialized.")

    async def _load_to_memory(self):
        # Gunakan cursor terpisah agar tidak mengubah global row_factory
        async with self._db_conn.execute("SELECT * FROM positions_v2 WHERE status IN ('OPEN', 'PENDING', 'ORPHANED')") as cur:
            cols = [column[0] for column in cur.description]
            rows = [dict(zip(cols, row)) async for row in cur]
            
        async with self._cache_lock:
            self.memory_positions.clear()
            for r in rows:
                self.memory_positions[int(r['ticket'])] = r

    async def save_position(self, ticket: int, data: dict):
        ticket = int(ticket)
        now = time.time()
        async with self._lock:
            async with self._cache_lock:
                pos = self.memory_positions.get(ticket, {})
                pos.update(data)
                pos['ticket'] = ticket
                pos['updated_at'] = now
                if 'opened_at' not in pos: pos['opened_at'] = now
                if 'initial_amount' not in pos: pos['initial_amount'] = data.get('amount', 0.01)
                self.memory_positions[ticket] = pos

            # Tulis ke disk via persistent connection
            await self._db_conn.execute("""
            INSERT INTO positions_v2 (
                ticket, symbol, exchange, side, amount, initial_amount,
                entry_price, current_sl, target_tp, magic_number,
                status, stage, opened_at, updated_at, strategy_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO UPDATE SET
                amount = excluded.amount,
                current_sl = excluded.current_sl,
                target_tp = excluded.target_tp,
                status = excluded.status,
                stage = excluded.stage,
                updated_at = excluded.updated_at
            """, (
                ticket, pos['symbol'], pos.get('exchange', 'EXNESS_MT5'),
                pos['side'], pos['amount'], pos['initial_amount'],
                pos['entry_price'], pos.get('current_sl', 0.0), pos.get('target_tp', 0.0),
                pos.get('magic_number', 0), pos['status'], pos.get('stage', 0),
                pos['opened_at'], pos['updated_at'], pos.get('strategy_type', 'QUANTUM')
            ))
            await self._db_conn.commit()

    async def get_open_positions(self, symbol: str = None):
        async with self._cache_lock:
            if symbol:
                return [p for p in self.memory_positions.values() if p['symbol'] == symbol and p['status'] == 'OPEN']
            return [p for p in self.memory_positions.values() if p['status'] == 'OPEN']

    async def close_position(self, ticket: int, exit_price: float = 0.0, reason: str = "NORMAL"):
        ticket = int(ticket)
        now = time.time()
        async with self._lock:
            async with self._cache_lock:
                if ticket in self.memory_positions:
                    self.memory_positions[ticket]['status'] = 'CLOSED'
                    self.memory_positions[ticket]['updated_at'] = now
            await self._db_conn.execute("UPDATE positions_v2 SET status = 'CLOSED', updated_at = ? WHERE ticket = ?", (now, ticket))
            await self._db_conn.commit()

    async def checkpoint_wal(self):
        """Memaksa SQLite membuang WAL ke database utama dan mengecilkan file .db-wal."""
        try:
            await self._db_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            await self._db_conn.commit()
        except Exception as e:
            logger.error(f"WAL Checkpoint Error: {e}")

    async def close(self):
        """Menutup koneksi DB dengan rapi saat bot dimatikan (Mencegah DB Corrupt)."""
        if self._db_conn:
            await self._db_conn.close()
            logger.info("[Database] Persistent connection closed gracefully.")


