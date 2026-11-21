import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord import app_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("licensebot")

CFG = json.loads(Path(__file__).with_name("config.json").read_text("utf-8"))
DB = sqlite3.connect(Path(__file__).with_name("state.db"))
DB.executescript(
    """
    CREATE TABLE IF NOT EXISTS players(
        key TEXT PRIMARY KEY, mc TEXT, server TEXT, version TEXT,
        session_start INTEGER, last_ts INTEGER);
    CREATE TABLE IF NOT EXISTS cmds(key TEXT PRIMARY KEY, cmd TEXT, ts INTEGER);
    """
)
DB.commit()


def now_ms():
    return int(time.time() * 1000)


def now_s():
    return int(time.time())


class Bin:
    def __init__(self):
        self.url = f"https://api.jsonbin.io/v3/b/{CFG['jsonbin_bin_id']}"
        self.headers = {"X-Master-Key": CFG["jsonbin_master_key"], "Content-Type": "application/json"}
        self.session = None
        self.cache = None
        self.cache_at = 0.0
        self.lock = asyncio.Lock()

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def read(self, fresh=False):
        if not fresh and self.cache is not None and time.time() - self.cache_at < CFG["cache_seconds"]:
            return self.cache
        async with self.session.get(self.url + "/latest", headers={**self.headers, "X-Bin-Meta": "false"}) as r:
            r.raise_for_status()
            data = await r.json()
        if isinstance(data, dict) and "record" in data and "metadata" in data:
            data = data["record"]
        self.cache, self.cache_at = data, time.time()
        return data

    async def write(self, data):
        async with self.session.put(self.url, headers=self.headers, data=json.dumps(data)) as r:
            r.raise_for_status()
        self.cache, self.cache_at = data, time.time()

    @staticmethod
    def entries(data):
        keys = data.get("keys", []) if isinstance(data, dict) else []
        return [e for e in keys if isinstance(e, dict)]

    def find(self, data, key):
        for e in self.entries(data):
            if str(e.get("key", "")) == key:
                return e
        return None

    async def mutate(self, key, fn):
        async with self.lock:
            data = await self.read(fresh=True)
            e = self.find(data, key)
            if e is None:
                return None
            fn(e)
            await self.write(data)
            return e


BIN = Bin()


def ban_entry(e, reason):
    if not e.get("banned"):
        e["banned"] = True
        e["expires_before_ban"] = e.get("expires", 0)
        e["days_before_ban"] = e.get("days", 7)
    e["ban_reason"] = reason
    e["expires"] = 1
    e["days"] = 0


def unban_entry(e):
    if e.get("banned"):
        e["expires"] = e.pop("expires_before_ban", 0)
        e["days"] = e.pop("days_before_ban", 7)
    e.pop("banned", None)
    e.pop("ban_reason", None)


def queue_cmd(key, cmd):
    DB.execute("INSERT OR REPLACE INTO cmds(key,cmd,ts) VALUES(?,?,?)", (key, cmd, now_s()))
    DB.commit()


