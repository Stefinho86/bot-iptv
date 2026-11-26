import datetime as dt
import re
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

import db

ROMA = ZoneInfo("Europe/Rome")
ORARIO_RE = re.compile(r"^([01]?\d|2[0-3])[:.,]?([0-5]\d)$")

SCOPES = [
    ("gruppo", "Nel gruppo", [
        ("sil_g_liste", "Rispondere alle richieste di lista"),
        ("sil_g_ringraziamenti", "Registrare i ringraziamenti"),
        ("sil_g_conferme", "Conferme brevi (saluti, grazie)"),
        ("sil_g_barzellette", "Barzellette sul tag"),
    ]),
    ("privato", "In privato con il bot", [
        ("sil_p_libere", "L'utente può scrivere al bot"),
        ("sil_p_liste", "Consegnare liste in privato"),
        ("sil_p_conferme", "Conferme in privato"),
    ]),
    ("notifiche", "Notifiche automatiche", [
        ("sil_n_avvisi", "Solleciti e avvisi"),
        ("sil_n_punizioni", "Avvisi di punizione"),
    ]),
]

CHIAVI = [k for _s, _t, voci in SCOPES for k, _e in voci]


def normalizza(valore):
    m = ORARIO_RE.match((valore or "").strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def _minuti(ora):
    h, m = ora.split(":")
    return int(h) * 60 + int(m)


def _valida(da, a):
    if da == a:
        return "Inizio e fine coincidono: la fascia non si applica."
    return ""


def dentro(da, a, quando=None):
    """True se l'ora di Roma (o quella passata) cade nella finestra.
    Gestisce la finestra che scavalca la mezzanotte."""
    ora = (quando or dt.datetime.now(ROMA)).strftime("%H:%M")
    d, f, m = _minuti(da), _minuti(a), _minuti(ora)
    if d == f:
        return False
    if d < f:
        return d <= m < f
    return m >= d or m < f


async def _finestra():
    da = normalizza(await db.get_setting("silenzio_da")) or "00:00"
    a = normalizza(await db.get_setting("silenzio_a")) or "07:00"
    return da, a


async def impostazioni():
    acceso = await db.get_int("silenzio_on")
    da, a = await _finestra()
    return acceso, da, a


async def attiva():
    if await db.get_int("silenzio_on") == 0:
        return False
    da, a = await _finestra()
    if _valida(da, a):
        return False
    return dentro(da, a)


async def sil_totale():
    return await db.get_int("sil_totale") == 1


async def totale():
    """Silenzio totale attivo adesso: nessuna risposta ai non admin."""
    if not await sil_totale():
        return False
    return await attiva()


async def consenti(chiave):
    if not await attiva():
        return True
    if await sil_totale():
        return False
    return await db.get_int(chiave) == 1


async def stato_breve(acceso, da, a):
    if not acceso:
        return "🔴 Fascia spenta"
    if _valida(da, a):
        return f"🔴 Orario non valido ({da}–{a})"
    sil = " · 🔇 totale" if await sil_totale() else ""
    if dentro(da, a):
        return f"🟢 In corso fino alle {a}{sil}"
    return f"🟡 {da}–{a} non in corso{sil}"


async def testo():
    acceso, da, a = await impostazioni()
    sil = await sil_totale()
    avviso = _valida(da, a)
    ora = dt.datetime.now(ROMA).strftime("%H:%M")
    in_corso = acceso and not avviso and dentro(da, a)

    righe = ["<b>🌙 Fascia silenzio</b>", ""]
    if not acceso:
        righe.append(f"Stato: <b>🔴 SPENTA</b> — orario memorizzato {da}–{a}")
    elif avviso:
        righe.append(f"Stato: <b>🔴 NON VALIDA</b> — {avviso}")
    elif in_corso:
        righe.append(f"Stato: <b>🟢 IN CORSO ADESSO</b> — sono le {ora}, "
                     f"fino alle <b>{a}</b>")
    else:
        righe.append(f"Stato: <b>🟡 ATTIVA MA NON IN CORSO</b> — sono le {ora}, "
                     f"scatta alle <b>{da}</b> e dura fino alle <b>{a}</b>")
    righe.append(f"Ora di Roma: <b>{ora}</b>")
    if sil:
        righe.append("🔇 <b>Silenzio totale</b>: dentro la fascia il bot non risponde a "
                     "nessuno tranne gli admin. Le voci qui sotto vengono ignorate.")
    righe += [
        "",
        "Nel gruppo il bot <b>non consegna mai le liste</b>: chi chiede riceve la "
        "barzelletta e il pulsante per la chat privata. Le consegne avvengono solo "
        "in privato.",
        "",
        "<b>Cosa resta acceso durante la fascia</b>",
        "Tocca una voce: 🟢 attivo, 🔴 bloccato. Fuori dalla fascia le voci non contano, "
        "il bot lavora normalmente.",
        "",
    ]
    return "\n".join(righe)


def kb(acceso, da, a):
    sil = await sil_totale()
    avviso = _valida(da, a)
    if not acceso:
        testa = "🔴 Fascia SPENTA — tocca per attivare"
    elif avviso:
        testa = "🔴 Orario non valido — controlla inizio e fine"
    elif dentro(da, a):
        testa = f"🟢 IN CORSO fino alle {a}"
    else:
        testa = f"🟡 Attiva {da}–{a} (non in corso)"
    righe = [
        [B(text=testa, callback_data="o:toggle")],
        [B(text=f"🕛 Inizio: {da}", callback_data="o:da"),
         B(text=f"🕖 Fine: {a}", callback_data="o:a")],
        [B(text=f"{'🔇' if sil else '🔊'} Silenzio totale: {'ON' if sil else 'OFF'}",
           callback_data="o:silence")],
    ]
    for _chiave, titolo, voci in SCOPES:
        righe.append([B(text=f"— {titolo} —", callback_data="a:noop")])
        for chiave, etichetta in voci:
            on = await db.get_int(chiave)
            righe.append([B(text=f"{'🟢' if on else '🔴'} {etichetta}",
                            callback_data=f"o:f:{chiave}")])
    righe.append([B(text="⬅️ Indietro", callback_data="a:set")])
    righe.append([B(text="🏠 Menu", callback_data="a:menu")])
    return K(inline_keyboard=righe)
