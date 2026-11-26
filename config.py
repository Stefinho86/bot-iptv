import os
import time
from pathlib import Path

TZ_NAME = os.environ.get("TZ_NAME", "Europe/Rome").strip() or "Europe/Rome"
os.environ["TZ"] = TZ_NAME
try:
    time.tzset()
except Exception:
    pass

try:
    from zoneinfo import ZoneInfo
    ZoneInfo(TZ_NAME)
except Exception as e:
    raise SystemExit(
        f"Fuso orario {TZ_NAME} non disponibile ({type(e).__name__}: {e}). "
        "Aggiungi 'tzdata' a requirements.txt oppure installa il pacchetto "
        "tzdata nell'immagine.")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

SUPER_ADMINS = set()
for _voce in os.environ.get("SUPER_ADMINS", "").replace(" ", "").split(","):
    if not _voce:
        continue
    try:
        SUPER_ADMINS.add(int(_voce))
    except ValueError:
        print(f"SUPER_ADMINS: ignoro '{_voce}', non è un ID numerico")

try:
    GROUP_ID = int((os.environ.get("GROUP_ID", "0") or "0").strip() or 0)
except ValueError:
    GROUP_ID = 0

if GROUP_ID > 0:
    print(f"GROUP_ID={GROUP_ID} è positivo: gli ID dei gruppi sono negativi, "
          "controlla di non aver messo l'ID di un utente")

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/tmp/media"))
try:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"MEDIA_DIR non creabile ({type(e).__name__}: {e})")

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
PLAYWRIGHT_FALLBACK = False

if not BOT_TOKEN:
    raise SystemExit("Manca la variabile BOT_TOKEN")
if not DATABASE_URL:
    raise SystemExit("Manca la variabile DATABASE_URL")
if not SUPER_ADMINS:
    print("ATTENZIONE: SUPER_ADMINS è vuoto. Nessuno può aprire /admin "
          "finché non aggiungi un admin a mano nel database.")
