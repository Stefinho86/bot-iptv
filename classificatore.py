import re

THANKS = "thanks"
REQUEST = "request"
ALTRO = "altro"

GRAZIE = re.compile(
    r"\b(grazie|ringrazi\w*|grz|thx|thanks|thank\s*you|gentilissim\w*|"
    r"perfetto|funziona|funge|va\s+bene|ottimo|top|grande)\b", re.I)

LISTA = re.compile(
    r"\b(lista|liste|iptv|m3u|xtream|stalker|abbonamento|rinnovo|"
    r"dammi|mandami|servirebbe|mi\s+serve|vorrei|posso\s+avere|"
    r"avrei\s+bisogno|consegna|nuova|link)\b", re.I)


def classifica(testo, *, foto=False, sticker=False, risposta_al_bot=False,
               solo_emoji=False, in_attesa=False):
    """THANKS, REQUEST o ALTRO.

    La foto vale ringraziamento solo se c'è una consegna in attesa: altrimenti
    è uno screenshot qualsiasi.
    """
    t = (testo or "").strip()
    grazie = bool(GRAZIE.search(t))
    lista = bool(LISTA.search(t))

    if in_attesa:
        if foto or grazie:
            return THANKS
        if risposta_al_bot and not lista:
            return THANKS

    if foto:
        return THANKS
    if solo_emoji or sticker:
        return ALTRO

    if grazie and lista:
        return REQUEST
    if grazie:
        return THANKS
    if lista:
        return REQUEST
    if risposta_al_bot:
        return THANKS
    return ALTRO
