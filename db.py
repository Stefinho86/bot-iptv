import datetime as dt
import json

import asyncpg

import config
from services import chiavi_lista

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(
  user_id BIGINT PRIMARY KEY, username TEXT, added_by BIGINT, added_at TIMESTAMPTZ NOT NULL);

CREATE TABLE IF NOT EXISTS admin_names(
  username TEXT PRIMARY KEY, added_by BIGINT, added_at TIMESTAMPTZ NOT NULL);

CREATE TABLE IF NOT EXISTS users(
  user_id BIGINT PRIMARY KEY, username TEXT, full_name TEXT, first_seen TIMESTAMPTZ NOT NULL,
  last_request TIMESTAMPTZ, next_available TIMESTAMPTZ,
  banned BOOLEAN NOT NULL DEFAULT FALSE, ban_reason TEXT);

CREATE TABLE IF NOT EXISTS lists(
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL, payload JSONB NOT NULL, label TEXT,
  status TEXT NOT NULL DEFAULT 'free', added_by BIGINT, added_at TIMESTAMPTZ NOT NULL,
  tested BOOLEAN NOT NULL DEFAULT FALSE, test_ok BOOLEAN NOT NULL DEFAULT FALSE, test_note TEXT);

CREATE TABLE IF NOT EXISTS assignments(
  id SERIAL PRIMARY KEY, list_id INTEGER NOT NULL, user_id BIGINT NOT NULL,
  assigned_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ, revoked BOOLEAN NOT NULL DEFAULT FALSE);

CREATE TABLE IF NOT EXISTS anon_verified(
  chat_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, verified_at TIMESTAMPTZ NOT NULL);

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS logs(
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, actor BIGINT, action TEXT NOT NULL, detail TEXT);

