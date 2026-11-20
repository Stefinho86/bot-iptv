import datetime as dt
import json
import uuid
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, FSInputFile,
                           InlineKeyboardMarkup, InlineKeyboardButton)

import db
import config
import utils as kb
from services import detect, preview, test_list
from utils import IsAdmin, Trigger, NotCommand, AnonUnverified

admin_router = Router()
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())

anon_router = Router()
user_router = Router()

BUSY = set()
TMP = {}

HINT = "\n\nScrivi /annulla per interrompere."


class Add(StatesGroup):
    data = State()
    confirm = State()


class SetTxt(StatesGroup):
    welcome = State()
    deny = State()
    not_member = State()


class Ask(StatesGroup):
    wait = State()


# ==================== AIUTI ====================
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


def _payload_text(kind, p):
    if kind == "xtream":
        return (f"Server: <code>{p['server']}</code>\nUtente: <code>{p['username']}</code>\n"
                f"Password: <code>{p['password']}</code>\n\n"
                f"M3U: <code>{p['server']}/get.php?username={p['username']}"
                f"&password={p['password']}&type=m3u_plus</code>")
    if kind == "stalker":
        return f"Portal: <code>{p['portal']}</code>\nMAC: <code>{p['mac']}</code>"
    return f"<code>{p['url']}</code>"


def _forwarded_uid(msg):
    if getattr(msg, "forward_from", None):
        return msg.forward_from.id
    fo = getattr(msg, "forward_origin", None)
    sender = getattr(fo, "sender_user", None) if fo else None
    return sender.id if sender else None


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
        return None


async def _target(msg, raw=None):
    uid = _forwarded_uid(msg)
    if uid:
        return uid
    return await resolve_uid(msg.bot, raw if raw is not None else (msg.text or ""))


async def _need_target(msg):
    await msg.answer("Non ho trovato questo utente. Mandami l'ID numerico, lo @username, "
                     "oppure inoltra un suo messaggio." + HINT)


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
            f"Bot: {'attivo' if en else 'spento'}\nTu: {tu}")


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
        await target.edit_text(testo, reply_markup=kbd)
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
    if not await db.is_admin(msg.from_user.id):
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
        s = await db.stats()
        return await msg.answer(
            f"Pannello admin\n\nLibere: {s['free']} | Assegnate: {s['assigned']} | "
            f"Rotte: {s['broken']} | Utenti: {s['users']} | Bannati: {s['banned']}",
            reply_markup=kb.admin_menu())
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
                     f"{a['expires_at'].astimezone():%d/%m/%Y %H:%M} (tra {_human(left)})")


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
        if u and u["next_available"]:
            nxt = u["next_available"]
            if dt.datetime.now(dt.timezone.utc) < nxt:
                left = nxt - dt.datetime.now(dt.timezone.utc)
                txt = (await db.get_setting("deny_text")).format(
                    when=nxt.astimezone().strftime("%d/%m/%Y alle %H:%M"), left=_human(left))
                return await msg.answer(txt)

        if await db.free_count() == 0:
            return await msg.answer(await db.get_setting("no_lists"))

        chosen = payload = shot = None
        for _ in range(await db.get_int("max_attempts")):
            row = await db.random_free()
            if not row:
                break
            p = await db.payload_of(row)
            res = await test_list(row["kind"], p)
            await db.set_test(row["id"], res.ok, res.note)
            if res.ok:
                chosen, payload, shot = row, p, res.frame
                break
            await db.mark_broken(row["id"])

        if not chosen:
            return await msg.answer(await db.get_setting("broken_all"))

        exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
        await db.assign(chosen["id"], uid, exp)
        await db.set_next(uid, exp)
        await db.log(uid, "consegna", f"lista {chosen['id']}")

        caption = (f"<b>La tua lista IPTV</b> (#{chosen['id']})\n\n"
                   f"{_payload_text(chosen['kind'], payload)}\n\n"
                   f"Prossima lista: <b>{exp.astimezone():%d/%m/%Y %H:%M}</b>")
        if shot and await db.get_int("send_proof") == 1:
            await msg.answer_photo(FSInputFile(shot), caption=caption)
            shot.unlink(missing_ok=True)
        else:
            await msg.answer(caption + "\n\n(test superato)")
    finally:
        BUSY.discard(uid)


