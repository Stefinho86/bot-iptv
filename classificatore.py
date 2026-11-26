import re

GRAZIE = re.compile(r"\b(grazie|ringrazi\w*|ringraziam\w*|grz|thx|thanks)\b", re.I)
BONTA = re.compile(r"\b(funziona|funzionante|ottima|ottimo|perfetta|perfetto|liscio|"
                   r"va bene|tutto ok|top|grande)\b", re.I)
SALUTO = re.compile(r"\b(ciao|salve|buongiorno|buonasera|buon pomeriggio|hey|ehi|ola)\b", re.I)
RICHIESTA = re.compile(r"\b(lista|liste|link|m3u|iptv|posso|vorrei|mi dai|mi mandi|mandi|"
                       r"serve|avrei|dammi|consegna|nuova)\b", re.I)


def _risposta_al_bot(msg, bot):
    rip = getattr(msg, "reply_to_message", None)
    if not rip:
        return False
    autore = getattr(rip, "from_user", None)
    return bool(autore and autore.id == bot.id)


async def classifica(msg, bot, testo, ha_attesa):
    """Restituisce ringraziamento, saluto, richiesta o ambiguo.

    ha_attesa dice se l'utente ha una consegna con il ringraziamento ancora
    aperto: senza quell'informazione "grazie" non significa niente."""

    t = (testo or "").strip()

    if getattr(msg, "photo", None) or getattr(msg, "document", None):
        return "ringraziamento" if ha_attesa else "richiesta"

    if not t:
        return "ambiguo"

    if ha_attesa:
        if GRAZIE.search(t) or BONTA.search(t):
            return "ringraziamento"
        if SALUTO.search(t) and not RICHIESTA.search(t):
            return "saluto"
        return "richiesta"

    if GRAZIE.search(t) and not RICHIESTA.search(t):
        return "ringraziamento"
    if SALUTO.search(t) and not RICHIESTA.search(t) and not _risposta_al_bot(msg, bot):
        return "saluto"
    return "richiesta"
