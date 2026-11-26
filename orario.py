"""Fascia oraria di silenzio.

Nella finestra impostata il bot non scrive nel gruppo, perché lì i messaggi
vengono cancellati dal bot di sicurezza. I ringraziamenti però vengono letti e
registrati lo stesso: la conferma parte in privato.
"""

import datetime as dt
import re
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

import db

ROMA = ZoneInfo("Europe/Rome")
ORA_RE = re.compile(r"^\s*(\d{1,2})(?:[:.,](\d{2}))?\s*$")


def _minuti(testo):
    m = ORA_RE.match(testo or "")
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def hhmm(minuti):
    minuti %= 1440
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


def normalizza(testo):
    m = _minuti(testo)
    return hhmm(m) if m is not None else None


async def impostazioni():
    acceso = await db.get_int("silenzio_on") == 1
    da = normalizza(await db.get_setting("silenzio_da")) or "00:00"
    a = normalizza(await db.get_setting("silenzio_a")) or "07:00"
    return acceso, da, a


def dentro(quando, da, a):
    """True se l'orario locale di Roma cade nella finestra. Gestisce l'overlap."""
    d, f = _minuti(da), _minuti(a)
    if d is None or f is None:
        return False
    locale = quando.astimezone(ROMA)
    m = locale.hour * 60 + locale.minute
    if d == f:
        return True
    if d < f:
        return d <= m < f
    return m >= d or m < f


async def attiva(quando=None):
    acceso, da, a = await impostazioni()
    if not acceso:
        return False
    return dentro(quando or dt.datetime.now(dt.timezone.utc), da, a)


async def riprende():
    _, _, a = await impostazioni()
    return a


def stato_breve(acceso, da, a):
    return f"ATTIVA {da}→{a}" if acceso else "SPENTA"


def kb(acceso, da, a):
    return K(inline_keyboard=[
        [B(text=f"🌙 Fascia silenzio: {stato_breve(acceso, da, a)}", callback_data="o:toggle")],
        [B(text=f"🕐 Inizio: {da}", callback_data="o:da"),
         B(text=f"🕘 Fine: {a}", callback_data="o:a")],
        [B(text="⬅️ Indietro", callback_data="a:set")],
        [B(text="🏠 Menu", callback_data="a:menu")],
    ])


async def testo():
    acceso, da, a = await impostazioni()
    adesso = dt.datetime.now(dt.timezone.utc)
    ora = adesso.astimezone(ROMA).strftime("%H:%M")
    if not acceso:
        stato = "SPENTA — il bot risponde a qualsiasi ora, come sempre."
    elif dentro(adesso, da, a):
        stato = (f"🔴 <b>IN CORSO ADESSO</b> (sono le {ora}). Fino alle {a} il bot "
                 f"resta zitto nel gruppo.")
    else:
        stato = (f"🟢 Attiva ma <b>non in corso</b> (sono le {ora}). "
                 f"Scatta alle {da} e dura fino alle {a}.")
    return ("<b>🌙 Fascia silenzio</b>\n\n"
            f"{stato}\n\n"
            "Dentro la finestra il bot non scrive nel gruppo, perché lì i messaggi vengono "
            "cancellati dal bot di sicurezza.\n\n"
            "<b>Cosa resta attivo</b>\n"
            "• i ringraziamenti vengono letti e registrati lo stesso;\n"
            "• la conferma del ringraziamento parte in privato, non nel gruppo;\n"
            "• chi chiede una lista riceve tutto in privato.\n\n"
            "<b>Cosa si ferma</b>\n"
            "• barzellette e inviti nel gruppo;\n"
            "• risposte pubbliche in generale.\n\n"
            f"Finestra attuale: <code>{da}</code> → <code>{a}</code> (ora di Roma)")


def _valida(da, a):
    d, f = _minuti(da), _minuti(a)
    if d is None or f is None:
        return None
    if d == f:
        return "Inizio e fine uguali: la fascia coprirebbe l'intera giornata."
    return None