# ==================== PANNELLO ====================
@admin_router.message(Command("admin"))
async def panel(msg: Message, state: FSMContext):
    await state.clear()
    s = await db.stats()
    await msg.answer(f"Pannello admin\n\nLibere: {s['free']} | Assegnate: {s['assigned']} | "
                     f"Rotte: {s['broken']} | Utenti: {s['users']} | Bannati: {s['banned']}",
                     reply_markup=kb.admin_menu())


@admin_router.message(Command("stato"))
async def stato_cmd(msg: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await msg.answer(await _stato_text(msg, bot), reply_markup=kb.back())


@admin_router.callback_query(F.data == "a:stato")
async def cb_stato(cb: CallbackQuery, bot: Bot):
    await cb.message.answer(await _stato_text(cb.message, bot), reply_markup=kb.back())
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
        "/annulla - interrompi\n\n"
        "Tutti accettano ID, @username o inoltro.",
        reply_markup=kb.back())


@admin_router.message(Command("verifica"))
async def verifica_cmd(msg: Message, bot: Bot):
    await _ask_verify(msg, bot)


@admin_router.message(Command("deverifica"))
async def deverifica_cmd(msg: Message):
    await db.clear_anon_verified(msg.chat.id)
    await msg.answer("Verifica revocata per questa chat.", reply_markup=kb.back())


@admin_router.callback_query(F.data == "a:verify")
async def cb_verify(cb: CallbackQuery, bot: Bot):
    await _ask_verify(cb.message, bot, edit=True)
    await cb.answer()


@admin_router.callback_query(F.data == "a:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Pannello admin:", reply_markup=kb.admin_menu())
    await cb.answer()


@admin_router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery):
    s = await db.stats()
    await cb.message.edit_text(
        f"Libere: {s['free']}\nAssegnate: {s['assigned']}\nRotte: {s['broken']}\n"
        f"Utenti: {s['users']}\nBannati: {s['banned']}", reply_markup=kb.back())
    await cb.answer()


@admin_router.callback_query(F.data == "a:comandi")
async def cb_comandi(cb: CallbackQuery, state: FSMContext):
    await comandi_cmd(cb.message, state)
    await cb.answer()


@admin_router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="utenti")
    await cb.message.edit_text("Chi vuoi cercare? Mandami ID o @username." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "a:revoca")
async def cb_revoca(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="revoca")
    await cb.message.edit_text("Quale assegnazione vuoi revocare? Mandami il numero "
                            "(lo trovi in Assegnazioni)." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "a:admin_add")
