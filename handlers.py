import asyncio
import datetime as dt
import io
import random
import shutil
import json
import re
import uuid
from html import escape as _esc
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, FSInputFile,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           LinkPreviewOptions)

import classificatore
import db
import config
import orario
import utils as kb
from services import detect, detect_many, ocr_bytes, preview, test_list
from utils import IsAdmin, Trigger, NotCommand, AnonUnverified, Ringrazia

page_router = Router()

admin_router = Router()
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())

anon_router = Router()
user_router = Router()

BUSY = set()
TMP = {}
BULK = {}
MULTI = {}
DSEL = {}

ROMA = ZoneInfo("Europe/Rome")

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

SEGNAPOSTO_RE = re.compile(r"\{(\w+)\}")

HINT = "\n\nScrivi /annulla per interrompere."

MAX_ANTEPRIMA = 15
PER_PAGE = 10
PER_PAGE_MAN = 8
PER_PAGE_HIST = 6

NONMOD = object()

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

BARZELLETTE = [
    "Un uomo entra in un bar e chiede un caffè. Il barista: «Decaffeinato?» "
    "«No, grazie, lo prendo qui.»",
    "Perché gli italiani non usano il GPS? Perché conoscono già tutte le scorciatoie "
    "e tutte le scuse.",
    "«Dottore, mi sento come un ponte.» «E cosa c'è sotto?» «L'acqua, dottore.»",
    "Al ristorante: «Cameriere, questo pollo è duro!» «E chi se ne frega, mica deve "
    "fare la caccia.»",
    "Perché il mare è salato? Perché ci hanno pisciato dentro i pesci per secoli.",
    "«Mamma, mi compri il gelato?» «No.» «E allora lo rubo.» «E allora compralo.»",
    "Un uomo va dal medico: «Dottore, quando mi alzo la mattina mi gira la testa.» "
    "«E chi te lo dice di alzarti?»",
    "Perché le donne vivono più degli uomini? Perché non devono sposarsi con un uomo.",
    "«Pronto, è il 118? Mia moglie sta per partorire!» «Respiri, signore.» "
    "«E poi?» «E poi si calmi, non è lei che deve partorire.»",
    "Un ubriaco entra in un bar alle nove del mattino e chiede un bicchiere d'acqua. "
    "Il barista lo guarda: «Ti senti bene?»",
    "Perché l'italiano medio mette il sale nel caffè? Perché ha sentito che fa bene "
    "alla pressione e lui non si fida di nessuno.",
    "«Come si chiama suo figlio?» «Mario, come il padre.» «E il padre?» «Mario.» "
    "«E suo marito?» «Mario.» «E lei?» «Maria.»",
    "Un uomo dice alla moglie: «Ho comprato una macchina nuova.» «E i soldi?» "
    "«Quelli li ho comprati dopo.»",
    "Perché il pesce non prende mai il raffreddore? Perché sta sempre in acqua e "
    "non si scopre.",
    "«Cameriere, c'è una mosca nel mio piatto.» «Normale, signore, il pesce è fresco.»",
    "Pierino torna da scuola: «Mamma, oggi ho preso dieci!» «In cosa?» «In condotta, "
    "però dieci.»",
    "Un tizio in farmacia: «Avete qualcosa per l'insonnia?» «Sì, questo.» "
    "«Funziona?» «Boh, io non lo uso mai.»",
    "Perché il caffè è meglio del sesso? Perché dopo non devi fumare e non devi "
    "chiamarlo il giorno dopo.",
    "«Dottore, mia moglie crede di essere un frigorifero.» «E da quanto?» «Da quando "
    "ha chiuso la porta e non parla più.»",
    "Un siciliano a Milano: «Scusi, per il Duomo?» «Sempre dritto.» «E chi si ferma?»",
    "Perché gli uomini amano le donne con il senso dell'umorismo? Perché così almeno "
    "una delle due ride.",
    "«Caro, mi trovi bella?» «Sì.» «E intelligente?» «Adesso non esageriamo.»",
    "Un prete in motorino viene fermato: «Patente e libretto.» «Guardi che sono un "
    "prete.» «E allora?» «E allora benedico la macchina.»",
    "Perché il matrimonio è come un assedio? Perché chi sta fuori vuole entrare e "
    "chi sta dentro vuole uscire.",
    "«Cameriere, il vino sa di tappo.» «Signore, è il tappo.»",
    "Un uomo al bar: «Un caffè e un bicchiere d'acqua.» «Per il caffè?» «No, per "
    "il bicchiere.»",
    "Perché a Napoli il mare è azzurro? Perché è tifoso.",
    "«Mamma, papà è caduto dalle scale!» «Si è fatto male?» «No, è caduto "
    "dall'ultimo gradino.»",
    "Un tizio al telefono: «Pronto, parlo con l'idraulico?» «Sì.» «Ho il lavandino "
    "otturato.» «E io che c'entro, mica l'ho otturato io.»",
    "Perché gli italiani scrivono le ricette? Perché non si fidano di chi cucina "
    "meglio di loro.",
    "«Dottore, ho perso la memoria.» «E da quando?» «Da quando? Da quando cosa?»",
    "Un uomo entra in banca: «Vorrei aprire un conto.» «Certo, quanto versa?» "
    "«Un litro.»",
    "Perché il gallo canta all'alba? Perché non ha il telefono e non può mettere "
    "la sveglia.",
    "«Caro, se morissi rifaresti il matrimonio?» «Con chi?» «Con te.» «Ah.»",
    "Perché i pesci non giocano a calcio? Perché hanno paura del portiere "
    "che è un polpo.",
    "Un tizio al ristorante: «Cameriere, questo vino è tappato.» «Ma è acqua.» "
    "«Appunto, è tappata.»",
]

_ULTIMA = [""]


class Add(StatesGroup):
    m3u = State()
    x_server = State()
    x_user = State()
    x_pass = State()
    s_portal = State()
    s_mac = State()
    confirm = State()
    bulk = State()


class SetTxt(StatesGroup):
    welcome = State()
    deny = State()
    not_member = State()
    consegna = State()
    richiesta = State()
    ok = State()
    ko = State()
    nolist = State()
    broken = State()
    minuti = State()
    punizione = State()
    pun_gruppo = State()
    pun_admin = State()
    joke_testo = State()
    joke_api = State()
    sil_da = State()
    sil_a = State()


class Ask(StatesGroup):
    wait = State()


# ==================== AIUTI ====================
def _ora(valore, fmt="%d/%m/%Y %H:%M"):
    try:
        return valore.astimezone(ROMA).strftime(fmt)
    except Exception:
        return "-"


def _fmt(testo, **valori):
    """Riempie solo i segnaposto conosciuti. Le graffe estranee restano scritte."""
    def sostituisci(m):
        chiave = m.group(1)
        return str(valori[chiave]) if chiave in valori else m.group(0)
    return SEGNAPOSTO_RE.sub(sostituisci, testo or "")


def _num(data, idx):
    try:
        return int((data or "").split(":")[idx])
    except Exception:
        return 0


def _human(delta):
    s = int(delta.total_seconds())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    out = []
    if d:
        out.append(f"{d}g")
    if h:
        out.append(f"{h}h")
    if m and not d:
        out.append(f"{m}m")
    return " ".join(out) or "pochi minuti"


def _quadro(s):
    return (f"<b>Pannello admin</b>\n\n"
            f"📋 Libere: {s['free']} · 📤 Assegnate: {s['assigned']}\n"
            f"👤 Utenti: {s['users']} · 🛡 Admin: {s['admins']} · 👑 Proprietario: {s['super']}\n"
            f"🚫 Bannati: {s['banned']}\n\n"
            f"🕐 {_ora(dt.datetime.now(dt.timezone.utc))}")


async def _statistiche_text():
    s = await db.stats()
    gid = await db.get_group_id()
    gonly = await db.get_int("group_only")
    en = await db.get_int("enabled")
    cool = await db.get_int("cooldown_days")
    minuti = await db.get_int("thanks_min")
    punizione = _esc(await db.get_setting("punizione"))
    acceso, da, a = await orario.impostazioni()
    return ("<b>📊 Statistiche</b>\n\n"
            f"📋 Liste libere: <b>{s['free']}</b>\n"
            f"📤 Liste assegnate: <b>{s['assigned']}</b>\n"
            f"🗂 Totale liste: <b>{s['free'] + s['assigned']}</b>\n\n"
            f"👤 Utenti: <b>{s['users']}</b>\n"
            f"🚫 Bannati: <b>{s['banned']}</b>\n"
            f"🛡 Admin: <b>{s['admins']}</b> · 👑 Proprietario: <b>{s['super']}</b>\n\n"
            f"👥 Gruppo: <code>{gid or 'non impostato'}</code>\n"
            f"🚦 Filtro gruppo: {'ON' if gonly else 'OFF (tutti passano)'}\n"
            f"⚡ Bot: {'ATTIVO' if en else 'SPENTO'}\n"
            f"🌙 Fascia silenzio: {orario.stato_breve(acceso, da, a)}\n"
            f"⏳ Attesa: {cool} giorni · ⏱ Ringraziamento: {minuti} min\n"
            f"⚖️ Punizione: {punizione}\n\n"
            f"🕐 {_ora(dt.datetime.now(dt.timezone.utc))}")


def _comandi_text():
    return ("<b>📖 Comandi</b>\n\n"
            "<b>👤 Utente</b>\n"
            "/start - avvia il bot e mostra il benvenuto\n"
            "/lista - ricevi una lista IPTV\n"
            "/mio - la tua lista attuale e lo stato del ringraziamento\n"
            "/ping - verifica che il bot risponda (chat id e tipo chat)\n\n"
            "<b>🎬 Liste</b>\n"
            "/aggiungi - inserisci una lista (M3U, Xtream, Stalker, foto o txt)\n"
            "/liberi - liste non assegnate\n"
            "/assegnazioni - storico consegne con i ringraziamenti\n"
            "/revoca - ritira un'assegnazione\n"
            "/esporta - esporta le liste libere in JSON\n"
            "/log - scarica il file di log attività\n\n"
            "<b>👥 Utenti</b>\n"
            "/utenti - cerca un utente\n"
            "/elenco - elenco utenti a pagine\n"
            "/bannati - elenco utenti bannati\n"
            "/ban - blocca un utente\n"
            "/unban - sblocca un utente\n"
            "/reset - azzera l'attesa di un utente\n\n"
            "<b>🛡 Amministratori</b>\n"
            "/admin_add - aggiungi un amministratore\n"
            "/admin_del - rimuovi un amministratore\n"
            "/verifica - verifica un admin che scrive in anonimo\n"
            "/deverifica - revoca la verifica della chat\n\n"
            "<b>⚙️ Sistema</b>\n"
            "/admin - pannello di controllo\n"
            "/orario - fascia silenzio: orari in cui il bot tace nel gruppo\n"
            "/stato - diagnostica di gruppo, filtro e bot\n"
            "/comandi - questo elenco\n"
            "/cooldown - giorni di attesa tra due liste\n"
            "/setgroup - gruppo autorizzato\n"
            "/setlink - link d'invito del gruppo\n"
            "/toggle - accendi o spegni il bot\n"
            "/annulla - interrompi l'operazione in corso\n\n"
            "<b>✏️ Personalizzazione</b>\n"
            "Impostazioni → Frasi per l'utente: testi inviati agli utenti e minuti per "
            "ringraziare.\n"
            "Impostazioni → Punizione mancato ringraziamento: cosa scrivere nel gruppo e "
            "agli admin quando il ringraziamento non arriva.\n"
            "Impostazioni → Barzellette sul tag: barzelletta quando il bot viene taggato "
            "nel gruppo per chiedere una lista.\n"
            "Pannello → Ringraziamenti manuali: segna a mano un ringraziamento.\n\n"
            "Tutti i comandi sugli utenti accettano ID, @username o inoltro.")


def _payload_text(kind, p):
    if kind == "xtream":
        return (f"Server: <code>{p['server']}</code>\nUtente: <code>{p['username']}</code>\n"
                f"Password: <code>{p['password']}</code>\n\n"
                f"M3U: <code>{p['server']}/get.php?username={p['username']}"
                f"&password={p['password']}&type=m3u_plus</code>")
    if kind == "stalker":
        return f"Portal: <code>{p['portal']}</code>\nMAC: <code>{p['mac']}</code>"
    return f"<code>{p['url']}</code>"


def _nome(r):
    return (r["full_name"] or "").strip() or "(senza nome)"


def _utente(r):
    return f"@{r['username']}" if r["username"] else "no username"


def _pulisci_user(nome):
    n = (nome or "").strip().lstrip("@").strip()
    n = re.sub(r"[-9A-Za-z_]", "", n)
    return n[:32]


def _link(uid, username):
    n = _pulisci_user(username)
    return f"https://t.me/{n}" if n else f"tg://user?id={uid}"


def _etichetta(r, stato=""):
    nome = _nome(r)[:9]
    user = _pulisci_user(r["username"])[:9]
    user = f"@{user}" if user else "no username"
    return f"{stato} {nome} {user} · {r['user_id']}"[:48]


def _voce(n, r, bannato=False, motivo=None):
    """Riga singola: numero, nome cliccabile, username, ID."""
    link = _link(r["user_id"], r["username"])
    nome = _esc(_nome(r))
    u = _pulisci_user(r["username"])
    utente = f"@{_esc(u)}" if u else "no username"
    segno = "🚫" if bannato else "✅"
    coda = f" · <i>{_esc(motivo)}</i>" if motivo else ""
    return (f"{segno} {n}. <a href=\"{link}\">{nome}</a> · {utente} · "
            f"<code>{r['user_id']}</code>{coda}")


def _etichetta_btn(n, r):
    """Testo del pulsante: numero, nome, username e ID. ID mai tagliato."""
    nome = _nome(r)
    if len(nome) > 16:
        nome = nome[:15] + "…"
    u = _pulisci_user(r["username"])
    utente = f"@{u}" if u else "no user"
    if len(utente) > 12:
        utente = utente[:11] + "…"
    return f"{n}. {nome} · {utente} · {r['user_id']}"


async def _bot_tag(bot):
    try:
        return "@" + (await bot.me()).username
    except Exception:
        return "@bot"


async def _edit(target, testo, kbd=None):
    try:
        await target.edit_text(testo, reply_markup=kbd, link_preview_options=NO_PREVIEW)
        return True
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
        try:
            await target.answer(testo, reply_markup=kbd, link_preview_options=NO_PREVIEW)
            return True
        except Exception:
            return False


async def _mostra(cb, testo, kbd=None):
    errore = None
    try:
        await cb.message.edit_text(testo, reply_markup=kbd, link_preview_options=NO_PREVIEW)
        return True
    except Exception as e:
        errore = e
        if "not modified" in str(e).lower():
            return True
    try:
        await cb.message.answer(testo, reply_markup=kbd, link_preview_options=NO_PREVIEW)
        return True
    except Exception as e:
        errore = e
    try:
        await cb.answer(f"{type(errore).__name__}: {errore}"[:195], show_alert=True)
    except Exception:
        pass
    return False


async def _pubblica(msg, testo, kbd_url, kbd_senza_url, edit=True):
    """Modifica con URL, poi modifica senza URL, poi nuovo messaggio.
    None se mostrata, NONMOD se identica, altrimenti l'eccezione."""
    if edit:
        try:
            await msg.edit_text(testo, reply_markup=kbd_url,
                                link_preview_options=NO_PREVIEW)
            return None
        except Exception as e:
            if "not modified" in str(e).lower():
                return NONMOD
    try:
        await msg.edit_text(testo, reply_markup=kbd_senza_url,
                            link_preview_options=NO_PREVIEW)
        return None
    except Exception:
        pass
    try:
        await msg.answer(testo, reply_markup=kbd_senza_url,
                         link_preview_options=NO_PREVIEW)
        return None
    except Exception as e:
        return e


async def _esito(cb, errore, nota="", ok_testo=""):
    """Unica risposta al callback: cosi' l'avviso si vede sempre."""
    if cb is None:
        return
    try:
        if errore is NONMOD:
            await cb.answer(nota or "Nessuna modifica", show_alert=True)
        elif errore is not None:
            await cb.answer(f"ERRORE: {type(errore).__name__}: {errore}"[:195], show_alert=True)
        else:
            await cb.answer(ok_testo)
    except Exception as e:
        print(f"esito: {type(e).__name__}: {e}")


