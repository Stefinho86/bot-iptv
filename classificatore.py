import re

GRAZIE = re.compile(r"\b(grazie|grazie mille|ringrazi\w*|ringraziam\w*|ti ringrazio|"
                    r"grz|thx|thanks)\b", re.I)
BONTA = re.compile(r"\b(funziona|funzionante|funge|ottima|ottimo|perfetta|perfetto|"
                   r"liscio|va bene|tutto ok|tutto bene|top|grande|grandissimo|"
                   r"grandissima|figata|spettacolo|mitico|mitica|una bomba|"
                   r"alla grande|consigliata|consigliato|sei un grande|bravo|brava)\b",
                   re.I)
GENTILE = re.compile(r"\b(gentile|gentilissimo|gentilissima|molto gentile)\b", re.I)
CONFERMA = re.compile(r"\b(ecco|eccola|eccolo|eccomi|arrivata|arrivato|"
                      r"ricevuta|ricevuto)\b", re.I)
SALUTO = re.compile(r"\b(ciao|salve|buongiorno|buonasera|buon pomeriggio|buondì|"
                    r"hey|ehi|ola)\b", re.I)
RICHIESTA = re.compile(r"\b(lista|liste|link|m3u|iptv|posso|vorrei|mi dai|mi mandi|"
                       r"mandi|serve|avrei|dammi|consegna|nuova)\b", re.I)
ALTRA = re.compile(r"\b(altra|altro|nuova|nuovo|ancora|seconda|riprovo|rifaccio)\b",
                   re.I)
NEGAZIONE = re.compile(r"\b(non funziona|non funzionano|non va|non vanno|non parte|"
                       r"non si vede|non si sente|non carica|non apre|non si apre|"
                       r"non gira|buona a niente|schifo|rotto|rotta|rotte|errore|"
                       r"errori|problema|problemi|scaduta|scaduto|scadute|bloccata|"
                       r"bloccato|si blocca|si impalla|schermo nero|buffer\w*|"
                       r"lenta|pessima|penosa|lista morta|liste morte)\b", re.I)
EMOJI_OK = re.compile("[🙏❤🔥👏💪😍🥰👍⭐✅]")
EMOJI_KO = re.compile("[😡🤬👎💩❌😤]")


def contiene_grazie(testo):
    """Esposta per handlers: dice se nella frase c'è un grazie anche quando
    l'esito è 'reclamo' (es. 'grazie ma non funziona')."""
    return bool(GRAZIE.search(testo or "") or CONFERMA.search(testo or ""))


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
    2. negazione o emoji negativa -> reclamo (vince su tutto)
    3. "grazie"/"eccola"/emoji positiva senza richiesta di un'altra lista
       -> ringraziamento
    4. richiesta di lista -> richiesta
    5. complimento o "gentile" -> ringraziamento
    6. saluto -> saluto

    Nota sul caso misto: "grazie ma non funziona" -> reclamo, ma
    contiene_grazie() torna True. Handlers deve registrare comunque il
    ringraziamento per non far scattare la punizione.
    """

    t = (testo or "").strip()

    if getattr(msg, "photo", None) or getattr(msg, "document", None):
        return "ringraziamento" if ha_attesa else "richiesta"

    if not t:
        return "ambiguo"

    grazie = bool(GRAZIE.search(t))
    bonta = bool(BONTA.search(t))
    gentile = bool(GENTILE.search(t))
    conferma = bool(CONFERMA.search(t))
    saluto = bool(SALUTO.search(t))
    richiesta = bool(RICHIESTA.search(t))
    altra = bool(ALTRA.search(t))
    negazione = bool(NEGAZIONE.search(t))
    eok = bool(EMOJI_OK.search(t))
    eko = bool(EMOJI_KO.search(t))

    if ha_attesa:
        if negazione or (eko and not eok):
            return "reclamo"
        if (grazie or conferma) and not (altra and richiesta):
            return "ringraziamento"
        if eok and not richiesta:
            return "ringraziamento"
        if richiesta:
            return "richiesta"
        if bonta or gentile:
            return "ringraziamento"
        if saluto:
            return "saluto"
        return "ambiguo"

    if eko and not eok:
        return "reclamo"
    if negazione and not (grazie or bonta):
        return "richiesta"
    if richiesta:
        return "richiesta"
    if grazie or conferma or eok:
        return "ringraziamento"
    if bonta or gentile:
        return "ringraziamento"
    if saluto and not risposta_al_bot(msg, bot):
        return "saluto"
    return "ambiguo"
