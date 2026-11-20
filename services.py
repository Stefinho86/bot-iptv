import io

import pytesseract
from PIL import Image, ImageOps


def ocr_bytes(data):
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    if w < 1600:
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img, lang="eng", config="--psm 6")
      
import asyncio
import json
import re
import uuid
from pathlib import Path

import aiohttp

import config

MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
URL = re.compile(r"https?://[^\s<>\"']+")
HOST = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,})(?::(\d{2,5}))?\b", re.I)
KV_U = re.compile(r"(?:username|user|login|utente|usr)\s*[:=]\s*([^\s&,;\"']+)", re.I)
KV_P = re.compile(r"(?:password|pass|pwd|senha|clave)\s*[:=]\s*([^\s&,;\"']+)", re.I)
PAIR = re.compile(r"^\s*([A-Za-z0-9._\-]{2,40})\s*[:/|]\s*([^\s]{2,40})\s*$", re.M)
CLEAN = re.compile(r"^(?:user(?:name)?|login|pass(?:word)?|pwd|utente)\s*[:=]\s*", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept": "*/*"}


def _base(url):
    m = re.match(r"(https?://[^/\s]+)", url)
    return m.group(1) if m else url


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
        if qu and qp:
            return {"kind": "xtream", "label": _base(u),
                    "payload": {"server": _base(u), "username": qu.group(1), "password": qp.group(1)}}
        if "/c/" in low or "portal.php" in low or "stalker" in low:
            return {"kind": "stalker", "label": _base(u),
                    "payload": {"portal": _base(u), "mac": mac.group(0).upper() if mac else ""}}
        if low.endswith((".m3u", ".m3u8")) or "get.php" in low or "type=m3u" in low:
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
           "-rw_timeout", "15000000", "-i", url, "-frames:v", "1",
           "-vf", "scale=720:-2", "-q:v", "3", str(out)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=40)
        except asyncio.TimeoutError:
            proc.kill()
            return None
    except FileNotFoundError:
        return None
    if out.exists() and out.stat().st_size > 4000:
        return out
    out.unlink(missing_ok=True)
    return None


async def _get(session, url, headers=None, timeout=20):
    h = {**HEAD, **(headers or {})}
    async with session.get(url, headers=h, ssl=False,
                           timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return r.status, await r.text(errors="ignore")


async def _test_m3u(session, p):
    st, body = await _get(session, p["url"], timeout=25)
    if st >= 400:
        return Result(False, f"HTTP {st}")
    urls = [l.strip() for l in body.splitlines() if l.strip().startswith(("http://", "https://"))]
    if not urls:
        return Result(False, "playlist vuota")
    for u in urls[:6]:
        f = await grab_frame(u)
        if f:
            return Result(True, "ok", f, f"{len(urls)} canali")
    return Result(False, "nessuno stream riproducibile")


async def _test_xtream(session, p):
    q = f"username={p['username']}&password={p['password']}"
    st, body = await _get(session, f"{p['server']}/player_api.php?{q}")
    if st >= 400:
        return Result(False, f"HTTP {st}")
    try:
        j = json.loads(body)
    except Exception:
        return Result(False, "host errato o risposta non valida")
    ui = j.get("user_info") or {}
    if ui.get("auth") in (0, False):
        return Result(False, "credenziali rifiutate")
    if str(ui.get("status", "")).lower() not in ("active", "1", "true"):
        return Result(False, f"stato {ui.get('status')}")
    info = f"scadenza {ui.get('exp_date') or '-'}"
    st, body = await _get(session, f"{p['server']}/player_api.php?{q}&action=get_live_streams", timeout=30)
    try:
        chans = json.loads(body)
    except Exception:
        chans = []
    for c in (chans or [])[:5]:
        sid = c.get("stream_id") or c.get("id")
        for ext in ("m3u8", "ts"):
            f = await grab_frame(f"{p['server']}/live/{p['username']}/{p['password']}/{sid}.{ext}")
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
        session, f"{base}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml", ck, 30)
    try:
        chans = (json.loads(body).get("js") or {}).get("data") or []
    except Exception:
        chans = []
    for c in chans[:5]:
        f = await grab_frame(f"{base}/play/live.php?mac={p['mac']}&stream={c.get('id')}&extension=m3u8")
        if f:
            return Result(True, "ok", f, f"{len(chans)} canali")
    return Result(False, "nessun canale riproducibile")


async def test_list(kind, payload):
    try:
        async with aiohttp.ClientSession() as s:
            if kind == "xtream":
                return await _test_xtream(s, payload)
            if kind == "stalker":
                return await _test_stalker(s, payload)
            return await _test_m3u(s, payload)
    except Exception as e:
        return Result(False, f"errore: {type(e).__name__}")

GETPHP = re.compile(r"https?://[^\s<>\"']*?/get\.php\?[^\s<>\"']*?username=([^&\s]+)"
                    r"[^\s<>\"']*?password=([^&\s]+)", re.I)


def detect_many(text):
    if not text:
        return []
    items, seen = [], set()

    def add(it):
        p = it["payload"]
        key = it["kind"] + "|" + "|".join(str(p.get(k, "")) for k in ("server", "url", "portal", "username", "password"))
        if key in seen:
            return
        seen.add(key)
        items.append(it)

    for m in GETPHP.finditer(text):
        server = _base(m.group(0))
        add({"kind": "xtream", "label": f"{server} · {m.group(1)}",
             "payload": {"server": server, "username": m.group(1), "password": m.group(2)}})

    for m in URL.finditer(text):
        u = m.group(0).rstrip(".,;)")
        low = u.lower()
        if "get.php" in low:
            continue
        if low.endswith((".m3u", ".m3u8")):
            add({"kind": "m3u", "label": _base(u), "payload": {"url": u}})
        elif "/c/" in low or "portal.php" in low:
            add({"kind": "stalker", "label": _base(u), "payload": {"portal": _base(u), "mac": ""}})

    macs = [m.group(0).upper() for m in MAC.finditer(text)]
    for it in items:
        if it["kind"] == "stalker" and not it["payload"]["mac"] and macs:
            it["payload"]["mac"] = macs.pop(0)

    return [i for i in items if i["kind"] != "stalker" or i["payload"]["mac"]]