async def _avvisa(cb, e):
    print(f"PAGINA: {type(e).__name__}: {e}")
    if cb is None:
        return
    try:
        await cb.answer(f"ERRORE: {type(e).__name__}: {e}"[:195], show_alert=True)
    except Exception:
        pass


def _forwarded_uid(msg):
    fo = getattr(msg, "forward_origin", None)
    if fo is not None:
        mittente = getattr(fo, "sender_user", None)
        if mittente:
            return mittente.id
        chat = getattr(fo, "sender_chat", None)
        if chat:
            return chat.id
        return None
    vecchio = getattr(msg, "forward_from", None)
    return vecchio.id if vecchio else None


async def resolve_uid(bot, text):
    text = (text or "").strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    name = text.lstrip("@").strip()
    if not name:
        return None
    uid = await db.find_by_username(name)
    if uid:
        return uid
    try:
        chat = await bot.get_chat("@" + name)
        return chat.id
    except Exception:
        pass
    righe = await db.find_users(name)
    if len(righe) == 1:
        return righe[0]["user_id"]
    return None


async def _target(msg, raw=None):
    uid = _forwarded_uid(msg)
    if uid:
        return uid
    return await resolve_uid(msg.bot, raw if raw is not None else (msg.text or ""))


async def _need_target(msg):
    await msg.answer("Non riesco a risolvere quel nome. Telegram permette di cercare uno "
                     "@username solo se il bot quella persona l'ha già incontrata.\n\n"
                     "Inoltra qui un suo messaggio, oppure mandami il suo ID numerico." + HINT,
                     reply_markup=kb.nav("a:menu"))


async def _stato_text(msg, bot):
    gid = await db.get_group_id()
    gonly = await db.get_int("group_only")
    en = await db.get_int("enabled")
    acceso, da, a = await orario.impostazioni()
    if await orario.attiva():
        fascia = f"IN CORSO fino alle {a}"
    elif acceso:
        fascia = f"impostata {da}→{a}, non in corso"
    else:
        fascia = "spenta"
    if msg.from_user.id == kb.ANON_ID:
        who = await db.get_anon_verified(msg.chat.id)
        tu = f"verificato come <code>{who}</code>" if who else "NON verificato"
    else:
        try:
            m = await bot.get_chat_member(gid, msg.from_user.id) if gid else None
            tu = f"stato membro: {m.status}" if m else "gruppo non impostato"
        except Exception as e:
            tu = f"errore lettura membri: {type(e).__name__}"
    return (f"Gruppo configurato: <code>{gid}</code>\n"
            f"Filtro gruppo: {'ON' if gonly else 'OFF (tutti passano)'}\n"
            f"Bot: {'attivo' if en else 'spento'}\n"
            f"Fascia silenzio: {fascia}\nTu: {tu}\n"
            f"Ora server: {_ora(dt.datetime.now(dt.timezone.utc))} (Europe/Rome)")


def _blocchi(testo):
    fuori, corrente = [], []
    for riga in (testo or "").splitlines():
        if riga.strip():
            corrente.append(riga.strip())
        elif corrente:
            fuori.append(corrente)
            corrente = []
    if corrente:
        fuori.append(corrente)
    return fuori


def _parse_m3u(testo):
    out = []
    for riga in (testo or "").splitlines():
        r = riga.strip()
        if r.startswith("http"):
            out.append({"kind": "m3u", "label": r[:40], "payload": {"url": r}})
    return out


def _parse_xtream(testo):
    out = []
    for b in _blocchi(testo):
        if len(b) < 3:
            continue
        server = b[0]
        if not server.startswith("http"):
            server = "http://" + server
        if not re.match(r"^https?://[^\s/]+", server):
            continue
        out.append({"kind": "xtream", "label": server.rstrip("/"),
                    "payload": {"server": server.rstrip("/"),
                                "username": b[1], "password": b[2]}})
    return out


def _parse_stalker(testo):
    out = []
    for b in _blocchi(testo):
        if len(b) < 2:
            continue
        portal = b[0]
        if not portal.startswith("http"):
            portal = "http://" + portal
        if not re.match(r"^https?://[^\s/]+", portal):
            continue
        mac = b[1].upper().replace("-", ":")
        if not MAC_RE.match(mac):
            continue
        out.append({"kind": "stalker", "label": portal.rstrip("/"),
                    "payload": {"portal": portal.rstrip("/"), "mac": mac}})
    return out


def _pulisci(uid):
    for cid, d in list(MULTI.items()):
        if d.get("uid") == uid:
            MULTI.pop(cid, None)
    for cid, it in list(TMP.items()):
        if it.get("uid") == uid:
            TMP.pop(cid, None)
    for cid, d in list(BULK.items()):
        if d.get("uid") == uid:
            BULK.pop(cid, None)


# ==================== BARZELLETTE ====================
def _barzelletta_interna():
    """Pesca una barzelletta italiana diversa dall'ultima usata."""
    if not BARZELLETTE:
        return "..."
    scelta = random.choice(BARZELLETTE)
    for _ in range(8):
        if scelta != _ULTIMA[0]:
            break
        scelta = random.choice(BARZELLETTE)
    _ULTIMA[0] = scelta
    return scelta


def _da_json(dati):
    """Estrae una battuta dai formati possibili del servizio."""
    if isinstance(dati, dict):
        elenco = dati.get("jokes")
        if isinstance(elenco, list):
            valide = [str(x).strip() for x in elenco if str(x).strip()]
            if valide:
                return random.choice(valide)
        testo = (dati.get("joke") or dati.get("text") or dati.get("battuta") or "")
        if not testo and dati.get("setup"):
            testo = (str(dati["setup"]) + "\n\n"
                     + str(dati.get("delivery") or dati.get("punchline") or ""))
        if str(testo).strip():
            return str(testo).strip()
        return ""
    if isinstance(dati, list) and dati:
        primo = dati[0]
        if isinstance(primo, str):
            valide = [str(x).strip() for x in dati if str(x).strip()]
            return random.choice(valide) if valide else ""
        if isinstance(primo, dict):
            testo = primo.get("joke") or primo.get("text") or ""
            return str(testo).strip()
    return ""


async def _barzelletta():
    url = (await db.get_setting("joke_api") or "").strip()
    if not url:
        return _barzelletta_interna()
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status == 200:
                    dati = await r.json(content_type=None)
                    testo = _da_json(dati)
                    if testo:
                        _ULTIMA[0] = testo
                        return testo
    except Exception as e:
        print(f"barzelletta: {type(e).__name__}: {e}")
    return _barzelletta_interna()


async def _risposta_tag(msg):
    """Barzelletta + invito in privato. False se le barzellette sono spente."""
    if await db.get_int("joke_on") == 0:
        return False
    battuta = await _barzelletta()
    coda = _fmt(await db.get_setting("joke_testo"),
                name=_esc(msg.from_user.first_name or "bello"))
    try:
        await msg.reply(f"{battuta}\n\n{coda}", link_preview_options=NO_PREVIEW)
    except Exception as e:
        print(f"risposta_tag: {type(e).__name__}: {e}")
        return False
    return True


async def _di_nulla(msg):
    try:
        await msg.reply("🙏 Di nulla!", link_preview_options=NO_PREVIEW)
    except Exception:
        pass


async def _in_silenzio(msg):
    """Avviso breve in privato quando la fascia silenzio è attiva."""
    try:
        await msg.bot.send_message(
            msg.from_user.id,
            f"🌙 In questo momento c'è la fascia silenzio (fino alle "
            f"{await orario.riprende()}). Ti rispondo qui in privato, così nel gruppo "
            f"non sporco.", link_preview_options=NO_PREVIEW)
        return True
    except Exception:
        return False


# ==================== PAGINE ====================
async def _sono_admin(cb):
    """Risponde solo se NON e' admin: e' l'unica risposta in quel caso."""
    try:
        if await kb.is_admin_cb(cb):
            return True
    except Exception as e:
        await _avvisa(cb, e)
        return True
    try:
        await cb.answer("Non sei amministratore", show_alert=True)
    except Exception:
        pass
    return False


