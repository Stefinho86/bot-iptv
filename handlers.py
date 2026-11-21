import asyncio
import datetime as dt
import io
import shutil
import json
import re
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, FSInputFile,
                           InlineKeyboardMarkup, InlineKeyboardButton)

import db
import config
import utils as kb
from services import detect, detect_many, ocr_bytes, preview, test_list
from utils import IsAdmin, Trigger, NotCommand, AnonUnverified

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

HINT = "\n\nScrivi /annulla per interrompere."

MAX_ANTEPRIMA = 15


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


class Ask(StatesGroup):
    wait = State()


# ==================== AIUTI ====================
def _ora(valore, fmt="%d/%m/%Y %H:%M"):
    try:
        return valore.astimezone(ROMA).strftime(fmt)
    except Exception:
        return "-"


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


def _link(uid, username):
    return f"https://t.me/{username}" if username else f"tg://user?id={uid}"


async def _edit(target, testo, kbd=None):
    try:
        await target.edit_text(testo, reply_markup=kbd)
    except Exception:
        await target.answer(testo, reply_markup=kbd)


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
            f"Bot: {'attivo' if en else 'spento'}\nTu: {tu}\n"
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
    txt = (await db.get_setting("welcome")).format(
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
    await msg.answer(f"Lista #{a['list_id']} ({a['kind']}), scade "
                     f"{_ora(a['expires_at'])} (tra {_human(left)})")


@user_router.callback_query(F.data == "u:get")
async def cb_get(cb: CallbackQuery, bot: Bot):
    await cb.answer("Preparo la lista...")
    await deliver(cb.message, cb.from_user, bot, await kb.is_admin_cb(cb))


@user_router.message(Trigger(), Command("lista"))
async def cmd_lista(msg: Message, bot: Bot):
    await deliver(msg, msg.from_user, bot, await kb.is_admin_event(msg))


@user_router.message(Trigger(), F.text)
async def mention_any(msg: Message, bot: Bot):
    await deliver(msg, msg.from_user, bot, await kb.is_admin_event(msg))


async def deliver(msg, user, bot, adm=False):
    uid = user.id
    if await db.get_int("enabled") == 0:
        return await msg.answer("Bot momentaneamente spento. Riprova più tardi.")
    await db.touch_user(uid, user.username, user.full_name)
    u = await db.get_user(uid)
    if u and u["banned"]:
        return await msg.answer(f"Sei bannato. Motivo: {u['ban_reason'] or 'non indicato'}")
    if not adm and not await kb.member_ok(bot, uid):
        return await msg.answer(await kb.not_member_text(), reply_markup=await kb.join_kb())
    if uid in BUSY:
        return await msg.answer("Sto già preparando la tua lista, attendi.")
    BUSY.add(uid)
    try:
        u = await db.get_user(uid)
        if u and u["banned"]:
            return await msg.answer(f"Sei bannato. Motivo: {u['ban_reason'] or 'non indicato'}")

        days = await db.get_int("cooldown_days")
        if not adm and u and u["next_available"]:
            nxt = u["next_available"]
            if dt.datetime.now(dt.timezone.utc) < nxt:
                left = nxt - dt.datetime.now(dt.timezone.utc)
                txt = (await db.get_setting("deny_text")).format(
                    when=_ora(nxt, "%d/%m/%Y alle %H:%M"), left=_human(left))
                return await msg.answer(txt)

        if await db.free_count() == 0:
            return await msg.answer(await db.get_setting("no_lists"))

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
            return await msg.answer(await db.get_setting("broken_all"))

        exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
        await db.assign(chosen["id"], uid, exp)
        if not adm:
            await db.set_next(uid, exp)
        await db.log(uid, "consegna", f"lista {chosen['id']}")

        caption = (f"<b>La tua lista IPTV</b> (#{chosen['id']})\n\n"
                   f"{_payload_text(chosen['kind'], payload)}\n\n"
                   f"Prossima lista: <b>{_ora(exp)}</b>")
        if shot and await db.get_int("send_proof") == 1:
            await msg.answer_photo(FSInputFile(shot), caption=caption)
            shot.unlink(missing_ok=True)
        else:
            await msg.answer(caption)
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
    await _edit(cb.message, await _stato_text(cb.message, bot), kb.admin_menu())
    await cb.answer()


@admin_router.message(Command("comandi"))
async def comandi_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "<b>Comandi</b>\n\n"
        "/admin - pannello\n"
        "/stato - diagnostica\n"
        "/aggiungi - inserisci una lista\n"
        "/liberi - liste non assegnate\n"
        "/assegnazioni - storico consegne\n"
        "/revoca - ritira un'assegnazione\n"
        "/utenti - cerca un utente\n"
        "/ban - blocca un utente\n"
        "/unban - sblocca un utente\n"
        "/reset - azzera l'attesa\n"
        "/admin_add - aggiungi amministratore\n"
        "/admin_del - rimuovi amministratore\n"
        "/cooldown - giorni di attesa\n"
        "/setgroup - gruppo autorizzato\n"
        "/setlink - link d'invito\n"
        "/verifica - riverifica admin anonimo\n"
        "/deverifica - revoca la verifica\n"
        "/toggle - accendi o spegni\n"
        "/esporta - esporta le liste libere\n"
        "/log - file di log\n"
        "/annulla - interrompi\n\n"
        "Tutti accettano ID, @username o inoltro.",
        reply_markup=kb.admin_menu())


