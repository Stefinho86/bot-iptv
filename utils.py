from aiogram.filters import BaseFilter
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

import db


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return await db.is_admin(message.from_user.id)


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


def user_kb(days):
    return K(inline_keyboard=[[B(text="Ricevi una lista", callback_data="u:get")]])


def admin_menu():
    return K(inline_keyboard=[
        [B(text="Aggiungi lista", callback_data="a:add")],
        [B(text="Liste libere", callback_data="a:free"), B(text="Assegnazioni", callback_data="a:hist")],
        [B(text="Utenti", callback_data="a:users"), B(text="Admin", callback_data="a:admins")],
        [B(text="Impostazioni", callback_data="a:set"), B(text="Statistiche", callback_data="a:stats")],
        [B(text="Esporta", callback_data="a:exp")],
    ])


def kinds():
    return K(inline_keyboard=[
        [B(text="Link M3U", callback_data="a:k:m3u")],
        [B(text="Xtream Codes", callback_data="a:k:xtream")],
        [B(text="Stalker Portal", callback_data="a:k:stalker")],
        [B(text="Indietro", callback_data="a:menu")],
    ])


def confirm(cid):
    return K(inline_keyboard=[
        [B(text="CONFERMA E SALVA", callback_data=f"a:ok:{cid}")],
        [B(text="Annulla", callback_data="a:cancel")],
    ])


def back():
    return K(inline_keyboard=[[B(text="Indietro", callback_data="a:menu")]])


def settings_kb(cooldown, proof, enabled):
    return K(inline_keyboard=[
        [B(text=f"Cooldown: {cooldown} giorni", callback_data="s:cool")],
        [B(text=f"Foto di prova: {'ON' if proof else 'OFF'}", callback_data="s:proof")],
        [B(text=f"Bot: {'ATTIVO' if enabled else 'SPENTO'}", callback_data="s:toggle")],
        [B(text="Testo benvenuto", callback_data="s:wel"), B(text="Testo rifiuto", callback_data="s:deny")],
        [B(text="Indietro", callback_data="a:menu")],
    ])


def user_row(uid, banned):
    return [B(text=("Sblocca" if banned else "Banna"),
              callback_data=f"u:{'unban' if banned else 'ban'}:{uid}")]