def _pagine(tot, per=PER_PAGE):
    return max(1, (tot + per - 1) // per)


def _taglio(righe, page, per=PER_PAGE):
    pagine = _pagine(len(righe), per)
    page = max(0, min(int(page), pagine - 1))
    fetta = righe[page * per:(page + 1) * per]
    if not fetta and page > 0:
        page = 0
        fetta = righe[:per]
        pagine = _pagine(len(righe), per)
    return page, pagine, fetta


def _coppia(data):
    """Legge a:u:<pagina>:<corrente> (o a:n:...) oppure lu:<pagina>."""
    pezzi = (data or "").split(":")
    if len(pezzi) >= 4 and pezzi[1] in ("u", "n"):
        try:
            return int(pezzi[2]), int(pezzi[3])
        except ValueError:
            pass
    if len(pezzi) >= 2:
        try:
            return int(pezzi[1]), None
        except ValueError:
            pass
    return 0, None


def _nav_pagine(prefisso, page, pagine):
    return [
        InlineKeyboardButton(text="◀️",
                             callback_data=f"{prefisso}:{max(0, page - 1)}:{page}"),
        InlineKeyboardButton(text=f"pag. {page + 1}/{pagine}",
                             callback_data=f"{prefisso}:{page}:{page}"),
        InlineKeyboardButton(text="▶️",
                             callback_data=f"{prefisso}:{min(pagine - 1, page + 1)}:{page}"),
    ]


def _numeri_pagina(prefisso, page, pagine):
    if pagine < 2 or pagine > 6:
        return None
    return [InlineKeyboardButton(text=("•" if n == page else str(n + 1)),
                                 callback_data=f"{prefisso}:{n}:{page}")
            for n in range(pagine)]


def _coda_pagina():
    return [
        [InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")],
        [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
    ]


def _chiusura(prefisso, page, pagine):
    righe = []
    numeri = _numeri_pagina(prefisso, page, pagine)
    if numeri:
        righe.append(numeri)
    righe.append(_nav_pagine(prefisso, page, pagine))
    righe += _coda_pagina()
    return righe


async def _mostra_utenti(msg, page, edit=True, cb=None, nota="", ok_testo=""):
    try:
        if msg is None:
            return await _esito(cb, RuntimeError("nessun messaggio"), nota, ok_testo)
        tutti = list(await db.all_users_list(config.SUPER_ADMINS))
        tot = len(tutti)
        if not tot:
            kbd = kb.nav("a:menu")
            errore = await _pubblica(msg, "Nessun utente normale registrato.",
                                     kbd, kbd, edit)
            return await _esito(cb, errore, nota, ok_testo)
        page, pagine, righe = _taglio(tutti, page)
        inizio = page * PER_PAGE
        con_url, senza_url, voci = [], [], []
        for scarto, r in enumerate(righe, 1):
            n = inizio + scarto
            bannato = bool(r["banned"])
            link = _link(r["user_id"], r["username"])
            azione = f"lb:{'unban' if bannato else 'ban'}:{r['user_id']}:{page}"
            segno = "♻️" if bannato else "🚫"
            voci.append(_voce(n, r, bannato))
            con_url.append([
                InlineKeyboardButton(text=_etichetta_btn(n, r), url=link),
                InlineKeyboardButton(text=segno, callback_data=azione),
            ])
            senza_url.append([
                InlineKeyboardButton(text=_etichetta_btn(n, r), callback_data="a:noop"),
                InlineKeyboardButton(text=segno, callback_data=azione),
            ])
        coda = "" if pagine > 1 else "\nUnica pagina."
        testo = (f"👤 Utenti normali: <b>{tot}</b> · pagina {page + 1}/{pagine}{coda}\n"
                 f"Tocca il numero o il nome per aprire la chat.\n\n" + "\n".join(voci))
        errore = await _pubblica(
            msg, testo,
            InlineKeyboardMarkup(inline_keyboard=con_url + _chiusura("a:u", page, pagine)),
            InlineKeyboardMarkup(inline_keyboard=senza_url + _chiusura("a:u", page, pagine)),
            edit)
        await _esito(cb, errore, nota or f"Sei già a pagina {page + 1} di {pagine}", ok_testo)
    except Exception as e:
        await _avvisa(cb, e)


async def _mostra_bannati(msg, page, edit=True, cb=None, nota="", ok_testo=""):
    try:
        if msg is None:
            return await _esito(cb, RuntimeError("nessun messaggio"), nota, ok_testo)
        tutti = list(await db.banned_users_list())
        tot = len(tutti)
        if not tot:
            kbd = kb.nav("a:menu")
            errore = await _pubblica(msg, "Nessun utente bannato.", kbd, kbd, edit)
            return await _esito(cb, errore, nota, ok_testo)
        page, pagine, righe = _taglio(tutti, page)
        inizio = page * PER_PAGE
        con_url, senza_url, voci = [], [], []
        for scarto, r in enumerate(righe, 1):
            n = inizio + scarto
            link = _link(r["user_id"], r["username"])
            azione = f"lb:unban:{r['user_id']}:{page}"
            voci.append(_voce(n, r, True, r["ban_reason"] or "senza motivo"))
            con_url.append([
                InlineKeyboardButton(text=_etichetta_btn(n, r), url=link),
                InlineKeyboardButton(text="♻️", callback_data=azione),
            ])
            senza_url.append([
                InlineKeyboardButton(text=_etichetta_btn(n, r), callback_data="a:noop"),
                InlineKeyboardButton(text="♻️", callback_data=azione),
            ])
        coda = "" if pagine > 1 else "\nUnica pagina."
        testo = (f"🚫 Utenti bannati: <b>{tot}</b> · pagina {page + 1}/{pagine}{coda}\n"
                 f"Tocca il numero o il nome per aprire la chat.\n\n" + "\n".join(voci))
        errore = await _pubblica(
            msg, testo,
            InlineKeyboardMarkup(inline_keyboard=con_url + _chiusura("a:n", page, pagine)),
            InlineKeyboardMarkup(inline_keyboard=senza_url + _chiusura("a:n", page, pagine)),
            edit)
        await _esito(cb, errore, nota or f"Sei già a pagina {page + 1} di {pagine}", ok_testo)
    except Exception as e:
        await _avvisa(cb, e)


async def _apri_utenti(cb, page, corrente=None):
    if not await _sono_admin(cb):
        return
    nota = f"Sei già a pagina {page + 1}" if corrente is not None and page == corrente else ""
    await _mostra_utenti(cb.message, page, edit=True, cb=cb, nota=nota)


async def _apri_bannati(cb, page, corrente=None):
    if not await _sono_admin(cb):
        return
    nota = f"Sei già a pagina {page + 1}" if corrente is not None and page == corrente else ""
    await _mostra_bannati(cb.message, page, edit=True, cb=cb, nota=nota)


@page_router.callback_query(F.data == "a:noop")
@admin_router.callback_query(F.data == "a:noop")
async def cb_noop(cb: CallbackQuery):
    await _esito(cb, None)


@page_router.callback_query(F.data == "a:allusers")
@admin_router.callback_query(F.data == "a:allusers")
async def cb_allusers(cb: CallbackQuery):
    await _apri_utenti(cb, 0)


@page_router.callback_query(F.data.startswith("a:u:"))
@admin_router.callback_query(F.data.startswith("a:u:"))
async def cb_utenti_pagina(cb: CallbackQuery):
    pagina, corrente = _coppia(cb.data)
    await _apri_utenti(cb, pagina, corrente)


@page_router.callback_query(F.data.startswith("lu"))
@admin_router.callback_query(F.data.startswith("lu"))
async def cb_utenti_vecchio(cb: CallbackQuery):
    pagina, corrente = _coppia(cb.data)
    await _apri_utenti(cb, pagina, corrente)


@page_router.callback_query(F.data == "a:banned")
@admin_router.callback_query(F.data == "a:banned")
async def cb_banned(cb: CallbackQuery):
    await _apri_bannati(cb, 0)


@page_router.callback_query(F.data.startswith("a:n:"))
@admin_router.callback_query(F.data.startswith("a:n:"))
async def cb_bannati_pagina(cb: CallbackQuery):
    pagina, corrente = _coppia(cb.data)
    await _apri_bannati(cb, pagina, corrente)


@page_router.callback_query(F.data.startswith("lp"))
@admin_router.callback_query(F.data.startswith("lp"))
async def cb_bannati_vecchio(cb: CallbackQuery):
    pagina, corrente = _coppia(cb.data)
    await _apri_bannati(cb, pagina, corrente)


@page_router.callback_query(F.data.startswith("lb:"))
@admin_router.callback_query(F.data.startswith("lb:"))
async def cb_lista_ban(cb: CallbackQuery):
    pezzi = (cb.data or "").split(":")
    if len(pezzi) < 4:
        return await cb.answer("Pulsante non valido", show_alert=True)
    azione, uid, page = pezzi[1], int(pezzi[2]), int(pezzi[3])
    await db.ensure_user(uid)
    if azione == "unban":
        await db.unban(uid)
        ok = "Sbloccato"
    else:
        await db.ban(uid, "deciso da admin")
        await db.log(cb.from_user.id, "ban", str(uid))
        ok = "Bannato"
    await _mostra_utenti(cb.message, page, edit=True, cb=cb, ok_testo=ok)


# ==================== RINGRAZIAMENTI MANUALI ====================
@admin_router.callback_query(F.data == "a:man")
async def cb_man(cb: CallbackQuery):
    await _man_utenti(cb, 0)


@admin_router.callback_query(F.data.startswith("a:man:"))
async def cb_man_pagina(cb: CallbackQuery):
    pezzi = (cb.data or "").split(":")
    try:
        pagina = int(pezzi[2])
    except (IndexError, ValueError):
        pagina = 0
    await _man_utenti(cb, pagina)


async def _man_utenti(cb, page):
    try:
        tutti = list(await db.utenti_con_assegnazioni())
        if not tutti:
            await cb.answer()
            return await _edit(cb.message, "Nessuna assegnazione registrata.",
                               kb.nav("a:menu"))
        page, pagine, righe = _taglio(tutti, page, PER_PAGE_MAN)
        bottoni = []
        for r in righe:
            nome = _nome(r)
            if len(nome) > 18:
                nome = nome[:17] + "…"
            u = _pulisci_user(r["username"])
            utente = f"@{u}" if u else "no user"
            bottoni.append([InlineKeyboardButton(
                text=f"{nome} · {utente} · {r['tot']}",
                callback_data=f"a:manu:{r['user_id']}:{page}")])
        sinistra = f"a:man:{page - 1}:{page}" if page > 0 else "a:noop"
        destra = f"a:man:{page + 1}:{page}" if page + 1 < pagine else "a:noop"
        bottoni.append([
            InlineKeyboardButton(text="◀️" if page > 0 else "·", callback_data=sinistra),
            InlineKeyboardButton(text=f"pag. {page + 1}/{pagine}", callback_data="a:noop"),
            InlineKeyboardButton(text="▶️" if page + 1 < pagine else "·",
                                 callback_data=destra),
        ])
        bottoni.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
        bottoni.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
        await cb.answer()
        await _edit(cb.message,
                    f"<b>🖐 Ringraziamenti manuali</b>\n\n"
                    f"Utenti con liste: <b>{len(tutti)}</b> · pagina {page + 1}/{pagine}\n"
                    f"Scegli l'utente, poi la lista, poi lo stato.",
                    InlineKeyboardMarkup(inline_keyboard=bottoni))
    except Exception as e:
        await _avvisa(cb, e)


@admin_router.callback_query(F.data.startswith("a:manu:"))
async def cb_man_utente(cb: CallbackQuery):
    try:
        _, _, uid, page = cb.data.split(":")
        uid = int(uid)
        righe = await db.assegnazioni_utente(uid)
        utente = await db.get_user(uid)
        bottoni = []
        for r in righe:
            segno = _stato_thanks(r)[:2]
            bottoni.append([InlineKeyboardButton(
                text=f"{segno} #{r['id']} · {_ora(r['assigned_at'], '%d/%m %H:%M')} · "
                     f"L#{r['list_id']} {r['kind']}",
                callback_data=f"a:manl:{r['id']}:{uid}")])
        bottoni.append([InlineKeyboardButton(text="⬅️ Indietro",
                                            callback_data=f"a:man:{page}:{page}")])
        bottoni.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
        chi = _nome(utente) if utente else str(uid)
        await cb.answer()
        await _edit(cb.message,
                    f"<b>🖐 {_esc(chi)}</b> · ID <code>{uid}</code>\n\n"
                    f"Liste prese: <b>{len(righe)}</b>. Scegline una per cambiarne lo stato.",
                    InlineKeyboardMarkup(inline_keyboard=bottoni))
    except Exception as e:
        await _avvisa(cb, e)


@admin_router.callback_query(F.data.startswith("a:manl:"))
async def cb_man_lista(cb: CallbackQuery):
    try:
        _, _, aid, uid = cb.data.split(":")
        aid, uid = int(aid), int(uid)
        r = await db.get_assignment(aid)
        if not r:
            return await cb.answer("Assegnazione non trovata", show_alert=True)
        bottoni = [
            [InlineKeyboardButton(text="✅ SEGNA RINGRAZIATO",
                                  callback_data=f"a:manset:{aid}:1:{uid}")],
            [InlineKeyboardButton(text="❌ SEGNA NON RINGRAZIATO",
                                  callback_data=f"a:manset:{aid}:0:{uid}")],
            [InlineKeyboardButton(text="⬅️ Indietro", callback_data=f"a:manu:{uid}:0")],
            [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
        ]
        await cb.answer()
        await _edit(cb.message,
                    f"<b>Assegnazione #{aid}</b>\n\n"
                    f"Lista: L#{r['list_id']}\n"
                    f"Data: {_ora(r['assigned_at'])}\n"
                    f"Stato attuale: {_stato_thanks(r)}\n\n"
                    f"Scegli il nuovo stato.",
                    InlineKeyboardMarkup(inline_keyboard=bottoni))
    except Exception as e:
        await _avvisa(cb, e)


@admin_router.callback_query(F.data.startswith("a:manset:"))
async def cb_man_set(cb: CallbackQuery):
    try:
        _, _, aid, si, uid = cb.data.split(":")
        aid, uid, si = int(aid), int(uid), si == "1"
        await db.set_thanked(aid, si)
        await db.log(cb.from_user.id, "thanks_man", f"assegnazione {aid} -> {'si' if si else 'no'}")
        r = await db.get_assignment(aid)
        bottoni = [
            [InlineKeyboardButton(text="✅ SEGNA RINGRAZIATO",
                                  callback_data=f"a:manset:{aid}:1:{uid}")],
            [InlineKeyboardButton(text="❌ SEGNA NON RINGRAZIATO",
                                  callback_data=f"a:manset:{aid}:0:{uid}")],
            [InlineKeyboardButton(text="⬅️ Indietro", callback_data=f"a:manu:{uid}:0")],
            [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
        ]
        await cb.answer("Ringraziato" if si else "Non ringraziato")
        await _edit(cb.message,
                    f"<b>Assegnazione #{aid}</b>\n\n"
                    f"Lista: L#{r['list_id']}\n"
                    f"Data: {_ora(r['assigned_at'])}\n"
                    f"Stato attuale: {_stato_thanks(r)}",
                    InlineKeyboardMarkup(inline_keyboard=bottoni))
    except Exception as e:
        await _avvisa(cb, e)


# ==================== VERIFICA ANONIMI ====================
async def _ask_verify(target, bot, edit=False):
    me = await bot.me()
    tok = kb.new_token(target.chat.id)
    url = f"https://t.me/{me.username}?start=verif_{tok}"
    kbd = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Verifica ora", url=url)],
        [InlineKeyboardButton(text="🔄 Ho verificato", callback_data="v:check")],
    ])
    testo = ("Stai scrivendo in <b>anonimo</b>, quindi non so chi sei.\n\n"
             "Premi «Verifica ora»: si apre la chat privata, premi START e la verifica "
             "si completa da sola. Vale in modo permanente finché resti admin.")
    if edit:
        await _edit(target, testo, kbd)
    else:
        await target.answer(testo, reply_markup=kbd)


@anon_router.callback_query(F.data == "v:check")
async def cb_check(cb: CallbackQuery):
    if await kb.anon_is_admin(cb.message.chat.id):
        await cb.answer("Verificato. Ripeti il comando.", show_alert=True)
    else:
        await cb.answer("Non ancora verificato. Premi «Verifica ora».", show_alert=True)


@anon_router.message(AnonUnverified(), F.text.startswith("/"))
async def anon_cmd(msg: Message, bot: Bot):
    await _ask_verify(msg, bot)


@anon_router.callback_query(AnonUnverified())
async def anon_cb(cb: CallbackQuery, bot: Bot):
    await cb.answer("Prima devi verificarti", show_alert=True)
    await _ask_verify(cb.message, bot, edit=True)


# ==================== UTENTE ====================
async def _welcome(msg):
    days = await db.get_int("cooldown_days")
    txt = _fmt(await db.get_setting("welcome"),
               name=msg.from_user.first_name or "utente", days=days)
    await msg.answer(txt, reply_markup=kb.user_kb(days))


@user_router.message(Command("ping"))
async def ping(msg: Message):
    await msg.answer(f"pong\nchat_id: <code>{msg.chat.id}</code>\ntipo chat: {msg.chat.type}")


@user_router.message(CommandStart(deep_link=True))
async def start_verify(msg: Message, command: CommandObject, bot: Bot):
    args = command.args or ""
    if not args.startswith("verif_"):
        return await start(msg, bot)
    chat_id = kb.pop_token(args[6:])
    if not chat_id:
        return await msg.answer("Link scaduto o non valido. Torna nel gruppo e riprova.")
    await db.touch_user(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    if not await db.is_admin(msg.from_user.id, msg.from_user.username):
        return await msg.answer("Sei un utente Telegram valido, ma non risulti tra gli "
                                "amministratori del bot.")
    await db.set_anon_verified(chat_id, msg.from_user.id)
    if await db.get_group_id() == 0:
        await db.set_setting("group_id", str(chat_id))
    nome = msg.from_user.first_name or "un admin"
    try:
        await bot.send_message(chat_id,
                               f"✅ <a href=\"tg://user?id={msg.from_user.id}\">{nome}</a> "
                               f"è verificato come amministratore.\n"
                               f"Puoi ripetere il comando. Resta valido finché sei admin.")
    except Exception:
        pass
    await db.log(msg.from_user.id, "anon_verify", str(chat_id))
    await msg.answer("Verifica completata e permanente.\nTorna nel gruppo e ripeti il comando.")


@user_router.message(Command("start"))
async def start(msg: Message, bot: Bot):
    await db.touch_user(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    if await kb.is_admin_event(msg):
        return await msg.answer(_quadro(await db.stats()), reply_markup=kb.admin_menu())
    if not await kb.member_ok(bot, msg.from_user.id):
        return await msg.answer(await kb.not_member_text(), reply_markup=await kb.join_kb())
    await _welcome(msg)


@user_router.message(Command("mio"))
async def mine(msg: Message):
    a = await db.last_assignment(msg.from_user.id)
    if not a:
        return await msg.answer("Non hai liste attive. Scrivi /lista per riceverne una.")
    left = a["expires_at"] - dt.datetime.now(dt.timezone.utc)
    if left.total_seconds() <= 0:
        return await msg.answer(f"La lista #{a['list_id']} è scaduta. Scrivi /lista per una nuova.")
    resto = ""
    if a["thanks_deadline"]:
        if a["thanked_at"]:
            resto = f"\nRingraziamento: ✅ registrato ({_ora(a['thanked_at'], '%H:%M')})"
        else:
            manca = a["thanks_deadline"] - dt.datetime.now(dt.timezone.utc)
            resto = (f"\nRingraziamento: ⏳ mancano {max(0, int(manca.total_seconds() // 60))} minuti"
                     if manca.total_seconds() > 0 else "\nRingraziamento: ❌ non registrato")
    await msg.answer(f"Lista #{a['list_id']} ({a['kind']}), scade "
                     f"{_ora(a['expires_at'])} (tra {_human(left)}){resto}")


@user_router.callback_query(F.data == "u:get")
async def cb_get(cb: CallbackQuery, bot: Bot):
    await cb.answer("Preparo la lista...")
    dorme = cb.message.chat.type != "private" and await orario.attiva()
    if dorme:
        await _in_silenzio(cb.message)
    await deliver(cb.message, cb.from_user, bot, await kb.is_admin_cb(cb),
                  dove=cb.from_user.id if dorme else None)


@user_router.message(Ringrazia())
async def thanks(msg: Message, bot: Bot):
    row = await db.pending_thanks(msg.from_user.id)
    if not row:
        return
    aid = await db.mark_thanked(msg.from_user.id)
    if not aid:
        return
    await db.log(msg.from_user.id, "thanks", f"lista {row['list_id']}")
    nome = msg.from_user.first_name or "utente"
    testo = _fmt(await db.get_setting("ph_ok"), name=_esc(nome), bot=await _bot_tag(bot))

    # In fascia silenzio il messaggio nel gruppo viene cancellato dal bot di
    # sicurezza: la conferma va in privato, il ringraziamento resta registrato.
    if await orario.attiva():
        try:
            await bot.send_message(msg.from_user.id, testo, link_preview_options=NO_PREVIEW)
            return
        except Exception:
            pass
    try:
        await msg.reply(testo, link_preview_options=NO_PREVIEW)
    except Exception:
        pass


@user_router.message(Trigger(), Command("lista"))
async def cmd_lista(msg: Message, bot: Bot):
    dorme = msg.chat.type != "private" and await orario.attiva()
    if dorme:
        await _in_silenzio(msg)
    await deliver(msg, msg.from_user, bot, await kb.is_admin_event(msg),
                  dove=msg.from_user.id if dorme else None)


@user_router.message(Trigger(), F.text)
async def mention_any(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        return await deliver(msg, msg.from_user, bot, await kb.is_admin_event(msg))

    testo = msg.text or ""
    ha_attesa = bool(await db.pending_thanks(msg.from_user.id))
    esito = await classificatore.classifica(msg, bot, testo, ha_attesa)
    dorme = await orario.attiva()

    if esito == "ringraziamento":
        # Se non c'è attesa il messaggio non è arrivato al gestore dei
        # ringraziamenti: rispondo solo se non dorme, senza barzelletta.
        if not ha_attesa and not dorme:
            await _di_nulla(msg)
        return

    if esito == "saluto" and not dorme:
        me = await bot.me()
        try:
            await msg.reply(
                "👋 Ciao! Per la lista scrivimi in privato qui sotto.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✍️ Scrivimi in privato",
                                         url=f"https://t.me/{me.username}")]]),
                link_preview_options=NO_PREVIEW)
        except Exception:
            pass
        return

    # richiesta o messaggio ambiguo: la barzelletta ha senso solo qui
    if dorme:
        await _in_silenzio(msg)
        return await deliver(msg, msg.from_user, bot, False, dove=msg.from_user.id)

    if await _risposta_tag(msg):
        return
    await deliver(msg, msg.from_user, bot, False)


async def _controlla_ringraziamento(bot, uid, aid, minuti, nome, username):
    try:
        await asyncio.sleep(max(1, minuti) * 60)
        row = await db.get_assignment(aid)
        if not row or row["thanked_at"] or row["revoked"]:
            return
        await db.log(uid, "thanks_miss", f"lista {row['list_id']}")
        await _avvisa_punizione(bot, uid, row, minuti, nome, username)
        frase = _fmt(await db.get_setting("ph_ko"), name=_esc(nome), bot=await _bot_tag(bot))
        try:
            await bot.send_message(uid, frase, link_preview_options=NO_PREVIEW)
        except Exception:
            pass
    except Exception as e:
        print(f"controlla_ringraziamento: {type(e).__name__}: {e}")


async def _avvisa_punizione(bot, uid, row, minuti, nome, username):
    """Nessun comando a bot esterni: avviso nel gruppo + privato a tutti gli admin."""
    punizione = (await db.get_setting("punizione") or "ammonizione").strip() or "ammonizione"
    u = _pulisci_user(username)
    menzione = f"@{u}" if u else f"<a href=\"tg://user?id={uid}\">{_esc(nome)}</a>"
    valori = {"user": menzione, "name": _esc(nome), "id": uid, "minuti": minuti,
              "punizione": _esc(punizione), "lista": row["list_id"],
              "quando": _ora(dt.datetime.now(dt.timezone.utc))}

    gid = await db.get_group_id()
    if gid:
        try:
            await bot.send_message(gid, _fmt(await db.get_setting("pun_gruppo"), **valori),
                                   link_preview_options=NO_PREVIEW)
        except Exception as e:
            print(f"avviso gruppo: {type(e).__name__}: {e}")

    destinatari = set(config.SUPER_ADMINS)
    for r in await db.all_admins():
        destinatari.add(r["user_id"])
    destinatari.discard(uid)

    testo = _fmt(await db.get_setting("pun_admin"), **valori)
    inviati = 0
    for admin_id in destinatari:
        try:
            await bot.send_message(admin_id, testo, link_preview_options=NO_PREVIEW)
            inviati += 1
        except Exception as e:
            print(f"avviso admin {admin_id}: {type(e).__name__}: {e}")
    await db.log(uid, "pun_notify", f"lista {row['list_id']} · {inviati} admin")

    if not inviati and gid:
        try:
            await bot.send_message(gid, "ℹ️ Nessun admin raggiungibile in privato: "
                                        "provvedete voi.", link_preview_options=NO_PREVIEW)
        except Exception:
            pass


async def deliver(msg, user, bot, adm=False, dove=None):
    uid = user.id

    async def _say(testo, kbd=None):
        if dove:
            return await bot.send_message(dove, testo, reply_markup=kbd,
                                          link_preview_options=NO_PREVIEW)
        return await msg.answer(testo, reply_markup=kbd, link_preview_options=NO_PREVIEW)

    async def _foto(path, caption):
        if dove:
            return await bot.send_photo(dove, FSInputFile(path), caption=caption,
                                        link_preview_options=NO_PREVIEW)
        return await msg.answer_photo(FSInputFile(path), caption=caption,
                                      link_preview_options=NO_PREVIEW)

    if await db.get_int("enabled") == 0:
        return await _say("Bot momentaneamente spento. Riprova più tardi.")
    await db.touch_user(uid, user.username, user.full_name)
    u = await db.get_user(uid)
    if u and u["banned"]:
        return await _say(f"Sei bannato. Motivo: {u['ban_reason'] or 'non indicato'}")
    if not adm and not await kb.member_ok(bot, uid):
        return await _say(await kb.not_member_text(), await kb.join_kb())
    if uid in BUSY:
        return await _say("Sto già preparando la tua lista, attendi.")
    BUSY.add(uid)
    try:
        u = await db.get_user(uid)
        if u and u["banned"]:
            return await _say(f"Sei bannato. Motivo: {u['ban_reason'] or 'non indicato'}")

        days = await db.get_int("cooldown_days")
        if not adm and u and u["next_available"]:
            nxt = u["next_available"]
            if dt.datetime.now(dt.timezone.utc) < nxt:
                left = nxt - dt.datetime.now(dt.timezone.utc)
                txt = _fmt(await db.get_setting("deny_text"),
                           when=_ora(nxt, "%d/%m/%Y alle %H:%M"), left=_human(left),
                           name=_esc(user.first_name or "utente"))
                return await _say(txt)

        if await db.free_count() == 0:
            return await _say(
                _fmt(await db.get_setting("no_lists"), name=_esc(user.first_name or "utente")))

        chosen = payload = shot = None
        for _ in range(await db.get_int("max_attempts")):
            row = await db.random_free_ok() or await db.random_free()
            if not row:
                break
            p = await db.payload_of(row)
            res = await test_list(row["kind"], p)
            await db.set_test(row["id"], res.ok, res.note)
            if res.ok:
                chosen, payload, shot = row, p, res.frame
                break

        if not chosen:
            return await _say(
                _fmt(await db.get_setting("broken_all"), name=_esc(user.first_name or "utente")))

        minuti = await db.get_int("thanks_min") or 10
        adesso = dt.datetime.now(dt.timezone.utc)
        exp = adesso + dt.timedelta(days=days)
        deadline = None if adm else adesso + dt.timedelta(minutes=minuti)
        aid = await db.assign(chosen["id"], uid, exp, deadline)
        if not adm:
            await db.set_next(uid, exp)
        await db.log(uid, "consegna", f"lista {chosen['id']}")

        nome = user.first_name or "utente"
        bot_tag = await _bot_tag(bot)
        frase = _fmt(await db.get_setting("ph_consegna"),
                     name=_esc(nome), days=days, bot=bot_tag)
        caption = (f"<b>La tua lista IPTV</b> (#{chosen['id']})\n\n"
                   f"{_payload_text(chosen['kind'], payload)}\n\n"
                   f"Prossima lista: <b>{_ora(exp)}</b>\n\n"
                   f"{frase}")
        if shot and await db.get_int("send_proof") == 1:
            await _foto(shot, caption)
            shot.unlink(missing_ok=True)
        else:
            await _say(caption)

        if deadline:
            avviso = _fmt(await db.get_setting("ph_richiesta"),
                          name=_esc(nome), minuti=minuti, bot=bot_tag,
                          group=(await db.get_setting("group_link") or ""))
            await _say(avviso, await kb.join_kb())
            asyncio.create_task(
                _controlla_ringraziamento(bot, uid, aid, minuti, nome, user.username))
    finally:
        BUSY.discard(uid)


# ==================== PANNELLO ====================
@admin_router.message(Command("admin"))
async def panel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(_quadro(await db.stats()), reply_markup=kb.admin_menu())


@admin_router.message(Command("stato"))
async def stato_cmd(msg: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await msg.answer(await _stato_text(msg, bot), reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:stato")
async def cb_stato(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await _edit(cb.message, await _stato_text(cb.message, bot), kb.admin_menu())


@admin_router.message(Command("comandi"))
async def comandi_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(_comandi_text(), reply_markup=kb.nav("a:menu"))


@admin_router.message(Command("verifica"))
async def verifica_cmd(msg: Message, bot: Bot):
    await _ask_verify(msg, bot)


@admin_router.message(Command("deverifica"))
async def deverifica_cmd(msg: Message):
    await db.clear_anon_verified(msg.chat.id)
    await msg.answer("Verifica revocata per questa chat.", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:verify")
async def cb_verify(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await _ask_verify(cb.message, bot, edit=True)


@admin_router.callback_query(F.data == "a:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    _pulisci(cb.from_user.id)
    await state.clear()
    await cb.answer()
    await _edit(cb.message, _quadro(await db.stats()), kb.admin_menu())


@admin_router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    await cb.answer()
    await _edit(cb.message, await _statistiche_text(), kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:comandi")
async def cb_comandi(cb: CallbackQuery):
    await cb.answer()
    await _edit(cb.message, _comandi_text(), kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:reload")
async def cb_reload(cb: CallbackQuery, bot: Bot):
    await cb.answer("Ricarico")
    from bot import avvisa_admin
    await avvisa_admin(bot, solo=cb.from_user.id)
    await _edit(cb.message, "Bot ricaricato.\n\nPannello:", kb.admin_menu())


@admin_router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="utenti")
    await _edit(cb.message, "Chi vuoi cercare? Mandami ID o @username." + HINT, kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:revoca")
async def cb_revoca(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="revoca")
    await _edit(cb.message, "Quale assegnazione vuoi revocare? Mandami il numero "
                            "(lo trovi in Assegnazioni)." + HINT, kb.nav("a:hist"))


@admin_router.callback_query(F.data == "a:reset")
async def cb_reset(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="reset")
    await _edit(cb.message, "A chi vuoi azzerare l'attesa? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT, kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:admin_add")
async def cb_admin_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_add")
    await _edit(cb.message, "Chi vuoi aggiungere come amministratore?\n\n"
                            "Mandami lo @username: funziona anche se non ha mai scritto al bot.\n"
                            "In alternativa l'ID numerico o un messaggio inoltrato." + HINT,
                kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:admin_del")
async def cb_admin_del(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_del")
    await _edit(cb.message, "Chi vuoi rimuovere dagli amministratori? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT, kb.nav("a:menu"))


@admin_router.callback_query(F.data == "a:toggle")
async def cb_toggle(cb: CallbackQuery):
    await cb.answer()
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await _edit(cb.message, f"Bot {'SPENTO' if cur else 'ATTIVO'}.", kb.admin_menu())


# ==================== FASCIA SILENZIO ====================
@admin_router.message(Command("orario"))
async def orario_cmd(msg: Message, state: FSMContext):
    await state.clear()
    acceso, da, a = await orario.impostazioni()
    await msg.answer(await orario.testo(), reply_markup=orario.kb(acceso, da, a))


@admin_router.callback_query(F.data == "a:orario")
async def cb_orario(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    acceso, da, a = await orario.impostazioni()
    await _edit(cb.message, await orario.testo(), orario.kb(acceso, da, a))


@admin_router.callback_query(F.data == "o:toggle")
async def cb_orario_toggle(cb: CallbackQuery):
    cur = await db.get_int("silenzio_on")
    await db.set_setting("silenzio_on", "0" if cur else "1")
    await db.log(cb.from_user.id, "silenzio", "off" if cur else "on")
    await cb.answer("Fascia spenta" if cur else "Fascia attivata")
    acceso, da, a = await orario.impostazioni()
    await _edit(cb.message, await orario.testo(), orario.kb(acceso, da, a))


@admin_router.callback_query(F.data == "o:da")
async def cb_orario_da(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    _, da, _a = await orario.impostazioni()
    await state.set_state(SetTxt.sil_da)
    await _edit(cb.message,
                f"Inizio della fascia. Attuale: <b>{da}</b>\n\n"
                f"Mandami l'ora nel formato <code>00:00</code> (ora di Roma)." + HINT,
                kb.nav("a:orario"))


@admin_router.callback_query(F.data == "o:a")
async def cb_orario_a(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    _, _da, a = await orario.impostazioni()
    await state.set_state(SetTxt.sil_a)
    await _edit(cb.message,
                f"Fine della fascia. Attuale: <b>{a}</b>\n\n"
                f"Mandami l'ora nel formato <code>07:00</code> (ora di Roma)." + HINT,
                kb.nav("a:orario"))


async def _salva_orario(msg, state, chiave, altra):
    valore = orario.normalizza(msg.text or "")
    if not valore:
        return await msg.answer("Orario non valido. Formato: 00:00", reply_markup=kb.nav("a:orario"))
    await db.set_setting(chiave, valore)
    await state.clear()
    acceso, da, a = await orario.impostazioni()
    avviso = orario._valida(da, a)
    testo = await orario.testo()
    if avviso:
        testo = f"⚠️ {avviso}\n\n{testo}"
    await msg.answer(testo, reply_markup=orario.kb(acceso, da, a))


@admin_router.message(SetTxt.sil_da)
async def set_sil_da(msg: Message, state: FSMContext):
    await _salva_orario(msg, state, "silenzio_da", "silenzio_a")


@admin_router.message(SetTxt.sil_a)
async def set_sil_a(msg: Message, state: FSMContext):
    await _salva_orario(msg, state, "silenzio_a", "silenzio_da")


# ==================== FRASI PER L'UTENTE ====================
FRASI = {
    "wel": ("👋 Benvenuto", SetTxt.welcome, "welcome",
            "{name} {days}",
            "nome dell'utente", "giorni di attesa"),
    "cons": ("🎬 Consegna lista", SetTxt.consegna, "ph_consegna",
             "{name} {days} {bot}",
             "nome dell'utente", "giorni", "@ del bot"),
    "rich": ("⏱ Richiesta ringraziamento", SetTxt.richiesta, "ph_richiesta",
             "{name} {minuti} {bot} {group}",
             "nome", "minuti a disposizione", "@ del bot", "link del gruppo"),
    "ok": ("🙏 Ringraziamento ricevuto", SetTxt.ok, "ph_ok",
           "{name} {bot}",
           "nome dell'utente", "@ del bot"),
    "ko": ("❌ Ringraziamento mancato", SetTxt.ko, "ph_ko",
           "{name} {bot}",
           "nome dell'utente", "@ del bot"),
    "deny": ("⏳ Rifiuto per attesa", SetTxt.deny, "deny_text",
             "{name} {when} {left}",
             "nome", "data e ora della prossima lista", "tempo che manca"),
    "blk": ("⛔ Blocco non membro", SetTxt.not_member, "not_member",
            "{link}",
            "link d'invito al gruppo"),
    "nolist": ("📭 Nessuna lista disponibile", SetTxt.nolist, "no_lists",
               "{name}",
               "nome dell'utente"),
    "broken": ("🛠 Liste tutte rotte", SetTxt.broken, "broken_all",
               "{name}",
               "nome dell'utente"),
}


async def _frasi_kb():
    righe = [[InlineKeyboardButton(text=etichetta, callback_data=f"f:{chiave}")]
             for chiave, (etichetta, _s, _k, _p, *_r) in FRASI.items()]
    righe.append([InlineKeyboardButton(text="⏱ Minuti per ringraziare",
                                       callback_data="f:min")])
    righe.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:set")])
    righe.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    return InlineKeyboardMarkup(inline_keyboard=righe)


async def _frasi_text():
    righe = ["<b>✏️ Frasi per l'utente</b>", "",
             "Questi sono i testi che il bot manda agli utenti. Tocca una frase per "
             "riscriverla.", "",
             "<b>Che cosa sono i segnaposto</b>",
             "I segnaposto sono i pezzi tra graffe, per esempio <code>{name}</code>. "
             "Il bot li sostituisce da solo al momento dell'invio: tu scrivi "
             "<code>{name}</code> e l'utente legge il suo nome. Servono perché la stessa "
             "frase va bene per tutti. <b>Non vanno tradotti né cancellati</b>: se li "
             "togli, quell'informazione sparisce dalla frase.", "",
             "<b>Legenda</b>",
             "<code>{name}</code> → nome dell'utente",
             "<code>{bot}</code> → @ del bot",
             "<code>{days}</code> → giorni di attesa",
             "<code>{minuti}</code> → minuti per ringraziare",
             "<code>{group}</code> → link del gruppo",
             "<code>{when}</code> → data e ora della prossima lista",
             "<code>{left}</code> → tempo che manca",
             "<code>{link}</code> → link d'invito al gruppo", "",
             "Ogni frase accetta solo i suoi segnaposto: quelli elencati quando la apri. "
             "Una graffa scritta male o un segnaposto inesistente non blocca l'invio: "
             "resta stampato così com'è e te ne accorgi subito.", "",
             "Il messaggio di punizione per il ringraziamento mancante non è qui: si "
             "imposta da Impostazioni → ⚖️ Punizione mancato ringraziamento.", ""]
    for chiave, (etichetta, _s, _k, segnaposto, *_r) in FRASI.items():
        righe.append(f"{etichetta} → <code>{segnaposto}</code>")
    return "\n".join(righe)


@admin_router.callback_query(F.data == "a:frasi")
async def cb_frasi(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _edit(cb.message, await _frasi_text(), await _frasi_kb())


@admin_router.callback_query(F.data.startswith("f:"))
async def cb_frase(cb: CallbackQuery, state: FSMContext):
    chiave = cb.data.split(":")[1]
    if chiave == "min":
        await cb.answer()
        attuale = await db.get_int("thanks_min")
        await state.set_state(SetTxt.minuti)
        await _edit(cb.message,
                    f"Minuti a disposizione per ringraziare: <b>{attuale}</b>.\n\n"
                    f"Mandami il nuovo numero." + HINT, kb.nav("a:frasi"))
        return
    voce = FRASI.get(chiave)
    if not voce:
        return await cb.answer("Frase non valida", show_alert=True)
    etichetta, stato, setting, segnaposto, *significati = voce
    legenda = "\n".join(f"<code>{s}</code> → {m}"
                        for s, m in zip(segnaposto.split(), significati))
    await cb.answer()
    attuale = _esc(await db.get_setting(setting))
    await state.set_state(stato)
    await _edit(cb.message,
                f"<b>{etichetta}</b>\n\n<b>Attuale</b>\n{attuale}\n\n"
                f"<b>Segnaposto che puoi usare</b>\n{legenda}\n\n"
                f"Sono pezzi che il bot riempie da solo: lasciali tra graffe, "
                f"spostali o togli quelli che non ti servono.\n"
                f"Mandami il nuovo testo." + HINT, kb.nav("a:frasi"))


@admin_router.message(StateFilter(SetTxt.welcome, SetTxt.consegna, SetTxt.richiesta,
                                  SetTxt.ok, SetTxt.ko, SetTxt.deny, SetTxt.not_member,
                                  SetTxt.nolist, SetTxt.broken))
async def set_frase(msg: Message, state: FSMContext):
    stato = await state.get_state()
    setting = None
    for _chiave, (_e, s, k, *_r) in FRASI.items():
        if s.state == stato:
            setting = k
            break
    if not setting:
        await state.clear()
        return await msg.answer("Frase non riconosciuta, riapri le impostazioni.",
                                reply_markup=kb.admin_menu())
    if not (msg.text or "").strip():
        return await msg.answer("Serve un testo.", reply_markup=kb.nav("a:frasi"))
    await db.set_setting(setting, msg.html_text)
    await state.clear()
    await msg.answer("Frase aggiornata.", reply_markup=await _frasi_kb())


@admin_router.message(SetTxt.minuti)
async def set_minuti(msg: Message, state: FSMContext):
    n = (msg.text or "").strip()
    if not n.isdigit() or int(n) < 1:
        return await msg.answer("Serve un numero di minuti maggiore di zero.",
                                reply_markup=kb.nav("a:frasi"))
    await db.set_setting("thanks_min", n)
    await state.clear()
    await msg.answer(f"Tempo di ringraziamento impostato a {n} minuti.",
                     reply_markup=await _frasi_kb())


# ==================== PUNIZIONE MANCATO RINGRAZIAMENTO ====================
PUN_HINT = ("\n\nSegnaposto: <code>{user}</code> menzione dell'utente, <code>{name}</code> "
            "nome, <code>{id}</code> ID, <code>{minuti}</code> minuti, "
            "<code>{punizione}</code> punizione, <code>{lista}</code> numero lista, "
            "<code>{quando}</code> data e ora.")


async def _pun_text():
    punizione = _esc(await db.get_setting("punizione"))
    gruppo = await db.get_setting("pun_gruppo")
    admin = await db.get_setting("pun_admin")
    destinatari = set(config.SUPER_ADMINS)
    for r in await db.all_admins():
        destinatari.add(r["user_id"])
    return ("<b>⚖️ Punizione per mancato ringraziamento</b>\n\n"
            "Allo scadere dei minuti il bot non lancia comandi a bot esterni: avvisa nel "
            "gruppo l'utente e manda un privato a tutti gli admin e superadmin, che "
            "applicano la punizione a mano.\n\n"
            f"<b>Punizione impostata</b>\n{punizione}\n\n"
            f"<b>Messaggio nel gruppo</b>\n{gruppo}\n\n"
            f"<b>Messaggio agli admin</b>\n{admin}\n\n"
            f"Destinatari del privato: <b>{len(destinatari)}</b> admin e superadmin.")


async def _pun_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Cambia punizione", callback_data="pun:azione")],
        [InlineKeyboardButton(text="✏️ Messaggio nel gruppo", callback_data="pun:gruppo")],
        [InlineKeyboardButton(text="✏️ Messaggio agli admin", callback_data="pun:admin")],
        [InlineKeyboardButton(text="🧪 Prova messaggio admin", callback_data="pun:prova")],
        [InlineKeyboardButton(text="♻️ Ripristina predefiniti", callback_data="pun:reset")],
        [InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:set")],
        [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
    ])


@admin_router.callback_query(F.data == "a:pun")
async def cb_pun(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _edit(cb.message, await _pun_text(), await _pun_kb())


@admin_router.callback_query(F.data.startswith("pun:"))
async def cb_pun_act(cb: CallbackQuery, state: FSMContext):
    cosa = cb.data.split(":")[1]
    if cosa == "azione":
        await cb.answer()
        attuale = _esc(await db.get_setting("punizione"))
        await state.set_state(SetTxt.punizione)
        return await _edit(cb.message,
                           "Che punizione si applica a chi non ringrazia?\n\n"
                           f"Attuale: <b>{attuale}</b>\n\n"
                           "Testo libero e breve: ammonizione, muto 24 ore, ban, "
                           "richiamo ufficiale... entra in entrambi i messaggi." + HINT,
                           kb.nav("a:pun"))
    if cosa == "gruppo":
        await cb.answer()
        await state.set_state(SetTxt.pun_gruppo)
        return await _edit(cb.message,
                           "Messaggio mandato <b>nel gruppo</b>, taggando l'utente." + PUN_HINT
                           + HINT, kb.nav("a:pun"))
    if cosa == "admin":
        await cb.answer()
        await state.set_state(SetTxt.pun_admin)
        return await _edit(cb.message,
                           "Messaggio mandato <b>in privato agli admin</b>." + PUN_HINT
                           + HINT, kb.nav("a:pun"))
    if cosa == "prova":
        minuti = await db.get_int("thanks_min")
        valori = {"user": "@prova", "name": "Mario Rossi", "id": 123456789,
                  "minuti": minuti, "punizione": _esc(await db.get_setting("punizione")),
                  "lista": 42, "quando": _ora(dt.datetime.now(dt.timezone.utc))}
        testo = _fmt(await db.get_setting("pun_admin"), **valori)
        try:
            await cb.message.bot.send_message(cb.from_user.id, testo,
                                              link_preview_options=NO_PREVIEW)
            await cb.answer("Prova inviata in privato")
        except Exception as e:
            await cb.answer(f"Invio non riuscito: {type(e).__name__}", show_alert=True)
        return
    if cosa == "reset":
        for chiave in ("punizione", "pun_gruppo", "pun_admin"):
            await db.set_setting(chiave, db.DEFAULTS[chiave])
        await cb.answer("Predefiniti ripristinati")
        return await _edit(cb.message, await _pun_text(), await _pun_kb())
    await cb.answer("Azione sconosciuta", show_alert=True)


@admin_router.message(SetTxt.punizione)
async def set_punizione(msg: Message, state: FSMContext):
    t = (msg.text or "").strip()
    if not t:
        return await msg.answer("Serve un testo.", reply_markup=kb.nav("a:pun"))
    if len(t) > 80:
        return await msg.answer("Tienilo sotto gli 80 caratteri: va dentro due frasi.",
                                reply_markup=kb.nav("a:pun"))
    await db.set_setting("punizione", t)
    await state.clear()
    await msg.answer(f"Punizione impostata: <b>{_esc(t)}</b>", reply_markup=await _pun_kb())


@admin_router.message(SetTxt.pun_gruppo)
async def set_pun_gruppo(msg: Message, state: FSMContext):
    t = (msg.text or "").strip()
    if not t:
        return await msg.answer("Serve un testo.", reply_markup=kb.nav("a:pun"))
    await db.set_setting("pun_gruppo", msg.html_text)
    await state.clear()
    await msg.answer("Messaggio per il gruppo aggiornato.", reply_markup=await _pun_kb())


@admin_router.message(SetTxt.pun_admin)
async def set_pun_admin(msg: Message, state: FSMContext):
    t = (msg.text or "").strip()
    if not t:
        return await msg.answer("Serve un testo.", reply_markup=kb.nav("a:pun"))
    await db.set_setting("pun_admin", msg.html_text)
    await state.clear()
    await msg.answer("Messaggio per gli admin aggiornato.", reply_markup=await _pun_kb())


# ==================== BARZELLETTE (impostazioni) ====================
async def _joke_text():
    acceso = await db.get_int("joke_on")
    api = _esc(await db.get_setting("joke_api"))
    testo = _esc(await db.get_setting("joke_testo"))
    return ("<b>🤡 Barzellette sul tag</b>\n\n"
            "Quando il bot viene taggato nel gruppo per <b>chiedere una lista</b> risponde "
            "con una battuta e poi con la frase finale.\n"
            "Quando invece lo si tagga per ringraziare, non parte nessuna barzelletta: "
            "lo decide il classificatore.\n\n"
            f"Stato: <b>{'ATTIVE' if acceso else 'SPENTE'}</b>\n"
            f"Riserva interna: <b>{len(BARZELLETTE)}</b> battute in italiano\n\n"
            f"<b>Frase finale</b>\n{testo}\n\n"
            f"<b>Servizio esterno</b>\n{api or 'nessuno (solo riserva interna)'}\n\n"
            "Servizio predefinito: archivio Battute.json su GitHub, in italiano. "
            "Se non risponde, il bot usa la riserva interna.")


async def _joke_kb():
    acceso = await db.get_int("joke_on")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("⛔ Spegni barzellette" if acceso else "✅ Accendi barzellette"),
                              callback_data="j:toggle")],
        [InlineKeyboardButton(text="✏️ Frase finale", callback_data="j:testo")],
        [InlineKeyboardButton(text="🌐 Servizio esterno", callback_data="j:api")],
        [InlineKeyboardButton(text="🎲 Prova ora", callback_data="j:prova")],
        [InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:set")],
        [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
    ])


@admin_router.callback_query(F.data == "a:joke")
async def cb_joke(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _edit(cb.message, await _joke_text(), await _joke_kb())


@admin_router.callback_query(F.data.startswith("j:"))
async def cb_joke_act(cb: CallbackQuery, state: FSMContext):
    cosa = cb.data.split(":")[1]
    if cosa == "toggle":
        await cb.answer("Aggiornato")
        cur = await db.get_int("joke_on")
        await db.set_setting("joke_on", "0" if cur else "1")
        return await _edit(cb.message, await _joke_text(), await _joke_kb())
    if cosa == "prova":
        await cb.answer("Eccola")
        battuta = await _barzelletta()
        coda = _fmt(await db.get_setting("joke_testo"), name=_esc(cb.from_user.first_name or "bello"))
        return await _edit(cb.message, f"{battuta}\n\n{coda}\n\n—————\n{await _joke_text()}",
                           await _joke_kb())
    if cosa == "testo":
        await cb.answer()
        await state.set_state(SetTxt.joke_testo)
        return await _edit(cb.message,
                           "Mandami la frase finale della barzelletta.\n\n"
                           "Segnaposto: <code>{name}</code> (nome di chi ha taggato)." + HINT,
                           kb.nav("a:joke"))
    if cosa == "api":
        await cb.answer()
        await state.set_state(SetTxt.joke_api)
        return await _edit(cb.message,
                           "Mandami l'indirizzo di un servizio di battute <b>in italiano</b>, "
                           "oppure <code>-</code> per usare solo la riserva interna.\n\n"
                           "Formati accettati: campo <code>joke</code>, campo <code>jokes</code> "
                           "con elenco, o elenco diretto. Attuale predefinito: archivio "
                           "Battute.json." + HINT, kb.nav("a:joke"))
    await cb.answer("Azione sconosciuta", show_alert=True)


@admin_router.message(SetTxt.joke_testo)
async def set_joke_testo(msg: Message, state: FSMContext):
    t = (msg.text or "").strip()
    if not t:
        return await msg.answer("Serve un testo.", reply_markup=kb.nav("a:joke"))
    await db.set_setting("joke_testo", msg.html_text)
    await state.clear()
    await msg.answer("Frase finale aggiornata.", reply_markup=await _joke_kb())


@admin_router.message(SetTxt.joke_api)
async def set_joke_api(msg: Message, state: FSMContext):
    t = (msg.text or "").strip()
    if t and t != "-" and not t.startswith("http"):
        return await msg.answer("Serve un indirizzo che inizi con http, oppure <code>-</code>.",
                                reply_markup=kb.nav("a:joke"))
    await db.set_setting("joke_api", "" if t == "-" else t)
    await state.clear()
    await msg.answer("Servizio aggiornato." if t != "-" else "Uso solo la riserva interna.",
                     reply_markup=await _joke_kb())


# ==================== COMANDI ELENCO ====================
@admin_router.message(Command("elenco"))
async def elenco_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    pagina = int(p[1]) - 1 if len(p) > 1 and p[1].strip().isdigit() else 0
    await _mostra_utenti(msg, max(0, pagina), edit=False)


@admin_router.message(Command("bannati"))
async def bannati_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await _mostra_bannati(msg, 0, edit=False)


# ==================== LOG ATTIVITA ====================
AZIONI = {
    "add": "lista aggiunta",
    "bulk_add": "import massivo",
    "del_lists": "liste eliminate",
    "consegna": "lista consegnata",
    "thanks": "ringraziamento ricevuto",
    "thanks_miss": "ringraziamento mancante",
    "thanks_man": "ringraziamento forzato",
    "pun_notify": "avviso punizione inviato",
    "revoca": "assegnazione revocata",
    "ban": "utente bannato",
    "admin_add": "admin aggiunto",
    "admin_add_name": "admin aggiunto per username",
    "admin_del": "admin rimosso",
    "admin_del_name": "admin rimosso per username",
    "anon_verify": "verifica admin anonimo",
    "silenzio": "fascia silenzio cambiata",
}


def _log_txt(righe):
    out = ["LOG ATTIVITA - LISTE PIAZZA ANDROID ITA", ""]
    for r in reversed(righe):
        quando = _ora(r["ts"])
        chi = f"@{r['username']}" if r["username"] else (r["full_name"] or str(r["actor"]))
        cosa = AZIONI.get(r["action"], r["action"])
        det = f" · {r['detail']}" if r["detail"] else ""
        out.append(f"{quando} · {chi} · {cosa}{det}")
    out.append("")
    out.append(f"Totale righe: {len(righe)}")
    return "\n".join(out)


async def _manda_log(target):
    righe = await db.all_logs()
    path = Path("/tmp/log_attivita.txt")
    path.write_text(_log_txt(righe), encoding="utf-8")
    await target.answer_document(FSInputFile(path), caption=f"Log attività: {len(righe)} righe")


@admin_router.message(Command("log"))
async def log_cmd(msg: Message):
    await _manda_log(msg)


@admin_router.callback_query(F.data == "a:log")
async def cb_log(cb: CallbackQuery):
    await cb.answer()
    await _manda_log(cb.message)


# ==================== INSERIMENTO LISTE ====================
@admin_router.message(Command("aggiungi"))
async def add_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Che tipo di lista vuoi inserire?", reply_markup=kb.kinds())


@admin_router.callback_query(F.data == "a:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())


PROMPT = {
    "m3u": (Add.m3u,
            "Mandami il link M3U.\n\n"
            "Va bene qualsiasi forma: <code>.m3u</code>, <code>.m3u8</code>, oppure "
            "<code>get.php?username=...&amp;password=...&amp;type=m3u_plus</code>.\n"
            "Puoi anche incollare più link, uno per riga."),
    "xtream": (Add.x_server,
               "Mandami indirizzo e porta del pannello.\n\n"
               "Esempio: <code>http://host:8080</code>\n"
               "Per più liste: indirizzo, utente e password, una lista ogni tre righe, "
               "separate da una riga vuota."),
    "stalker": (Add.s_portal,
                "Mandami indirizzo e porta del portale.\n\n"
                "Esempio: <code>http://host:8080/c/</code>\n"
                "Per più liste: portale e MAC, una lista ogni due righe, "
                "separate da una riga vuota."),
}


@admin_router.callback_query(F.data == "a:cancel")
async def add_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Annullato")
    _pulisci(cb.from_user.id)
    await state.clear()
    await _edit(cb.message, _quadro(await db.stats()), kb.admin_menu())


@admin_router.callback_query(F.data.startswith("a:b:"))
async def cb_back(cb: CallbackQuery, state: FSMContext):
    dest = cb.data.split(":", 2)[2]
    await cb.answer("Indietro")
    if dest == "menu":
        _pulisci(cb.from_user.id)
        await state.clear()
        return await _edit(cb.message, _quadro(await db.stats()), kb.admin_menu())
    if dest == "kinds":
        _pulisci(cb.from_user.id)
        await state.clear()
        return await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
    if dest == "set":
        await state.clear()
        return await _edit(cb.message, "Impostazioni", await _settings_kb())
    if dest == "hist":
        await state.clear()
        return await _hist(cb.message, edit=True)
    if dest == "xserver":
        await state.set_state(Add.x_server)
        testo = ("Mandami indirizzo e porta del pannello.\n\n"
                 "Esempio: <code>http://host:8080</code>")
        return await _edit(cb.message, testo + HINT, kb.nav("a:add"))
    if dest == "xuser":
        await state.set_state(Add.x_user)
        return await _edit(cb.message, "Ora l'utente." + HINT, kb.nav("a:b:xserver"))
    if dest == "sportal":
        await state.set_state(Add.s_portal)
        testo = ("Mandami indirizzo e porta del portale.\n\n"
                 "Esempio: <code>http://host:8080/c/</code>")
        return await _edit(cb.message, testo + HINT, kb.nav("a:add"))
    if dest in PROMPT:
        _pulisci(cb.from_user.id)
        passo, testo = PROMPT[dest]
        await state.update_data(kind=dest)
        await state.set_state(passo)
        return await _edit(cb.message, testo + HINT, kb.nav("a:add"))
    _pulisci(cb.from_user.id)
    await state.clear()
    await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())


@admin_router.callback_query(F.data.startswith("a:k:"))
async def cb_kind(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split(":")[2]
    if kind == "bulk":
        await cb.answer()
        await state.clear()
        await state.update_data(kind="bulk")
        await state.set_state(Add.bulk)
        await _edit(cb.message,
                    "Mandami il <b>file .txt</b>, uno <b>screenshot</b> con le liste nella "
                    "didascalia, oppure incolla il testo.\n"
                    "Riconosco Xtream, M3U e Stalker, scarto le copie e ti mostro cosa ho "
                    "trovato prima di salvare." + HINT, kb.nav("a:add"))
        return
    if kind not in PROMPT:
        await cb.answer("Scegli un tipo")
        return await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
    await cb.answer()
    await state.clear()
    await state.update_data(kind=kind)
    passo, testo = PROMPT[kind]
    await state.set_state(passo)
    await _edit(cb.message, testo + HINT, kb.nav("a:add"))


async def _ask_confirm(target, state: FSMContext, item):
    cid = uuid.uuid4().hex[:10]
    item["uid"] = target.from_user.id
    TMP[cid] = item
    await state.update_data(kind=item["kind"])
    await state.set_state(Add.confirm)
    indietro = f"a:b:{item['kind']}"
    dup = await db.lista_simile(item["kind"], item["payload"])
    if dup:
        vecchio = {"kind": dup["kind"], "payload": json.loads(dup["payload"])}
        stato = {"free": "libera", "assigned": "già assegnata"}.get(dup["status"], dup["status"])
        text = (f"⚠️ <b>Attenzione: lista già presente</b>\n\n"
                f"Corrisponde alla lista <b>#{dup['id']}</b> ({stato}):\n"
                f"{preview(vecchio)}\n\n"
                f"──────────\n\n<b>Stai inserendo:</b>\n{preview(item)}\n\n"
                f"La aggiungo lo stesso?")
        kbd = kb.doppione(cid, indietro)
    else:
        text = f"Confermi questo inserimento?\n\n{preview(item)}"
        kbd = kb.confirm(cid, indietro)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await _edit(target.message, text, kbd)
    else:
        await target.answer(text, reply_markup=kbd)


async def _mostra_multiplo(target, state: FSMContext, kind, items):
    cid = uuid.uuid4().hex[:10]
    MULTI[cid] = {"items": items, "kind": kind, "uid": target.from_user.id}
    await state.update_data(kind=kind)
    await state.set_state(Add.confirm)
    mostra = items[:MAX_ANTEPRIMA]
    righe = "\n\n".join(_bulk_line(n, it) for n, it in enumerate(mostra, 1))
    extra = f"\n\n… e altre {len(items) - MAX_ANTEPRIMA}" if len(items) > MAX_ANTEPRIMA else ""
    testo = (f"Ho riconosciuto <b>{len(items)}</b> liste separate:\n\n{righe}{extra}\n\n"
             f"Sono corrette? Se confermi le salvo tutte, altrimenti rimandamele.")
    if isinstance(target, CallbackQuery):
        await target.answer()
        await _edit(target.message, testo, kb.multiplo(cid, f"a:b:{kind}"))
    else:
        await target.answer(testo, reply_markup=kb.multiplo(cid, f"a:b:{kind}"))


@admin_router.message(Add.m3u, F.text)
async def add_m3u(msg: Message, state: FSMContext):
    fatte = _parse_m3u(msg.text)
    if len(fatte) > 1:
        return await _mostra_multiplo(msg, state, "m3u", fatte)
    if len(fatte) == 1:
        return await _ask_confirm(msg, state, fatte[0])
    await msg.answer("Serve un link che inizi con http. Riprova o /annulla.",
                     reply_markup=kb.nav("a:add"))


@admin_router.message(Add.x_server, F.text)
async def add_x_server(msg: Message, state: FSMContext):
    testo = msg.text or ""
    fatte = _parse_xtream(testo)
    if len(fatte) > 1:
        return await _mostra_multiplo(msg, state, "xtream", fatte)
    if len(fatte) == 1:
        return await _ask_confirm(msg, state, fatte[0])
    s = testo.strip()
    if not s.startswith("http"):
        s = "http://" + s
    if not re.match(r"^https?://[^\s/]+", s):
        return await msg.answer("Indirizzo non valido. Esempio: http://host:8080",
                                reply_markup=kb.nav("a:add"))
    await state.update_data(server=s.rstrip("/"))
    await state.set_state(Add.x_user)
    await msg.answer("Ora l'utente.", reply_markup=kb.nav("a:b:xserver"))


@admin_router.message(Add.x_user, F.text)
async def add_x_user(msg: Message, state: FSMContext):
    u = (msg.text or "").strip()
    if not u or " " in u:
        return await msg.answer("Utente non valido: niente spazi.",
                                reply_markup=kb.nav("a:b:xserver"))
    await state.update_data(username=u)
    await state.set_state(Add.x_pass)
    await msg.answer("Ora la password.", reply_markup=kb.nav("a:b:xuser"))


@admin_router.message(Add.x_pass, F.text)
async def add_x_pass(msg: Message, state: FSMContext):
    p = (msg.text or "").strip()
    if not p or " " in p:
        return await msg.answer("Password non valida: niente spazi.",
                                reply_markup=kb.nav("a:b:xuser"))
    d = await state.get_data()
    item = {"kind": "xtream", "label": d["server"],
            "payload": {"server": d["server"], "username": d["username"], "password": p}}
    await _ask_confirm(msg, state, item)


@admin_router.message(Add.s_portal, F.text)
async def add_s_portal(msg: Message, state: FSMContext):
    testo = msg.text or ""
    fatte = _parse_stalker(testo)
    if len(fatte) > 1:
        return await _mostra_multiplo(msg, state, "stalker", fatte)
    if len(fatte) == 1:
        return await _ask_confirm(msg, state, fatte[0])
    s = testo.strip()
    if not s.startswith("http"):
        s = "http://" + s
    if not re.match(r"^https?://[^\s/]+", s):
        return await msg.answer("Indirizzo non valido. Esempio: http://host:8080/c/",
                                reply_markup=kb.nav("a:add"))
    await state.update_data(portal=s.rstrip("/"))
    await state.set_state(Add.s_mac)
    await msg.answer("Ora il MAC address.\n\nEsempio: <code>00:1A:79:12:34:56</code>",
                     reply_markup=kb.nav("a:b:sportal"))


@admin_router.message(Add.s_mac, F.text)
async def add_s_mac(msg: Message, state: FSMContext):
    m = (msg.text or "").strip().upper().replace("-", ":")
    if not MAC_RE.match(m):
        return await msg.answer("MAC non valido. Formato: 00:1A:79:12:34:56",
                                reply_markup=kb.nav("a:b:sportal"))
    d = await state.get_data()
    item = {"kind": "stalker", "label": d["portal"],
            "payload": {"portal": d["portal"], "mac": m}}
    await _ask_confirm(msg, state, item)


@admin_router.message(StateFilter(Add.m3u, Add.x_server, Add.x_user, Add.x_pass,
                                  Add.s_portal, Add.s_mac, Add.confirm), F.photo)
async def add_foto_reject(msg: Message):
    await msg.answer("Qui accetto solo testo o copia-incolla.\n"
                     "Per una foto o un file usa /aggiungi e scegli «Aggiungi foto/txt».",
                     reply_markup=kb.nav("a:add"))


@admin_router.message(Add.confirm, F.text)
async def confirm_text(msg: Message, state: FSMContext):
    kind = (await state.get_data()).get("kind")
    testo = msg.text or ""
    if kind == "xtream":
        fatte = _parse_xtream(testo)
    elif kind == "stalker":
        fatte = _parse_stalker(testo)
    elif kind == "m3u":
        fatte = _parse_m3u(testo)
    else:
        fatte = _parse_m3u(testo) + _parse_xtream(testo) + _parse_stalker(testo)
    if not fatte:
        return await msg.answer("Non ho riconosciuto liste. Rimandamele oppure /annulla.",
                                reply_markup=kb.nav("a:add"))
    if len(fatte) == 1:
        return await _ask_confirm(msg, state, fatte[0])
    await _mostra_multiplo(msg, state, kind or fatte[0]["kind"], fatte)


@admin_router.callback_query(F.data.startswith("a:mok:"))
async def multi_ok(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    d = MULTI.pop(cid, None)
    await state.clear()
    if not d:
        return await cb.answer("Elenco scaduto, rimanda le liste", show_alert=True)
    await cb.answer()
    await _edit(cb.message, f"Salvo {len(d['items'])} liste...")
    ids = []
    for it in d["items"]:
        lid = await db.add_list(it["kind"], it["payload"], it.get("label") or "-", cb.from_user.id)
        ids.append(lid)
    await db.log(cb.from_user.id, "bulk_add", str(len(ids)))
    await _edit(cb.message, f"Salvate {len(ids)} liste. Test in corso...")
    asyncio.create_task(_bulk_test(cb.message, ids))


@admin_router.callback_query(F.data.startswith("a:mredo:"))
async def multi_redo(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    d = MULTI.pop(cid, None)
    if not d:
        return await cb.answer("Elenco scaduto", show_alert=True)
    kind = d["kind"]
    if kind not in PROMPT:
        await cb.answer("Scegli un tipo")
        await state.clear()
        return await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
    await cb.answer("Rimanda le liste")
    passo, testo = PROMPT[kind]
    await state.update_data(kind=kind)
    await state.set_state(passo)
    await _edit(cb.message, testo + HINT, kb.nav("a:add"))


@admin_router.callback_query(F.data.startswith("a:ok:"))
async def add_confirm(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    item = TMP.get(cid)
    await state.clear()
    if not item:
        return await cb.answer("Anteprima scaduta, rifai l'inserimento", show_alert=True)
    await cb.answer()
    await _edit(cb.message, "Sto testando la lista, attendi...")
    res = await test_list(item["kind"], item["payload"])
    if res.frame:
        res.frame.unlink(missing_ok=True)
    if res.ok:
        TMP.pop(cid, None)
        lid = await db.add_list(item["kind"], item["payload"],
                                item.get("label") or "-", cb.from_user.id)
        await db.set_test(lid, True, res.note)
        await db.log(cb.from_user.id, "add", f"{lid} {item['kind']}")
        return await _edit(cb.message,
                           f"Salvata - lista #{lid} ({item['kind']}).\nTest superato.",
                           kb.admin_menu())
    await _edit(cb.message,
                f"Test <b>non superato</b>: {res.note}\n\n{preview(item)}\n\n"
                f"Vuoi inserirla ugualmente?", kb.force(cid, f"a:b:{item['kind']}"))


@admin_router.callback_query(F.data.startswith("a:force:"))
async def add_force(cb: CallbackQuery):
    cid = cb.data.split(":")[2]
    item = TMP.pop(cid, None)
    if not item:
        return await cb.answer("Anteprima scaduta", show_alert=True)
    await cb.answer()
    lid = await db.add_list(item["kind"], item["payload"],
                            item.get("label") or "-", cb.from_user.id)
    await db.set_test(lid, False, "inserita senza test superato")
    await db.log(cb.from_user.id, "add", f"{lid} {item['kind']} forzata")
    await _edit(cb.message, f"Salvata ugualmente - lista #{lid} ({item['kind']}).",
                kb.admin_menu())


# ---- import massivo da txt ----
def _bulk_line(n, it):
    p = it["payload"]
    if it["kind"] == "xtream":
        return (f"{n}. <b>XT</b>\n"
                f"user: <code>{p['username']}</code>\n"
                f"pass: <code>{p['password']}</code>\n"
                f"<code>{p['server']}</code>")
    if it["kind"] == "stalker":
        return f"{n}. <b>ST</b> <code>{p['mac']}</code> · <code>{p['portal']}</code>"
    return f"{n}. <b>M3U</b>\n<code>{p['url']}</code>"


BULK_PER_PAGE = 10


def _bulk_summary(cid):
    d = BULK[cid]
    items = d["items"]
    per_tipo = {}
    for it in items:
        per_tipo[it["kind"]] = per_tipo.get(it["kind"], 0) + 1
    righe = "\n".join(f"• {k}: {v}" for k, v in sorted(per_tipo.items()))
    anteprima = "\n".join(_bulk_line(n, it) for n, it in enumerate(items[:10], 1))
    extra = f"\n… e altre {len(items) - 10}" if len(items) > 10 else ""
    dup = f" ({d['dup']} duplicate scartate)" if d.get("dup") else ""
    gia = ""
    if d.get("dub"):
        gia = (f"\n⚠️ <b>{len(d['dub'])}</b> risultano già in archivio: sono "
               f"deselezionate, riattivabili da «Scegli quali».")
    testo = (f"Ho letto il contenuto e riconosciuto <b>{len(items)}</b> liste valide{dup}:\n"
             f"{righe}\n\n<b>Anteprima</b>\n{anteprima}{extra}{gia}\n\n"
             f"Posso salvarle tutte, oppure scegli tu quali tenere.")
    kbd = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ SALVA TUTTE ({len(items)})", callback_data=f"a:bok:{cid}")],
        [InlineKeyboardButton(text="☑️ SCEGLI QUALI", callback_data=f"a:bsel:{cid}")],
        [InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:add")],
        [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
        [InlineKeyboardButton(text="❌ Annulla", callback_data="a:cancel")],
    ])
    return testo, kbd


def _bulk_page(cid, page):
    d = BULK[cid]
    items = d["items"]
    sel = d["sel"]
    dub = d.get("dub") or set()
    pagine = max(1, (len(items) - 1) // BULK_PER_PAGE + 1)
    page = max(0, min(page, pagine - 1))
    inizio = page * BULK_PER_PAGE
    righe = []
    for i in range(inizio, min(inizio + BULK_PER_PAGE, len(items))):
        segno = "🔁 " if i in dub else ""
        righe.append(segno + _bulk_line(i + 1, items[i]))
    testo = (f"<b>Scegli quali salvare</b> — pagina {page + 1}/{pagine}\n"
             f"Selezionate: {len(sel)} su {len(items)}\n"
             f"🔁 = già presente in archivio\n\n" + "\n".join(righe))
    kbd = []
    for i in range(inizio, min(inizio + BULK_PER_PAGE, len(items))):
        kbd.append([InlineKeyboardButton(text=("✅ " if i in sel else "⬜ ") + str(i + 1),
                                         callback_data=f"a:btg:{cid}:{i}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"a:bpg:{cid}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pagine}", callback_data="a:noop"))
    if (page + 1) * BULK_PER_PAGE < len(items):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"a:bpg:{cid}:{page + 1}"))
    kbd.append(nav)
    kbd.append([InlineKeyboardButton(text=f"💾 SALVA SELEZIONATE ({len(sel)})",
                                     callback_data=f"a:bok:{cid}")])
    kbd.append([InlineKeyboardButton(text="⬅️ Riepilogo", callback_data=f"a:ball:{cid}")])
    kbd.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    return testo, InlineKeyboardMarkup(inline_keyboard=kbd)


@admin_router.message(Add.bulk, F.document | F.photo | F.text)
async def bulk_input(msg: Message, state: FSMContext):
    testo = ""
    if msg.document:
        nome = (msg.document.file_name or "").lower()
        if not nome.endswith((".txt", ".m3u", ".m3u8")):
            return await msg.answer("Mandami un file .txt, .m3u o .m3u8",
                                    reply_markup=kb.nav("a:add"))
        buf = io.BytesIO()
        await msg.bot.download(msg.document, destination=buf)
        testo = buf.getvalue().decode("utf-8", errors="ignore")
    elif msg.photo:
        buf = io.BytesIO()
        await msg.bot.download(msg.photo[-1], destination=buf)
        try:
            letto = await asyncio.to_thread(ocr_bytes, buf.getvalue())
        except Exception as e:
            return await msg.answer(
                f"OCR in errore: {type(e).__name__}: {e}\n"
                f"binario: {shutil.which('tesseract')}", reply_markup=kb.nav("a:add"))
        testo = (msg.caption or "") + "\n" + letto
        if not letto.strip() and not (msg.caption or "").strip():
            return await msg.answer("Ho ricevuto la foto ma non ci ho letto niente. "
                                    "Prova a mandarla come .txt o più dritta.",
                                    reply_markup=kb.nav("a:add"))
    else:
        testo = msg.text or ""

    grezzi = detect_many(testo)
    if not grezzi:
        return await msg.answer("Non ho trovato nessuna lista riconoscibile. Riprova o /annulla.",
                                reply_markup=kb.nav("a:add"))

    visti, items, dup = set(), [], 0
    for it in grezzi:
        p = it["payload"]
        chiave = (it["kind"], p.get("server"), p.get("url"), p.get("portal"),
                  p.get("username"), p.get("password"), p.get("mac"))
        if chiave in visti:
            dup += 1
            continue
        visti.add(chiave)
        items.append(it)

    gia = set()
    for i, it in enumerate(items):
        if await db.lista_simile(it["kind"], it["payload"]):
            gia.add(i)

    cid = uuid.uuid4().hex[:10]
    BULK[cid] = {"items": items, "sel": set(range(len(items))) - gia,
                 "dup": dup, "dub": gia, "uid": msg.from_user.id}
    await state.set_state(Add.confirm)
    testo_rip, kbd = _bulk_summary(cid)
    await msg.answer(testo_rip, reply_markup=kbd)


@admin_router.callback_query(F.data.startswith("a:bsel:"))
async def bulk_sel(cb: CallbackQuery):
    cid = cb.data.split(":")[2]
    await cb.answer()
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_page(cid, 0)
    await _mostra(cb, testo, kbd)


@admin_router.callback_query(F.data.startswith("a:bpg:"))
async def bulk_page(cb: CallbackQuery):
    _, _, cid, page = cb.data.split(":")
    await cb.answer()
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_page(cid, int(page))
    await _mostra(cb, testo, kbd)


@admin_router.callback_query(F.data.startswith("a:btg:"))
async def bulk_toggle(cb: CallbackQuery):
    _, _, cid, idx, page = cb.data.split(":")
    await cb.answer()
    d = BULK.get(cid)
    if not d:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    i = int(idx)
    if i in d["sel"]:
        d["sel"].discard(i)
    else:
        d["sel"].add(i)
    testo, kbd = _bulk_page(cid, int(page))
    await _mostra(cb, testo, kbd)


@admin_router.callback_query(F.data.startswith("a:ball:"))
async def bulk_back(cb: CallbackQuery):
    cid = cb.data.split(":")[2]
    await cb.answer()
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_summary(cid)
    await _mostra(cb, testo, kbd)


async def _bulk_test(msg, ids):
    ok = ko = 0
    for n, lid in enumerate(ids, 1):
        row = await db.get_list(lid)
        p = await db.payload_of(row)
        res = await test_list(row["kind"], p)
        await db.set_test(lid, res.ok, res.note)
        if res.ok:
            ok += 1
            await db.set_payload(lid, p)
        else:
            ko += 1
        if n % 10 == 0:
            try:
                await msg.edit_text(f"Test {n}/{len(ids)} — superati {ok}, falliti {ko}")
            except Exception:
                pass
    fine = (f"Test finito: {ok} superati, {ko} non superati su {len(ids)}.\n"
            f"Restano tutte in uso.")
    try:
        await msg.edit_text(fine, reply_markup=kb.admin_menu())
    except Exception:
        try:
            await msg.answer(fine, reply_markup=kb.admin_menu())
        except Exception:
            pass


@admin_router.callback_query(F.data.startswith("a:bok:"))
async def bulk_confirm(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    d = BULK.pop(cid, None)
    await state.clear()
    if not d:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    scelte = [d["items"][i] for i in sorted(d["sel"])]
    if not scelte:
        BULK[cid] = d
        return await cb.answer("Non hai selezionato nessuna lista", show_alert=True)
    await cb.answer()
    await _edit(cb.message, f"Salvo {len(scelte)} liste...")
    ids = []
    for it in scelte:
        lid = await db.add_list(it["kind"], it["payload"], it.get("label") or "-", cb.from_user.id)
        ids.append(lid)
    await db.log(cb.from_user.id, "bulk_add", str(len(ids)))
    await _edit(cb.message, f"Salvate {len(ids)} liste. Test in corso...")
    asyncio.create_task(_bulk_test(cb.message, ids))


@admin_router.message(Command("annulla"))
async def cancel_cmd(msg: Message, state: FSMContext):
    _pulisci(msg.from_user.id)
    await state.clear()
    await msg.answer("Operazione annullata.", reply_markup=kb.admin_menu())


# ==================== LISTE E STORICO ====================
@admin_router.message(Command("liberi"))
async def free_cmd(msg: Message, state: FSMContext):
    await state.clear()
    rows = await db.free_lists()
    if not rows:
        return await msg.answer("Nessuna lista libera. Inseriscine una con /aggiungi.",
                                reply_markup=kb.admin_menu())
    txt = "\n".join(f"#{r['id']} · {r['kind']} · {r['label'] or '-'}" for r in rows)
    await msg.answer(f"Liste libere:\n\n{txt}", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:free")
async def cb_free(cb: CallbackQuery):
    await cb.answer()
    await _edit(cb.message, "Quali liste libere vuoi vedere?", kb.free_kinds())


async def _show_free(cb, kind, page):
    per_page = 5
    tot = await db.count_free_kind(kind)
    if not tot:
        await _mostra(cb, "Nessuna lista libera di questo tipo.", kb.admin_menu())
        return
    pagine = max(1, (tot - 1) // per_page + 1)
    page = max(0, min(page, pagine - 1))
    inizio = page * per_page
    righe = await db.free_lists_kind(kind, inizio, per_page)
    sel = DSEL.setdefault(cb.from_user.id, set())
    blocchi, bottoni = [], []
    for n, r in enumerate(righe, inizio + 1):
        p = await db.payload_of(r)
        if r["kind"] == "xtream":
            corpo = (f"<b>#{n}</b> · XTREAM · user <code>{p['username']}</code> · "
                     f"pass <code>{p['password']}</code>\n<code>{p['server']}</code>")
            etichetta = p.get("username") or "xtream"
        elif r["kind"] == "stalker":
            corpo = (f"<b>#{n}</b> · STALKER · MAC <code>{p['mac']}</code>\n"
                     f"<code>{p['portal']}</code>")
            etichetta = p.get("mac") or "stalker"
        else:
            corpo = f"<b>#{n}</b> · M3U\n<code>{p['url']}</code>"
            etichetta = p.get("url") or "m3u"
        blocchi.append(corpo)
        segno = "✅" if r["id"] in sel else "🗑"
        bottoni.append([InlineKeyboardButton(
            text=f"{segno} #{n} {etichetta[:24]}",
            callback_data=f"a:dtg:{r['id']}:{kind or 'all'}:{page}")])
    nav = []
    nav.append(InlineKeyboardButton(
        text="◀️" if page > 0 else "·",
        callback_data=f"a:fp:{kind or 'all'}:{max(0, page - 1)}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pagine}", callback_data="a:noop"))
    nav.append(InlineKeyboardButton(
        text="▶️" if (page + 1) * per_page < tot else "·",
        callback_data=f"a:fp:{kind or 'all'}:{min(pagine - 1, page + 1)}"))
    coda = []
    if sel:
        coda.append([InlineKeyboardButton(text=f"🗑 ELIMINA SELEZIONATE ({len(sel)})",
                                          callback_data=f"a:ddel:{kind or 'all'}:{page}")])
    coda.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:free")])
    coda.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _mostra(cb, "\n\n".join(blocchi),
                  InlineKeyboardMarkup(inline_keyboard=[nav] + bottoni + coda))


@admin_router.callback_query(F.data.in_({"a:freem3u", "a:freextream", "a:freestalker", "a:freeall"}))
async def cb_free_kind(cb: CallbackQuery):
    await cb.answer()
    kind = {"a:freem3u": "m3u", "a:freextream": "xtream",
            "a:freestalker": "stalker", "a:freeall": None}[cb.data]
    await _show_free(cb, kind, 0)


@admin_router.callback_query(F.data.startswith("a:fp:"))
async def cb_free_page(cb: CallbackQuery):
    await cb.answer()
    _, _, k, page = cb.data.split(":")
    await _show_free(cb, None if k == "all" else k, int(page))


@admin_router.callback_query(F.data.startswith("a:dtg:"))
async def cb_del_toggle(cb: CallbackQuery):
    await cb.answer()
    _, _, lid, kind, page = cb.data.split(":")
    sel = DSEL.setdefault(cb.from_user.id, set())
    if int(lid) in sel:
        sel.discard(int(lid))
    else:
        sel.add(int(lid))
    await _show_free(cb, None if kind == "all" else kind, int(page))


@admin_router.callback_query(F.data.startswith("a:ddel:"))
async def cb_del_selected(cb: CallbackQuery):
    _, _, kind, page = cb.data.split(":")
    sel = DSEL.get(cb.from_user.id, set())
    if not sel:
        return await cb.answer("Nessuna lista selezionata", show_alert=True)
    await cb.answer()
    fatte = 0
    for lid in list(sel):
        if await db.del_list(lid):
            fatte += 1
    DSEL[cb.from_user.id] = set()
    await db.log(cb.from_user.id, "del_lists", str(fatte))
    await _edit(cb.message, f"🗑 Eliminate {fatte} liste.\n\nPannello:", kb.admin_menu())


@admin_router.message(Command("assegnazioni"))
async def hist_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    pagina = int(p[1]) - 1 if len(p) > 1 and p[1].strip().isdigit() else 0
    await _hist(msg, page=max(0, pagina))


@admin_router.callback_query(F.data == "a:hist")
async def cb_hist(cb: CallbackQuery):
    await cb.answer()
    await _hist(cb.message, edit=True)


@admin_router.callback_query(F.data.startswith("a:hist:"))
async def cb_hist_page(cb: CallbackQuery):
    pezzi = (cb.data or "").split(":")
    try:
        pagina = int(pezzi[2])
    except (IndexError, ValueError):
        pagina = 0
    await cb.answer()
    await _hist(cb.message, edit=True, page=pagina)


def _stato_thanks(r):
    if r["revoked"]:
        return "🚫 revocata"
    if r["thanked_at"]:
        return f"✅ ringraziato {_ora(r['thanked_at'], '%H:%M')}"
    if r["thanks_deadline"]:
        manca = (r["thanks_deadline"] - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if manca > 0:
            return f"⏳ mancano {max(1, int(manca // 60))}m"
        return "❌ non ringraziato"
    return "—"


async def _hist(target, edit=False, page=0):
    rows = await db.assignments(300)
    if not rows:
        testo = "Nessuna assegnazione registrata."
        kbd = kb.nav("a:menu")
        if edit:
            return await _edit(target, testo, kbd)
        return await target.answer(testo, reply_markup=kbd)

    tot = len(rows)
    pagine = max(1, (tot - 1) // PER_PAGE_HIST + 1)
    page = max(0, min(int(page), pagine - 1))
    fetta = rows[page * PER_PAGE_HIST:(page + 1) * PER_PAGE_HIST]
    inizio = page * PER_PAGE_HIST

    si = sum(1 for r in rows if r["thanked_at"])
    no = sum(1 for r in rows if not r["thanked_at"] and not r["revoked"]
             and r["thanks_deadline"]
             and r["thanks_deadline"] <= dt.datetime.now(dt.timezone.utc))
    attesa = sum(1 for r in rows if not r["thanked_at"] and not r["revoked"]
                 and r["thanks_deadline"]
                 and r["thanks_deadline"] > dt.datetime.now(dt.timezone.utc))
    revocate = sum(1 for r in rows if r["revoked"])

    blocchi = []
    for scarto, r in enumerate(fetta, 1):
        n = inizio + scarto
        who = f"@{r['username']}" if r["username"] else (_nome(r) if r["full_name"]
                                                         else str(r["user_id"]))
        if len(who) > 20:
            who = who[:19] + "…"
        when = _ora(r["assigned_at"], "%d/%m %H:%M")
        blocchi.append(f"<b>#{r['id']}</b> · {when}\n"
                       f"L#{r['list_id']} <i>{_esc(r['kind'] or '?')}</i> · "
                       f"{_esc(who)}\n"
                       f"→ {_stato_thanks(r)}")

    bottoni = []
    for r in fetta:
        if r["revoked"]:
            continue
        bottoni.append([InlineKeyboardButton(text=f"↩️ Revoca #{r['id']}",
                                             callback_data=f"a:rev:{r['id']}:{page}")])
    nav = [
        InlineKeyboardButton(text="◀️" if page > 0 else "·",
                             callback_data=f"a:hist:{max(0, page - 1)}"),
        InlineKeyboardButton(text=f"pag. {page + 1}/{pagine}", callback_data="a:noop"),
        InlineKeyboardButton(text="▶️" if page + 1 < pagine else "·",
                             callback_data=f"a:hist:{min(pagine - 1, page + 1)}"),
    ]
    bottoni.append(nav)
    bottoni.append([InlineKeyboardButton(text="🖐 Ringraziamenti", callback_data="a:man")])
    bottoni.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    bottoni.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])

    testo = (f"<b>📤 Assegnazioni</b> · {tot} totali · pagina {page + 1}/{pagine}\n"
             f"✅ {si} · ⏳ {attesa} · ❌ {no} · 🚫 {revocate}\n"
             f"──────────\n\n" + "\n\n".join(blocchi))
    kbd = InlineKeyboardMarkup(inline_keyboard=bottoni)
    if edit:
        return await _edit(target, testo, kbd)
    await target.answer(testo, reply_markup=kbd)


@admin_router.callback_query(F.data.startswith("a:rev:"))
async def revoke_cb(cb: CallbackQuery):
    pezzi = (cb.data or "").split(":")
    try:
        aid = int(pezzi[2])
        page = int(pezzi[3])
    except (IndexError, ValueError):
        aid, page = int(pezzi[2]), 0
    row = await db.revoke(aid)
    await cb.answer("Revocata" if row else "Non trovata")
    if row:
        await db.log(cb.from_user.id, "revoca", str(row["id"]))
    await _hist(cb.message, edit=True, page=page)


@admin_router.message(Command("revoca"))
async def revoca_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        row = await db.revoke(int(p[1])) if p[1].isdigit() else None
        return await msg.answer("Revocata." if row else "ID assegnazione non valido.",
                                reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="revoca")
    await msg.answer("Quale assegnazione vuoi revocare? Mandami il numero "
                     "(lo trovi con /assegnazioni)." + HINT, reply_markup=kb.nav("a:hist"))


# ==================== UTENTI ====================
@admin_router.message(Command("utenti"))
async def users_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        return await _show_users(msg, p[1])
    await state.set_state(Ask.wait)
    await state.update_data(action="utenti")
    await msg.answer("Chi vuoi cercare? Mandami ID o @username." + HINT,
                     reply_markup=kb.nav("a:menu"))


async def _show_users(msg, q):
    rows = await db.find_users(q)
    if not rows:
        return await msg.answer("Nessun utente trovato.", reply_markup=kb.nav("a:menu"))
    for r in rows:
        await msg.answer(
            f"<b>{_esc(_nome(r))}</b> · {_esc(_utente(r))} · <code>{r['user_id']}</code>\n"
            f"bannato: {'si' if r['banned'] else 'no'} · prossima: "
            f"{_ora(r['next_available']) if r['next_available'] else '-'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [kb.open_row(r["user_id"], r["username"])],
                kb.user_row(r["user_id"], bool(r["banned"])),
                [InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")],
                [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")],
            ]))


@admin_router.callback_query(F.data.startswith("u:ban:"))
async def cb_ban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await cb.answer("Bannato")
    await db.ensure_user(uid)
    await db.ban(uid, "deciso da admin")
    await db.log(cb.from_user.id, "ban", str(uid))
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[kb.open_row(uid)], kb.user_row(uid, True),
                         [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")]]))


@admin_router.callback_query(F.data.startswith("u:unban:"))
async def cb_unban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await cb.answer("Sbloccato")
    await db.unban(uid)
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[kb.open_row(uid)], kb.user_row(uid, False),
                         [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")]]))


@admin_router.message(Command("ban"))
async def ban_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=2)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.ensure_user(uid)
        await db.ban(uid, p[2] if len(p) > 2 else "deciso da admin")
        await db.log(msg.from_user.id, "ban", str(uid))
        return await msg.answer(f"Utente {uid} bannato.", reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="ban")
    await msg.answer("Chi vuoi bannare? Mandami ID, @username o inoltra un suo messaggio." + HINT,
                     reply_markup=kb.nav("a:menu"))


@admin_router.message(Command("unban"))
async def unban_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.unban(uid)
        return await msg.answer(f"Utente {uid} sbloccato.", reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="unban")
    await msg.answer("Chi vuoi sbloccare? Mandami ID, @username o inoltra un suo messaggio." + HINT,
                     reply_markup=kb.nav("a:menu"))


@admin_router.message(Command("reset"))
async def reset_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.clear_next(uid)
        return await msg.answer(f"Attesa azzerata per {uid}.", reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="reset")
    await msg.answer("A chi vuoi azzerare l'attesa? Mandami ID, @username "
                     "o inoltra un suo messaggio." + HINT, reply_markup=kb.nav("a:menu"))


# ==================== AMMINISTRATORI ====================
async def _add_by(msg, raw):
    uid = await resolve_uid(msg.bot, raw)
    nome = raw.strip().lstrip("@").strip()
    if uid:
        await db.add_admin(uid, nome or None, msg.from_user.id)
        await db.log(msg.from_user.id, "admin_add", str(uid))
        return await msg.answer(f"{uid} ora è amministratore.", reply_markup=kb.admin_menu())
    if not nome or " " in nome:
        return await _need_target(msg)
    await db.add_admin_name(nome, msg.from_user.id)
    await db.log(msg.from_user.id, "admin_add_name", nome)
    await msg.answer(f"@{nome} ora è amministratore, senza bisogno di ID.\n\n"
                     f"Funziona appena scrive al bot o nel gruppo. "
                     f"Se cambia username va riaggiunto.", reply_markup=kb.admin_menu())


async def _del_by(msg, raw):
    uid = await resolve_uid(msg.bot, raw)
    nome = raw.strip().lstrip("@").strip()
    fatto = False
    if uid:
        await db.del_admin(uid)
        await db.clear_anon_by_user(uid)
        fatto = True
    if nome and " " not in nome:
        await db.del_admin_name(nome)
        fatto = True
    if not fatto:
        return await _need_target(msg)
    await db.log(msg.from_user.id, "admin_del", str(uid or nome))
    await msg.answer(f"{uid or '@' + nome} non è più amministratore.", reply_markup=kb.admin_menu())


@admin_router.message(Command("admin_add"))
async def admin_add(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        return await _add_by(msg, p[1])
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_add")
    await msg.answer("Chi vuoi aggiungere come amministratore?\n\n"
                     "Mandami lo @username: funziona anche se non ha mai scritto al bot.\n"
                     "In alternativa l'ID numerico o un messaggio inoltrato." + HINT,
                     reply_markup=kb.nav("a:menu"))


@admin_router.message(Command("admin_del"))
async def admin_del(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        return await _del_by(msg, p[1])
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_del")
    await msg.answer("Chi vuoi rimuovere dagli amministratori? Mandami ID, @username "
                     "o inoltra un suo messaggio." + HINT, reply_markup=kb.nav("a:menu"))


async def _show_admins(cb):
    righe = await db.all_admins()
    nomi = await db.list_admin_names()
    proprietari = sorted(config.SUPER_ADMINS)
    altri = [r for r in righe if r["user_id"] not in config.SUPER_ADMINS]
    tastiera = []
    for uid in proprietari:
        tastiera.append([
            InlineKeyboardButton(text=f"👑 {uid}", url=f"tg://user?id={uid}"),
            InlineKeyboardButton(text="proprietario", callback_data="a:noop"),
        ])
    for r in altri:
        nome = (_pulisci_user(r["username"]) or "no username")[:9]
        tastiera.append([
            InlineKeyboardButton(text=f"🛡 {nome} · {r['user_id']}",
                                 url=_link(r["user_id"], r["username"])),
            InlineKeyboardButton(text="Rimuovi", callback_data=f"a:rmadmin:{r['user_id']}"),
        ])
    for r in nomi:
        nome = _pulisci_user(r["username"])[:18]
        tastiera.append([
            InlineKeyboardButton(text=f"🛡 @{nome}", url=f"https://t.me/{nome}"),
            InlineKeyboardButton(text="Rimuovi", callback_data=f"a:rmname:{nome}"),
        ])
    tastiera.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    tastiera.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _mostra(cb,
                  f"👑 Proprietario: {len(proprietari)} · 🛡 Admin: {len(altri) + len(nomi)}",
                  InlineKeyboardMarkup(inline_keyboard=tastiera))


@admin_router.callback_query(F.data == "a:alladmins")
async def cb_alladmins(cb: CallbackQuery):
    await cb.answer()
    await _show_admins(cb)


@admin_router.callback_query(F.data.startswith("a:rmadmin:"))
async def cb_rmadmin(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await cb.answer("Rimosso")
    await db.del_admin(uid)
    await db.clear_anon_by_user(uid)
    await db.log(cb.from_user.id, "admin_del", str(uid))
    await _show_admins(cb)


@admin_router.callback_query(F.data.startswith("a:rmname:"))
async def cb_rmname(cb: CallbackQuery):
    nome = cb.data.split(":", 2)[2]
    await cb.answer("Rimosso")
    await db.del_admin_name(nome)
    await db.log(cb.from_user.id, "admin_del_name", nome)
    await _show_admins(cb)


# ==================== IMPOSTAZIONI ====================
@admin_router.message(Command("cooldown"))
async def cooldown_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        if not p[1].isdigit():
            return await msg.answer("Serve un numero di giorni, per esempio /cooldown 7",
                                    reply_markup=kb.nav("a:set"))
        await db.set_setting("cooldown_days", p[1])
        return await msg.answer(f"Attesa impostata a {p[1]} giorni.", reply_markup=kb.admin_menu())
    attuale = await db.get_int("cooldown_days")
    await state.set_state(Ask.wait)
    await state.update_data(action="cooldown")
    await msg.answer(f"L'attesa è impostata a <b>{attuale} giorni</b>.\n\n"
                     f"Da quanti giorni la vuoi? Mandami un numero." + HINT,
                     reply_markup=kb.nav("a:set"))


@admin_router.message(Command("setgroup"))
async def setgroup_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        gid = p[1].strip()
        await db.set_setting("group_id", gid)
        return await msg.answer(f"Gruppo impostato: <code>{gid}</code>", reply_markup=kb.admin_menu())
    if msg.chat.type != "private":
        await db.set_setting("group_id", str(msg.chat.id))
        return await msg.answer(f"Gruppo impostato: <code>{msg.chat.id}</code>\n"
                                f"Ora solo i membri possono usare il bot.",
                                reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="setgroup")
    await msg.answer("Mandami l'ID del gruppo (inizia con -100). In alternativa scrivi "
                     "/setgroup dentro il gruppo." + HINT, reply_markup=kb.nav("a:set"))


@admin_router.message(Command("setlink"))
async def setlink_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        await db.set_setting("group_link", p[1].strip())
        return await msg.answer("Link salvato.", reply_markup=kb.admin_menu())
    await state.set_state(Ask.wait)
    await state.update_data(action="setlink")
    await msg.answer("Mandami il link d'invito del gruppo (https://t.me/+...)." + HINT,
                     reply_markup=kb.nav("a:set"))


@admin_router.message(Command("toggle"))
async def toggle_cmd(msg: Message, state: FSMContext):
    await state.clear()
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await msg.answer("Bot spento. Nessuno riceverà liste." if cur else "Bot attivo.",
                     reply_markup=kb.admin_menu())


async def _settings_kb():
    acceso, da, a = await orario.impostazioni()
    return kb.settings_kb(await db.get_int("cooldown_days"), await db.get_int("send_proof"),
                          await db.get_int("enabled"), await db.get_int("group_only"),
                          await db.get_setting("group_id"), orario.stato_breve(acceso, da, a))


@admin_router.callback_query(F.data == "a:set")
async def cb_set(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _edit(cb.message, "Impostazioni", await _settings_kb())


@admin_router.callback_query(F.data == "s:proof")
async def s_proof(cb: CallbackQuery):
    await cb.answer("Foto di prova")
    cur = await db.get_int("send_proof")
    await db.set_setting("send_proof", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())


@admin_router.callback_query(F.data == "s:gonly")
async def s_gonly(cb: CallbackQuery):
    await cb.answer("Filtro gruppo")
    cur = await db.get_int("group_only")
    await db.set_setting("group_only", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())


@admin_router.callback_query(F.data == "s:toggle")
async def s_toggle(cb: CallbackQuery):
    await cb.answer("Aggiornato")
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())


@admin_router.callback_query(F.data == "s:cool")
async def s_cool(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    attuale = await db.get_int("cooldown_days")
    await state.set_state(Ask.wait)
    await state.update_data(action="cooldown")
    await _edit(cb.message, f"L'attesa è impostata a <b>{attuale} giorni</b>.\n\n"
                            f"Da quanti giorni la vuoi?" + HINT, kb.nav("a:set"))


@admin_router.callback_query(F.data == "s:group")
async def s_group(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="setgroup")
    await _edit(cb.message, "Mandami l'ID del gruppo (inizia con -100). In alternativa scrivi "
                            "/setgroup dentro il gruppo." + HINT, kb.nav("a:set"))


@admin_router.callback_query(F.data == "s:link")
async def s_link(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Ask.wait)
    await state.update_data(action="setlink")
    await _edit(cb.message, "Mandami il link d'invito del gruppo (https://t.me/+...)." + HINT,
                kb.nav("a:set"))


@admin_router.callback_query(F.data == "s:wel")
async def s_wel(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(SetTxt.welcome)
    await _edit(cb.message, "Invia il nuovo testo di benvenuto. Puoi usare {name} e {days}." + HINT,
                kb.nav("a:set"))


@admin_router.callback_query(F.data == "s:deny")
async def s_deny(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(SetTxt.deny)
    await _edit(cb.message, "Invia il nuovo testo di rifiuto. Puoi usare {when} e {left}." + HINT,
                kb.nav("a:set"))


@admin_router.callback_query(F.data == "s:ntext")
async def s_ntext(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(SetTxt.not_member)
    await _edit(cb.message, "Invia il nuovo testo di blocco. Puoi usare {link}." + HINT,
                kb.nav("a:set"))


@admin_router.message(SetTxt.welcome)
async def set_wel(msg: Message, state: FSMContext):
    await db.set_setting("welcome", msg.html_text)
    await state.clear()
    await msg.answer("Testo di benvenuto aggiornato.", reply_markup=kb.admin_menu())


@admin_router.message(SetTxt.deny)
async def set_deny(msg: Message, state: FSMContext):
    await db.set_setting("deny_text", msg.html_text)
    await state.clear()
    await msg.answer("Testo di rifiuto aggiornato.", reply_markup=kb.admin_menu())


@admin_router.message(SetTxt.not_member)
async def set_ntext(msg: Message, state: FSMContext):
    await db.set_setting("not_member", msg.html_text)
    await state.clear()
    await msg.answer("Testo di blocco aggiornato.", reply_markup=kb.admin_menu())


# ==================== ESPORTA ====================
@admin_router.message(Command("esporta"))
async def export_cmd(msg: Message, state: FSMContext):
    await state.clear()
    data = await db.all_free_payloads()
    if not data:
        return await msg.answer("Nessuna lista libera da esportare.", reply_markup=kb.admin_menu())
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    await msg.answer_document(FSInputFile(path), reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:exp")
async def cb_exp(cb: CallbackQuery):
    data = await db.all_free_payloads()
    if not data:
        return await cb.answer("Nessuna lista libera", show_alert=True)
    await cb.answer()
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    await cb.message.answer_document(FSInputFile(path))


# ==================== INSERIMENTO LIBERO ====================
@admin_router.message(StateFilter(None), F.chat.type == "private", NotCommand(), F.text | F.photo)
async def freeform(msg: Message, state: FSMContext):
    text = msg.text or msg.caption or ""
    item = detect(text)
    if item:
        await _ask_confirm(msg, state, item)


# ==================== RISPOSTE AI PROMPT ====================
@admin_router.message(Ask.wait, F.text | F.photo)
async def ask_input(msg: Message, state: FSMContext):
    action = (await state.get_data()).get("action")
    raw = msg.text or msg.caption or ""

    if action == "cooldown":
        if not raw.strip().isdigit():
            return await msg.answer("Serve un numero. Riprova o scrivi /annulla.",
                                    reply_markup=kb.nav("a:set"))
        await db.set_setting("cooldown_days", raw.strip())
        await state.clear()
        return await msg.answer(f"Attesa impostata a {raw.strip()} giorni.",
                                reply_markup=kb.admin_menu())

    if action == "setlink":
        if not raw.strip().startswith("http"):
            return await msg.answer("Serve un link che inizi con http. Riprova o scrivi /annulla.",
                                    reply_markup=kb.nav("a:set"))
        await db.set_setting("group_link", raw.strip())
        await state.clear()
        return await msg.answer("Link salvato.", reply_markup=kb.admin_menu())

    if action == "setgroup":
        await db.set_setting("group_id", raw.strip())
        await state.clear()
        return await msg.answer(f"Gruppo impostato: <code>{raw.strip()}</code>",
                                reply_markup=kb.admin_menu())

    if action == "utenti":
        await state.clear()
        return await _show_users(msg, raw.strip())

    if action == "revoca":
        if not raw.strip().isdigit():
            return await msg.answer("Serve il numero dell'assegnazione. Riprova o scrivi /annulla.",
                                    reply_markup=kb.nav("a:hist"))
        row = await db.revoke(int(raw.strip()))
        await state.clear()
        return await msg.answer("Assegnazione revocata." if row else "Assegnazione non trovata.",
                                reply_markup=kb.admin_menu())

    if action == "admin_add":
        await state.clear()
        return await _add_by(msg, raw)

    if action == "admin_del":
        await state.clear()
        return await _del_by(msg, raw)

    uid = await _target(msg, raw)
    if not uid:
        return await _need_target(msg)
    await db.ensure_user(uid)

    if action == "ban":
        await db.ban(uid, "deciso da admin")
        await db.log(msg.from_user.id, "ban", str(uid))
        testo = f"Utente {uid} bannato."
    elif action == "unban":
        await db.unban(uid)
        testo = f"Utente {uid} sbloccato."
    elif action == "reset":
        await db.clear_next(uid)
        testo = f"Attesa azzerata per {uid}."
    else:
        await state.clear()
        return await msg.answer("Azione sconosciuta, riprova.", reply_markup=kb.admin_menu())

    await state.clear()
    await msg.answer(testo, reply_markup=kb.admin_menu())


# ==================== PARACADUTE ====================
@admin_router.callback_query(F.data.startswith("a:"))
async def cb_fallback_a(cb: CallbackQuery):
    await cb.answer("Azione non disponibile, riapri il pannello con /admin")


@admin_router.callback_query(F.data.startswith("s:"))
async def cb_fallback_s(cb: CallbackQuery):
    await cb.answer("Azione non disponibile, riapri le impostazioni con /admin")
