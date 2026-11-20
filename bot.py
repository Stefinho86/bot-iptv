import asyncio
import logging
import os
import socket
import ssl
import time
import traceback

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

TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


def make_connector(conn_proxy):
    if conn_proxy and conn_proxy.startswith("socks"):
        from aiohttp_socks import ProxyConnector
        return ProxyConnector.from_url(conn_proxy, ssl=ssl.create_default_context())
    return aiohttp.TCPConnector(family=socket.AF_INET, ssl=ssl.create_default_context())


def _create_connector(self, *args, **kwargs):
    return make_connector(getattr(self, "_conn_proxy", None))


AiohttpSession.create_connector = _create_connector
AiohttpSession.proxy = property(
    lambda self: getattr(self, "_req_proxy", None),
    lambda self, v: setattr(self, "_req_proxy", v),
)


def nuova_sessione(proxy, api=None):
    session = AiohttpSession(timeout=TIMEOUT, api=api) if api else AiohttpSession(timeout=TIMEOUT)
    if proxy and proxy.startswith("socks"):
        session._conn_proxy = proxy
        session.proxy = None
    else:
        session._conn_proxy = None
        session.proxy = proxy
    return session


def _mask(url):
    if not url or "@" not in url:
        return url
    schema, resto = url.split("://", 1)
    cred, host = resto.split("@", 1)
    return f"{schema}://{cred.split(':')[0]}:***@{host}"


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


async def _get(url, proxy=None, etichetta=""):
    try:
        conn = make_connector(proxy)
        kw = {}
        if proxy and not proxy.startswith("socks"):
            kw["proxy"] = proxy
        async with aiohttp.ClientSession(connector=conn, timeout=TIMEOUT) as s:
            async with s.get(url, **kw) as r:
                corpo = (await r.text())[:90]
                print(f"   {etichetta}: HTTP {r.status} {corpo}")
                return r.status == 200
    except Exception as e:
        print(f"   {etichetta}: {type(e).__name__}")
        return False


async def _attendi_rete(bot) -> bool:
    for i in range(1, 4):
        try:
            me = await bot.me()
            print(f"Avviato come @{me.username}")
            return True
        except Exception as e:
            print(f"Telegram non raggiungibile ({type(e).__name__}), tentativo {i}/3")
            traceback.print_exc()
            await asyncio.sleep(5)
    return False

async def _registra_comandi(bot):
    await asyncio.sleep(8)
    for tentativo in range(1, 3):
        try:
            await bot.set_my_commands(USER_CMDS, scope=BotCommandScopeDefault())
            for uid in config.SUPER_ADMINS:
                try:
                    await bot.set_my_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=uid))
                except Exception:
                    pass
            print("Comandi registrati")
            return
        except Exception as e:
            print(f"Comandi non registrati ({type(e).__name__})")
            await asyncio.sleep(15)

async def run_once():
    await db.init()

    api = (os.getenv("TELEGRAM_API") or "").strip().rstrip("/") or None
    proxy = (os.getenv("PROXY_URL") or "").strip() or None
    base = api or "https://api.telegram.org"
    getme = f"{base}/bot{config.BOT_TOKEN}/getMe"

    if api:
        print(f"Relay configurato: {api}")

    via_relay = via_direct = via_proxy = False

    if api:
        print("--- TEST R: relay ---")
        via_relay = await _get(getme, None, "telegram via relay")

    if not via_relay:
        print("--- TEST B: telegram diretto ---")
        via_direct = await _get(getme, None, "telegram diretto")

    if not via_relay and not via_direct and proxy:
        print(f"--- TEST C: proxy {_mask(proxy)} ---")
        via_proxy = await _get(getme, proxy, "telegram via proxy")

    if not (via_relay or via_direct or via_proxy):
        print("=== NESSUNA VIA VALIDA ===")
        if not api:
            print("Crea il relay Cloudflare e imposta TELEGRAM_API")
        await db.close()
        return

    if via_relay:
        print("Via scelta: relay")
        session = nuova_sessione(None, api)
        polling_timeout = 20
    elif via_direct:
        print("Via scelta: diretta")
        session = nuova_sessione(None)
        polling_timeout = 30
    else:
        print("Via scelta: proxy")
        session = nuova_sessione(proxy)
        polling_timeout = 30

    bot = Bot(config.BOT_TOKEN, session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

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
    print("POLLING ATTIVO")

    try:
        await dp.start_polling(bot, polling_timeout=polling_timeout)
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
            traceback.print_exc()
        print("Nuovo tentativo tra 10 secondi")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(supervisor())
