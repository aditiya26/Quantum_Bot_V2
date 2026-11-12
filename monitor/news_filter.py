import aiohttp, asyncio, logging, time, os, json
from datetime import datetime, timezone
import config.settings as cfg

logger = logging.getLogger(__name__)

class NewsFilter:
    def __init__(self, cache_file="economic_calendar_cache.json"):
        self.cache_file = cache_file
        self.high_impact_events = []
        self.last_fetch_time = 0
        self.cache_ttl = 3600 * 4 # Update setiap 4 jam

    async def fetch_calendar(self):
        # Menggunakan API ForexFactory asli
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10.0) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.high_impact_events = [
                            item for item in data 
                            if item.get('country') in ('USD', 'GBP', 'EUR', 'JPY') and item.get('impact') == 'High'
                        ]
                        self.last_fetch_time = time.time()
                        with open(self.cache_file, 'w') as f:
                            json.dump({'ts': self.last_fetch_time, 'events': self.high_impact_events}, f)
                    else:
                        self._load_local_cache()
                        self.last_fetch_time = time.time()
        except Exception as e:
            self._load_local_cache()
            self.last_fetch_time = time.time()

    def _load_local_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cached = json.load(f)
                    self.high_impact_events = cached.get('events', [])
            except: self.high_impact_events = []

    async def is_high_impact_news_near(self, pre_min=30, post_min=30) -> tuple:
        """
        Mengecek apakah saat ini berada di dalam zona bahaya (default: 30 menit sebelum & sesudah).
        """
        if not getattr(cfg, 'NEWS_FILTER_ENABLED', True): 
            return False, ""

        if time.time() - self.last_fetch_time > self.cache_ttl:
            await self.fetch_calendar()

        now = datetime.now(timezone.utc) # Gunakan UTC untuk sinkronisasi dengan API
        
        for ev in self.high_impact_events:
            date_str = ev.get('date', '')
            try:
                # Parsing waktu berita (biasanya API mengembalikan format ISO 8601)
                ev_time = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(timezone.utc)
                diff_sec = (ev_time - now).total_seconds()
                
                # Zona Bahaya (Pre-buffer dan Post-buffer)
                if 0 <= diff_sec <= (pre_min * 60):
                    return True, f"UPCOMING: {ev.get('title')} dalam {int(diff_sec/60)}m"
                if -(post_min * 60) <= diff_sec < 0:
                    return True, f"JUST RELEASED: {ev.get('title')} ({int(abs(diff_sec)/60)}m lalu)"
            except Exception as e:
                continue
                
        return False, ""
