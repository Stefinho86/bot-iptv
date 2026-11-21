import os
import time
from pathlib import Path

TZ_NAME = os.environ.get("TZ_NAME", "Europe/Rome")
os.environ["TZ"] = TZ_NAME
try:
    time.tzset()
except Exception:
    pass

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

SUPER_ADMINS = {int(x) for x in os.environ.get("SUPER_ADMINS", "").replace(" ", "").split(",") if x}
GROUP_ID = int(os.environ.get("GROUP_ID", "0") or 0)
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/tmp/media"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
PLAYWRIGHT_FALLBACK = False

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN:
    raise SystemExit("Manca la variabile BOT_TOKEN")
if not DATABASE_URL:
    raise SystemExit("Manca la variabile DATABASE_URL")
