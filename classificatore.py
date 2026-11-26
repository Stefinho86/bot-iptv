"""Decide cosa ha scritto l'utente nel gruppo.

Tre soli esiti:
  ringraziamento -> screenshot, foto, grazie, risposta al messaggio di consegna
  richiesta      -> vuole una lista
  saluto / altro -> tutto il resto

Regola d'oro: se c'è un verbo di richiesta, vince la richiesta, anche se dentro
la frase c'è la parola "grazie" ("grazie, mi mandi la lista?").
"""

import re

RICHIESTA = re.compile(
    r"\b(lista|liste|link|m3u|iptv|xtream|stalker|m3u8|"
    r"mi serve|mi servirebbe|mi mandate|me la mandi|me la passi|"
    r"posso avere|posso averla|vorrei|voglio|riesco ad avere|"
    r"come faccio|come si fa|info|disponibil\w*)\b", re.I)

GRAZIE = re.compile(
    r"\b(grazie|ringrazi\w*|grz|thanks|thx|gentilissim\w*|"
    r"funziona|funge|va benissimo|tutto ok|ottim\w*|perfett\w*|"
    r"sei un grande|grande|top|spettacol\w*)\b", re.I)

SALUTO = re.compile(r"^\s*(ciao|buongiorno|buonasera|salve|ehi|hey)\b", re.I)

DOMANDA = re.compile(r"\?")

FOTO_MIME = ("image/",)


def _foto(msg):
    if msg.photo:
        return True
    doc = getattr(msg, "document", None)
    if doc and (doc.mime_type or "").startswith(FOTO_MIME):
        return True
    return False


def _risposta_al_bot(msg, bot_id):
    rip = msg.reply_to_message
    if not rip:
        return False
    autore = getattr(rip, "from_user", None)
    return bool(autore and autore.id == bot_id)


async def _menziona(msg, bot, testo):
    ent = msg.entities or msg.caption_entities or []
    if not ent:
        return False
    try:
        mio = (await bot.me()).username.lower()
    except Exception:
        return False
    for e in ent:
        if e.type == "mention":
            frammento = testo[e.offset:e.offset + e.length].lstrip("@").lower()
            if frammento == mio:
                return True
    return False


async def classifica(msg, bot, testo, ha_attesa: bool) -> str:
    testo = (testo or "").strip()

    # 1. uno screenshot è sempre la prova di un ringraziamento
    if _foto(msg):
        return "ringraziamento" if ha_attesa else "richiesta"

    # 2. risposta a un messaggio del bot: è la consegna, quindi ringrazia
    if _risposta_al_bot(msg, bot.id):
        return "ringraziamento"

    menzione = await _menziona(msg, bot, testo)
    chiede = bool(RICHIESTA.search(testo))
    ringrazia = bool(GRAZIE.search(testo))

    # 3. chiede esplicitamente una lista: vince su tutto
    if chiede:
        return "richiesta"

    # 4. ringrazia (con o senza tag)
    if ringrazia and ha_attesa:
        return "ringraziamento"

    # 5. tag del bot senza verbo di richiesta e senza attesa
    if menzione:
        return "richiesta"

    if ringrazia:
        return "ringraziamento"

    if SALUTO.match(testo) and len(testo) < 40 and not DOMANDA.search(testo):
        return "saluto"

    return "altro"
