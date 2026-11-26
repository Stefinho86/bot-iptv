import datetime as dt
import re
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

import db

ROMA = ZoneInfo("Europe/Rome")
ORARIO_RE = re.compile(r"^([01]?\d|2[0-3])[:.,]?([0-5]\d)$")

SCOPES = [
    ("gruppo", "Nel gruppo", [
        ("sil_g_liste", "Richieste di lista nel gruppo"),
        ("sil_g_ringraziamenti", "Registrare i ringraziamenti"),
        ("sil_g_conferme", "Conferme brevi nel gruppo"),
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


async def impostazioni():
    acceso = await db.get_int("silenzio_on")
    da = normalizza(await db.get_setting("silenzio_da")) or "00:00"
    a = normalizza(await db.get_setting("silenzio_a")) or "07:00"
    return acceso, da, a


async def attiva():
    if await db.get_int("silenzio_on") == 0:
        return False
    _acceso, da, a = await impostazioni()
    if _valida(da, a):
        return False
    ora = dt.datetime.now(ROMA).strftime("%H:%M")
    if _minuti(da) <= _minuti(a):
        return _minuti(da) <= _minuti(ora) < _minuti(a)
    return _minuti(ora) >= _minuti(da) or _minuti(ora) < _minuti(a)


async def consenti(chiave):
    if not await attiva():
        return True
    return await db.get_int(chiave) == 1


async def stato_breve(acceso, da, a):
    if not acceso:
        return "🔴 Fascia spenta"
    return f"🟢 Fascia {da}–{a}"


async def testo():
    acceso, da, a = await impostazioni()
    avviso = _valida(da, a)
    righe = ["<b>🌙 Fascia silenzio</b>", ""]
    if acceso:
        righe.append(f"Stato: <b>🟢 ATTIVA</b> — dalle <b>{da}</b> alle <b>{a}</b> (ora di Roma)")
    else:
        righe.append(f"Stato: <b>🔴 SPENTA</b> — orario memorizzato {da}–{a}")
    if avviso:
        righe.append(f"⚠️ {avviso}")
    righe += [
        "",
        "Nella fascia il bot tace nel gruppo. I ringraziamenti vengono letti lo stesso: "
        "la conferma parte in privato, così il messaggio cancellato dal bot di sicurezza "
        "non penalizza nessuno.",
        "",
        "<b>Cosa resta acceso durante la fascia</b>",
        "Tocca una voce: verde = attivo, rosso = bloccato. Le voci fuori fascia non "
        "cambiano niente, il bot lavora normalmente.",
        "",
    ]
    return "\n".join(righe)


async def kb(acceso, da, a):
    righe = [
        [B(text=(f"🟢 Fascia ATTIVA {da}–{a}" if acceso else "🔴 Fascia SPENTA"),
           callback_data="o:toggle")],
        [B(text=f"🕛 Inizio: {da}", callback_data="o:da"),
         B(text=f"🕖 Fine: {a}", callback_data="o:a")],
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
