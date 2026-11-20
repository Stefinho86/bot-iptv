import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiohttp import web

import config
import db
from handlers import admin_router, anon_router, user_router

USER_CMDS = [
    BotCommand(command="lista", description="Ricevi una lista IPTV"),
    BotCommand(command="mio", description="La tua lista attuale"),
    BotCommand(command="start", description="Avvia"),
]
ADMIN_CMDS = USER_CMDS + [
    BotCommand(command="admin", description="Pannello admin"),
    BotCommand(command="stato", description="Diagnostica"),
    BotCommand(command="comandi", description="Elenco comandi"),
    BotCommand(command="aggiungi", description="Aggiungi lista"),
    BotCommand(command="liberi", description="Liste libere"),
    BotCommand(command="assegnazioni", description="Storico assegnazioni"),
    BotCommand(command="revoca", description="Revoca un'assegnazione"),
    BotCommand(command="utenti", description="Cerca utente"),
    BotCommand(command="ban", description="Banna utente"),
    BotCommand(command="unban", description="Sblocca utente"),
    BotCommand(command="reset", description="Azzera cooldown"),
    BotCommand(command="admin_add", description="Aggiungi admin"),
    BotCommand(command="admin_del", description="Rimuovi admin"),
    BotCommand(command="cooldown", description="Imposta giorni"),
    BotCommand(command="setgroup", description="Imposta gruppo"),
    BotCommand(command="setlink", description="Imposta link gruppo"),
    BotCommand(command="verifica", description="Riverifica admin anonimo"),
    BotCommand(command="deverifica", description="Revoca verifica anonimo"),
    BotCommand(command="toggle", description="Accendi/spegni"),
    BotCommand(command="esporta", description="Esporta liste libere"),
]


async def _health(_):
    return web.Response(text="ok")


async def start_health():
    port = os.getenv("PORT")
    if not port:
        return
    app = web.Application()
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()


async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init()
    await start_health()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(anon_router)
    dp.include_router(user_router)

    await bot.set_my_commands(USER_CMDS, scope=BotCommandScopeDefault())
    for uid in config.SUPER_ADMINS:
        try:
            await bot.set_my_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=uid))
        except Exception:
            pass

    await bot.delete_webhook(drop_pending_updates=True)
    print(f"Avviato come @{(await bot.me()).username}")
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
