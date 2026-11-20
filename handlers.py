import datetime as dt
import json
import uuid
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, FSInputFile,
                           InlineKeyboardMarkup, InlineKeyboardButton)

import db
import utils as kb
from services import detect, preview, test_list
from utils import IsAdmin, Trigger

admin_router = Router()
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())

user_router = Router()

BUSY = set()
TMP = {}


class Add(StatesGroup):
    data = State()
    confirm = State()


class SetTxt(StatesGroup):
    welcome = State()
    deny = State()


# ==================== UTENTE ====================
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


async def _welcome(msg):
    days = await db.get_int("cooldown_days")
    txt = (await db.get_setting("welcome")).format(
        name=msg.from_user.first_name or "utente", days=days)
    await msg.answer(txt, reply_markup=kb.user_kb(days))


@user_router.message(Command("start"))
async def start(msg: Message):
    await db.touch_user(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    if await db.is_admin(msg.from_user.id):
        await msg.answer("Pannello admin:", reply_markup=kb.admin_menu())
    await _welcome(msg)


@user_router.message(Command("mio"))
async def mine(msg: Message):
    a = await db.last_assignment(msg.from_user.id)
    if not a:
        return await msg.answer("Non hai liste attive.")
    left = a["expires_at"] - dt.datetime.now(dt.timezone.utc)
    if left.total_seconds() <= 0:
        return await msg.answer(f"Lista #{a['list_id']} scaduta.")
    await msg.answer(f"Lista #{a['list_id']} ({a['kind']}), scade "
                     f"{a['expires_at'].astimezone():%d/%m/%Y %H:%M} (tra {_human(left)})")


@user_router.callback_query(F.data == "u:get")
async def cb_get(cb: CallbackQuery):
    await cb.answer()
    await deliver(cb.message, cb.from_user)


@user_router.message(Trigger(), Command("lista"))
async def cmd_lista(msg: Message):
    await deliver(msg, msg.from_user)


@user_router.message(Trigger(), F.text)
async def mention_any(msg: Message):
    await deliver(msg, msg.from_user)


async def deliver(msg, user):
    uid = user.id
    if await db.get_int("enabled") == 0:
        return await msg.answer("Bot momentaneamente spento.")
    if uid in BUSY:
        return await msg.answer("Sto gia preparando la tua lista, attendi.")
    BUSY.add(uid)
    try:
        await db.touch_user(uid, user.username, user.full_name)
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


# ==================== ADMIN ====================
@admin_router.message(Command("admin"))
async def panel(msg: Message):
    s = await db.stats()
    await msg.answer(f"Pannello admin\n\nLibere: {s['free']} | Assegnate: {s['assigned']} | "
                     f"Rotte: {s['broken']} | Utenti: {s['users']} | Bannati: {s['banned']}",
                     reply_markup=kb.admin_menu())


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


@admin_router.message(Command("aggiungi"))
async def add_cmd(msg: Message):
    await msg.answer("Che tipo di lista?", reply_markup=kb.kinds())


@admin_router.callback_query(F.data == "a:add")
async def cb_add(cb: CallbackQuery):
    await cb.message.edit_text("Che tipo di lista?", reply_markup=kb.kinds())
    await cb.answer()


@admin_router.callback_query(F.data.startswith("a:k:"))
async def cb_kind(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split(":")[2]
    await state.set_state(Add.data)
    await state.update_data(kind=kind)
    hint = {
        "m3u": "Mandami l'URL della playlist M3U.",
        "xtream": "Mandami server, utente e password (anche in un testo unico).",
        "stalker": "Mandami indirizzo del portal e MAC.",
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
        return await cb.answer("Scaduto", show_alert=True)
    await cb.message.edit_text("Sto testando la lista, attendi...")
    res = await test_list(item["kind"], item["payload"])
    lid = await db.add_list(item["kind"], item["payload"], item.get("label") or "-", cb.from_user.id)
    await db.set_test(lid, res.ok, res.note)
    if not res.ok:
        await db.mark_broken(lid)
    await db.log(cb.from_user.id, "add", f"{lid} {item['kind']} ok={res.ok}")
    await cb.message.edit_text(
        f"{'OK' if res.ok else 'ATTENZIONE'} - lista #{lid} salvata.\n"
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


@admin_router.message(StateFilter(None), F.chat.type == "private", F.text | F.photo)
async def freeform(msg: Message, state: FSMContext):
    text = msg.text or msg.caption or ""
    if text.startswith("/"):
        return
    item = detect(text)
    if item:
        await _ask_confirm(msg, state, item)


@admin_router.message(Command("liberi"))
async def free_cmd(msg: Message):
    rows = await db.free_lists()
    if not rows:
        return await msg.answer("Nessuna lista libera.")
    txt = "\n".join(f"#{r['id']} - {r['kind']} - {r['label'] or '-'}" for r in rows)
    await msg.answer(f"Liste libere:\n\n{txt}")


@admin_router.callback_query(F.data == "a:free")
async def cb_free(cb: CallbackQuery):
    rows = await db.free_lists()
    txt = ("Nessuna lista libera." if not rows else
           "Liste libere:\n\n" + "\n".join(
               f"#{r['id']} - {r['kind']} - {r['label'] or '-'}" for r in rows))
    await cb.message.edit_text(txt, reply_markup=kb.back())
    await cb.answer()


@admin_router.message(Command("assegnazioni"))
async def hist_cmd(msg: Message):
    await _hist(msg)


@admin_router.callback_query(F.data == "a:hist")
async def cb_hist(cb: CallbackQuery):
    await _hist(cb.message)
    await cb.answer()


async def _hist(target):
    rows = await db.assignments()
    if not rows:
        return await target.answer("Nessuna assegnazione.")
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
async def revoke(cb: CallbackQuery):
    row = await db.revoke(int(cb.data.split(":")[2]))
    await cb.answer("Revocata" if row else "Non trovata")
    if row:
        await db.log(cb.from_user.id, "revoca", str(row["id"]))
        await _hist(cb.message)


@admin_router.message(Command("utenti"))
async def users_cmd(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.answer("Uso: /utenti IDoppureusername")
    rows = await db.find_users(parts[1])
    if not rows:
        return await msg.answer("Nessun utente.")
    for r in rows:
        await msg.answer(f"{r['full_name']} @{r['username']} - <code>{r['user_id']}</code>\n"
                         f"bannato: {'si' if r['banned'] else 'no'} - prossima: {r['next_available'] or '-'}")
        await msg.answer("Azioni:", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[kb.user_row(r["user_id"], bool(r["banned"]))]))


@admin_router.callback_query(F.data.startswith("u:ban:"))
async def cb_ban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[2])
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
async def ban_cmd(msg: Message):
    p = msg.text.split(maxsplit=2)
    if len(p) < 2:
        return await msg.answer("Uso: /ban ID [motivo]")
    await db.ban(int(p[1]), p[2] if len(p) > 2 else "deciso da admin")
    await db.log(msg.from_user.id, "ban", p[1])
    await msg.answer(f"{p[1]} bannato.")


@admin_router.message(Command("unban"))
async def unban_cmd(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        return await msg.answer("Uso: /unban ID")
    await db.unban(int(p[1]))
    await msg.answer("Sbloccato.")


@admin_router.message(Command("reset"))
async def reset_cmd(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        return await msg.answer("Uso: /reset ID")
    await db.clear_next(int(p[1]))
    await msg.answer("Cooldown azzerato.")


@admin_router.message(Command("admin_add"))
async def admin_add(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        return await msg.answer("Uso: /admin_add ID")
    await db.add_admin(int(p[1]), None, msg.from_user.id)
    await msg.answer(f"{p[1]} ora e admin.")


@admin_router.message(Command("admin_del"))
async def admin_del(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        return await msg.answer("Uso: /admin_del ID")
    await db.del_admin(int(p[1]))
    await msg.answer(f"{p[1]} rimosso dagli admin.")


@admin_router.callback_query(F.data == "a:admins")
async def cb_admins(cb: CallbackQuery):
    rows = await db.list_admins()
    body = "\n".join(f"<code>{r['user_id']}</code> @{r['username']}" for r in rows) or "solo super-admin"
    await cb.message.edit_text(f"Admin:\n\n{body}\n\nComandi: /admin_add ID - /admin_del ID",
                               reply_markup=kb.back())
    await cb.answer()


@admin_router.message(Command("cooldown"))
async def cooldown_cmd(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2 or not p[1].isdigit():
        return await msg.answer("Uso: /cooldown giorni")
    await db.set_setting("cooldown_days", p[1])
    await msg.answer(f"Cooldown impostato a {p[1]} giorni.")


@admin_router.message(Command("setgroup"))
async def setgroup_cmd(msg: Message):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        return await msg.answer("Uso: /setgroup IDGRUPPO")
    await db.set_setting("group_id", p[1])
    await msg.answer("Gruppo impostato.")


@admin_router.message(Command("toggle"))
async def toggle_cmd(msg: Message):
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await msg.answer("Bot spento." if cur else "Bot attivo.")


@admin_router.callback_query(F.data == "a:set")
async def cb_set(cb: CallbackQuery):
    await cb.message.edit_text("Impostazioni", reply_markup=kb.settings_kb(
        await db.get_int("cooldown_days"), await db.get_int("send_proof"), await db.get_int("enabled")))
    await cb.answer()


@admin_router.callback_query(F.data == "s:proof")
async def s_proof(cb: CallbackQuery):
    cur = await db.get_int("send_proof")
    await db.set_setting("send_proof", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=kb.settings_kb(
        await db.get_int("cooldown_days"), 0 if cur else 1, await db.get_int("enabled")))
    await cb.answer()


@admin_router.callback_query(F.data == "s:toggle")
async def s_toggle(cb: CallbackQuery):
    cur = await db.get_int("enabled")
    await db.set_setting("enabled", "0" if cur else "1")
    await cb.message.edit_reply_markup(reply_markup=kb.settings_kb(
        await db.get_int("cooldown_days"), await db.get_int("send_proof"), 0 if cur else 1))
    await cb.answer()


@admin_router.callback_query(F.data == "s:cool")
async def s_cool(cb: CallbackQuery):
    await cb.message.answer("Scrivi: /cooldown numero")
    await cb.answer()


@admin_router.callback_query(F.data == "s:wel")
async def s_wel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.welcome)
    await cb.message.answer("Invia il nuovo testo di benvenuto. Puoi usare {name} e {days}")
    await cb.answer()


@admin_router.callback_query(F.data == "s:deny")
async def s_deny(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SetTxt.deny)
    await cb.message.answer("Invia il nuovo testo di rifiuto. Puoi usare {when} e {left}")
    await cb.answer()


@admin_router.message(SetTxt.welcome)
async def set_wel(msg: Message, state: FSMContext):
    await db.set_setting("welcome", msg.html_text)
    await state.clear()
    await msg.answer("Aggiornato.")


@admin_router.message(SetTxt.deny)
async def set_deny(msg: Message, state: FSMContext):
    await db.set_setting("deny_text", msg.html_text)
    await state.clear()
    await msg.answer("Aggiornato.")


@admin_router.message(Command("esporta"))
async def export_cmd(msg: Message):
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(await db.all_free_payloads(), ensure_ascii=False, indent=1),
                    encoding="utf-8")
    await msg.answer_document(FSInputFile(path))


@admin_router.callback_query(F.data == "a:exp")
async def cb_exp(cb: CallbackQuery):
    path = Path("/tmp/export.json")
    path.write_text(json.dumps(await db.all_free_payloads(), ensure_ascii=False, indent=1),
                    encoding="utf-8")
    await cb.message.answer_document(FSInputFile(path))
    await cb.answer()
