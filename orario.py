"""Fascia di silenzio configurabile dagli admin.

In fascia il gruppo e' chiuso e il bot di sicurezza cancella i messaggi.
Il bot li riceve comunque: registra i ringraziamenti, non punisce e conferma
in privato, perche' nel gruppo la risposta sparirebbe.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

import config
import db

try:
    ROMA = ZoneInfo(config.TZ_NAME)
except Exception:
    ROMA = ZoneInfo("Europe/Rome")

HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


def _minuti(valore, fallback):
    m = HHMM.match((valore or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else fallback


async def attiva() -> bool:
    return await db.get_int("sleep_on") == 1


async def conferma_privato() -> bool:
    return await db.get_int("sleep_dm") == 1


async def sospendi_punizioni() -> bool:
    return await db.get_int("sleep_no_punish") == 1


async def giorni() -> set:
    raw = await db.get_setting("sleep_days") or ""
    out = {int(p) for p in raw.split(",") if p.strip().isdigit() and 0 <= int(p) <= 6}
    return out or set(range(7))


async def in_finestra(adesso=None) -> bool:
    if not await attiva():
        return False
    adesso = adesso or dt.datetime.now(ROMA)
    if adesso.weekday() not in await giorni():
        return False
    start = _minuti(await db.get_setting("sleep_start"), 0)
    end = _minuti(await db.get_setting("sleep_end"), 7 * 60)
    if start == end:
        return False
    cur = adesso.hour * 60 + adesso.minute
    return start <= cur < end if start < end else (cur >= start or cur < end)


async def minuti_alla_riapertura(adesso=None) -> int:
    if not await in_finestra(adesso):
        return 0
    adesso = adesso or dt.datetime.now(ROMA)
    cur = adesso.hour * 60 + adesso.minute
    end = _minuti(await db.get_setting("sleep_end"), 7 * 60)
    return end - cur if end > cur else (1440 - cur) + end


async def attesa_riapertura(massimo=900):
    """Dorme finche' la fascia non e' finita. Tetto per non restare appesi."""
    atteso = 0
    while await in_finestra() and atteso < massimo:
        await _dormi(30)
        atteso += 30


async def _dormi(secondi):
    import asyncio
    await asyncio.sleep(secondi)


async def descrizione() -> str:
    if not await attiva():
        return "🔕 Fascia di silenzio: <b>disattivata</b>"
    start = await db.get_setting("sleep_start") or "00:00"
    end = await db.get_setting("sleep_end") or "07:00"
    g = await giorni()
    nomi = "tutti i giorni" if len(g) == 7 else ", ".join(GIORNI[i] for i in sorted(g))
    stato = "🟢 <b>IN CORSO</b>" if await in_finestra() else "⚪ fuori fascia"
    return (f"🔕 Fascia di silenzio: <b>{start} → {end}</b>\n"
            f"📅 Giorni: {nomi}\n"
            f"Stato: {stato}\n"
            f"✉️ Conferma in privato: {'sì' if await conferma_privato() else 'no'} · "
            f"⚖️ Punizioni: {'sospese' if await sospendi_punizioni() else 'attive'}")
