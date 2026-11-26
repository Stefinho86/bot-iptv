import asyncio
import io
import json
import os
import random
import re
import shutil
import subprocess
import uuid

import aiohttp
import pytesseract
from PIL import Image, ImageOps, ImageStat

import config

MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
URL = re.compile(r"https?://[^\s<>\"']+")
HOST = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,})(?::(\d{2,5}))?\b", re.I)
KV_U = re.compile(r"(?:username|user|login|utente|usr)\s*[:=]\s*([^\s&,;\"']+)", re.I)
KV_P = re.compile(r"(?:password|pass|pwd|senha|clave)\s*[:=]\s*([^\s&,;\"']+)", re.I)
PAIR = re.compile(r"^\s*([A-Za-z0-9._\-]{2,40})\s*[:/|]\s*([^\s]{2,40})\s*$", re.M)
CLEAN = re.compile(r"^(?:user(?:name)?|login|pass(?:word)?|pwd|utente)\s*[:=]\s*", re.I)
M3U_EXT = re.compile(r"\.m3u8?(?:$|[?#])", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept": "*/*"}

TEST_BUDGET = int(os.environ.get("TEST_BUDGET", "75") or 75)
MAX_PIXEL = 24_000_000

_BINS_OK = False
_LINGUE = None


def _LANCZOS():
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _ensure_bins():
    """Solo controllo: nessuna installazione a runtime (blocca l'event loop
    e in un container non-root fallisce comunque)."""
    global _BINS_OK
    if _BINS_OK:
        return
    _BINS_OK = True
    manca = [p for p in ("tesseract", "ffmpeg") if not shutil.which(p)]
    if not manca:
        return
    print("mancano:", manca)
    if os.environ.get("AUTO_INSTALL", "").strip() in ("1", "true", "yes"):
        pacchetti = [{"tesseract": "tesseract-ocr", "ffmpeg": "ffmpeg"}[p] for p in manca]
        try:
            subprocess.run(["apt-get", "update", "-qq"], timeout=180)
            subprocess.run(["apt-get", "install", "-y", "-qq", *pacchetti,
                            "tesseract-ocr-ita", "tesseract-ocr-eng"], timeout=420)
        except Exception as e:
            print("installazione fallita:", e)
    print("ffmpeg:", shutil.which("ffmpeg"), "tesseract:", shutil.which("tesseract"))


def _lingue():
    global _LINGUE
    if _LINGUE is not None:
        return _LINGUE
    try:
        disp = set(pytesseract.get_languages(config=""))
    except Exception:
        disp = set()
    if "ita" in disp and "eng" in disp:
        _LINGUE = "ita+eng"
    elif "ita" in disp:
        _LINGUE = "ita"
    else:
        _LINGUE = "eng"
    return _LINGUE


def ocr_bytes(data):
    _ensure_bins()
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    if w < 2200:
        k = min(3, max(2, 2200 // max(w, 1)))
        if w * k * h * k <= MAX_PIXEL:
            img = img.resize((w * k, h * k), _LANCZOS())
    if ImageStat.Stat(img).mean[0] < 110:
        img = ImageOps.invert(img)
    img = ImageOps.autocontrast(img)
    lingua = _lingue()
    migliore = ""
    for psm in ("4", "6", "11"):
        try:
            out = pytesseract.image_to_string(img, lang=lingua,
                                              config=f"--oem 3 --psm {psm}")
        except Exception:
            try:
                out = pytesseract.image_to_string(img, lang="eng",
                                                  config=f"--oem 3 --psm {psm}")
            except Exception:
                continue
        if out.count("http") > migliore.count("http"):
            migliore = out
        elif not migliore.strip() and out.strip():
            migliore = out
    return migliore


def _base(url):
    m = re.match(r"(https?://)([^/\s]+)", url)
    if not m:
        return url
    scheme, netloc = m.group(1), m.group(2)
    if "@" in netloc:
        u, h = netloc.split("@", 1)
        return scheme + u + h
    return scheme + netloc


def _xtream(host, user, pwd):
    port = host.group(2)
    server = f"http://{host.group(1)}" + (f":{port}" if port else ":8080")
    return {"kind": "xtream", "label": host.group(1),
            "payload": {"server": server, "username": user, "password": pwd}}


def detect(text):
    if not text:
        return None
    text = text.strip()
    mac = MAC.search(text)
    host = HOST.search(text)

    if mac and host:
        port = host.group(2)
        portal = f"http://{host.group(1)}" + (f":{port}" if port else "")
        return {"kind": "stalker", "label": host.group(1),
                "payload": {"portal": portal, "mac": mac.group(0).upper()}}

    url = URL.search(text)
    if url:
        u = url.group(0).rstrip(".,;)")
        low = u.lower()
        qu = re.search(r"[?&]username=([^&\s]+)", u)
        qp = re.search(r"[?&]password=([^&\s]+)", u)
        if qu and qp and ("type=m3u" in low or M3U_EXT.search(low) or "output=" in low):
            return {"kind": "m3u", "label": _base(u), "payload": {"url": u}}
        if qu and qp:
            return {"kind": "xtream", "label": _base(u),
                    "payload": {"server": _base(u), "username": qu.group(1),
                                "password": qp.group(1)}}
        if "/c/" in low or "portal.php" in low or "stalker" in low:
            if not mac:
                return None
            return {"kind": "stalker", "label": _base(u),
                    "payload": {"portal": _base(u), "mac": mac.group(0).upper()}}
        if M3U_EXT.search(low) or "get.php" in low or "type=m3u" in low:
            return {"kind": "m3u", "label": _base(u), "payload": {"url": u}}

    if host:
        ku, kp = KV_U.search(text), KV_P.search(text)
        if ku and kp:
            return _xtream(host, ku.group(1), kp.group(1))
        lines = [CLEAN.sub("", l).strip() for l in text.splitlines()]
        lines = [l for l in lines if l]
        if len(lines) == 2 and all(" " not in l for l in lines):
            return _xtream(host, lines[0], lines[1])
        m = PAIR.search(text)
        if m:
            return _xtream(host, m.group(1), m.group(2))
    return None


def preview(item):
    p = item["payload"]
    if item["kind"] == "xtream":
        return (f"Tipo: Xtream Codes\nServer: <code>{p['server']}</code>\n"
                f"Utente: <code>{p['username']}</code>\nPassword: <code>{p['password']}</code>")
    if item["kind"] == "stalker":
        return (f"Tipo: Stalker Portal\nPortal: <code>{p['portal']}</code>\n"
                f"MAC: <code>{p['mac']}</code>")
    return f"Tipo: M3U\nURL: <code>{p['url']}</code>"


# ---------- confronto doppioni ----------
def _host_norm(valore):
    s = (valore or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/")[0]
    if "@" in s:
        s = s.split("@", 1)[1]
    s = s.split(":")[0]
    return s.strip(".")


def _url_norm(u):
    s = (u or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s).rstrip("/")
    host, _, resto = s.partition("/")
    host = host.split(":")[0]
    if "@" in host:
        host = host.split("@", 1)[1]
    utente = password = ""
    for parte in re.split(r"[?&]", resto):
        if parte.startswith("username="):
            utente = parte.split("=", 1)[1]
        elif parte.startswith("password="):
            password = parte.split("=", 1)[1]
    return f"{host}|{resto.split('?')[0]}|{utente}|{password}"


def chiavi_lista(kind, p):
    out = set()
    if kind == "xtream":
        h = _host_norm(p.get("server"))
        u = (p.get("username") or "").strip().lower()
        w = (p.get("password") or "").strip().lower()
        if h and u:
            out.add(f"xt|{h}|{u}")
        if u and w:
            out.add(f"xp|{u}|{w}")
    elif kind == "stalker":
        h = _host_norm(p.get("portal"))
        m = (p.get("mac") or "").replace("-", ":").upper()
        if h and m:
            out.add(f"sp|{h}|{m}")
        elif m:
            out.add(f"sm|{m}")
    else:
        u = _url_norm(p.get("url"))
        if u.strip("|"):
            out.add("m3u|" + u)
    return out


class Result:
    def __init__(self, ok, note="", frame=None, info=""):
        self.ok, self.note, self.frame, self.info = ok, note, frame, info


def _out():
    return config.MEDIA_DIR / f"{uuid.uuid4().hex}.jpg"


async def grab_frame(url, timeout=20):
    out = _out()
    cmd = [config.FFMPEG, "-y", "-loglevel", "error", "-user_agent", UA,
           "-rw_timeout", "10000000", "-probesize", "500000",
           "-analyzeduration", "1200000",
           "-i", url, "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(out)]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            raise
    except FileNotFoundError:
        return None
    except (asyncio.TimeoutError, asyncio.CancelledError) as e:
        if proc is not None:
            try:
                proc.kill()
                await asyncio.shield(proc.wait())
            except Exception:
                pass
        out.unlink(missing_ok=True)
        if isinstance(e, asyncio.CancelledError):
            raise
        return None
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        out.unlink(missing_ok=True)
        return None
    if out.exists() and out.stat().st_size > 4000:
        return out
    out.unlink(missing_ok=True)
    return None


async def _primo_frame(urls, blocchi=3):
    for i in range(0, len(urls), blocchi):
        gruppo = urls[i:i + blocchi]
        esiti = await asyncio.gather(*(grab_frame(u) for u in gruppo),
                                     return_exceptions=True)
        buoni = [f for f in esiti if f and not isinstance(f, Exception)]
        if buoni:
            for f in buoni[1:]:
                f.unlink(missing_ok=True)
            return buoni[0]
    return None


def _mescola(urls, quanti):
    if not urls:
        return []
    c = list(urls)
    random.shuffle(c)
    return c[:quanti]


async def _get(session, url, headers=None, timeout=20):
    h = {**HEAD, **(headers or {})}
    async with session.get(url, headers=h, ssl=False, allow_redirects=True,
                           timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return r.status, await r.text(errors="ignore")


def _canali_m3u(corpo):
    urls = []
    for riga in corpo.splitlines():
        riga = riga.strip()
        if riga.startswith(("http://", "https://")):
            urls.append(riga)
    return urls


async def _test_m3u(session, p):
    try:
        st, body = await _get(session, p["url"], timeout=25)
    except Exception as e:
        return Result(False, f"connessione: {type(e).__name__}")
    if st >= 400:
        return Result(False, f"HTTP {st}")
    urls = _canali_m3u(body)
    if not urls:
        return Result(False, "playlist vuota o non valida")
    f = await _primo_frame(_mescola(urls, 9))
    if f:
        return Result(True, "ok", f, f"{len(urls)} canali")
    return Result(False, "nessuno stream riproducibile")


def _pwd_varianti(pwd):
    v = [pwd]
    for a, b in (("0", "o"), ("o", "0"), ("1", "l"), ("l", "1")):
        x = pwd.replace(a, b)
        if x != pwd and x not in v:
            v.append(x)
    for x in list(v):
        if x.endswith(".") and (x[:-1] + "0") not in v:
            v.append(x[:-1] + "0")
        if x.endswith("0") and (x[:-1] + ".") not in v:
            v.append(x[:-1] + ".")
    return v[:4]


async def _api_xtream(session, srv, user, pwd, azione=None):
    q = f"username={user}&password={pwd}"
    if azione:
        q += f"&action={azione}"
    return await _get(session, f"{srv}/player_api.php?{q}", timeout=20)


async def _test_xtream(session, p):
    ultimo = "host non raggiungibile"
    buono = None
    for srv in [p["server"], *(p.get("alts") or [])]:
        for pwd in _pwd_varianti(p["password"]):
            try:
                st, body = await _api_xtream(session, srv, p["username"], pwd)
            except Exception as e:
                ultimo = f"{type(e).__name__} su {srv}"
                continue
            if st >= 400:
                ultimo = f"HTTP {st} su {srv}"
                continue
            try:
                j = json.loads(body)
            except Exception:
                ultimo = f"risposta non valida da {srv}"
                continue
            ui = j.get("user_info") or {}
            if ui.get("auth") in (0, False):
                ultimo = "credenziali rifiutate"
                continue
            if str(ui.get("status", "")).lower() not in ("active", "1", "true"):
                ultimo = f"stato {ui.get('status')}"
                continue
            buono = (srv, pwd, ui)
            break
        if buono:
            break
    if not buono:
        return Result(False, ultimo)

    base, p["password"], ui = buono
    info = f"scadenza {ui.get('exp_date') or '-'}"

    ids = []
    try:
        st, body = await _api_xtream(session, base, p["username"], p["password"],
                                     "get_live_streams")
        for c in json.loads(body) or []:
            sid = c.get("stream_id") or c.get("id")
            if sid:
                ids.append(sid)
    except Exception:
        ids = []

    urls = []
    for sid in _mescola(ids, 6):
        for ext in ("m3u8", "ts"):
            urls.append(f"{base}/live/{p['username']}/{p['password']}/{sid}.{ext}")
    if urls:
        f = await _primo_frame(urls)
        if f:
            return Result(True, "ok", f, info)

    try:
        st, corpo = await _get(
            session, f"{base}/get.php?username={p['username']}&password={p['password']}"
                     f"&type=m3u_plus&output=m3u8", timeout=30)
    except Exception as e:
        return Result(False, f"connessione: {type(e).__name__}", info=info)
    if st < 400:
        f = await _primo_frame(_mescola(_canali_m3u(corpo), 9))
        if f:
            return Result(True, "ok", f, info)
    return Result(False, "nessun canale riproducibile", info=info)


async def _test_stalker(session, p):
    if not p.get("mac"):
        return Result(False, "MAC mancante")
    base = p["portal"].rstrip("/")
    ck = {"Cookie": f"mac={p['mac']}; stb_lang=en; timezone=Europe/Rome"}
    try:
        st, body = await _get(
            session,
            f"{base}/server/load.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml",
            ck)
    except Exception as e:
        return Result(False, f"connessione: {type(e).__name__}")
    try:
        tok = (json.loads(body).get("js") or {}).get("token")
    except Exception:
        tok = None
    if not tok:
        return Result(False, "handshake fallito")
    ck["Authorization"] = f"Bearer {tok}"
    try:
        st, body = await _get(
            session,
            f"{base}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml",
            ck, 30)
    except Exception as e:
        return Result(False, f"connessione: {type(e).__name__}")
    try:
        chans = (json.loads(body).get("js") or {}).get("data") or []
    except Exception:
        chans = []
    if not chans:
        return Result(False, "nessun canale nell'elenco")
    urls = [f"{base}/play/live.php?mac={p['mac']}&stream={c.get('id')}&extension=m3u8"
            for c in _mescola(chans, 6)]
    f = await _primo_frame(urls)
    if f:
        return Result(True, "ok", f, f"{len(chans)} canali")
    return Result(False, "nessun canale riproducibile")


async def test_list(kind, payload):
    try:
        _ensure_bins()
    except Exception:
        pass
    try:
        conn = aiohttp.TCPConnector(force_close=True, limit=8)
        async with aiohttp.ClientSession(connector=conn) as s:
            if kind == "xtream":
                coro = _test_xtream(s, payload)
            elif kind == "stalker":
                coro = _test_stalker(s, payload)
            else:
                coro = _test_m3u(s, payload)
            return await asyncio.wait_for(coro, timeout=TEST_BUDGET)
    except asyncio.TimeoutError:
        return Result(False, f"tempo scaduto ({TEST_BUDGET}s)")
    except Exception as e:
        return Result(False, f"errore: {type(e).__name__}")


GETPHP = re.compile(r"https?://[^\s<>\"']*?/get\.php\?[^\s<>\"']*?username=([^&\s]+)"
                    r"[^\s<>\"']*?password=([^&\s]+)", re.I)


def _host_voto(text, host):
    parti = host.split(".")
    if len(parti) < 2:
        return host
    dominio = ".".join(parti[-2:])
    conta = {}
    pat = r"(?<![a-z0-9.\-@/])[a-z0-9][a-z0-9.\-]*\." + re.escape(dominio)
    for m in re.finditer(pat, text, re.I):
        h = m.group(0).lower().rstrip(".")
        conta[h] = conta.get(h, 0) + 1
    if not conta:
        return host
    return max(conta, key=conta.get)


def detect_many(text):
    if not text:
        return []
    items, seen = [], set()

    def add(it):
        p = it["payload"]
        key = "|".join([it["kind"], str(p.get("server", "")), str(p.get("url", "")),
                        str(p.get("portal", "")), str(p.get("username", "")),
                        str(p.get("password", "")), str(p.get("mac", ""))])
        if key in seen:
            return
        seen.add(key)
        items.append(it)

    for m in GETPHP.finditer(text):
        raw = m.group(0)
        user = m.group(1).rstrip(".,;:)]'\"")
        pwd = m.group(2).rstrip(".,;:)]'\"")
        alts = []
        net = re.match(r"(https?://)([^/\s]+)", raw)
        if net:
            scheme, netloc = net.group(1), net.group(2)
            if ":" in netloc:
                host, port = netloc.rpartition(":")[0], netloc.rpartition(":")[2]
            else:
                host, port = netloc, ""
            host = _host_voto(text, host.rsplit("@", 1)[-1])
            server = f"{scheme}{host}" + (f":{port}" if port else "")
            if "@" in netloc:
                alts.append(f"{scheme}{netloc.split('@', 1)[1]}")
        else:
            server = _base(raw)
        add({"kind": "xtream", "label": f"{server} · {user}",
             "payload": {"server": server, "username": user,
                         "password": pwd, "alts": alts}})

    for m in URL.finditer(text):
        u = m.group(0).rstrip(".,;)")
        low = u.lower()
        if "get.php" in low:
            continue
        if M3U_EXT.search(low):
            add({"kind": "m3u", "label": _base(u), "payload": {"url": u}})
        elif "/c/" in low or "portal.php" in low:
            it = {"kind": "stalker", "label": _base(u),
                  "payload": {"portal": _base(u), "mac": ""}}
            it["_pos"] = m.start()
            add(it)

    mac_pos = [(m.start(), m.group(0).upper()) for m in MAC.finditer(text)]
    usati = set()
    for it in items:
        if it["kind"] != "stalker" or it["payload"].get("mac"):
            it.pop("_pos", None)
            continue
        pos = it.pop("_pos", None)
        if pos is None:
            continue
        candidati = [(abs(pos - mp), i, mm) for i, (mp, mm) in enumerate(mac_pos)
                     if i not in usati]
        if candidati:
            _, i, mm = min(candidati)
            usati.add(i)
            it["payload"]["mac"] = mm

    senza_mac = [i for i in items if i["kind"] == "stalker" and not i["payload"]["mac"]]
    if senza_mac:
        print(f"detect_many: {len(senza_mac)} portali scartati, MAC non trovato")
    return [i for i in items if i["kind"] != "stalker" or i["payload"]["mac"]]