CREATE INDEX IF NOT EXISTS idx_lists_status ON lists(status);
CREATE INDEX IF NOT EXISTS idx_assign_user ON assignments(user_id);
"""

DEFAULTS = {
    "cooldown_days": "7",
    "enabled": "1",
    "send_proof": "1",
    "max_attempts": "6",
    "anon_ttl_min": "120",
    "group_id": "0",
    "group_only": "1",
    "group_link": "",
    "not_member": "Per usare questo bot devi essere membro del gruppo ufficiale. Entra nel gruppo, poi riprova con /start.",
    "welcome": "Ciao {name}!\n\nPremi il pulsante per ricevere una lista IPTV. Una lista ogni {days} giorni.",
    "deny_text": "Hai gia ricevuto una lista. Potrai richiederne un'altra <b>{when}</b> (tra {left}).",
    "no_lists": "Al momento non ci sono liste disponibili. Riprova piu tardi.",
    "broken_all": "Nessuna lista ha superato il test. Avvisa un admin.",
}

_pool = None


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def init():
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5,
                                      ssl=False, command_timeout=30)
    async with _pool.acquire() as c:
        await c.execute(SCHEMA)
        for k, v in DEFAULTS.items():
            await c.execute("INSERT INTO settings(key,value) VALUES($1,$2) "
                            "ON CONFLICT(key) DO NOTHING", k, v)


async def close():
    if _pool:
        await _pool.close()


async def log(actor, action, detail=""):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO logs(ts,actor,action,detail) VALUES($1,$2,$3,$4)",
                        now(), actor, action, detail)


async def all_logs(limit=3000):
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT g.id, g.ts, g.actor, g.action, g.detail, u.username, u.full_name "
            "FROM logs g LEFT JOIN users u ON u.user_id = g.actor "
            "ORDER BY g.id DESC LIMIT $1", limit)


async def get_setting(key):
    async with _pool.acquire() as c:
        val = await c.fetchval("SELECT value FROM settings WHERE key=$1", key)
    return val if val is not None else DEFAULTS.get(key, "")


async def get_int(key):
    try:
        return int(await get_setting(key))
    except ValueError:
        return int(DEFAULTS.get(key, "0"))


async def get_group_id():
    raw = (await get_setting("group_id") or "").strip()
    if raw and raw != "0":
        try:
            return int(raw)
        except ValueError:
            pass
    return config.GROUP_ID


async def set_setting(key, value):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO settings(key,value) VALUES($1,$2) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, value)


async def is_admin(uid, username=None):
    if uid in config.SUPER_ADMINS:
        return True
    async with _pool.acquire() as c:
        if await c.fetchval("SELECT 1 FROM admins WHERE user_id=$1", uid) is not None:
            return True
        if username:
            return await c.fetchval(
                "SELECT 1 FROM admin_names WHERE lower(username)=lower($1)", username) is not None
    return False


async def add_admin_name(username, by):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO admin_names(username,added_by,added_at) VALUES($1,$2,$3) "
                        "ON CONFLICT(username) DO NOTHING", username.lstrip("@"), by, now())


async def del_admin_name(username):
    async with _pool.acquire() as c:
        await c.execute("DELETE FROM admin_names WHERE lower(username)=lower($1)",
                        username.lstrip("@"))


async def list_admin_names():
    async with _pool.acquire() as c:
        return await c.fetch("SELECT username FROM admin_names ORDER BY added_at")


async def add_admin(uid, username, by):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO admins(user_id,username,added_by,added_at) VALUES($1,$2,$3,$4) "
                        "ON CONFLICT(user_id) DO NOTHING", uid, username, by, now())


async def del_admin(uid):
    async with _pool.acquire() as c:
        await c.execute("DELETE FROM admins WHERE user_id=$1", uid)


async def list_admins():
    async with _pool.acquire() as c:
        return await c.fetch("SELECT user_id, username FROM admins ORDER BY added_at")


async def set_anon_verified(chat_id, user_id):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO anon_verified(chat_id,user_id,verified_at) VALUES($1,$2,$3) "
                        "ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id, "
                        "verified_at=excluded.verified_at", chat_id, user_id, now())


async def get_anon_verified(chat_id):
    async with _pool.acquire() as c:
        return await c.fetchval("SELECT user_id FROM anon_verified WHERE chat_id=$1", chat_id)


async def clear_anon_verified(chat_id):
    async with _pool.acquire() as c:
        await c.execute("DELETE FROM anon_verified WHERE chat_id=$1", chat_id)


async def clear_anon_by_user(uid):
    async with _pool.acquire() as c:
        await c.execute("DELETE FROM anon_verified WHERE user_id=$1", uid)


async def touch_user(uid, username, full_name):
    async with _pool.acquire() as c:
        await c.execute(
            "INSERT INTO users(user_id,username,full_name,first_seen) VALUES($1,$2,$3,$4) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "full_name=excluded.full_name", uid, username, full_name, now())
        if username:
            migrato = await c.fetchval(
                "DELETE FROM admin_names WHERE lower(username)=lower($1) RETURNING username",
                username)
            if migrato:
                await c.execute(
                    "INSERT INTO admins(user_id,username,added_at) VALUES($1,$2,$3) "
                    "ON CONFLICT(user_id) DO NOTHING", uid, username, now())


async def get_user(uid):
    async with _pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)


async def set_next(uid, when):
    async with _pool.acquire() as c:
        await c.execute("UPDATE users SET last_request=$1, next_available=$2 WHERE user_id=$3",
                        now(), when, uid)


async def clear_next(uid):
    async with _pool.acquire() as c:
        await c.execute("UPDATE users SET next_available=NULL WHERE user_id=$1", uid)


async def ban(uid, reason):
    async with _pool.acquire() as c:
        await c.execute("UPDATE users SET banned=TRUE, ban_reason=$1 WHERE user_id=$2", reason, uid)


async def unban(uid):
    async with _pool.acquire() as c:
        await c.execute("UPDATE users SET banned=FALSE, ban_reason=NULL WHERE user_id=$1", uid)


async def find_users(q):
    q2 = q.lstrip("@")
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT * FROM users WHERE user_id::text=$1 OR username ILIKE $2 LIMIT 10",
            q2, f"%{q2}%")


async def find_by_username(name):
    async with _pool.acquire() as c:
        return await c.fetchval("SELECT user_id FROM users WHERE username ILIKE $1 LIMIT 1", name)


async def ensure_user(uid):
    async with _pool.acquire() as c:
        await c.execute("INSERT INTO users(user_id,first_seen) VALUES($1,$2) "
                        "ON CONFLICT(user_id) DO NOTHING", uid, now())


async def add_list(kind, payload, label, by):
    async with _pool.acquire() as c:
        return await c.fetchval(
            "INSERT INTO lists(kind,payload,label,added_by,added_at) VALUES($1,$2,$3,$4,$5) RETURNING id",
            kind, json.dumps(payload, ensure_ascii=False), label, by, now())


async def lista_simile(kind, payload):
    chiavi = chiavi_lista(kind, payload)
    if not chiavi:
        return None
    async with _pool.acquire() as c:
        righe = await c.fetch(
            "SELECT id, kind, payload, status, label FROM lists WHERE kind=$1 ORDER BY id", kind)
    for r in righe:
        if chiavi & chiavi_lista(kind, json.loads(r["payload"])):
            return r
    return None


async def set_test(lid, ok, note):
    async with _pool.acquire() as c:
        await c.execute("UPDATE lists SET tested=TRUE, test_ok=$1, test_note=$2 WHERE id=$3",
                        ok, (note or "")[:400], lid)


async def free_count():
    async with _pool.acquire() as c:
        return await c.fetchval("SELECT COUNT(*) FROM lists WHERE status='free'")


async def random_free():
    async with _pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM lists WHERE status='free' ORDER BY random() LIMIT 1")


async def random_free_ok():
    async with _pool.acquire() as c:
        return await c.fetchrow(
            "SELECT * FROM lists WHERE status='free' AND test_ok=TRUE ORDER BY random() LIMIT 1")


async def free_lists(limit=15):
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT id,kind,label,added_at FROM lists WHERE status='free' ORDER BY id DESC LIMIT $1",
            limit)


async def payload_of(row):
    return json.loads(row["payload"])


async def assign(lid, uid, expires_at):
    async with _pool.acquire() as c:
        await c.execute("UPDATE lists SET status='assigned' WHERE id=$1", lid)
        return await c.fetchval(
            "INSERT INTO assignments(list_id,user_id,assigned_at,expires_at) VALUES($1,$2,$3,$4) RETURNING id",
            lid, uid, now(), expires_at)


async def last_assignment(uid):
    async with _pool.acquire() as c:
        return await c.fetchrow(
            "SELECT a.*, l.kind, l.label FROM assignments a JOIN lists l ON l.id=a.list_id "
            "WHERE a.user_id=$1 AND a.revoked=FALSE ORDER BY a.id DESC LIMIT 1", uid)


async def assignments(limit=20):
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT a.*, l.kind, l.label, u.username FROM assignments a "
            "JOIN lists l ON l.id=a.list_id LEFT JOIN users u ON u.user_id=a.user_id "
            "ORDER BY a.id DESC LIMIT $1", limit)


async def revoke(aid):
    async with _pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM assignments WHERE id=$1", aid)
        if not row:
            return None
        await c.execute("UPDATE assignments SET revoked=TRUE WHERE id=$1", aid)
        await c.execute("UPDATE lists SET status='free' WHERE id=$1", row["list_id"])
        await c.execute("UPDATE users SET next_available=NULL WHERE user_id=$1", row["user_id"])
        return row


async def stats():
    async with _pool.acquire() as c:
        out = {}
        for label, sql in (
            ("free", "SELECT COUNT(*) FROM lists WHERE status='free'"),
            ("assigned", "SELECT COUNT(*) FROM lists WHERE status='assigned'"),
            ("users", "SELECT COUNT(*) FROM users"),
            ("banned", "SELECT COUNT(*) FROM users WHERE banned=TRUE"),
        ):
            out[label] = await c.fetchval(sql)
        return out


async def all_free_payloads():
    async with _pool.acquire() as c:
        rows = await c.fetch("SELECT id,kind,payload,label,added_at FROM lists WHERE status='free'")
        return [{**dict(r), "added_at": r["added_at"].isoformat()} for r in rows]


async def all_users(offset, limit, escludi):
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT user_id, username, full_name, banned FROM users "
            "WHERE user_id <> ALL($3::bigint[]) "
            "AND user_id NOT IN (SELECT user_id FROM admins) "
            "AND (username IS NULL OR lower(username) NOT IN "
            "     (SELECT lower(username) FROM admin_names)) "
            "ORDER BY first_seen DESC OFFSET $1 LIMIT $2",
            offset, limit, list(escludi))


async def count_users(escludi):
    async with _pool.acquire() as c:
        return await c.fetchval(
            "SELECT COUNT(*) FROM users WHERE user_id <> ALL($1::bigint[]) "
            "AND user_id NOT IN (SELECT user_id FROM admins) "
            "AND (username IS NULL OR lower(username) NOT IN "
            "     (SELECT lower(username) FROM admin_names))", list(escludi))


async def banned_users(offset, limit):
    async with _pool.acquire() as c:
        return await c.fetch(
            "SELECT user_id, username, full_name, ban_reason FROM users "
            "WHERE banned = TRUE ORDER BY first_seen DESC OFFSET $1 LIMIT $2", offset, limit)


async def count_banned():
    async with _pool.acquire() as c:
        return await c.fetchval("SELECT COUNT(*) FROM users WHERE banned = TRUE")


async def all_admins():
    async with _pool.acquire() as c:
        return await c.fetch("SELECT user_id, username FROM admins ORDER BY added_at")


async def get_list(lid):
    async with _pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM lists WHERE id=$1", lid)


async def free_lists_kind(kind, offset, limit):
    async with _pool.acquire() as c:
        if kind:
            return await c.fetch(
                "SELECT * FROM lists WHERE status='free' AND kind=$1 "
                "ORDER BY id ASC OFFSET $2 LIMIT $3", kind, offset, limit)
        return await c.fetch(
            "SELECT * FROM lists WHERE status='free' ORDER BY id ASC OFFSET $1 LIMIT $2",
            offset, limit)


async def count_free_kind(kind):
    async with _pool.acquire() as c:
        if kind:
            return await c.fetchval("SELECT COUNT(*) FROM lists WHERE status='free' AND kind=$1", kind)
        return await c.fetchval("SELECT COUNT(*) FROM lists WHERE status='free'")


async def set_payload(lid, payload):
    async with _pool.acquire() as c:
        await c.execute("UPDATE lists SET payload=$1 WHERE id=$2",
                        json.dumps(payload, ensure_ascii=False), lid)


async def del_list(lid):
    async with _pool.acquire() as c:
        return await c.fetchval("DELETE FROM lists WHERE id=$1 AND status='free' RETURNING id",
                                int(lid))