def pop_cmd(key):
    row = DB.execute("SELECT cmd,ts FROM cmds WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    DB.execute("DELETE FROM cmds WHERE key=?", (key,))
    DB.commit()
    return row[0] if now_s() - row[1] <= CFG["command_ttl_seconds"] else None


def player(key):
    return DB.execute("SELECT mc,server,version,session_start,last_ts FROM players WHERE key=?", (key,)).fetchone()


def is_online(key):
    p = player(key)
    return bool(p) and now_s() - p[4] <= CFG["online_timeout_seconds"]


intents = discord.Intents.none()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
NAMES = {}


async def dc_name(e):
    d = e.get("discord")
    uid = None
    fallback = None
    if isinstance(d, dict):
        uid = d.get("id")
        fallback = d.get("username") or d.get("name")
    elif d:
        uid = d
    if not uid:
        return "—"
    uid = str(uid)
    if uid in NAMES:
        return NAMES[uid]
    try:
        u = client.get_user(int(uid)) or await client.fetch_user(int(uid))
        NAMES[uid] = f"{u.name} ({uid})"
    except Exception:
        NAMES[uid] = f"{fallback or 'nieznany'} ({uid})"
    return NAMES[uid]


def status_of(e):
    if e.get("banned"):
        return "🔴 ban"
    if not e.get("hwid"):
        return "⚫ nieaktywowany"
    if int(e.get("expires", 0) or 0) <= now_ms():
        return "⚪ wygasł"
    return "🟢 online" if is_online(str(e.get("key"))) else "🟡 offline"


def is_admin(i: discord.Interaction):
    if i.user.id in CFG["admin_user_ids"]:
        return True
    roles = getattr(i.user, "roles", [])
    return any(r.id in CFG["admin_role_ids"] for r in roles)


async def admin_check(i: discord.Interaction):
    if is_admin(i):
        return True
    await i.response.send_message("Brak uprawnień.", ephemeral=True)
    return False


class Pager(discord.ui.View):
    def __init__(self, user_id, title, lines, per=10, color=0x5865F2):
        super().__init__(timeout=180)
        self.user_id, self.title, self.color = user_id, title, color
        self.pages = ["\n\n".join(lines[i:i + per]) for i in range(0, len(lines), per)] or ["Brak wyników."]
        self.idx = 0
        self.prev.disabled = True
        self.next.disabled = len(self.pages) <= 1

    def embed(self):
        return discord.Embed(title=self.title, description=self.pages[self.idx], color=self.color).set_footer(
            text=f"Strona {self.idx + 1}/{len(self.pages)}"
        )

    async def flip(self, i, d):
        if i.user.id != self.user_id:
            return await i.response.send_message("To nie Twój panel.", ephemeral=True)
        self.idx = max(0, min(len(self.pages) - 1, self.idx + d))
        self.prev.disabled = self.idx == 0
        self.next.disabled = self.idx == len(self.pages) - 1
        await i.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, i, b):
        await self.flip(i, -1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, i, b):
        await self.flip(i, 1)


async def key_ac(i: discord.Interaction, cur: str):
    try:
        data = await BIN.read()
    except Exception:
        return []
    out = []
    for e in BIN.entries(data):
        k = str(e.get("key", ""))
        p = player(k)
        label = f"{k} • {p[0] if p and p[0] else '-'}"
        if cur.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=k[:100]))
        if len(out) >= 25:
            break
    return out


async def load_entry(i, key):
    try:
        data = await BIN.read(fresh=True)
    except Exception as ex:
        await i.followup.send(f"Błąd połączenia z bazą kluczy: `{ex}`", ephemeral=True)
        return None
    e = BIN.find(data, key)
    if e is None:
        await i.followup.send("Nie ma takiego klucza.", ephemeral=True)
    return e