async def cb_admin_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_add")
    await cb.message.edit_text("Chi vuoi aggiungere come amministratore? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "a:admin_del")
async def cb_admin_del(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_del")
    await cb.message.edit_text("Chi vuoi rimuovere dagli amministratori? Mandami ID, @username "
                            "o inoltra un suo messaggio." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "a:toggle")
async def cb_toggle(cb: CallbackQuery):
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await cb.message.edit_text(f"Bot {'SPENTO' if cur else 'ATTIVO'}.", reply_markup=kb.admin_menu())
    await cb.answer()


# ==================== INSERIMENTO LISTE ====================
@admin_router.message(Command("aggiungi"))
async def add_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Che tipo di lista vuoi inserire?", reply_markup=kb.kinds())


@admin_router.callback_query(F.data == "a:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Che tipo di lista vuoi inserire?", reply_markup=kb.kinds())
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:k:"))
async def cb_kind(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split(":")[2]
    await state.set_state(Add.data)
    await state.update_data(kind=kind)
    hint = {
        "m3u": "Mandami l'URL della playlist M3U." + HINT,
        "xtream": "Mandami server, utente e password, anche in un testo unico." + HINT,
        "stalker": "Mandami indirizzo del portal e MAC." + HINT,
    }[kind]
    await cb.message.edit_text(hint, reply_markup=kb.back())
    await cb.answer()


async def _ask_confirm(target, state: FSMContext, item):
    cid = uuid.uuid4().hex[:10]
    TMP[cid] = item
    await state.set_state(Add.confirm)
    text = f"Confermi questo inserimento?\n\n{preview(item)}"
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb.confirm(cid))
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb.confirm(cid))


@admin_router.message(Add.data, F.text | F.photo)
async def add_data(msg: Message, state: FSMContext):
    text = msg.text or msg.caption or ""
    kind = (await state.get_data())["kind"]
    item = detect(text)
    if item and item["kind"] != kind:
        item = None
    if not item:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if kind == "m3u" and text.startswith("http"):
            item = {"kind": "m3u", "label": text[:40], "payload": {"url": text.strip()}}
        elif kind == "xtream" and len(lines) >= 3:
            item = {"kind": "xtream", "label": lines[0],
                    "payload": {"server": lines[0], "username": lines[1], "password": lines[2]}}
        elif kind == "stalker" and len(lines) >= 2:
            item = {"kind": "stalker", "label": lines[0],
                    "payload": {"portal": lines[0], "mac": lines[1].upper()}}
    if not item:
        return await msg.answer("Non ho riconosciuto i dati. Riprova o scrivi /annulla.")
    await _ask_confirm(msg, state, item)


@admin_router.callback_query(F.data.startswith("a:ok:"))
async def add_confirm(cb: CallbackQuery, state: FSMContext):
    item = TMP.pop(cb.data.split(":")[2], None)
    await state.clear()
    if not item:
        return await cb.answer("Anteprima scaduta, rifai l'inserimento", show_alert=True)
    await cb.message.edit_text("Sto testando la lista, attendi...")
    res = await test_list(item["kind"], item["payload"])
    lid = await db.add_list(item["kind"], item["payload"], item.get("label") or "-", cb.from_user.id)
    await db.set_test(lid, res.ok, res.note)
    if not res.ok:
        await db.mark_broken(lid)
    await db.log(cb.from_user.id, "add", f"{lid} {item['kind']} ok={res.ok}")
    await cb.message.edit_text(
        f"{'Fatto' if res.ok else 'Salvata ma rotta'} - lista #{lid} ({item['kind']}).\n"
        f"Test: {'superato' if res.ok else 'fallito: ' + res.note}", reply_markup=kb.back())
    if res.frame:
        res.frame.unlink(missing_ok=True)
    await cb.answer()


@admin_router.callback_query(F.data == "a:cancel")
async def add_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Annullato.", reply_markup=kb.back())
    await cb.answer()


@admin_router.message(Command("annulla"))
async def cancel_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Operazione annullata.")


# ==================== LISTE E STORICO ====================
@admin_router.message(Command("liberi"))
async def free_cmd(msg: Message, state: FSMContext):
    await state.clear()
    rows = await db.free_lists()
    if not rows:
        return await msg.answer("Nessuna lista libera. Inseriscine una con /aggiungi.",
                                reply_markup=kb.back())
    txt = "\n".join(f"#{r['id']} - {r['kind']} - {r['label'] or '-'}" for r in rows)
    await msg.answer(f"Liste libere:\n\n{txt}", reply_markup=kb.back())


@admin_router.callback_query(F.data == "a:free")
async def cb_free(cb: CallbackQuery):
    rows = await db.free_lists()
    txt = ("Nessuna lista libera." if not rows else
           "Liste libere:\n\n" + "\n".join(
               f"#{r['id']} - {r['kind']} - {r['label'] or '-'}" for r in rows))
    await cb.message.edit_text(txt, reply_markup=kb.back())
    await cb.answer()


@admin_router.message(Command("assegnazioni"))
async def hist_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await _hist(msg)


@admin_router.callback_query(F.data == "a:hist")
async def cb_hist(cb: CallbackQuery):
    await _hist(cb.message)
    await cb.answer()


async def _hist(target):
    rows = await db.assignments()
    if not rows:
        return await target.answer("Nessuna assegnazione registrata.", reply_markup=kb.back())
    lines = []
    for r in rows:
        who = f"@{r['username']}" if r["username"] else str(r["user_id"])
        when = r["assigned_at"].astimezone().strftime("%d/%m/%y %H:%M")
        lines.append(f"#{r['id']} - lista #{r['list_id']} ({r['kind']}) -> {who} - {when} - "
                     f"{'revocata' if r['revoked'] else 'attiva'}")
    buttons = [[InlineKeyboardButton(text=f"Revoca #{r['id']}", callback_data=f"a:rev:{r['id']}")]
               for r in rows[:10]]
    buttons.append([InlineKeyboardButton(text="Indietro", callback_data="a:menu")])
    await target.answer("Ultime assegnazioni:\n\n" + "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_router.callback_query(F.data.startswith("a:rev:"))
async def revoke_cb(cb: CallbackQuery):
    row = await db.revoke(int(cb.data.split(":")[2]))
    await cb.answer("Revocata" if row else "Non trovata")
    if row:
        await db.log(cb.from_user.id, "revoca", str(row["id"]))
        await _hist(cb.message)


@admin_router.message(Command("revoca"))
async def revoca_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        row = await db.revoke(int(p[1])) if p[1].isdigit() else None
        return await msg.answer("Revocata." if row else "ID assegnazione non valido.",
                                reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="revoca")
    await msg.answer("Quale assegnazione vuoi revocare? Mandami il numero "
                     "(lo trovi con /assegnazioni)." + HINT)


# ==================== UTENTI ====================
@admin_router.message(Command("utenti"))
async def users_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        return await _show_users(msg, p[1])
    await state.set_state(Ask.wait)
    await state.update_data(action="utenti")
    await msg.answer("Chi vuoi cercare? Mandami ID o @username." + HINT)


async def _show_users(msg, q):
    rows = await db.find_users(q)
    if not rows:
        return await msg.answer("Nessun utente trovato.", reply_markup=kb.back())
    for r in rows:
        await msg.answer(f"{r['full_name'] or '-'} @{r['username'] or '-'} - <code>{r['user_id']}</code>\n"
                         f"bannato: {'si' if r['banned'] else 'no'} - prossima: {r['next_available'] or '-'}")
        await msg.answer("Azioni:", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[kb.user_row(r["user_id"], bool(r["banned"]))]))


@admin_router.callback_query(F.data.startswith("u:ban:"))
async def cb_ban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await db.ensure_user(uid)
    await db.ban(uid, "deciso da admin")
    await db.log(cb.from_user.id, "ban", str(uid))
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[kb.user_row(uid, True)]))
    await cb.answer("Bannato")


