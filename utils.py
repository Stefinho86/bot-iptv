import time

from aiogram.filters import BaseFilter
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

import db

ANON_ID = 1087968824

_TOKENS = {}


def new_token(chat_id):
    import secrets
    t = secrets.token_urlsafe(9)
    _TOKENS[t] = (chat_id, time.time() + 600)
    return t


def pop_token(t):
    item = _TOKENS.pop(t, None)
    if not item:
        return None
    chat_id, exp = item
    return chat_id if exp > time.time() else None


def _event_parts(event):
    msg = event if isinstance(event, Message) else getattr(event, "message", None)
    user = getattr(event, "from_user", None)
    sc = getattr(msg, "sender_chat", None) if msg else None
    chat = getattr(msg, "chat", None) if msg else None
    return user, sc, chat


async def anon_is_admin(chat_id) -> bool:
    uid = await db.get_anon_verified(chat_id)
    if not uid:
        return False
    return await db.is_admin(uid)


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        u = message.from_user
        return await db.is_admin(u.id, u.username if u else None)


class AnonUnverified(BaseFilter):
    async def __call__(self, event) -> bool:
        user, sc, chat = _event_parts(event)
        if not user or user.id != ANON_ID or not sc:
            return False
        gid = await db.get_group_id()
        if gid and sc.id != gid:
            return False
        return not await anon_is_admin(sc.id)


async def is_admin_event(event) -> bool:
    return await IsAdmin()(event)


async def is_admin_cb(cb) -> bool:
    if cb.from_user.id == ANON_ID:
        if not cb.message:
            return False
        return await anon_is_admin(cb.message.chat.id)
    return await db.is_admin(cb.from_user.id, cb.from_user.username)


class Trigger(BaseFilter):
    async def __call__(self, message: Message, bot) -> bool:
        if message.chat.type == "private":
            return True
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
            return True
        if message.entities:
            for e in message.entities:
                if e.type == "mention":
                    frag = message.text[e.offset:e.offset + e.length].lstrip("@").lower()
                    if frag == (await bot.me()).username.lower():
                        return True
        return False