@tree.command(name="keyban", description="Banuje klucz licencyjny")
@app_commands.describe(key="Klucz licencyjny", reason="Powód")
@app_commands.autocomplete(key=key_ac)
async def keyban(i: discord.Interaction, key: str, reason: str = "brak powodu"):
    if not await admin_check(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        e = await BIN.mutate(key, lambda x: ban_entry(x, reason))
    except Exception as ex:
        return await i.followup.send(f"Błąd zapisu: `{ex}`", ephemeral=True)
    if e is None:
        return await i.followup.send("Nie ma takiego klucza.", ephemeral=True)
    queue_cmd(key, "quit")
    await i.followup.send(f"Zbanowano `{key}` ({reason}). Jeśli gracz jest online, gra zostanie zamknięta.", ephemeral=True)


@tree.command(name="keyunban", description="Odbanowuje klucz licencyjny")
@app_commands.autocomplete(key=key_ac)
async def keyunban(i: discord.Interaction, key: str):
    if not await admin_check(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        e = await BIN.mutate(key, unban_entry)
    except Exception as ex:
        return await i.followup.send(f"Błąd zapisu: `{ex}`", ephemeral=True)
    DB.execute("DELETE FROM cmds WHERE key=?", (key,))
    DB.commit()
    await i.followup.send("Odbanowano." if e else "Nie ma takiego klucza.", ephemeral=True)


@tree.command(name="quit", description="Wyłącza grę graczowi podpiętemu pod klucz")
@app_commands.autocomplete(key=key_ac)
async def quit_cmd(i: discord.Interaction, key: str):
    if not await admin_check(i):
        return
    await i.response.defer(ephemeral=True)
    e = await load_entry(i, key)
    if e is None:
        return
    queue_cmd(key, "quit")
    if is_online(key):
        p = player(key)
        msg = f"Wysłano `quit` dla **{p[0] or '?'}** ({await dc_name(e)}). Gra zamknie się przy najbliższym pingu klienta."
    else:
        msg = f"Gracz jest offline. Komenda poczeka do {CFG['command_ttl_seconds']}s na jego połączenie."
    await i.followup.send(msg, ephemeral=True)


@tree.command(name="keylist", description="Lista kluczy z nickami Discord i Minecraft")
async def keylist(i: discord.Interaction):
    if not await admin_check(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        data = await BIN.read(fresh=True)
    except Exception as ex:
        return await i.followup.send(f"Błąd połączenia z bazą kluczy: `{ex}`", ephemeral=True)
    lines = []
    for e in BIN.entries(data):
        k = str(e.get("key", ""))
        p = player(k)
        exp = int(e.get("expires", 0) or 0)
        exp_txt = f"<t:{exp // 1000}:R>" if exp > 1 and e.get("hwid") else "—"
        lines.append(
            f"`{k}` {status_of(e)}\n👤 {await dc_name(e)} • ⛏ {p[0] if p and p[0] else '—'} • ⏳ {exp_txt}"
        )
    view = Pager(i.user.id, f"Klucze ({len(lines)})", lines)
    await i.followup.send(embed=view.embed(), view=view, ephemeral=True)


async def online_list(i: discord.Interaction):
    if not await admin_check(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        data = await BIN.read()
    except Exception as ex:
        return await i.followup.send(f"Błąd połączenia z bazą kluczy: `{ex}`", ephemeral=True)
    lines = []
    for e in BIN.entries(data):
        k = str(e.get("key", ""))
        if not is_online(k):
            continue
        p = player(k)
        lines.append(
            f"`{k}`\n👤 {await dc_name(e)} • ⛏ **{p[0] or '?'}**\n"
            f"🌐 {p[1] or '—'} • 🎮 {p[2] or '—'} • ⏱ <t:{p[3]}:R>"
        )
    view = Pager(i.user.id, f"Online ({len(lines)})", lines, color=0x57F287)
    await i.followup.send(embed=view.embed(), view=view, ephemeral=True)


tree.command(name="keyonline", description="Klucze, które teraz grają w Minecrafta")(online_list)
tree.command(name="keyactive", description="Alias: klucze online")(online_list)


async def heartbeat(req: web.Request):
    if CFG["api_secret"] and req.headers.get("X-Api-Secret") != CFG["api_secret"]:
        return web.json_response({"ok": False}, status=401)
    try:
        d = await req.json()
        key, hwid = str(d.get("key", "")), str(d.get("hwid", ""))
    except Exception:
        return web.json_response({"ok": False}, status=400)
    try:
        e = BIN.find(await BIN.read(), key)
    except Exception:
        return web.json_response({"ok": True, "cmd": pop_cmd(key)})
    if e is None or (e.get("hwid") and str(e["hwid"]) != hwid):
        return web.json_response({"ok": False})
    if e.get("banned"):
        return web.json_response({"ok": False, "cmd": "quit"})
    t = now_s()
    row = player(key)
    start = row[3] if row and t - row[4] <= CFG["online_timeout_seconds"] else t
    DB.execute(
        "INSERT OR REPLACE INTO players(key,mc,server,version,session_start,last_ts) VALUES(?,?,?,?,?,?)",
        (key, str(d.get("mc", ""))[:32], str(d.get("server", ""))[:100], str(d.get("version", ""))[:20], start, t),
    )
    DB.commit()
    return web.json_response({"ok": True, "cmd": pop_cmd(key)})


@client.event
async def on_ready():
    log.info("Zalogowano jako %s", client.user)


async def setup():
    await BIN.start()
    app = web.Application()
    app.router.add_post("/api/heartbeat", heartbeat)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, CFG["api_host"], CFG["api_port"]).start()
    guild = discord.Object(id=CFG["guild_id"]) if CFG["guild_id"] else None
    if guild:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()


client.setup_hook = setup
client.run(CFG["discord_token"])