@admin_router.callback_query(F.data.startswith("u:unban:"))
async def cb_unban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
    await db.unban(uid)
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[kb.user_row(uid, False)]))
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
        return await msg.answer(f"Utente {uid} bannato.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="ban")
    await msg.answer("Chi vuoi bannare? Mandami ID, @username o inoltra un suo messaggio." + HINT)


@admin_router.message(Command("unban"))
async def unban_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.unban(uid)
        return await msg.answer(f"Utente {uid} sbloccato.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="unban")
    await msg.answer("Chi vuoi sbloccare? Mandami ID, @username o inoltra un suo messaggio." + HINT)


@admin_router.message(Command("reset"))
async def reset_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.clear_next(uid)
        return await msg.answer(f"Attesa azzerata per {uid}.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="reset")
    await msg.answer("A chi vuoi azzerare l'attesa? Mandami ID, @username "
                     "o inoltra un suo messaggio." + HINT)


# ==================== AMMINISTRATORI ====================
@admin_router.message(Command("admin_add"))
async def admin_add(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.add_admin(uid, None, msg.from_user.id)
        await db.log(msg.from_user.id, "admin_add", str(uid))
        return await msg.answer(f"{uid} ora è amministratore.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_add")
    await msg.answer("Chi vuoi aggiungere come amministratore? Mandami ID, @username "
                     "o inoltra un suo messaggio." + HINT)


@admin_router.message(Command("admin_del"))
async def admin_del(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        uid = await resolve_uid(msg.bot, p[1])
        if not uid:
            return await _need_target(msg)
        await db.del_admin(uid)
        await db.clear_anon_by_user(uid)
        await db.log(msg.from_user.id, "admin_del", str(uid))
        return await msg.answer(f"{uid} non è più amministratore.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="admin_del")
    await msg.answer("Chi vuoi rimuovere dagli amministratori? Mandami ID, @username "
                     "o inoltra un suo messaggio." + HINT)


@admin_router.callback_query(F.data == "a:admins")
async def cb_admins(cb: CallbackQuery):
    rows = await db.list_admins()
    body = "\n".join(f"<code>{r['user_id']}</code> @{r['username']}" for r in rows) or "nessun admin aggiuntivo"
    await cb.message.edit_text(f"Admin:\n\n{body}", reply_markup=kb.back())
    await cb.answer()

PER_PAGE = 8


@admin_router.callback_query(F.data == "a:noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


async def _show_users_page(cb, page):
    tot = await db.count_users(config.SUPER_ADMINS)
    righe = await db.all_users(page * PER_PAGE, PER_PAGE, config.SUPER_ADMINS)
    if not tot:
        await cb.message.edit_text("Nessun utente registrato.", reply_markup=kb.back())
        return
    tastiera = []
    for r in righe:
        nome = (r["full_name"] or r["username"] or str(r["user_id"]))[:26]
        stato = "🚫" if r["banned"] else "✅"
        tastiera.append([
            InlineKeyboardButton(text=f"{stato} {nome}", callback_data="a:noop"),
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
    tastiera.append([InlineKeyboardButton(text="⬅️ Menu", callback_data="a:menu")])
    await cb.message.edit_text(f"Utenti registrati: {tot}",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=tastiera))


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
    await db.ensure_user(uid)
    if azione == "ban":
        await db.ban(uid, "deciso da admin")
        await db.log(cb.from_user.id, "ban", str(uid))
    else:
        await db.unban(uid)
    if azione == "ban":
        await _show_users_page(cb, int(page))
    else:
        await _show_banned_page(cb, int(page))
    await cb.answer("Bannato" if azione == "ban" else "Sbloccato")

async def _show_banned_page(cb, page):
    tot = await db.count_banned()
    righe = await db.banned_users(page * PER_PAGE, PER_PAGE)
    if not tot:
        await cb.message.edit_text("Nessun utente bannato.", reply_markup=kb.back())
        return
    tastiera = []
    for r in righe:
        nome = (r["full_name"] or r["username"] or str(r["user_id"]))[:24]
        tastiera.append([
            InlineKeyboardButton(text=f"🚫 {nome}", callback_data="a:noop"),
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
    tastiera.append([InlineKeyboardButton(text="⬅️ Menu", callback_data="a:menu")])
    await cb.message.edit_text(f"Utenti bannati: {tot}",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=tastiera))

@admin_router.callback_query(F.data.startswith("a:banned"))
async def cb_banned(cb: CallbackQuery):
    parti = cb.data.split(":")
    page = int(parti[2]) if len(parti) > 2 else 0
    await _show_banned_page(cb, page)
    await cb.answer()

async def _show_admins(cb):
    righe = await db.all_admins()
    if not righe:
        await cb.message.edit_text("Nessun admin aggiuntivo, solo i super-admin.",
                                   reply_markup=kb.back())
        return
    tastiera = []
    for r in righe:
        nome = (r["username"] or str(r["user_id"]))[:24]
        tastiera.append([
            InlineKeyboardButton(text=f"🛡 {nome}", callback_data="a:noop"),
            InlineKeyboardButton(text="Rimuovi", callback_data=f"a:rmadmin:{r['user_id']}"),
        ])
    tastiera.append([InlineKeyboardButton(text="⬅️ Menu", callback_data="a:menu")])
    await cb.message.edit_text(f"Amministratori: {len(righe)}",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=tastiera))


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


# ==================== IMPOSTAZIONI ====================
@admin_router.message(Command("cooldown"))
async def cooldown_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        if not p[1].isdigit():
            return await msg.answer("Serve un numero di giorni, per esempio /cooldown 7")
        await db.set_setting("cooldown_days", p[1])
        return await msg.answer(f"Attesa impostata a {p[1]} giorni.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="cooldown")
    await msg.answer("Da quanti giorni vuoi che sia l'attesa tra una lista e l'altra? "
                     "Mandami un numero." + HINT)


@admin_router.message(Command("setgroup"))
async def setgroup_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        gid = p[1].strip()
        await db.set_setting("group_id", gid)
        return await msg.answer(f"Gruppo impostato: <code>{gid}</code>", reply_markup=kb.back())
    if msg.chat.type != "private":
        await db.set_setting("group_id", str(msg.chat.id))
        return await msg.answer(f"Gruppo impostato: <code>{msg.chat.id}</code>\n"
                                f"Ora solo i membri possono usare il bot.")
    await state.set_state(Ask.wait)
    await state.update_data(action="setgroup")
    await msg.answer("Mandami l'ID del gruppo (inizia con -100). In alternativa scrivi "
                     "/setgroup dentro il gruppo." + HINT)


@admin_router.message(Command("setlink"))
async def setlink_cmd(msg: Message, state: FSMContext):
    await state.clear()
    p = msg.text.split(maxsplit=1)
    if len(p) > 1:
        await db.set_setting("group_link", p[1].strip())
        return await msg.answer("Link salvato.", reply_markup=kb.back())
    await state.set_state(Ask.wait)
    await state.update_data(action="setlink")
    await msg.answer("Mandami il link d'invito del gruppo (https://t.me/+...)." + HINT)


@admin_router.message(Command("toggle"))
async def toggle_cmd(msg: Message, state: FSMContext):
    await state.clear()
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await msg.answer("Bot spento. Nessuno riceverà liste." if cur else "Bot attivo.",
                     reply_markup=kb.back())


async def _settings_kb():
    return kb.settings_kb(await db.get_int("cooldown_days"), await db.get_int("send_proof"),
                          await db.get_int("enabled"), await db.get_int("group_only"),
                          await db.get_setting("group_id"))


@admin_router.callback_query(F.data == "a:set")
async def cb_set(cb: CallbackQuery):
    await cb.message.edit_text("Impostazioni", reply_markup=await _settings_kb())
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
    await state.set_state(Ask.wait)
    await state.update_data(action="cooldown")
    await cb.message.edit_text("Da quanti giorni vuoi che sia l'attesa tra una lista e l'altra?" + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "s:group")
async def s_group(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="setgroup")
    await cb.message.edit_text("Mandami l'ID del gruppo (inizia con -100). In alternativa scrivi "
                            "/setgroup dentro il gruppo." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "s:link")
async def s_link(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.wait)
    await state.update_data(action="setlink")
    await cb.message.edit_text("Mandami il link d'invito del gruppo (https://t.me/+...)." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "s:wel")
async def s_wel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.welcome)
    await cb.message.edit_text("Invia il nuovo testo di benvenuto. Puoi usare {name} e {days}." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "s:deny")
async def s_deny(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.deny)
    await cb.message.edit_text("Invia il nuovo testo di rifiuto. Puoi usare {when} e {left}." + HINT)
    await cb.answer()


@admin_router.callback_query(F.data == "s:ntext")
async def s_ntext(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.not_member)
    await cb.message.edit_text("Invia il nuovo testo di blocco. Puoi usare {link}." + HINT)
    await cb.answer()


@admin_router.message(SetTxt.welcome)
async def set_wel(msg: Message, state: FSMContext):
    await db.set_setting("welcome", msg.html_text)
    await state.clear()
    await msg.answer("Testo di benvenuto aggiornato.", reply_markup=kb.back())


@admin_router.message(SetTxt.deny)
async def set_deny(msg: Message, state: FSMContext):
    await db.set_setting("deny_text", msg.html_text)
    await state.clear()
    await msg.answer("Testo di rifiuto aggiornato.", reply_markup=kb.back())


@admin_router.message(SetTxt.not_member)
async def set_ntext(msg: Message, state: FSMContext):
    await db.set_setting("not_member", msg.html_text)
    await state.clear()
    await msg.answer("Testo di blocco aggiornato.", reply_markup=kb.back())


# ==================== ESPORTA ====================
@admin_router.message(Command("esporta"))
async def export_cmd(msg: Message, state: FSMContext):
    await state.clear()
    data = await db.all_free_payloads()
    if not data:
        return await msg.answer("Nessuna lista libera da esportare.", reply_markup=kb.back())
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    await msg.answer_document(FSInputFile(path))


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
            return await msg.answer("Serve un numero. Riprova o scrivi /annulla.")
        await db.set_setting("cooldown_days", raw.strip())
        await state.clear()
        return await msg.answer(f"Attesa impostata a {raw.strip()} giorni.", reply_markup=kb.back())

    if action == "setlink":
        if not raw.strip().startswith("http"):
            return await msg.answer("Serve un link che inizi con http. Riprova o scrivi /annulla.")
        await db.set_setting("group_link", raw.strip())
        await state.clear()
        return await msg.answer("Link salvato.", reply_markup=kb.back())

    if action == "setgroup":
        await db.set_setting("group_id", raw.strip())
        await state.clear()
        return await msg.answer(f"Gruppo impostato: <code>{raw.strip()}</code>", reply_markup=kb.back())

    if action == "utenti":
        await state.clear()
        return await _show_users(msg, raw.strip())

    if action == "revoca":
        if not raw.strip().isdigit():
            return await msg.answer("Serve il numero dell'assegnazione. Riprova o scrivi /annulla.")
        row = await db.revoke(int(raw.strip()))
        await state.clear()
        return await msg.answer("Assegnazione revocata." if row else "Assegnazione non trovata.",
                                reply_markup=kb.back())

    uid = await _target(msg, raw)
    if not uid:
        return await _need_target(msg)
    await db.ensure_user(uid)

    if action == "admin_add":
        await db.add_admin(uid, None, msg.from_user.id)
        await db.log(msg.from_user.id, "admin_add", str(uid))
        testo = f"{uid} ora è amministratore."
    elif action == "admin_del":
        await db.del_admin(uid)
        await db.clear_anon_by_user(uid)
        await db.log(msg.from_user.id, "admin_del", str(uid))
        testo = f"{uid} non è più amministratore."
    elif action == "ban":
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
        return await msg.answer("Azione sconosciuta, riprova.")

    await state.clear()
    await msg.answer(testo, reply_markup=kb.back())


# ==================== PARACADUTE ====================
@admin_router.callback_query(F.data.startswith("a:"))
async def cb_fallback_a(cb: CallbackQuery):
    await cb.answer("Azione non disponibile, riapri il pannello con /admin")


@admin_router.callback_query(F.data.startswith("s:"))
async def cb_fallback_s(cb: CallbackQuery):
    await cb.answer("Azione non disponibile, riapri le impostazioni con /admin")
