"""Distingue ringraziamenti, richieste e rumore.

Punteggio pesato con soglie: un "grazie" dentro una richiesta non fa partire
la barzelletta. La barzelletta e l'invito in privato scattano solo su RICHIESTA.
"""
THANKS = "thanks"
REQUEST = "request"
OTHER = "other"

SOGLIA_THANKS = 3
SOGLIA_REQUEST = 3

PAROLE_THANKS = {
    "grazie mille": 4, "grazie infinite": 4, "grazie": 3, "ringrazio": 3,
    "ti ringrazio": 3, "vi ringrazio": 3, "sei un grande": 3, "siete grandi": 3,
    "tutto perfetto": 3, "funziona alla grande": 3, "gentilissimo": 2,
    "gentilissima": 2, "gentili": 2, "perfetto": 2, "funziona": 2, "tutto ok": 2,
    "ottimo": 2, "ottima": 2, "complimenti": 2, "consigliato": 2, "affidabile": 2,
    "che spettacolo": 2, "mitico": 2, "grandissimo": 2, "ricevuto": 1,
    "arrivato": 1, "veloce": 1, "puntuale": 1, "top": 1, "grande": 1,
}

PAROLE_REQUEST = {
    "lista": 4, "liste": 4, "m3u": 3, "mi passi": 3, "mi mandi": 3,
    "potrei avere": 3, "posso avere": 3, "cercavo": 3, "cerco": 3,
    "qualcuno ha": 3, "come funziona": 3, "abbonamento": 3, "rinnovo": 3,
    "avete": 2, "info": 2, "informazioni": 2, "prezzo": 2, "costo": 2,
    "in privato": 2, "link": 2, "prova": 2, "attivare": 2, "scadenza": 2,
}

EMOJI_THANKS = {"🙏": 2, "👏": 2, "❤️": 1, "💯": 1, "👍": 1, "🔥": 1, "😍": 1, "🤩": 1}

CHIAVI_FORTI_REQUEST = ("lista", "liste", "avete", "mi passi", "mi mandi",
                        "info", "prezzo", "costo", "m3u")


def _punti(testo, dizionario):
    t = " " + (testo or "").lower().strip() + " "
    return sum(p for parola, p in dizionario.items() if " " + parola + " " in t)


def classifica(testo="", *, foto=False, sticker=False, risposta_al_bot=False,
               solo_emoji=False, ocr=None):
    testo = (testo or "").strip()
    s_t = _punti(testo, PAROLE_THANKS)
    s_r = _punti(testo, PAROLE_REQUEST)

    for emoji, peso in EMOJI_THANKS.items():
        if emoji in testo:
            s_t += peso

    if ocr:
        basso = ocr.lower()
        if "grazie" in basso or "ringrazio" in basso:
            s_t += 3
        if "lista" in basso or "m3u" in basso:
            s_r += 2

    if solo_emoji and s_t:
        s_t += 1
    if risposta_al_bot and len(testo.split()) <= 5:
        s_t += 2
    if sticker:
        s_t += 2
    if foto and s_r < SOGLIA_REQUEST:
        s_t += 2

    if s_r >= SOGLIA_REQUEST and s_r > s_t:
        return REQUEST
    if s_r >= SOGLIA_REQUEST and s_t >= SOGLIA_THANKS:
        basso = testo.lower()
        return REQUEST if any(k in basso for k in CHIAVI_FORTI_REQUEST) else THANKS
    if s_t >= SOGLIA_THANKS:
        return THANKS
    return OTHER
