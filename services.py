import asyncio
import io
import json
import random
import re
import shutil
import subprocess
import uuid

import aiohttp
import pytesseract
from PIL import Image, ImageOps

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

_BINS_OK = False


def _ensure_bins():
    global _BINS_OK
    if _BINS_OK:
        return
    _BINS_OK = True
    manca = [p for p in ("tesseract", "ffmpeg") if not shutil.which(p)]
    if not manca:
        return
    pacchetti = [{"tesseract": "tesseract-ocr", "ffmpeg": "ffmpeg"}[p] for p in manca]
    print("mancano:", manca)
    try:
        subprocess.run(["apt-get", "update", "-qq"], timeout=180)
        subprocess.run(["apt-get", "install", "-y", "-qq", *pacchetti,
                        "tesseract-ocr-eng"], timeout=420)
    except Exception as e:
        print("installazione fallita:", e)
    print("ffmpeg:", shutil.which("ffmpeg"), "tesseract:", shutil.which("tesseract"))


def ocr_bytes(data):
    _ensure_bins()
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    if w < 2200:
        k = min(4, max(2, 2200 // max(w, 1)))
        img = img.resize((w * k, h * k), Image.LANCZOS)
    if sum(img.getdata()) / (img.size[0] * img.size[1]) < 110:
        img = ImageOps.invert(img)
    img = ImageOps.autocontrast(img)
    migliore = ""
    for psm in ("4", "6", "11"):
        out = pytesseract.image_to_string(img, lang="eng", config=f"--oem 3 --psm {psm}")
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
                    "payload": {"server": _base(u), "username": qu.group(1), "password": qp.group(1)}}
        if "/c/" in low or "portal.php" in low or "stalker" in low:
            return {"kind": "stalker", "label": _base(u),
                    "payload": {"portal": _base(u), "mac": mac.group(0).upper() if mac else ""}}
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
        return f"Tipo: Stalker Portal\nPortal: <code>{p['portal']}</code>\nMAC: <code>{p['mac']}</code>"
    return f"Tipo: M3U\nURL: <code>{p['url']}</code>"


class Result:
    def __init__(self, ok, note="", frame=None, info=""):
        self.ok, self.note, self.frame, self.info = ok, note, frame, info


def _out():
    return config.MEDIA_DIR / f"{uuid.uuid4().hex}.jpg"


async def grab_frame(url):
    out = _out()
    cmd = [config.FFMPEG, "-y", "-loglevel", "error", "-user_agent", UA,
           "-rw_timeout", "12000000", "-probesize", "600000", "-analyzeduration", "1500000",
           "-i", url, "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(out)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=25)
        except asyncio.TimeoutError:
            proc.kill()
            out.unlink(missing_ok=True)
            return None
    except FileNotFoundError:
        return None
    if out.exists() and out.stat().st_size > 4000:
        return out
    out.unlink(missing_ok=True)
    return None


async def _primo_frame(urls, blocchi=3):
    for i in range(0, len(urls), blocchi):
        gruppo = urls[i:i + blocchi]
        esiti = await asyncio.gather(*(grab_frame(u) for u in gruppo))
        buoni = [f for f in esiti if f]
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


async def _get(session, url, headers=None, timeout=25):
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
    st, body = await _get(session, p["url"], timeout=30)
    if st >= 400:
        return Result(False, f"HTTP {st}")
    urls = _canali_m3u(body)
    if not urls:
        return Result(False, "playlist vuota o non valida")
    f = await _primo_frame(_mescola(urls, 18))
    if f:
        return Result(True, "ok", f, f"{len(urls)} canali")
    return Result(False, "nessuno stream riproducibile")


def _pwd_varianti(pwd):
    v = [pwd]
    for a, b in (("0", "o"), ("o", "0"), ("1", "l"), ("l", "1"), ("O", "0"), ("S", "5")):
        x = pwd.replace(a, b)
        if x != pwd and x not in v:
            v.append(x)
    for x in list(v):
        if x.endswith(".") and (x[:-1] + "0") not in v:
            v.append(x[:-1] + "0")
        if x.endswith("0") and (x[:-1] + ".") not in v:
            v.append(x[:-1] + ".")
    return v


async def _api_xtream(session, srv, user, pwd, azione=None):
    q = f"username={user}&password={pwd}"
    if azione:
        q += f"&action={azione}"
    return await _get(session, f"{srv}/player_api.php?{q}", timeout=30)


async def _test_xtream(session, p):
    ultimo = "host non raggiungibile"
    buono = None
    for srv in [p["server"], *(p.get("alts") or [])]:
        for pwd in _pwd_varianti(p["password"]):
            st, body = await _api_xtream(session, srv, p["username"], pwd)
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

    st, body = await _api_xtream(session, base, p["username"], p["password"], "get_live_streams")
    ids = []
    try:
        for c in json.loads(body) or []:
            sid = c.get("stream_id") or c.get("id")
            if sid:
                ids.append(sid)
    except Exception:
        pass

    urls = []
    for sid in _mescola(ids, 12):
        for ext in ("m3u8", "ts"):
            urls.append(f"{base}/live/{p['username']}/{p['password']}/{sid}.{ext}")
    if urls:
        f = await _primo_frame(urls)
        if f:
            return Result(True, "ok", f, info)

    st, corpo = await _get(
        session, f"{base}/get.php?username={p['username']}&password={p['password']}"
                 f"&type=m3u_plus&output=m3u8", timeout=35)
    if st < 400:
        f = await _primo_frame(_mescola(_canali_m3u(corpo), 15))
        if f:
            return Result(True, "ok", f, info)
    return Result(False, "nessun canale riproducibile", info=info)


async def _test_stalker(session, p):
    if not p.get("mac"):
        return Result(False, "MAC mancante")
    base = p["portal"].rstrip("/")
    ck = {"Cookie": f"mac={p['mac']}; stb_lang=en; timezone=Europe/Rome"}
    st, body = await _get(
        session, f"{base}/server/load.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml", ck)
    try:
        tok = (json.loads(body).get("js") or {}).get("token")
    except Exception:
        tok = None
    if not tok:
        return Result(False, "handshake fallito")
    ck["Authorization"] = f"Bearer {tok}"
    st, body = await _get(
        session, f"{base}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml",
        ck, 35)
    try:
        chans = (json.loads(body).get("js") or {}).get("data") or []
    except Exception:
        chans = []
    if not chans:
        return Result(False, "nessun canale nell'elenco")
    urls = [f"{base}/play/live.php?mac={p['mac']}&stream={c.get('id')}&extension=m3u8"
            for c in _mescola(chans, 10)]
    f = await _primo_frame(urls)
    if f:
        return Result(True, "ok", f, f"{len(chans)} canali")
    return Result(False, "nessun canale riproducibile")


async def test_list(kind, payload):
    try:
        _ensure_bins()
        conn = aiohttp.TCPConnector(force_close=True, limit=8)
        async with aiohttp.ClientSession(connector=conn) as s:
            if kind == "xtream":
                return await _test_xtream(s, payload)
            if kind == "stalker":
                return await _test_stalker(s, payload)
            return await _test_m3u(s, payload)
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
    pat = r"(?<![a-z0-9.\-@/])[a-z0-9][a-z0-9.\-@]*\." + re.escape(dominio)
    for m in re.finditer(pat, text, re.I):
        h = m.group(0).lower().rstrip(".").replace("@", "0")
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
        key = it["kind"] + "|" + "|".join(
            str(p.get(k, "")) for k in ("server", "url", "portal", "username", "password"))
        if key in seen:
            return
        seen.add(key)
        items.append(it)

    srv_host = None
    m2 = re.search(r"server\s*:\s*(?:https?://)?([a-z0-9.\-]+\.[a-z]{2,})(?::(\d{2,5}))?",
                   text, re.I)
    if m2:
        srv_host = (m2.group(1), m2.group(2))

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
            add({"kind": "stalker", "label": _base(u),
                 "payload": {"portal": _base(u), "mac": ""}})

    macs = [m.group(0).upper() for m in MAC.finditer(text)]
    for it in items:
        if it["kind"] == "stalker" and not it["payload"]["mac"] and macs:
            it["payload"]["mac"] = macs.pop(0)

    return [i for i in items if i["kind"] != "stalker" or i["payload"]["mac"]]