class NotCommand(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        t = message.text or message.caption or ""
        return not t.startswith("/")


def user_kb(days):
    return K(inline_keyboard=[[B(text="Ricevi una lista", callback_data="u:get")]])


def admin_menu():
    return K(inline_keyboard=[
        [B(text="🎬 Ricevi una lista", callback_data="u:get")],
        [B(text="➕ Aggiungi lista", callback_data="a:add")],
        [B(text="📋 Liste libere", callback_data="a:free"), B(text="📤 Assegnazioni", callback_data="a:hist")],
        [B(text="👤 Cerca utente", callback_data="a:users"), B(text="↩️ Revoca", callback_data="a:revoca")],
        [B(text="📇 Elenco utenti", callback_data="a:allusers"), B(text="🛡 Elenco admin", callback_data="a:alladmins")],
        [B(text="🚫 Utenti bannati", callback_data="a:banned")],
        [B(text="➕ Aggiungi admin", callback_data="a:admin_add"), B(text="🗑 Rimuovi admin", callback_data="a:admin_del")],
        [B(text="🔁 Reset attesa", callback_data="a:reset"), B(text="⏳ Cooldown", callback_data="s:cool")],
        [B(text="⚙️ Impostazioni", callback_data="a:set"), B(text="📊 Statistiche", callback_data="a:stats")],
        [B(text="📦 Esporta", callback_data="a:exp"), B(text="📄 Log attività", callback_data="a:log")],
        [B(text="📖 Comandi", callback_data="a:comandi"), B(text="♻️ Ricarica bot", callback_data="a:reload")],
        [B(text="🔐 Riverifica admin", callback_data="a:verify"), B(text="🩺 Stato", callback_data="a:stato")],
        [B(text="⛔ Accendi / spegni bot", callback_data="a:toggle")],
    ])


def free_kinds():
    return K(inline_keyboard=[
        [B(text="🔗 Solo M3U", callback_data="a:freem3u")],
        [B(text="🌐 Solo Xtream", callback_data="a:freextream")],
        [B(text="📡 Solo Stalker", callback_data="a:freestalker")],
        [B(text="📚 Tutte", callback_data="a:freeall")],
        [B(text="⬅️ Menu", callback_data="a:menu")],
    ])


def kinds():
    return K(inline_keyboard=[
        [B(text="🔗 Link M3U", callback_data="a:k:m3u")],
        [B(text="🌐 Xtream Codes", callback_data="a:k:xtream")],
        [B(text="📡 Stalker Portal", callback_data="a:k:stalker")],
        [B(text="📷 Aggiungi foto/txt", callback_data="a:k:bulk")],
        [B(text="⬅️ Indietro", callback_data="a:menu")],
    ])


def confirm(cid):
    return K(inline_keyboard=[
        [B(text="🧪 TESTA E SALVA", callback_data=f"a:ok:{cid}")],
        [B(text="❌ Annulla", callback_data="a:cancel")],
    ])


def doppione(cid):
    return K(inline_keyboard=[
        [B(text="✅ AGGIUNGI COMUNQUE", callback_data=f"a:ok:{cid}")],
        [B(text="❌ Annulla", callback_data="a:cancel")],
    ])


def force(cid):
    return K(inline_keyboard=[
        [B(text="✅ INSERISCI UGUALMENTE", callback_data=f"a:force:{cid}")],
        [B(text="❌ Annulla", callback_data="a:cancel")],
    ])


def multiplo(cid):
    return K(inline_keyboard=[
        [B(text="✅ CONFERMA", callback_data=f"a:mok:{cid}")],
        [B(text="🔁 RICOMINCIA", callback_data=f"a:mredo:{cid}")],
        [B(text="❌ Annulla", callback_data="a:cancel")],
    ])


def back():
    return K(inline_keyboard=[[B(text="⬅️ Menu", callback_data="a:menu")]])


def settings_kb(cooldown, proof, enabled, gonly, group_id):
    gid = group_id if group_id and group_id != "0" else "non impostato"
    return K(inline_keyboard=[
        [B(text=f"⏳ Attesa: {cooldown} giorni", callback_data="s:cool")],
        [B(text=f"👥 Gruppo: {gid}", callback_data="s:group")],
        [B(text="🔗 Link d'invito", callback_data="s:link")],
        [B(text=f"🖼 Foto di prova: {'ON' if proof else 'OFF'}", callback_data="s:proof")],
        [B(text=f"🚦 Filtro gruppo: {'ON' if gonly else 'OFF'}", callback_data="s:gonly")],
        [B(text=f"⚡ Bot: {'ATTIVO' if enabled else 'SPENTO'}", callback_data="s:toggle")],
        [B(text="✏️ Testo benvenuto", callback_data="s:wel"), B(text="✏️ Testo rifiuto", callback_data="s:deny")],
        [B(text="✏️ Testo blocco", callback_data="s:ntext")],
        [B(text="⬅️ Menu", callback_data="a:menu")],
    ])


def user_row(uid, banned):
    return [B(text=("✅ Sblocca" if banned else "🚫 Banna"),
              callback_data=f"u:{'unban' if banned else 'ban'}:{uid}")]


async def member_ok(bot, uid):
    if await db.get_int("group_only") == 0:
        return True
    gid = await db.get_group_id()
    if gid == 0:
        return False
    try:
        m = await bot.get_chat_member(gid, uid)
    except Exception:
        return False
    if m.status in ("creator", "administrator", "member"):
        return True
    return bool(m.status == "restricted" and getattr(m, "is_member", False))


async def not_member_text():
    txt = await db.get_setting("not_member")
    link = (await db.get_setting("group_link") or "").strip()
    out = txt.replace("{link}", link).strip()
    if link and link not in txt:
        out += f"\n\n{link}"
    return out


async def join_kb():
    link = (await db.get_setting("group_link") or "").strip()
    if not link:
        return None
    return K(inline_keyboard=[[B(text="Entra nel gruppo", url=link)]])
