import subprocess
import logging
from datetime import datetime

logger = logging.getLogger("GitBackup")

def run_github_backup():
    try:
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return # Tidak ada perubahan, skip

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subprocess.run(["git", "commit", "-m", f"Auto-backup: {timestamp}"], check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
        logger.info(f"[GITHUB] Backup sukses: {timestamp}")
    except Exception as e:
        logger.error(f"[GITHUB] Gagal backup: {e}")
