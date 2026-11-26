import re

GRAZIE = re.compile(r"\b(grazie|ringrazi\w*|ringraziam\w*|ti ringrazio|grz|thx|thanks)\b", re.I)
BONTA = re.compile(r"\b(funziona|funzionante|ottima|ottimo|perfetta|perfetto|liscio|"
                   r"va bene|tutto ok|top|grande|figata|spettacolo)\b", re.I)
SALUTO = re.compile(r"\b(ciao|salve|buongiorno|buonasera|buon pomeriggio|hey|ehi|ola)\b", re.I)
RICHIESTA = re.compile(r"\b(lista|liste|link|m3u|iptv|posso|vorrei|mi dai|mi mandi|mandi|"
                       r"serve|avrei|dammi|consegna|nuova)\b", re.I)
ALTRA = re.compile(r"\b(altra|altro|nuova|nuovo|ancora|seconda|riprovo|rifaccio)\b", re.I)
NEGAZIONE = re.compile(r"\b(non funziona|non funzionano|non va|non vanno|non parte|"
                       r"non si vede|non si sente|non carica|non apre|non si apre|"
                       r"non gira|buona a niente|schifo|rotto|rotta|rotte|errore|errori|"
                       r"problema|problemi|scaduta|scadute|bloccata|si blocca|"
                       r"schermo nero|buffer\w*|lista morta|liste morte)\b", re.I)


def risposta_al_bot(msg, bot):
    rip = getattr(msg, "reply_to_message", None)
    if not rip:
        return False
    autore = getattr(rip, "from_user", None)
    return bool(autore and autore.id == bot.id)


async def classifica(msg, bot, testo, ha_attesa):
    """Restituisce ringraziamento, reclamo, saluto, richiesta o ambiguo.

    ha_attesa dice se l'utente ha una consegna col ringraziamento ancora
    aperto: senza quel dato "grazie" non significa niente.

    Ordine delle priorità, quando c'è un'attesa aperta:
    1. foto o file -> ringraziamento (è lo screenshot)
    2. negazione ("non funziona", "scaduta", "errore") -> reclamo, vince su tutto
    3. "grazie" senza richiesta di un'altra lista -> ringraziamento
    4. richiesta di lista -> richiesta
    5. complimento -> ringraziamento
    6. saluto -> saluto
    """

    t = (testo or "").strip()

    if getattr(msg, "photo", None) or getattr(msg, "document", None):
        return "ringraziamento" if ha_attesa else "richiesta"

    if not t:
        return "ambiguo"

    grazie = bool(GRAZIE.search(t))
    bonta = bool(BONTA.search(t))
    saluto = bool(SALUTO.search(t))
    richiesta = bool(RICHIESTA.search(t))
    altra = bool(ALTRA.search(t))
    negazione = bool(NEGAZIONE.search(t))

    if ha_attesa:
        if negazione:
            return "reclamo"
        if grazie and not (altra and richiesta):
            return "ringraziamento"
        if richiesta:
            return "richiesta"
        if bonta:
            return "ringraziamento"
        if saluto:
            return "saluto"
        return "ambiguo"

    if negazione:
        return "reclamo"
    if richiesta:
        return "richiesta"
    if grazie or bonta:
        return "ringraziamento"
    if saluto and not risposta_al_bot(msg, bot):
        return "saluto"
    return "ambiguo"
