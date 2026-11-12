import subprocess
import logging
from datetime import datetime

logger = logging.getLogger("GitBackup")

def run_github_backup():
    try:
        # 1. Add semua perubahan
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        
        # 2. Cek apakah ada perubahan (commit hash)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            logger.info("[GITHUB BACKUP] Tidak ada perubahan kode. Melewati backup.")
            return

        # 3. Commit
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Auto-backup from VPS: {timestamp}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
        
        # 4. Push ke GitHub
        subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
        
        logger.info(f"[GITHUB BACKUP] Berhasil push kode ke GitHub: {commit_msg}")
    except subprocess.CalledProcessError as e:
        logger.error(f"[GITHUB BACKUP] Gagal push ke GitHub: {e.stderr.decode() if e.stderr else e}")
    except Exception as e:
        logger.error(f"[GITHUB BACKUP] Error: {e}")