@admin_router.message(Command("verifica"))
async def verifica_cmd(msg: Message, bot: Bot):
    await _ask_verify(msg, bot)


@admin_router.message(Command("deverifica"))
async def deverifica_cmd(msg: Message):
    await db.clear_anon_verified(msg.chat.id)
    await msg.answer("Verifica revocata per questa chat.", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:verify")
async def cb_verify(cb: CallbackQuery, bot: Bot):
    await _ask_verify(cb.message, bot, edit=True)
    await cb.answer()


@admin_router.callback_query(F.data == "a:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    _pulisci(cb.from_user.id)
    await state.clear()
    await _edit(cb.message, _quadro(await db.stats()), kb.admin_menu())
    await cb.answer()


@admin_router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    await _edit(cb.message, _quadro(await db.stats()), kb.admin_menu())
    await cb.answer()


@admin_router.callback_query(F.data == "a:comandi")
async def cb_comandi(cb: CallbackQuery, state: FSMContext):
    await comandi_cmd(cb.message, state)
    await cb.answer()


@admin_router.callback_query(F.data == "a:reload")
async def cb_reload(cb: CallbackQuery, bot: Bot):
    await cb.answer("Ricarico")
    from bot import avvisa_admin
    await avvisa_admin(bot, solo=cb.from_user.id)
    await _edit(cb.message, "Bot ricaricato.\n\nPannello:", kb.admin_menu())


@admin_router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="utenti")
    await _edit(cb.message, "Chi vuoi cercare? Mandami ID o @username." + HINT,
                kb.nav("a:menu"))
    await cb.answer()


@admin_router.callback_query(F.data == "a:revoca")
async def cb_revoca(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="revoca")
    await _edit(cb.message, "Quale assegnazione vuoi revocare? Mandami il numero "
                            "(lo trovi in Assegnazioni)." + HINT, kb.nav("a:hist"))
    await cb.answer()


@admin_router.callback_query(F.data == "a:reset")
async def cb_reset(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="reset")
    await _edit(cb.message, "A chi vuoi azzerare l'attesa? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT, kb.nav("a:menu"))
    await cb.answer()


@admin_router.callback_query(F.data == "a:admin_add")
async def cb_admin_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_add")
    await _edit(cb.message, "Chi vuoi aggiungere come amministratore?\n\n"
                            "Mandami lo @username: funziona anche se non ha mai scritto al bot.\n"
                            "In alternativa l'ID numerico o un messaggio inoltrato." + HINT,
                kb.nav("a:menu"))
    await cb.answer()


@admin_router.callback_query(F.data == "a:admin_del")
async def cb_admin_del(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_del")
    await _edit(cb.message, "Chi vuoi rimuovere dagli amministratori? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT, kb.nav("a:menu"))
    await cb.answer()


@admin_router.callback_query(F.data == "a:toggle")
async def cb_toggle(cb: CallbackQuery):
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await _edit(cb.message, f"Bot {'SPENTO' if cur else 'ATTIVO'}.", kb.admin_menu())
    await cb.answer()


# ==================== LOG ATTIVITA ====================
AZIONI = {
    "add": "lista aggiunta",
    "bulk_add": "import massivo",
    "del_lists": "liste eliminate",
    "consegna": "lista consegnata",
    "revoca": "assegnazione revocata",
    "ban": "utente bannato",
    "admin_add": "admin aggiunto",
    "admin_add_name": "admin aggiunto per username",
    "admin_del": "admin rimosso",
    "admin_del_name": "admin rimosso per username",
    "anon_verify": "verifica admin anonimo",
}


def _log_txt(righe):
    out = ["LOG ATTIVITA - LISTE PIAZZA ANDROID ITA", ""]
    for r in reversed(righe):
        quando = _ora(r["ts"])
        chi = f"@{r['username']}" if r["username"] else (r["full_name"] or str(r["actor"]))
        cosa = AZIONI.get(r["action"], r["action"])
        det = f" - {r['detail']}" if r["detail"] else ""
        out.append(f"{quando} | {chi} | {cosa}{det}")
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
    await _manda_log(cb.message)
    await cb.answer()


# ==================== INSERIMENTO LISTE ====================
@admin_router.message(Command("aggiungi"))
async def add_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Che tipo di lista vuoi inserire?", reply_markup=kb.kinds())


@admin_router.callback_query(F.data == "a:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
    await cb.answer()


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
        await state.clear()
        await state.update_data(kind="bulk")
        await state.set_state(Add.bulk)
        await _edit(cb.message,
                    "Mandami il <b>file .txt</b>, uno <b>screenshot</b> con le liste nella "
                    "didascalia, oppure incolla il testo.\n"
                    "Riconosco Xtream, M3U e Stalker, scarto le copie e ti mostro cosa ho "
                    "trovato prima di salvare." + HINT, kb.nav("a:add"))
        return await cb.answer()
    if kind not in PROMPT:
        await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
        return await cb.answer("Scegli un tipo")
    await state.clear()
    await state.update_data(kind=kind)
    passo, testo = PROMPT[kind]
    await state.set_state(passo)
    await _edit(cb.message, testo + HINT, kb.nav("a:add"))
    await cb.answer()


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
        await _edit(target.message, text, kbd)
        await target.answer()
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
        await _edit(target.message, testo, kb.multiplo(cid, f"a:b:{kind}"))
        await target.answer()
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
    await _edit(cb.message, f"Salvo {len(d['items'])} liste...")
    ids = []
    for it in d["items"]:
        lid = await db.add_list(it["kind"], it["payload"], it.get("label") or "-", cb.from_user.id)
        ids.append(lid)
    await db.log(cb.from_user.id, "bulk_add", str(len(ids)))
    await _edit(cb.message, f"Salvate {len(ids)} liste. Test in corso...")
    asyncio.create_task(_bulk_test(cb.message, ids))
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:mredo:"))
async def multi_redo(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    d = MULTI.pop(cid, None)
    if not d:
        return await cb.answer("Elenco scaduto", show_alert=True)
    kind = d["kind"]
    if kind not in PROMPT:
        await state.clear()
        await _edit(cb.message, "Che tipo di lista vuoi inserire?", kb.kinds())
        return await cb.answer("Scegli un tipo")
    passo, testo = PROMPT[kind]
    await state.update_data(kind=kind)
    await state.set_state(passo)
    await _edit(cb.message, testo + HINT, kb.nav("a:add"))
    await cb.answer("Rimanda le liste")


@admin_router.callback_query(F.data.startswith("a:ok:"))
async def add_confirm(cb: CallbackQuery, state: FSMContext):
    cid = cb.data.split(":")[2]
    item = TMP.get(cid)
    await state.clear()
    if not item:
        return await cb.answer("Anteprima scaduta, rifai l'inserimento", show_alert=True)
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
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:force:"))
async def add_force(cb: CallbackQuery):
    cid = cb.data.split(":")[2]
    item = TMP.pop(cid, None)
    if not item:
        return await cb.answer("Anteprima scaduta", show_alert=True)
    lid = await db.add_list(item["kind"], item["payload"],
                            item.get("label") or "-", cb.from_user.id)
    await db.set_test(lid, False, "inserita senza test superato")
    await db.log(cb.from_user.id, "add", f"{lid} {item['kind']} forzata")
    await _edit(cb.message, f"Salvata ugualmente - lista #{lid} ({item['kind']}).",
                kb.admin_menu())
    await cb.answer()


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
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_page(cid, 0)
    await _edit(cb.message, testo, kbd)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:bpg:"))
async def bulk_page(cb: CallbackQuery):
    _, _, cid, page = cb.data.split(":")
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_page(cid, int(page))
    await _edit(cb.message, testo, kbd)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:btg:"))
async def bulk_toggle(cb: CallbackQuery):
    _, _, cid, idx, page = cb.data.split(":")
    d = BULK.get(cid)
    if not d:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    i = int(idx)
    if i in d["sel"]:
        d["sel"].discard(i)
    else:
        d["sel"].add(i)
    testo, kbd = _bulk_page(cid, int(page))
    await _edit(cb.message, testo, kbd)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:ball:"))
async def bulk_back(cb: CallbackQuery):
    cid = cb.data.split(":")[2]
    if cid not in BULK:
        return await cb.answer("Riepilogo scaduto", show_alert=True)
    testo, kbd = _bulk_summary(cid)
    await _edit(cb.message, testo, kbd)
    await cb.answer()


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
    await _edit(cb.message, f"Salvo {len(scelte)} liste...")
    ids = []
    for it in scelte:
        lid = await db.add_list(it["kind"], it["payload"], it.get("label") or "-", cb.from_user.id)
        ids.append(lid)
    await db.log(cb.from_user.id, "bulk_add", str(len(ids)))
    await _edit(cb.message, f"Salvate {len(ids)} liste. Test in corso...")
    asyncio.create_task(_bulk_test(cb.message, ids))
    await cb.answer()


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
    txt = "\n".join(f"#{r['id']} - {r['kind']} - {r['label'] or '-'}" for r in rows)
    await msg.answer(f"Liste libere:\n\n{txt}", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data == "a:free")
async def cb_free(cb: CallbackQuery):
    await _edit(cb.message, "Quali liste libere vuoi vedere?", kb.free_kinds())
    await cb.answer()


async def _show_free(cb, kind, page):
    per_page = 5
    tot = await db.count_free_kind(kind)
    if not tot:
        await _edit(cb.message, "Nessuna lista libera di questo tipo.", kb.admin_menu())
        return
    inizio = page * per_page
    righe = await db.free_lists_kind(kind, inizio, per_page)
    sel = DSEL.setdefault(cb.from_user.id, set())
    blocchi, bottoni = [], []
    for n, r in enumerate(righe, inizio + 1):
        p = await db.payload_of(r)
        if r["kind"] == "xtream":
            corpo = (f"<b>#{n} XTREAM</b>\nServer: <code>{p['server']}</code>\n"
                     f"User: <code>{p['username']}</code>\nPass: <code>{p['password']}</code>\n"
                     f"M3U: <code>{p['server']}/get.php?username={p['username']}"
                     f"&password={p['password']}&type=m3u_plus</code>")
            etichetta = p.get("username") or "xtream"
        elif r["kind"] == "stalker":
            corpo = (f"<b>#{n} STALKER</b>\nPortal: <code>{p['portal']}</code>\n"
                     f"MAC: <code>{p['mac']}</code>")
            etichetta = p.get("mac") or "stalker"
        else:
            corpo = f"<b>#{n} M3U</b>\n<code>{p['url']}</code>"
            etichetta = p.get("url") or "m3u"
        blocchi.append(corpo)
        segno = "✅" if r["id"] in sel else "🗑"
        bottoni.append([InlineKeyboardButton(
            text=f"{segno} #{n} {etichetta[:24]}",
            callback_data=f"a:dtg:{r['id']}:{kind or 'all'}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"a:fp:{kind or 'all'}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{(tot - 1) // per_page + 1}",
                                    callback_data="a:noop"))
    if (page + 1) * per_page < tot:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"a:fp:{kind or 'all'}:{page + 1}"))
    coda = []
    if sel:
        coda.append([InlineKeyboardButton(text=f"🗑 ELIMINA SELEZIONATE ({len(sel)})",
                                          callback_data=f"a:ddel:{kind or 'all'}:{page}")])
    coda.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:free")])
    coda.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _edit(cb.message, "\n\n".join(blocchi),
                InlineKeyboardMarkup(inline_keyboard=[nav] + bottoni + coda))


@admin_router.callback_query(F.data.in_({"a:freem3u", "a:freextream", "a:freestalker", "a:freeall"}))
async def cb_free_kind(cb: CallbackQuery):
    kind = {"a:freem3u": "m3u", "a:freextream": "xtream",
            "a:freestalker": "stalker", "a:freeall": None}[cb.data]
    await _show_free(cb, kind, 0)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:fp:"))
async def cb_free_page(cb: CallbackQuery):
    _, _, k, page = cb.data.split(":")
    await _show_free(cb, None if k == "all" else k, int(page))
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:dtg:"))
async def cb_del_toggle(cb: CallbackQuery):
    _, _, lid, kind, page = cb.data.split(":")
    sel = DSEL.setdefault(cb.from_user.id, set())
    if int(lid) in sel:
        sel.discard(int(lid))
    else:
        sel.add(int(lid))
    await _show_free(cb, None if kind == "all" else kind, int(page))
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:ddel:"))
async def cb_del_selected(cb: CallbackQuery):
    _, _, kind, page = cb.data.split(":")
    sel = DSEL.get(cb.from_user.id, set())
    if not sel:
        return await cb.answer("Nessuna lista selezionata", show_alert=True)
    fatte = 0
    for lid in list(sel):
        if await db.del_list(lid):
            fatte += 1
    DSEL[cb.from_user.id] = set()
    await db.log(cb.from_user.id, "del_lists", str(fatte))
    await cb.answer(f"Eliminate {fatte}")
    await _edit(cb.message, f"🗑 Eliminate {fatte} liste.\n\nPannello:", kb.admin_menu())


@admin_router.message(Command("assegnazioni"))
async def hist_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await _hist(msg)


@admin_router.callback_query(F.data == "a:hist")
async def cb_hist(cb: CallbackQuery):
    await _hist(cb.message, edit=True)
    await cb.answer()


async def _hist(target, edit=False):
    rows = await db.assignments()
    if not rows:
        testo = "Nessuna assegnazione registrata."
        kbd = kb.nav("a:menu")
        if edit:
            return await _edit(target, testo, kbd)
        return await target.answer(testo, reply_markup=kbd)
    lines = []
    for r in rows:
        who = f"@{r['username']}" if r["username"] else str(r["user_id"])
        when = _ora(r["assigned_at"], "%d/%m/%y %H:%M")
        lines.append(f"#{r['id']} - lista #{r['list_id']} ({r['kind']}) -> {who} - {when} - "
                     f"{'revocata' if r['revoked'] else 'attiva'}")
    buttons = [[InlineKeyboardButton(text=f"Revoca #{r['id']}", callback_data=f"a:rev:{r['id']}")]
               for r in rows[:10]]
    buttons.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    buttons.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    testo = "Ultime assegnazioni:\n\n" + "\n".join(lines)
    kbd = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        return await _edit(target, testo, kbd)
    await target.answer(testo, reply_markup=kbd)


@admin_router.callback_query(F.data.startswith("a:rev:"))
async def revoke_cb(cb: CallbackQuery):
    row = await db.revoke(int(cb.data.split(":")[2]))
    await cb.answer("Revocata" if row else "Non trovata")
    if row:
        await db.log(cb.from_user.id, "revoca", str(row["id"]))
        await _hist(cb.message, edit=True)


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
            f"<b>{_nome(r)}</b>\n{_utente(r)}\nID: <code>{r['user_id']}</code>\n"
            f"bannato: {'si' if r['banned'] else 'no'} - prossima: "
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
    await db.ensure_user(uid)
    await db.ban(uid, "deciso da admin")
    await db.log(cb.from_user.id, "ban", str(uid))
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[kb.open_row(uid)], kb.user_row(uid, True),
                         [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")]]))
    await cb.answer("Bannato")


@admin_router.callback_query(F.data.startswith("u:unban:"))
async def cb_unban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await db.unban(uid)
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[kb.open_row(uid)], kb.user_row(uid, False),
                         [InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")]]))
    await cb.answer("Sbloccato")


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


PER_PAGE = 8


@admin_router.callback_query(F.data == "a:noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


async def _show_users_page(cb, page):
    tot, righe = await asyncio.gather(
        db.count_users(config.SUPER_ADMINS),
        db.all_users(page * PER_PAGE, PER_PAGE, config.SUPER_ADMINS))
    if not tot:
        await _edit(cb.message, "Nessun utente normale registrato.", kb.nav("a:menu"))
        return
    tastiera = []
    for r in righe:
        nome = _nome(r)[:10]
        stato = "🚫" if r["banned"] else "✅"
        link = _link(r["user_id"], r["username"])
        tastiera.append([
            InlineKeyboardButton(text=f"{stato} {nome} · {r['user_id']}", url=link),
            InlineKeyboardButton(text=_utente(r)[:14], url=link),
            InlineKeyboardButton(
                text=("Sblocca" if r["banned"] else "Banna"),
                callback_data=f"lb:{'unban' if r['banned'] else 'ban'}:{r['user_id']}:{page}"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"a:allusers:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{(tot - 1) // PER_PAGE + 1}",
                                    callback_data="a:noop"))
    if (page + 1) * PER_PAGE < tot:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"a:allusers:{page + 1}"))
    tastiera.append(nav)
    tastiera.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    tastiera.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _edit(cb.message, f"👤 Utenti normali: {tot}\nTocca nome o @utente per aprire la chat.",
                InlineKeyboardMarkup(inline_keyboard=tastiera))


@admin_router.callback_query(F.data.startswith("a:allusers"))
async def cb_allusers(cb: CallbackQuery):
    parti = cb.data.split(":")
    page = int(parti[2]) if len(parti) > 2 else 0
    await _show_users_page(cb, page)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("lb:"))
async def cb_list_ban(cb: CallbackQuery):
    _, azione, uid, page = cb.data.split(":")
    uid = int(uid)
    if azione == "ban":
        await db.ban(uid, "deciso da admin")
        await db.log(cb.from_user.id, "ban", str(uid))
        await _show_users_page(cb, int(page))
    else:
        await db.unban(uid)
        await _show_banned_page(cb, int(page))
    await cb.answer("Bannato" if azione == "ban" else "Sbloccato")


async def _show_banned_page(cb, page):
    tot, righe = await asyncio.gather(
        db.count_banned(),
        db.banned_users(page * PER_PAGE, PER_PAGE))
    if not tot:
        await _edit(cb.message, "Nessun utente bannato.", kb.nav("a:menu"))
        return
    tastiera = []
    for r in righe:
        nome = _nome(r)[:10]
        link = _link(r["user_id"], r["username"])
        tastiera.append([
            InlineKeyboardButton(text=f"🚫 {nome} · {r['user_id']}", url=link),
            InlineKeyboardButton(text=_utente(r)[:14], url=link),
            InlineKeyboardButton(text="Sblocca",
                                 callback_data=f"lb:unban:{r['user_id']}:{page}"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"a:banned:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{(tot - 1) // PER_PAGE + 1}",
                                    callback_data="a:noop"))
    if (page + 1) * PER_PAGE < tot:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"a:banned:{page + 1}"))
    tastiera.append(nav)
    tastiera.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    tastiera.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _edit(cb.message, f"🚫 Utenti bannati: {tot}",
                InlineKeyboardMarkup(inline_keyboard=tastiera))


@admin_router.callback_query(F.data.startswith("a:banned"))
async def cb_banned(cb: CallbackQuery):
    parti = cb.data.split(":")
    page = int(parti[2]) if len(parti) > 2 else 0
    await _show_banned_page(cb, page)
    await cb.answer()


async def _show_admins(cb):
    righe, nomi = await asyncio.gather(db.all_admins(), db.list_admin_names())
    proprietari = sorted(config.SUPER_ADMINS)
    altri = [r for r in righe if r["user_id"] not in config.SUPER_ADMINS]
    tastiera = []
    for uid in proprietari:
        tastiera.append([
            InlineKeyboardButton(text=f"👑 {uid}", url=f"tg://user?id={uid}"),
            InlineKeyboardButton(text="proprietario", callback_data="a:noop"),
        ])
    for r in altri:
        nome = (r["username"] or "senza username")[:12]
        tastiera.append([
            InlineKeyboardButton(text=f"🛡 {nome} · {r['user_id']}",
                                 url=_link(r["user_id"], r["username"])),
            InlineKeyboardButton(text="Rimuovi", callback_data=f"a:rmadmin:{r['user_id']}"),
        ])
    for r in nomi:
        nome = r["username"][:20]
        tastiera.append([
            InlineKeyboardButton(text=f"🛡 @{nome}", url=f"https://t.me/{nome}"),
            InlineKeyboardButton(text="Rimuovi", callback_data=f"a:rmname:{nome}"),
        ])
    tastiera.append([InlineKeyboardButton(text="⬅️ Indietro", callback_data="a:menu")])
    tastiera.append([InlineKeyboardButton(text="🏠 Menu", callback_data="a:menu")])
    await _edit(cb.message,
                f"👑 Proprietario: {len(proprietari)} · 🛡 Admin: {len(altri) + len(nomi)}",
                InlineKeyboardMarkup(inline_keyboard=tastiera))


@admin_router.callback_query(F.data == "a:alladmins")
async def cb_alladmins(cb: CallbackQuery):
    await _show_admins(cb)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:rmadmin:"))
async def cb_rmadmin(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await db.del_admin(uid)
    await db.clear_anon_by_user(uid)
    await db.log(cb.from_user.id, "admin_del", str(uid))
    await _show_admins(cb)
    await cb.answer("Rimosso")


@admin_router.callback_query(F.data.startswith("a:rmname:"))
async def cb_rmname(cb: CallbackQuery):
    nome = cb.data.split(":", 2)[2]
    await db.del_admin_name(nome)
    await db.log(cb.from_user.id, "admin_del_name", nome)
    await _show_admins(cb)
    await cb.answer("Rimosso")


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
    return kb.settings_kb(await db.get_int("cooldown_days"), await db.get_int("send_proof"),
                          await db.get_int("enabled"), await db.get_int("group_only"),
                          await db.get_setting("group_id"))


@admin_router.callback_query(F.data == "a:set")
async def cb_set(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit(cb.message, "Impostazioni", await _settings_kb())
    await cb.answer()


@admin_router.callback_query(F.data == "s:proof")
async def s_proof(cb: CallbackQuery):
    cur = await db.get_int("send_proof")
    await db.set_setting("send_proof", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())
    await cb.answer("Foto di prova " + ("spenta" if cur else "accesa"))


@admin_router.callback_query(F.data == "s:gonly")
async def s_gonly(cb: CallbackQuery):
    cur = await db.get_int("group_only")
    await db.set_setting("group_only", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())
    await cb.answer("Filtro gruppo " + ("spento" if cur else "acceso"))


@admin_router.callback_query(F.data == "s:toggle")
async def s_toggle(cb: CallbackQuery):
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=await _settings_kb())
    await cb.answer("Bot " + ("spento" if cur else "attivo"))


@admin_router.callback_query(F.data == "s:cool")
async def s_cool(cb: CallbackQuery, state: FSMContext):
    attuale = await db.get_int("cooldown_days")
    await state.set_state(Ask.wait)
    await state.update_data(action="cooldown")
    await _edit(cb.message, f"L'attesa è impostata a <b>{attuale} giorni</b>.\n\n"
                            f"Da quanti giorni la vuoi?" + HINT, kb.nav("a:set"))
    await cb.answer()


@admin_router.callback_query(F.data == "s:group")
async def s_group(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="setgroup")
    await _edit(cb.message, "Mandami l'ID del gruppo (inizia con -100). In alternativa scrivi "
                            "/setgroup dentro il gruppo." + HINT, kb.nav("a:set"))
    await cb.answer()


@admin_router.callback_query(F.data == "s:link")
async def s_link(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="setlink")
    await _edit(cb.message, "Mandami il link d'invito del gruppo (https://t.me/+...)." + HINT,
                kb.nav("a:set"))
    await cb.answer()


@admin_router.callback_query(F.data == "s:wel")
async def s_wel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.welcome)
    await _edit(cb.message, "Invia il nuovo testo di benvenuto. Puoi usare {name} e {days}." + HINT,
                kb.nav("a:set"))
    await cb.answer()


@admin_router.callback_query(F.data == "s:deny")
async def s_deny(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.deny)
    await _edit(cb.message, "Invia il nuovo testo di rifiuto. Puoi usare {when} e {left}." + HINT,
                kb.nav("a:set"))
    await cb.answer()


@admin_router.callback_query(F.data == "s:ntext")
async def s_ntext(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.not_member)
    await _edit(cb.message, "Invia il nuovo testo di blocco. Puoi usare {link}." + HINT,
                kb.nav("a:set"))
    await cb.answer()


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
        await cb.answer("Nessuna lista libera", show_alert=True)
        return
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    await cb.message.answer_document(FSInputFile(path))
    await cb.answer()


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
