import asyncio
import datetime as dt
import re
from zoneinfo import ZoneInfo

import config
import db

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

CHIAVI = {
    "lists": "Consegna liste",
    "thanks": "Registra ringraziamenti",
    "dm": "Conferma in privato",
    "joke": "Barzellette sul tag",
    "no_punish": "Sospendi punizioni",
}


def _tz():
    try:
        return ZoneInfo(config.TZ_NAME)
    except Exception:
        return ZoneInfo("Europe/Rome")


def adesso():
    return dt.datetime.now(_tz())


async def attiva():
    return await db.get_int("sleep_on") == 1


async def consenti(chiave):
    """True se l'azione è ammessa in questo momento."""
    if not await in_finestra():
        return True
    return await db.get_int(f"sleep_{chiave}") == 1


async def silenzio():
    return await db.get_int("sleep_silence") == 1


async def conferma_privato():
    return await db.get_int("sleep_dm") == 1


async def sospendi_punizioni():
    return await db.get_int("sleep_no_punish") == 1


async def giorni():
    raw = await db.get_setting("sleep_days") or ""
    out = set()
    for pezzo in raw.split(","):
        pezzo = pezzo.strip()
        if pezzo.isdigit() and 0 <= int(pezzo) <= 6:
            out.add(int(pezzo))
    return out


def _minuti(valore, difetto):
    m = HHMM.match((valore or "").strip())
    if not m:
        hh, mm = difetto.split(":")
        return int(hh) * 60 + int(mm)
    hh, mm = valore.split(":")
    return int(hh) * 60 + int(mm)


async def _confini():
    inizio = _minuti(await db.get_setting("sleep_start"), "00:00")
    fine = _minuti(await db.get_setting("sleep_end"), "07:00")
    return inizio, fine


async def in_finestra(momento=None):
    """True se adesso cade nella fascia attiva."""
    if not await attiva():
        return False
    g = await giorni()
    if not g:
        return False
    ora = momento or adesso()
    if ora.weekday() not in g:
        return False
    inizio, fine = await _confini()
    if inizio == fine:
        return True
    m = ora.hour * 60 + ora.minute
    if inizio < fine:
        return inizio <= m < fine
    return m >= inizio or m < fine


async def attesa_riapertura():
    while await in_finestra():
        await asyncio.sleep(60)


async def descrizione():
    acceso = await attiva()
    inizio = await db.get_setting("sleep_start") or "00:00"
    fine = await db.get_setting("sleep_end") or "07:00"
    g = await giorni()
    elenco = ", ".join(GIORNI[i] for i in sorted(g)) or "nessun giorno"
    stato = "IN CORSO" if await in_finestra() else ("attiva" if acceso else "spenta")
    righe = [
        "<b>🌙 Fascia di silenzio</b>",
        "",
        f"Stato: <b>{stato}</b>",
        f"Orario: <b>{inizio} → {fine}</b>",
        f"Giorni: {elenco}",
        "",
        "<b>Cosa può fare il bot nella fascia</b>",
    ]
    for chiave, etichetta in CHIAVI.items():
        si = await db.get_int(f"sleep_{chiave}") == 1
        righe.append(f"{'🟢' if si else '🔴'} {etichetta}")
    sil = await silenzio()
    righe.append(f"{'🔴' if sil else '🟢'} Silenzio totale"
                 + (" (ignora tutto tranne gli admin)" if sil else ""))
    righe.append("")
    if sil:
        righe.append("Con il silenzio totale gli utenti non ricevono liste né risposte: "
                     "solo gli admin vengono serviti. Gli interruttori sopra restano "
                     "salvati e tornano attivi quando togli il silenzio.")
    else:
        righe.append("Le azioni con 🔴 vengono saltate, le altre passano. I messaggi "
                     "destinati al gruppo vanno in privato, così il bot di sicurezza "
                     "non li cancella.")
    return "\n".join(righe)
