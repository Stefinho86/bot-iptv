import asyncio
import logging
import os
import socket
import ssl
import time

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
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
    BotCommand(command="verifica", description="Riverifica admin"),
    BotCommand(command="deverifica", description="Revoca verifica"),
    BotCommand(command="toggle", description="Accendi o spegni"),
    BotCommand(command="esporta", description="Esporta liste libere"),
    BotCommand(command="comandi", description="Elenco comandi"),
]


class SafeSession(AiohttpSession):
    def __init__(self, timeout=None, proxy=None):
        if timeout is None:
            super().__init__()
        else:
            super().__init__(timeout=timeout)
        self.proxy = proxy

    async def create_connector(self, *args, **kwargs):
        return aiohttp.TCPConnector(family=socket.AF_INET, ssl=ssl.create_default_context())


async def _health(_):
    return web.Response(text="ok")


async def start_health_once():
    port = os.getenv("PORT")
    if not port:
        return
    app = web.Application()
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    print(f"Health server su :{port}")


def build_dispatcher() -> Dispatcher:
    for r in (admin_router, anon_router, user_router):
        r.parent_router = None
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(anon_router)
    dp.include_router(user_router)
    return dp


async def _diag():
    try:
        a = [x[4][0] for x in socket.getaddrinfo("api.telegram.org", 443, socket.AF_INET)]
        print(f"DNS IPv4: {a[:3]}")
    except Exception as e:
        print(f"DNS IPv4 fallito: {repr(e)}")
    try:
        a = [x[4][0] for x in socket.getaddrinfo("api.telegram.org", 443, socket.AF_INET6)]
        print(f"DNS IPv6: {a[:2]}")
    except Exception as e:
        print(f"DNS IPv6 assente: {type(e).__name__}")
    try:
        conn = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(
                connector=conn, timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe") as r:
                print(f"GET getMe -> {r.status}: {(await r.text())[:150]}")
    except Exception as e:
        print(f"GET getMe fallita: {repr(e)}")


async def _attendi_rete(bot) -> bool:
    await _diag()
    for i in range(1, 11):
        try:
            me = await bot.me()
            print(f"Avviato come @{me.username}")
            return True
        except Exception as e:
            print(f"Telegram non raggiungibile ({type(e).__name__}), tentativo {i}/10")
            await asyncio.sleep(6)
    print("RETE BLOCCATA: api.telegram.org non risponde.")
    return False


async def _registra_comandi(bot):
    await asyncio.sleep(8)
    for tentativo in range(1, 4):
        try:
            await bot.set_my_commands(USER_CMDS, scope=BotCommandScopeDefault())
            for uid in config.SUPER_ADMINS:
                try:
                    await bot.set_my_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=uid))
                except Exception as e:
                    print(f"Comandi admin per {uid}: {type(e).__name__}")
            print("Comandi registrati")
            return
        except Exception as e:
            print(f"Registrazione comandi fallita ({type(e).__name__}), tentativo {tentativo}/3")
            await asyncio.sleep(20 * tentativo)
    print("Comandi non registrati, usa /setcommands su BotFather")


async def run_once():
    await db.init()

    proxy = (os.getenv("PROXY_URL") or "").strip() or None
    session = SafeSession(timeout=aiohttp.ClientTimeout(total=60, connect=20), proxy=proxy)
    bot = Bot(config.BOT_TOKEN, session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    if proxy:
        print("Proxy attivo")

    if not await _attendi_rete(bot):
        try:
            await bot.session.close()
        except Exception:
            pass
        await db.close()
        return

    dp = build_dispatcher()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"delete_webhook saltato: {type(e).__name__}")

    asyncio.create_task(_registra_comandi(bot))

    try:
        await dp.start_polling(bot)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
        await db.close()


async def supervisor():
    logging.basicConfig(level=logging.INFO)
    await start_health_once()
    while True:
        try:
            await run_once()
        except Exception as e:
            print(f"Crash: {type(e).__name__}: {e}")
        print("Riavvio interno tra 15 secondi")
        await asyncio.sleep(15)


if __name__ == "__main__":
    asyncio.run(supervisor())
