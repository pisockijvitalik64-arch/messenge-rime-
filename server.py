#!/usr/bin/env python3
"""Rime server v2. Standard library only. Run: python server.py  (PC or Pydroid on Android)"""
import os, re, sys, json, time, gzip, sqlite3, hashlib, secrets, random, mimetypes, threading, socket, datetime
import webbrowser, traceback, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
ROOT = getattr(sys, "_MEIPASS", APP_DIR)
DB = os.path.join(APP_DIR, "rime.db")
UP = os.path.join(APP_DIR, "uploads")
PRIV = os.path.join(APP_DIR, "private")
PORT = int(os.environ.get("PORT") or os.environ.get("RIME_PORT") or "8080")
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])
IMG = (".png", ".webp", ".jpg", ".jpeg", ".gif")
START_RUBIES, DAILY, ORG_PRICE, ORG_MAX = 500, 100, 1200, 100
BADGES = ("", "popular", "partner", "major", "developer", "owner")
BADGE_REQ = {"popular": "Популярный", "major": "Мажор", "partner": "Партнёр мессенджера", "developer": "Разработчик игры"}
BOT = "rimebot"
AI_BOT = "codexclyd"
AI_NAME, AI_VER = "Codex Clyd", "AVI 1.0"
BLOCK = {"rime.db", "server.py"}
UPLOAD_LIMIT = {"avatar": 8 << 20, "banner": 25 << 20, "org": 8 << 20, "icon": 8 << 20, "game": 64 << 20}
MARKET_FEE = 10
mimetypes.add_type("application/manifest+json", ".webmanifest")
lock = threading.Lock()
rng = random.SystemRandom()
now = lambda: int(time.time())

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def init():
    os.makedirs(UP, exist_ok=True); os.makedirs(PRIV, exist_ok=True)
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, display TEXT, salt TEXT, pw TEXT,
      rubies INTEGER DEFAULT 0, badge TEXT DEFAULT '', daily INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER);
    CREATE TABLE IF NOT EXISTS friends(a INTEGER, b INTEGER, PRIMARY KEY(a,b));
    CREATE TABLE IF NOT EXISTS friend_req(id INTEGER PRIMARY KEY, frm INTEGER, to_ INTEGER, ts INTEGER, UNIQUE(frm,to_));
    CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, owner INTEGER, name TEXT, img TEXT, backdrop TEXT, pattern TEXT, value INTEGER, tier TEXT);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, frm INTEGER, to_ INTEGER, text TEXT, gift TEXT, ts INTEGER);
    CREATE TABLE IF NOT EXISTS notifs(id INTEGER PRIMARY KEY, uid INTEGER, kind TEXT, text TEXT, data TEXT, ts INTEGER, read INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS orgs(id INTEGER PRIMARY KEY, name TEXT, letter TEXT, icon TEXT, owner INTEGER);
    CREATE TABLE IF NOT EXISTS bot_state(uid INTEGER PRIMARY KEY, state TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, uid INTEGER, kind TEXT, badge TEXT, text TEXT, rating INTEGER,
      status TEXT DEFAULT 'new', result TEXT, ts INTEGER);
    CREATE TABLE IF NOT EXISTS reads(uid INTEGER, peer INTEGER, last_id INTEGER, PRIMARY KEY(uid,peer));
    CREATE TABLE IF NOT EXISTS spins(id INTEGER PRIMARY KEY, uid INTEGER, mode TEXT, name TEXT, img TEXT, tier TEXT, value INTEGER, ts INTEGER);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY, seller INTEGER, kind TEXT, title TEXT, descr TEXT, price INTEGER,
      name TEXT, img TEXT, file TEXT, icon TEXT, tier TEXT, value INTEGER, buyer INTEGER DEFAULT 0, status TEXT DEFAULT 'active', ts INTEGER);""")
    for t, col, decl in [("users", "bio", "TEXT DEFAULT ''"), ("users", "avatar", "TEXT DEFAULT ''"),
                         ("users", "banner", "TEXT DEFAULT ''"), ("users", "birthday", "TEXT DEFAULT ''"),
                         ("users", "org_id", "INTEGER DEFAULT 0"), ("users", "is_bot", "INTEGER DEFAULT 0"),
                         ("users", "rare_spin", "INTEGER DEFAULT 0"), ("users", "created", "INTEGER DEFAULT 0"),
                         ("users", "sc", "TEXT DEFAULT ''"), ("users", "sp", "TEXT DEFAULT ''"),
                         ("messages", "buttons", "TEXT"), ("messages", "music", "TEXT")]:
        if col not in [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (t, col, decl))
    for k, v in SPIN_DEFAULT.items():
        c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, str(v)))
    if not c.execute("SELECT 1 FROM users WHERE username=?", (BOT,)).fetchone():
        s = secrets.token_hex(16)
        c.execute("INSERT INTO users(username,display,salt,pw,is_bot,bio) VALUES(?,?,?,?,1,?)",
                  (BOT, "Rime Bot", s, secrets.token_hex(32), "Официальный бот Rime: галочки, поддержка, отзывы"))
    if not c.execute("SELECT 1 FROM users WHERE username=?", (AI_BOT,)).fetchone():
        s = secrets.token_hex(16)
        cur = c.execute("INSERT INTO users(username,display,salt,pw,is_bot,bio) VALUES(?,?,?,?,1,?)",
                        (AI_BOT, AI_NAME, s, secrets.token_hex(32), "Встроенный ИИ мессенджера Rime · версия " + AI_VER))
        for r in c.execute("SELECT id FROM users WHERE is_bot=0").fetchall():
            c.execute("INSERT INTO messages(frm,to_,text,ts) VALUES(?,?,?,?)",
                      (cur.lastrowid, r[0], "Привет! Я %s, встроенный ИИ Rime версии %s. Помогу с карточками, маркетом, музыкой и всем, что есть в мессенджере. Напиши «помощь», чтобы увидеть, что я умею." % (AI_NAME, AI_VER), now()))
    c.commit(); c.close()

# ---------- catalog ----------
def pretty(fn):
    n = os.path.splitext(fn)[0]
    return re.sub(r"#U([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), n).strip()

def seeded(key):
    return random.Random(hashlib.md5(key.encode("utf-8")).hexdigest())

TIERS = [("common", 50, 30, 90), ("rare", 25, 100, 300), ("epic", 8, 400, 900), ("legendary", 1.5, 1500, 4000)]

def roll_item(key):
    r = seeded(key)
    t = r.choices(TIERS, weights=[x[1] for x in TIERS])[0]
    return t[0], t[1], r.randint(t[2], t[3])

def imgs(folder):
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(IMG)) if os.path.isdir(folder) else []

def url(*parts):
    return "/" + "/".join(quote(p) for p in parts)

def load(n):
    p = os.path.join(ROOT, "data", n)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []

def build_catalog():
    meta = load("tg_gifts.json") or {}
    gifts, seen = [], set()
    gdir = os.path.join(ROOT, "gift")
    gfiles = sorted((f for f in os.listdir(gdir) if f.lower().endswith(IMG + (".tgs",))),
                    key=lambda f: (bool(re.search(r" \(\d+\)$", os.path.splitext(f)[0])), f.lower())) if os.path.isdir(gdir) else []
    for f in gfiles:
        h = hashlib.md5(open(os.path.join(gdir, f), "rb").read()).hexdigest()
        if h in seen: continue
        seen.add(h)
        anim = f.lower().endswith(".tgs")
        if f in meta:
            m = meta[f]; gifts.append(dict(key="gift/" + f, name=m["name"], img=url("gift", f), price=m["price"], tier=m["tier"], anim=anim))
        else:
            tier, _, val = roll_item("gift/" + f)
            gifts.append(dict(key="gift/" + f, name=pretty(f), img=url("gift", f), price=val, tier=tier, anim=anim))
    cases, cdir = [], os.path.join(ROOT, "case")
    for d in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
        files = imgs(os.path.join(cdir, d))
        if not files:
            continue
        its = []
        for f in files:
            tier, w, val = roll_item("case/%s/%s" % (d, f))
            its.append(dict(name=pretty(f), img=url("case", d, f), tier=tier, w=w, value=val))
        tw = sum(i["w"] for i in its)
        avg = sum(i["w"] * i["value"] for i in its) / tw
        for i in its:
            i["chance"] = round(i["w"] / tw * 100, 3)
        cases.append(dict(id=d, name=d, price=max(10, int(round(avg * 1.2 / 5) * 5)), items=its))
    if gifts:
        profiles = ((("gift-all-mini", "Все подарки · Мини", 300),
                     {"common": 6, "rare": 3, "epic": 1, "legendary": 0.3}),
                    (("gift-all-prime", "Все подарки · Премиум", 1500),
                     {"common": 1, "rare": 2.5, "epic": 3, "legendary": 1.5}))
        for (cid, cname, price), weights in profiles:
            its = [dict(name=g["name"], img=g["img"], tier=g["tier"], value=g["price"],
                        anim=bool(g.get("anim")), w=weights.get(g["tier"], 1)) for g in gifts]
            tw = sum(i["w"] for i in its)
            for i in its:
                i["chance"] = round(i["w"] / tw * 100, 3)
            cases.append(dict(id=cid, name=cname, price=price, items=its))
    cards = []
    for i, f in enumerate(imgs(os.path.join(ROOT, "cards"))):
        tier, w, val = roll_item("cards/" + f)
        num = re.search(r"\d+", f)
        cards.append(dict(key="cards/" + f, name="Карточка %s" % (num.group(0) if num else i + 1),
                          img=url("cards", f), tier=tier, value=val, w=w))
    tw = sum(x["w"] for x in cards) or 1
    for x in cards:
        x["chance"] = round(x["w"] / tw * 100, 3)
    return dict(gifts=gifts, cases=cases, backdrops=load("backdrops.json"), patterns=load("patterns.json"), cards=cards)

CAT = None
TGS_CACHE = {}
SPIN_DEFAULT = {"spin_cost": 150, "rare_cost": 600, "rare_cd": 600}
SPIN_LIMITS = {"spin_cost": (10, 100000), "rare_cost": (10, 100000), "rare_cd": (5, 86400)}

def setting(c, k):
    r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    try:
        return int(r[0]) if r else SPIN_DEFAULT[k]
    except (TypeError, ValueError):
        return SPIN_DEFAULT[k]

def spin_cfg(c):
    return dict(cost=setting(c, "spin_cost"), rare_cost=setting(c, "rare_cost"), rare_cd=setting(c, "rare_cd"))

# ---------- helpers ----------
def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 150000).hex()

def org_of(c, oid):
    r = c.execute("SELECT id,name,letter,icon FROM orgs WHERE id=?", (oid,)).fetchone() if oid else None
    return dict(r) if r else None

def pub(c, u):
    return dict(username=u["username"], display=u["display"], badge=u["badge"], avatar=u["avatar"] or "",
                org=org_of(c, u["org_id"]), bot=bool(u["is_bot"]))

def full(c, u):
    return dict(pub(c, u), bio=u["bio"] or "", banner=u["banner"] or "", birthday=u["birthday"] or "",
                music=dict(soundcloud=u["sc"] or "", spotify=u["sp"] or ""))

def item_out(r):
    bd = next((b for b in CAT["backdrops"] if b["name"] == r["backdrop"]), None)
    pt = next((p for p in CAT["patterns"] if p["name"] == r["pattern"]), None)
    return dict(id=r["id"], name=r["name"], img=r["img"], value=r["value"], tier=r["tier"], backdrop=r["backdrop"],
                hex=bd["hex"] if bd else None, pattern=r["pattern"], symbol=pt["symbol"] if pt else None,
                anim=str(r["img"] or "").lower().endswith(".tgs"))

class Err(Exception):
    def __init__(s, msg, code=400):
        s.msg, s.code = msg, code

def uname(v):
    return str(v or "").strip().lstrip("@").lower()

def media_ok(v):
    v = str(v or "")
    if v and not re.fullmatch(r"/uploads/[0-9a-f]{32}\.(jpg|png|gif|mp4)", v):
        raise Err("Неверный файл")
    return v

def clean_music(v):
    v = str(v or "").strip()[:400]
    if not v: return ""
    if not v.startswith("https://"): raise Err("Ссылка должна начинаться с https://")
    p = urlparse(v); host = (p.hostname or "").lower()
    if host in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com"):
        return "https://" + host + p.path
    if host in ("open.spotify.com", "play.spotify.com"):
        m = re.match(r"/(?:intl-[a-z-]+/)?((?:track|album|playlist|artist|show|episode)/[A-Za-z0-9]+|user/[A-Za-z0-9]+/playlist/[A-Za-z0-9]+)", p.path)
        if not m: raise Err("Spotify: нужна ссылка на трек, альбом, плейлист или исполнителя")
        return "https://open.spotify.com/" + m.group(1)
    raise Err("Поддерживаются только SoundCloud и Spotify")

def music_payload(b):
    m = b.get("music")
    if not isinstance(m, dict): raise Err("Неверная музыка")
    url = clean_music(m.get("url"))
    if not url: raise Err("Пустая ссылка на музыку")
    return dict(provider="spotify" if "spotify.com" in url else "soundcloud", url=url)

def listing_out(r):
    d = dict(r); d.pop("file", None)
    d["anim"] = str(d.get("img") or "").lower().endswith(".tgs")
    return d

def sniff(data):
    if data[:3] == b"\xff\xd8\xff": return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "png"
    if data[:4] == b"GIF8": return "gif"
    if data[4:8] == b"ftyp": return "mp4"
    return None

def notify(c, uid, kind, text, data=""):
    c.execute("INSERT INTO notifs(uid,kind,text,data,ts) VALUES(?,?,?,?,?)", (uid, kind, text, data, now()))

def msg(c, frm, to, text, gift=None, buttons=None, music=None):
    c.execute("INSERT INTO messages(frm,to_,text,gift,ts,buttons,music) VALUES(?,?,?,?,?,?,?)",
              (frm, to, text, gift, now(), json.dumps(buttons, ensure_ascii=False) if buttons else None,
               json.dumps(music, ensure_ascii=False) if music else None))

def botid(c):
    return c.execute("SELECT id FROM users WHERE username=?", (BOT,)).fetchone()[0]

def aiid(c):
    return c.execute("SELECT id FROM users WHERE username=?", (AI_BOT,)).fetchone()[0]

MENU = [dict(id="badge", t="Подать заявку на галочку"), dict(id="support", t="Поддержка"), dict(id="review", t="Оставить отзыв")]

def bot_say(c, uid, text, buttons=None):
    msg(c, botid(c), uid, text, None, buttons)

def set_state(c, uid, st, data=""):
    c.execute("INSERT OR REPLACE INTO bot_state VALUES(?,?,?)", (uid, st, data))

def admins(c):
    return c.execute("SELECT id FROM users WHERE badge IN ('owner','developer')").fetchall()

def bot_click(c, u, bid):
    if bid == "menu":
        set_state(c, u["id"], ""); bot_say(c, u["id"], "Что нужно?", MENU)
    elif bid == "badge":
        bot_say(c, u["id"], "Какую галочку хочешь?", [dict(id="b:" + k, t=v) for k, v in BADGE_REQ.items()] + [dict(id="menu", t="Назад")])
    elif bid.startswith("b:") and bid[2:] in BADGE_REQ:
        set_state(c, u["id"], "badge_reason", bid[2:])
        bot_say(c, u["id"], "Галочка «%s». Напиши причину: почему она тебе нужна." % BADGE_REQ[bid[2:]])
    elif bid == "support":
        set_state(c, u["id"], "support")
        bot_say(c, u["id"], "Опиши причину обращения и что случилось. Одним сообщением.")
    elif bid == "review":
        bot_say(c, u["id"], "Оцени Rime от 1 до 5", [dict(id="r:%d" % i, t="★" * i) for i in range(1, 6)])
    elif re.fullmatch(r"r:[1-5]", bid):
        set_state(c, u["id"], "review_text", bid[2])
        bot_say(c, u["id"], "Оценка %s из 5. Теперь напиши отзыв текстом." % bid[2])
    elif bid.startswith("ai:"):
        ai_reply(c, u, bid[3:])
    else:
        raise Err("Неизвестное действие")

def new_request(c, u, kind, text, badge="", rating=0):
    c.execute("INSERT INTO requests(uid,kind,badge,text,rating,ts) VALUES(?,?,?,?,?,?)", (u["id"], kind, badge, text[:2000], rating, now()))
    lab = {"badge": "заявка на галочку", "support": "обращение в поддержку", "review": "отзыв"}[kind]
    for a in admins(c):
        notify(c, a[0], "admin", "Новое: %s от @%s" % (lab, u["username"]))

AI_BTNS = [dict(id="ai:help", t="Что умеешь"), dict(id="ai:cards", t="Карточки"), dict(id="ai:market", t="Маркет"), dict(id="ai:music", t="Музыка")]

def ai_reply(c, u, text):
    t = str(text or "").strip(); tl = t.lower()
    aliases = {"help": "помощь", "cards": "карточки", "market": "маркет", "music": "музыка", "rubies": "рубины"}
    if tl in aliases: tl = aliases[tl]
    def say(s, buttons=None): msg(c, aiid(c), u["id"], s, buttons=buttons or AI_BTNS)
    m = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*([+\-*/])\s*(-?\d+(?:[.,]\d+)?)\s*", t)
    if m:
        a, b = float(m.group(1).replace(",", ".")), float(m.group(3).replace(",", "."))
        try:
            r = {"+": a + b, "-": a - b, "*": a * b, "/": a / b}[m.group(2)]
            return say("Получается %s." % (("%g" % r).replace(".", ",")))
        except ZeroDivisionError:
            return say("На ноль делить нельзя — даже во встроенном ИИ.")
    if re.match(r"^(привет|прив|здравств|хай|hello|hi|здаров|добрый)", tl):
        return say("Привет, @%s! Я %s, встроенный ИИ Rime версии %s. Спроси про карточки, маркет, музыку, рубины или напиши «помощь»." % (u["username"], AI_NAME, AI_VER))
    if any(k in tl for k in ("кто ты", "как тебя", "твоё имя", "твое имя", "ты кто", "codex", "clyd", "клэйд", "клайд")):
        return say("Я %s, версия %s. Я живу только внутри мессенджера Rime: знаю его функции, помогаю с карточками, маркетом, музыкой и подсказываю по интерфейсу." % (AI_NAME, AI_VER))
    if "верси" in tl or "avi" in tl:
        return say("Моя версия — %s. Имя: %s. Работаю локально, без внешних сервисов." % (AI_VER, AI_NAME))
    if any(k in tl for k in ("помощь", "help", "что умеешь", "команд", "возможност", "что нового")):
        return say("Вот что я умею:\n• «карточки» — крутки и таймер редкой;\n• «маркет» — продажа карточек, подарков и игр;\n• «музыка» — SoundCloud и Spotify в профиле и чате;\n• «рубины» — как заработать ◆;\n• «подарки», «кейсы», «крафт», «профиль» — расскажу по каждому.\nЕщё могу посчитать простой пример.")
    if any(k in tl for k in ("карточ", "крут", "спин", "выпал", "коллекц")):
        cfg = spin_cfg(c)
        return say("Карточки живут во вкладке «Карточки»: обычная крутка — %d ◆, редкая — %d ◆ (только эпические и легендарные). После редкой крутки таймер %d мин. Карточки падают в инвентарь, а собрать их можно в коллекции." % (cfg["cost"], cfg["rare_cost"], round(cfg["rare_cd"] / 60)))
    if any(k in tl for k in ("маркет", "магазин", "продать", "продаж", "купить", "лот", "игр")):
        return say("Маркет — во вкладке «Маркет». Можно продать карточку или подарок из инвентаря либо загрузить свою игру любым файлом с иконкой. Покупатели платят рубинами, продавец получает %d%% (комиссия %d%%). Свои покупки и лоты — во вкладке «Куплено»." % (100 - MARKET_FEE, MARKET_FEE))
    if any(k in tl for k in ("музык", "спотиф", "spotify", "soundcloud", "саунд", "плейлист", "трек")):
        return say("Привяжи SoundCloud и Spotify в «Профиле» — появятся кнопки с плеером в твоей карточке профиля. Оттуда же можно отправить трек или плейлист другу в чат: сообщение придёт с встроенным плеером.")
    if any(k in tl for k in ("рубин", "бонус", "дейли", "деньг", "заработ")):
        return say("Рубины ◆: бонус дня +%d, продажи на маркете, а тратить можно на подарки, кейсы, крафт, крутки карточек и организации. Бонус забирается в «Подарках»." % DAILY)
    if any(k in tl for k in ("подар", "кейс", "крафт", "фон", "узор")):
        return say("Во вкладке «Подарки» — магазин и бонус дня. «Кейсы» — случайный предмет за ◆. «Крафт» — модель + фон + узор, цена складывается из выбранных элементов. Всё купленное и выбитое лежит в «Инвентаре».")
    if any(k in tl for k in ("профил", "аватар", "баннер", "организац")):
        return say("В «Профиле» меняются имя, био, аватар, баннер, день рождения и музыкальные ссылки. Организация даёт общую метку у ника; создать её можно во вкладке «Организация» за %d ◆." % ORG_PRICE)
    if any(k in tl for k in ("сколько", "статистик", "пользовател", "людей", "предмет")):
        users = c.execute("SELECT COUNT(*) FROM users WHERE is_bot=0").fetchone()[0]
        items = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        spins = c.execute("SELECT COUNT(*) FROM spins").fetchone()[0]
        return say("Сейчас в Rime %d пользователей, %d предметов в инвентарях и %d круток карточек. Впечатляет?" % (users, items, spins))
    if any(k in tl for k in ("спасиб", "благодар", "круто", "класс")):
        return say("Всегда пожалуйста, @%s! Если что — я тут, в этом чате." % u["username"])
    if re.match(r"^(пока|до свид|бай|bye)", tl):
        return say("До связи, @%s! Заходи ещё — я никуда не денусь." % u["username"])
    if "?" in t or any(k in tl for k in ("как", "где", "что", "почему", "можно")):
        return say("Я локальный ИИ Rime (%s), поэтому лучше всего разбираюсь в самом мессенджере. Уточни тему: карточки, маркет, музыка, рубины, подарки, кейсы, крафт, профиль — и я расскажу подробно." % AI_VER)
    return say("Принял: «%s». Я пока учусь понимать свободные фразы, но отлично знаю функции Rime. Напиши «помощь» — покажу, о чём можно спросить." % t[:120])

def bot_text(c, u, text):
    st = c.execute("SELECT * FROM bot_state WHERE uid=?", (u["id"],)).fetchone()
    s = st["state"] if st else ""
    if s == "badge_reason":
        new_request(c, u, "badge", text, st["data"]); set_state(c, u["id"], "")
        bot_say(c, u["id"], "Заявка отправлена. Ответ придёт сюда.", [dict(id="menu", t="В меню")])
    elif s == "support":
        new_request(c, u, "support", text); set_state(c, u["id"], "")
        bot_say(c, u["id"], "Обращение передано в поддержку. Ответ придёт сюда.", [dict(id="menu", t="В меню")])
    elif s == "review_text":
        new_request(c, u, "review", text, "", int(st["data"])); set_state(c, u["id"], "")
        bot_say(c, u["id"], "Спасибо за отзыв!", [dict(id="menu", t="В меню")])
    else:
        bot_say(c, u["id"], "Что нужно?", MENU)

def birthdays(c, u):
    d = datetime.date.today(); md, yr = d.strftime("%m-%d"), d.year
    rows = c.execute("SELECT f.id,f.username FROM friends x JOIN users f ON f.id=x.b WHERE x.a=? AND substr(f.birthday,6,5)=?", (u["id"], md)).fetchall()
    for r in rows:
        key = "%d:%d" % (r["id"], yr)
        if not c.execute("SELECT 1 FROM notifs WHERE uid=? AND kind='birthday' AND data=?", (u["id"], key)).fetchone():
            notify(c, u["id"], "birthday", "Сегодня день рождения у @%s!" % r["username"], key)
    if (u["birthday"] or "")[5:] == md:
        key = "self:%d" % yr
        if not c.execute("SELECT 1 FROM notifs WHERE uid=? AND kind='birthday' AND data=?", (u["id"], key)).fetchone():
            notify(c, u["id"], "birthday", "С днём рождения! Подарок — в инвентаре «Подарки».", key)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass

    def send(self, obj, code=200, cache="no-store"):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/api/market/download":
            return self.market_file(parse_qs(p.query))
        if p.path == "/api/tgs":
            return self.tgs(parse_qs(p.query))
        if p.path.startswith("/api/"):
            return self.api("GET", p.path[5:], parse_qs(p.query), {}, None)
        name = unquote(p.path).lstrip("/") or "index.html"
        if name.startswith("uploads/"):
            full_ = os.path.realpath(os.path.join(UP, name[8:]))
            inside = full_.startswith(os.path.realpath(UP) + os.sep)
        else:
            full_ = os.path.realpath(os.path.join(ROOT, name))
            inside = full_.startswith(os.path.realpath(ROOT) + os.sep)
        if not inside or not os.path.isfile(full_) or os.path.basename(full_) in BLOCK:
            self.send_response(404); self.end_headers(); return
        size = os.path.getsize(full_); start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1): start = int(m.group(1))
            if m.group(2): end = min(int(m.group(2)), size - 1)
            if not m.group(1): start, end = max(0, size - int(m.group(2))), size - 1
            if start > end: self.send_response(416); self.end_headers(); return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", mimetypes.guess_type(full_)[0] or "application/octet-stream")
        self.send_header("X-Content-Type-Options", "nosniff")
        if full_.startswith(UP + os.sep): self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif full_.lower().endswith((".html", ".py", ".json", ".db")): self.send_header("Cache-Control", "no-cache")
        else: self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Accept-Ranges", "bytes")
        if code == 206: self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(full_, "rb") as f:
            f.seek(start); left = end - start + 1
            while left > 0:
                ch = f.read(min(65536, left))
                if not ch: break
                self.wfile.write(ch); left -= len(ch)

    def do_POST(self):
        p = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if p.path == "/api/upload":
            q = parse_qs(p.query); kind = q.get("kind", ["avatar"])[0]
            if kind not in UPLOAD_LIMIT or n <= 0 or n > UPLOAD_LIMIT[kind]:
                self.rfile.read(min(n, 1 << 20)); return self.send({"error": "Файл слишком большой или пустой (лимит %d МБ)" % (UPLOAD_LIMIT.get(kind, 8 << 20) >> 20)}, 400)
            return self.api("POST", "upload", q, {}, self.rfile.read(n))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send({"error": "bad json"}, 400)
        self.api("POST", p.path[5:], {}, body, None)

    def api(self, method, path, q, b, raw):
        out, code = None, 200
        with lock:
            c = db()
            try:
                out = self.route(c, method, path, q, b, raw); c.commit()
            except Err as e:
                c.rollback(); out, code = {"error": e.msg}, e.code
            except Exception as e:
                c.rollback(); out, code = {"error": "server: %s" % e}, 500
            finally:
                c.close()
        self.send(out, code)

    def tgs(self, q):
        f = unquote(q.get("f", [""])[0]).lstrip("/")
        gdir = os.path.realpath(os.path.join(ROOT, "gift"))
        full_ = os.path.realpath(os.path.join(ROOT, f))
        if (".." in f or not f.startswith("gift/") or not f.lower().endswith(".tgs")
                or not full_.startswith(gdir + os.sep) or not os.path.isfile(full_)):
            return self.send({"error": "Анимация не найдена"}, 404)
        st = os.stat(full_)
        cached = TGS_CACHE.get(f)
        if not cached or cached[0] != st.st_mtime:
            try:
                data = json.loads(gzip.decompress(open(full_, "rb").read()))
            except Exception:
                return self.send({"error": "Не удалось прочитать TGS"}, 400)
            cached = (st.st_mtime, data); TGS_CACHE[f] = cached
        self.send(cached[1], 200, "public, max-age=86400")

    def market_file(self, q):
        token = self.headers.get("Authorization", "")[7:]
        data, code, msg = None, 200, ""
        with lock:
            c = db()
            try:
                r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token=?", (token,)).fetchone()
                if not r: raise Err("Нужен вход", 401)
                try:
                    lid = int(q.get("id", ["0"])[0] or 0)
                except ValueError:
                    lid = 0
                l = c.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
                if not l or l["kind"] != "game": raise Err("Файл не найден", 404)
                if not (l["buyer"] == r["id"] or l["seller"] == r["id"] or r["badge"] in ("owner", "developer")):
                    raise Err("Файл доступен только после покупки", 403)
                path = os.path.realpath(os.path.join(PRIV, l["file"] or ""))
                if not path.startswith(PRIV + os.sep) or not os.path.isfile(path): raise Err("Файл удалён", 404)
                data = open(path, "rb").read()
                title, ext = l["title"] or "game", os.path.splitext(path)[1]
            except Err as e:
                code, msg = e.code, e.msg
            finally:
                c.close()
        if data is None:
            return self.send({"error": msg}, code)
        ascii_fn = (re.sub(r"[^\w.-]+", "_", title)[:40] or "game") + ext
        disp = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_fn.encode("ascii", "ignore").decode() or "game" + ext, quote(title[:60] + ext))
        self.send_response(200)
        for k, v in [("Content-Type", "application/octet-stream"), ("Content-Disposition", disp),
                     ("Content-Length", str(len(data))), ("X-Content-Type-Options", "nosniff"),
                     ("Cache-Control", "no-store")]:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def user(self, c):
        t = self.headers.get("Authorization", "")[7:]
        r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token=?", (t,)).fetchone()
        if not r: raise Err("Нужен вход", 401)
        return r

    def session(self, c, uid):
        t = secrets.token_hex(24)
        c.execute("INSERT INTO sessions VALUES(?,?)", (t, uid))
        return dict(token=t)

    def peer(self, c, u, un):
        t = c.execute("SELECT * FROM users WHERE username=?", (uname(un),)).fetchone()
        if not t: raise Err("Пользователь не найден", 404)
        return t

    def pay(self, c, u, cost):
        cur = c.execute("SELECT rubies FROM users WHERE id=?", (u["id"],)).fetchone()[0]
        if cur < cost: raise Err("Не хватает рубинов: нужно %d, у тебя %d" % (cost, cur))
        c.execute("UPDATE users SET rubies=rubies-? WHERE id=?", (cost, u["id"]))
        return cur - cost

    def give(self, c, uid, name, img, value, tier):
        cur = c.execute("INSERT INTO items(owner,name,img,value,tier) VALUES(?,?,?,?,?)", (uid, name, img, value, tier))
        return item_out(c.execute("SELECT * FROM items WHERE id=?", (cur.lastrowid,)).fetchone())

    def route(self, c, m, path, q, b, raw):
        if path == "ping":
            return dict(app="Rime", ok=True, version=AI_VER)
        if path == "register":
            un, pw = uname(b.get("username")), str(b.get("password", ""))
            if not re.fullmatch(r"[a-z0-9_]{3,20}", un): raise Err("Юзернейм: 3-20 символов, a-z, 0-9, _")
            if un == BOT: raise Err("Юзернейм занят")
            if len(pw) < 6: raise Err("Пароль минимум 6 символов")
            if c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone(): raise Err("Юзернейм занят")
            first = c.execute("SELECT COUNT(*) FROM users WHERE is_bot=0").fetchone()[0] == 0
            salt = secrets.token_hex(16)
            cur = c.execute("INSERT INTO users(username,display,salt,pw,rubies,badge,created) VALUES(?,?,?,?,?,?,?)",
                            (un, (str(b.get("display") or un).strip() or un)[:30], salt, hash_pw(pw, salt), START_RUBIES,
                             "owner" if first else "", now()))
            bot_say(c, cur.lastrowid, "Добро пожаловать в наш мессенджер", MENU)
            aid = c.execute("SELECT id FROM users WHERE username=?", (AI_BOT,)).fetchone()
            if aid:
                msg(c, aid[0], cur.lastrowid, "Привет! Я %s, встроенный ИИ Rime версии %s. Напиши «помощь» — расскажу про карточки, маркет, музыку и рубины." % (AI_NAME, AI_VER))
            return self.session(c, cur.lastrowid)
        if path == "login":
            u = c.execute("SELECT * FROM users WHERE username=? AND is_bot=0", (uname(b.get("username")),)).fetchone()
            if not u or not secrets.compare_digest(u["pw"], hash_pw(str(b.get("password", "")), u["salt"])):
                raise Err("Неверный юзернейм или пароль", 401)
            return self.session(c, u["id"])

        u = self.user(c)
        if path == "me":
            cfg = spin_cfg(c)
            return dict(me=dict(full(c, u), rubies=u["rubies"], daily=now() - u["daily"] >= 86400,
                                rare_next=(u["rare_spin"] + cfg["rare_cd"]) if u["rare_spin"] else 0, spin=cfg))
        if path == "profile" and m == "POST":
            disp = str(b.get("display", u["display"])).strip()[:30] or u["username"]
            bd = str(b.get("birthday", "") or "")
            if bd and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", bd): raise Err("Дата рождения: ГГГГ-ММ-ДД")
            if bd: datetime.date.fromisoformat(bd)
            c.execute("UPDATE users SET display=?,bio=?,avatar=?,banner=?,birthday=?,sc=?,sp=? WHERE id=?",
                      (disp, str(b.get("bio", ""))[:300], media_ok(b.get("avatar")), media_ok(b.get("banner")), bd,
                       clean_music(b.get("soundcloud")), clean_music(b.get("spotify")), u["id"]))
            return dict(me=full(c, c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()))
        if path == "upload":
            kind = q["kind"][0]
            if kind == "game":
                fn = unquote(q.get("name", [""])[0])
                ext = os.path.splitext(fn)[1].lstrip(".").lower()
                if not re.fullmatch(r"[a-z0-9]{1,8}", ext): ext = "bin"
                name = secrets.token_hex(16) + "." + ext
                open(os.path.join(PRIV, name), "wb").write(raw)
                return dict(file=name)
            ext = sniff(raw)
            if not ext: raise Err("Формат: только jpg, png, gif, mp4")
            if kind == "icon" and ext not in ("jpg", "png", "gif"): raise Err("Иконка: jpg, png или gif")
            if ext == "mp4" and kind == "org" and len(raw) > (8 << 20): raise Err("Слишком большой файл")
            name = secrets.token_hex(16) + "." + ext
            open(os.path.join(UP, name), "wb").write(raw)
            return dict(url="/uploads/" + name)
        if path == "user":
            t = c.execute("SELECT * FROM users WHERE username=?", (uname(q.get("u", [""])[0]),)).fetchone()
            if not t: raise Err("Не найден", 404)
            fr = bool(c.execute("SELECT 1 FROM friends WHERE a=? AND b=?", (u["id"], t["id"])).fetchone())
            d = full(c, t); d["birthday"] = d["birthday"][5:] if d["birthday"] else ""
            return dict(user=d, friend=fr)
        if path == "catalog":
            return dict(gifts=CAT["gifts"], backdrops=CAT["backdrops"], patterns=CAT["patterns"], org_price=ORG_PRICE,
                        cases=[dict(id=k["id"], name=k["name"], price=k["price"], items=k["items"]) for k in CAT["cases"]],
                        cards=CAT["cards"], spin=spin_cfg(c))
        if path == "daily":
            if now() - u["daily"] < 86400: raise Err("Бонус уже получен, приходи позже")
            c.execute("UPDATE users SET rubies=rubies+?, daily=? WHERE id=?", (DAILY, now(), u["id"]))
            return dict(ok=True)
        if path == "users/search":
            s = uname(q.get("q", [""])[0])
            rows = c.execute("SELECT * FROM users WHERE username LIKE ? AND id!=? AND is_bot=0 LIMIT 20", (s + "%", u["id"])).fetchall()
            return dict(users=[pub(c, r) for r in rows])
        # ----- friends & notifications -----
        if path == "friends":
            rows = c.execute("SELECT u.* FROM friends f JOIN users u ON u.id=f.b WHERE f.a=? ORDER BY u.username", (u["id"],)).fetchall()
            inc = c.execute("SELECT r.id, u.* FROM friend_req r JOIN users u ON u.id=r.frm WHERE r.to_=?", (u["id"],)).fetchall()
            out = c.execute("SELECT u.* FROM friend_req r JOIN users u ON u.id=r.to_ WHERE r.frm=?", (u["id"],)).fetchall()
            return dict(friends=[pub(c, r) for r in rows], incoming=[dict(pub(c, r), req=r["id"]) for r in inc], outgoing=[pub(c, r) for r in out])
        if path == "friends/add":
            t = c.execute("SELECT * FROM users WHERE username=? AND is_bot=0", (uname(b.get("username")),)).fetchone()
            if not t: raise Err("Пользователь не найден", 404)
            if t["id"] == u["id"]: raise Err("Нельзя добавить себя")
            if c.execute("SELECT 1 FROM friends WHERE a=? AND b=?", (u["id"], t["id"])).fetchone(): raise Err("Вы уже друзья")
            rev = c.execute("SELECT id FROM friend_req WHERE frm=? AND to_=?", (t["id"], u["id"])).fetchone()
            if rev: return self.accept(c, u, rev["id"])
            if c.execute("SELECT 1 FROM friend_req WHERE frm=? AND to_=?", (u["id"], t["id"])).fetchone(): raise Err("Заявка уже отправлена")
            cur = c.execute("INSERT INTO friend_req(frm,to_,ts) VALUES(?,?,?)", (u["id"], t["id"], now()))
            notify(c, t["id"], "friend_req", "@%s хочет добавить тебя в друзья" % u["username"], str(cur.lastrowid))
            return dict(status="sent")
        if path == "friends/respond":
            r = c.execute("SELECT * FROM friend_req WHERE id=? AND to_=?", (b.get("id"), u["id"])).fetchone()
            if not r: raise Err("Заявка не найдена", 404)
            if b.get("accept"): return self.accept(c, u, r["id"])
            c.execute("DELETE FROM friend_req WHERE id=?", (r["id"],))
            return dict(status="declined")
        if path == "friends/remove":
            t = self.peer(c, u, b.get("username"))
            c.execute("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (u["id"], t["id"], t["id"], u["id"]))
            return dict(ok=True)
        if path == "notifs":
            if m == "POST":
                c.execute("UPDATE notifs SET read=1 WHERE uid=?", (u["id"],))
            birthdays(c, u)
            rows = c.execute("SELECT * FROM notifs WHERE uid=? ORDER BY id DESC LIMIT 50", (u["id"],)).fetchall()
            return dict(notifs=[dict(id=r["id"], kind=r["kind"], text=r["text"], data=r["data"], ts=r["ts"], read=bool(r["read"])) for r in rows],
                        unread=sum(1 for r in rows if not r["read"]))
        # ----- chats -----
        if path == "chats":
            ids = {}
            for r in c.execute("SELECT CASE WHEN frm=? THEN to_ ELSE frm END AS p, MAX(id) AS mid FROM messages WHERE frm=? OR to_=? GROUP BY p", (u["id"], u["id"], u["id"])):
                ids[r["p"]] = r["mid"]
            for r in c.execute("SELECT b FROM friends WHERE a=?", (u["id"],)):
                ids.setdefault(r["b"], 0)
            for r in c.execute("SELECT id FROM users WHERE is_bot=1"):
                ids.setdefault(r["id"], 0)
            ids.setdefault(u["id"], 0)
            out = []
            for pid, mid in ids.items():
                p = c.execute("SELECT * FROM users WHERE id=?", (pid,)).fetchone()
                if not p: continue
                last = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone() if mid else None
                rd = c.execute("SELECT last_id FROM reads WHERE uid=? AND peer=?", (u["id"], pid)).fetchone()
                unread = 0 if pid == u["id"] else c.execute("SELECT COUNT(*) FROM messages WHERE frm=? AND to_=? AND id>?", (pid, u["id"], rd[0] if rd else 0)).fetchone()[0]
                d = pub(c, p)
                if pid == u["id"]: d["display"] = "Избранное"
                prev = "🎁 подарок" if last and last["gift"] else "🎧 музыка" if last and last["music"] else (last["text"][:60] if last and last["text"] else "")
                out.append(dict(user=d, unread=unread, mid=mid, last=prev, ts=last["ts"] if last else 0))
            out.sort(key=lambda x: (0 if x["user"]["bot"] else 1 if x["user"]["username"] == u["username"] else 2, -x["mid"]))
            return dict(chats=out)
        if path == "messages":
            f = self.peer(c, u, b.get("to") if m == "POST" else q.get("with", [""])[0])
            if m == "POST":
                music = music_payload(b) if b.get("music") else None
                t = str(b.get("text", "")).strip()[:2000]
                if not t and not music: raise Err("Пустое сообщение")
                if c.execute("SELECT COUNT(*) FROM messages WHERE frm=? AND ts>?", (u["id"], now() - 10)).fetchone()[0] >= 25:
                    raise Err("Слишком часто, подожди несколько секунд")
                msg(c, u["id"], f["id"], t, music=music)
                if f["is_bot"]:
                    if f["username"] == AI_BOT: ai_reply(c, u, t or "я поделился музыкой")
                    else: bot_text(c, u, t)
            after = int(q.get("after", ["0"])[0] or 0)
            w = "((frm=? AND to_=?) OR (frm=? AND to_=?))"
            if after:
                rows = c.execute("SELECT * FROM messages WHERE id>? AND " + w + " ORDER BY id LIMIT 200", (after, u["id"], f["id"], f["id"], u["id"])).fetchall()
            else:
                rows = c.execute("SELECT * FROM messages WHERE " + w + " ORDER BY id DESC LIMIT 100", (u["id"], f["id"], f["id"], u["id"])).fetchall()[::-1]
            if rows:
                c.execute("INSERT OR REPLACE INTO reads VALUES(?,?,?)", (u["id"], f["id"], max(r["id"] for r in rows)))
            return dict(messages=[dict(id=r["id"], mine=r["frm"] == u["id"], text=r["text"], gift=json.loads(r["gift"]) if r["gift"] else None,
                                       buttons=json.loads(r["buttons"]) if r["buttons"] else None,
                                       music=json.loads(r["music"]) if r["music"] else None, ts=r["ts"]) for r in rows])
        if path == "bot/action":
            bot_click(c, u, str(b.get("id", "")))
            return dict(ok=True)
        # ----- economy -----
        if path == "inventory":
            return dict(items=[item_out(r) for r in c.execute("SELECT * FROM items WHERE owner=? ORDER BY id DESC", (u["id"],))])
        if path == "shop/buy":
            g = next((g for g in CAT["gifts"] if g["key"] == b.get("key")), None)
            if not g: raise Err("Подарок не найден", 404)
            self.pay(c, u, g["price"])
            return dict(item=self.give(c, u["id"], g["name"], g["img"], g["price"], g["tier"]))
        if path == "case/open":
            k = next((k for k in CAT["cases"] if k["id"] == b.get("case")), None)
            if not k: raise Err("Кейс не найден", 404)
            self.pay(c, u, k["price"])
            w = rng.choices(k["items"], weights=[i["w"] for i in k["items"]])[0]
            return dict(item=self.give(c, u["id"], w["name"], w["img"], w["value"], w["tier"]))
        # ----- cards -----
        if path == "card/spin":
            mode = b.get("mode", "normal")
            if mode not in ("normal", "rare"): raise Err("Неверный режим крутки")
            cfg, cd = spin_cfg(c), None
            cost = cfg["cost"] if mode == "normal" else cfg["rare_cost"]
            if mode == "rare":
                cd = cfg["rare_cd"]
                left = (u["rare_spin"] or 0) + cd - now()
                if left > 0: raise Err("Редкая крутка перезаряжается ещё %d сек" % left)
            pool = CAT["cards"]
            if mode == "rare":
                pool = [x for x in pool if x["tier"] in ("epic", "legendary")] or pool
            if not pool: raise Err("В папке cards нет картинок", 404)
            self.pay(c, u, cost)
            w = rng.choices(pool, weights=[x["w"] for x in pool])[0]
            item = self.give(c, u["id"], w["name"], w["img"], w["value"], w["tier"])
            c.execute("INSERT INTO spins(uid,mode,name,img,tier,value,ts) VALUES(?,?,?,?,?,?,?)",
                      (u["id"], mode, w["name"], w["img"], w["tier"], w["value"], now()))
            if mode == "rare": c.execute("UPDATE users SET rare_spin=? WHERE id=?", (now(), u["id"]))
            rub = c.execute("SELECT rubies FROM users WHERE id=?", (u["id"],)).fetchone()[0]
            rn = now() + cd if cd else ((u["rare_spin"] + cfg["rare_cd"]) if u["rare_spin"] else 0)
            return dict(item=item, rubies=rub, rare_next=rn, mode=mode)
        if path == "card/history":
            rows = c.execute("SELECT * FROM spins WHERE uid=? ORDER BY id DESC LIMIT 30", (u["id"],)).fetchall()
            return dict(spins=[dict(r) for r in rows])
        if path == "card/collection":
            rows = c.execute("SELECT name,img,tier,value,COUNT(*) AS n FROM items WHERE owner=? AND img LIKE '/cards/%' GROUP BY name ORDER BY value DESC", (u["id"],)).fetchall()
            return dict(cards=[dict(r) for r in rows], total=sum(r["n"] for r in rows))
        # ----- marketplace -----
        if path == "market":
            rows = c.execute("SELECT l.*, u.username, u.display, u.avatar, u.badge FROM listings l JOIN users u ON u.id=l.seller WHERE l.status='active' ORDER BY l.id DESC LIMIT 200").fetchall()
            return dict(listings=[listing_out(r) for r in rows], fee=MARKET_FEE)
        if path == "market/mine":
            mine = c.execute("SELECT l.*, u.username AS seller_name FROM listings l JOIN users u ON u.id=l.seller WHERE l.seller=? ORDER BY l.id DESC LIMIT 100", (u["id"],)).fetchall()
            bought = c.execute("SELECT l.*, u.username AS seller_name FROM listings l JOIN users u ON u.id=l.seller WHERE l.buyer=? AND l.status='sold' ORDER BY l.id DESC LIMIT 100", (u["id"],)).fetchall()
            return dict(mine=[listing_out(r) for r in mine], bought=[listing_out(r) for r in bought])
        if path == "market/create":
            kind = b.get("kind")
            title = str(b.get("title", "")).strip()[:60]
            descr = str(b.get("descr", "")).strip()[:500]
            try:
                price = int(b.get("price", 0))
            except (TypeError, ValueError):
                raise Err("Неверная цена")
            if not 1 <= price <= 1000000: raise Err("Цена: от 1 до 1 000 000 ◆")
            if kind == "item":
                it = c.execute("SELECT * FROM items WHERE id=? AND owner=?", (b.get("item_id"), u["id"])).fetchone()
                if not it: raise Err("Предмет не найден в инвентаре", 404)
                title = title or it["name"]
                c.execute("DELETE FROM items WHERE id=?", (it["id"],))
                cur = c.execute("INSERT INTO listings(seller,kind,title,descr,price,name,img,tier,value,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (u["id"], "item", title, descr, price, it["name"], it["img"], it["tier"], it["value"], now()))
            elif kind == "game":
                if not title: raise Err("Название игры обязательно")
                f = str(b.get("file", "") or "")
                if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{1,8}", f): raise Err("Загрузи файл игры (любой формат)")
                icon = media_ok(b.get("icon"))
                cur = c.execute("INSERT INTO listings(seller,kind,title,descr,price,file,icon,ts) VALUES(?,?,?,?,?,?,?,?)",
                                (u["id"], "game", title, descr, price, f, icon, now()))
            else:
                raise Err("Неверный тип лота")
            return dict(id=cur.lastrowid, ok=True)
        if path == "market/cancel":
            l = c.execute("SELECT * FROM listings WHERE id=? AND seller=? AND status='active'", (b.get("id"), u["id"])).fetchone()
            if not l: raise Err("Лот не найден", 404)
            if l["kind"] == "item":
                self.give(c, u["id"], l["name"], l["img"], l["value"], l["tier"])
            c.execute("UPDATE listings SET status='canceled' WHERE id=?", (l["id"],))
            return dict(ok=True)
        if path == "market/buy":
            l = c.execute("SELECT * FROM listings WHERE id=? AND status='active'", (b.get("id"),)).fetchone()
            if not l: raise Err("Лот уже продан или снят", 404)
            if l["seller"] == u["id"]: raise Err("Нельзя купить свой лот")
            self.pay(c, u, l["price"])
            earn = max(1, l["price"] * (100 - MARKET_FEE) // 100)
            c.execute("UPDATE users SET rubies=rubies+? WHERE id=?", (earn, l["seller"]))
            c.execute("UPDATE listings SET status='sold', buyer=? WHERE id=?", (u["id"], l["id"]))
            item = None
            if l["kind"] == "item":
                item = self.give(c, u["id"], l["name"], l["img"], l["value"], l["tier"])
            notify(c, l["seller"], "gift", "Твой лот «%s» купил @%s за %d ◆ (на счёт: %d ◆)" % (l["title"], u["username"], l["price"], earn))
            notify(c, u["id"], "info", "Ты купил «%s» за %d ◆" % (l["title"], l["price"]))
            rub = c.execute("SELECT rubies FROM users WHERE id=?", (u["id"],)).fetchone()[0]
            return dict(ok=True, item=item, rubies=rub, earn=earn)
        if path == "craft":
            it = c.execute("SELECT * FROM items WHERE id=? AND owner=?", (b.get("item_id"), u["id"])).fetchone()
            if not it: raise Err("Подарок не найден", 404)
            if it["backdrop"] or it["pattern"]: raise Err("Этот подарок уже скрафчен")
            bd = next((x for x in CAT["backdrops"] if x["name"] == b.get("backdrop")), None)
            pt = next((x for x in CAT["patterns"] if x["name"] == b.get("pattern")), None)
            if not bd or not pt: raise Err("Выбери фон и узор")
            cost = bd["price"] + pt["price"]
            self.pay(c, u, cost)
            c.execute("UPDATE items SET backdrop=?, pattern=?, value=value+? WHERE id=?", (bd["name"], pt["name"], cost, it["id"]))
            return dict(item=item_out(c.execute("SELECT * FROM items WHERE id=?", (it["id"],)).fetchone()))
        if path == "gift/send":
            f = self.peer(c, u, b.get("to"))
            if f["is_bot"] or f["id"] == u["id"]: raise Err("Этому чату нельзя дарить")
            it = c.execute("SELECT * FROM items WHERE id=? AND owner=?", (b.get("item_id"), u["id"])).fetchone()
            if not it: raise Err("Подарок не найден", 404)
            c.execute("UPDATE items SET owner=? WHERE id=?", (f["id"], it["id"]))
            msg(c, u["id"], f["id"], "", json.dumps(item_out(it), ensure_ascii=False))
            notify(c, f["id"], "gift", "@%s подарил(а) тебе «%s»" % (u["username"], it["name"]))
            return dict(ok=True)
        # ----- organizations -----
        if path.startswith("org/"):
            return self.org(c, u, path[4:], b)
        # ----- admin -----
        if path.startswith("admin/"):
            if u["badge"] not in ("owner", "developer"): raise Err("Нет доступа", 403)
            return self.admin(c, u, path[6:], q, b, m)
        raise Err("Нет такого метода", 404)

    def accept(self, c, u, rid):
        r = c.execute("SELECT * FROM friend_req WHERE id=?", (rid,)).fetchone()
        c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)", (r["frm"], r["to_"]))
        c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)", (r["to_"], r["frm"]))
        c.execute("DELETE FROM friend_req WHERE id=?", (rid,))
        other = r["frm"] if r["to_"] == u["id"] else r["to_"]
        notify(c, other, "info", "@%s принял(а) заявку в друзья" % u["username"])
        return dict(status="friends")

    def org(self, c, u, a, b):
        mine = org_of(c, u["org_id"])
        if a == "mine":
            if not mine: return dict(org=None)
            mem = c.execute("SELECT * FROM users WHERE org_id=? ORDER BY id", (mine["id"],)).fetchall()
            own = c.execute("SELECT owner FROM orgs WHERE id=?", (mine["id"],)).fetchone()[0]
            return dict(org=mine, owner=(own == u["id"]), members=[dict(pub(c, x), owner=(x["id"] == own)) for x in mem])
        if a == "create":
            if mine: raise Err("Ты уже состоишь в организации")
            name, letter = str(b.get("name", "")).strip()[:30], str(b.get("letter", "")).strip()[:1]
            icon = media_ok(b.get("icon"))
            if len(name) < 2: raise Err("Название от 2 символов")
            if not icon and not letter: raise Err("Нужна иконка (png/gif/mp4) или одна буква")
            self.pay(c, u, ORG_PRICE)
            cur = c.execute("INSERT INTO orgs(name,letter,icon,owner) VALUES(?,?,?,?)", (name, letter, icon, u["id"]))
            c.execute("UPDATE users SET org_id=? WHERE id=?", (cur.lastrowid, u["id"]))
            return dict(ok=True)
        if not mine: raise Err("У тебя нет организации")
        own = c.execute("SELECT owner FROM orgs WHERE id=?", (mine["id"],)).fetchone()[0] == u["id"]
        if a == "leave":
            if own: raise Err("Владелец не может выйти: удали организацию")
            c.execute("UPDATE users SET org_id=0 WHERE id=?", (u["id"],)); return dict(ok=True)
        if not own: raise Err("Только владелец организации", 403)
        if a == "edit":
            name, letter = str(b.get("name", mine["name"])).strip()[:30], str(b.get("letter", mine["letter"] or "")).strip()[:1]
            icon = media_ok(b.get("icon", mine["icon"]))
            if len(name) < 2 or (not icon and not letter): raise Err("Нужны название и иконка или буква")
            c.execute("UPDATE orgs SET name=?,letter=?,icon=? WHERE id=?", (name, letter, icon, mine["id"])); return dict(ok=True)
        if a in ("add", "remove"):
            t = c.execute("SELECT * FROM users WHERE username=? AND is_bot=0", (uname(b.get("username")),)).fetchone()
            if not t: raise Err("Пользователь не найден", 404)
            if a == "add":
                if t["org_id"]: raise Err("Он уже состоит в организации")
                if c.execute("SELECT COUNT(*) FROM users WHERE org_id=?", (mine["id"],)).fetchone()[0] >= ORG_MAX: raise Err("Лимит участников")
                c.execute("UPDATE users SET org_id=? WHERE id=?", (mine["id"], t["id"]))
                notify(c, t["id"], "info", "Тебя добавили в организацию «%s»" % mine["name"])
            else:
                if t["id"] == u["id"] or t["org_id"] != mine["id"]: raise Err("Нельзя")
                c.execute("UPDATE users SET org_id=0 WHERE id=?", (t["id"],))
            return dict(ok=True)
        if a == "delete":
            c.execute("UPDATE users SET org_id=0 WHERE org_id=?", (mine["id"],)); c.execute("DELETE FROM orgs WHERE id=?", (mine["id"],)); return dict(ok=True)
        raise Err("Нет такого метода", 404)

    def admin(self, c, u, a, q, b, m="GET"):
        def cnt(sql, *p):
            return c.execute(sql, p).fetchone()[0]
        if a == "stats":
            day = now() - 86400
            top = c.execute("SELECT username,display,rubies FROM users WHERE is_bot=0 ORDER BY rubies DESC LIMIT 5").fetchall()
            recent = c.execute("SELECT s.*,u.username FROM spins s JOIN users u ON u.id=s.uid ORDER BY s.id DESC LIMIT 10").fetchall()
            return dict(users=cnt("SELECT COUNT(*) FROM users WHERE is_bot=0"), bots=cnt("SELECT COUNT(*) FROM users WHERE is_bot=1"),
                        items=cnt("SELECT COUNT(*) FROM items"), cards=cnt("SELECT COUNT(*) FROM items WHERE img LIKE '/cards/%'"),
                        messages=cnt("SELECT COUNT(*) FROM messages"), orgs=cnt("SELECT COUNT(*) FROM orgs"),
                        spins=cnt("SELECT COUNT(*) FROM spins"), spins_rare=cnt("SELECT COUNT(*) FROM spins WHERE mode='rare'"),
                        spins_today=cnt("SELECT COUNT(*) FROM spins WHERE ts>?", day),
                        new_users=cnt("SELECT COUNT(*) FROM users WHERE is_bot=0 AND created>?", day),
                        listings=cnt("SELECT COUNT(*) FROM listings WHERE status='active'"),
                        sold=cnt("SELECT COUNT(*) FROM listings WHERE status='sold'"),
                        volume=cnt("SELECT COALESCE(SUM(price),0) FROM listings WHERE status='sold'"),
                        rubies=cnt("SELECT COALESCE(SUM(rubies),0) FROM users"),
                        top=[dict(r) for r in top], recent=[dict(r) for r in recent])
        if a == "spins":
            mode = q.get("mode", ["all"])[0]
            where, p = ("WHERE s.mode=?", (mode,)) if mode in ("normal", "rare") else ("", ())
            rows = c.execute("SELECT s.*,u.username FROM spins s JOIN users u ON u.id=s.uid " + where + " ORDER BY s.id DESC LIMIT 100", p).fetchall()
            return dict(spins=[dict(r) for r in rows])
        if a == "settings":
            if m == "POST":
                for k, (lo, hi) in SPIN_LIMITS.items():
                    if k in b:
                        try:
                            v = int(b[k])
                        except (TypeError, ValueError):
                            raise Err("Неверное значение: %s" % k)
                        if not lo <= v <= hi: raise Err("%s: допустимо %d–%d" % (k, lo, hi))
                        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))
                return dict(ok=True, settings=spin_cfg(c))
            return dict(settings=spin_cfg(c))
        if a == "broadcast":
            text = str(b.get("text", "")).strip()[:1000]
            if not text: raise Err("Пустой текст рассылки")
            users = c.execute("SELECT id FROM users WHERE is_bot=0").fetchall()
            for r in users:
                bot_say(c, r["id"], text)
                notify(c, r["id"], "info", "Рассылка: " + text)
            return dict(ok=True, sent=len(users))
        if a == "users":
            return dict(users=[dict(pub(c, r), rubies=r["rubies"], org=org_of(c, r["org_id"]),
                                     items=cnt("SELECT COUNT(*) FROM items WHERE owner=?", r["id"]),
                                     cards=cnt("SELECT COUNT(*) FROM items WHERE owner=? AND img LIKE '/cards/%'", r["id"]),
                                     spins=cnt("SELECT COUNT(*) FROM spins WHERE uid=?", r["id"]),
                                     birthday=r["birthday"] or "", created=r["created"] or 0)
                         for r in c.execute("SELECT * FROM users WHERE is_bot=0 ORDER BY id")])
        if a == "requests":
            kind = q.get("kind", ["badge"])[0]
            rows = c.execute("SELECT r.*, u.username, u.display FROM requests r JOIN users u ON u.id=r.uid WHERE r.kind=? ORDER BY (r.status='new') DESC, r.id DESC LIMIT 100", (kind,)).fetchall()
            avg = c.execute("SELECT AVG(rating) FROM requests WHERE kind='review'").fetchone()[0]
            return dict(requests=[dict(r) for r in rows], avg=round(avg, 2) if avg else None)
        if a == "resolve":
            r = c.execute("SELECT * FROM requests WHERE id=?", (b.get("id"),)).fetchone()
            if not r: raise Err("Не найдено", 404)
            t = c.execute("SELECT * FROM users WHERE id=?", (r["uid"],)).fetchone()
            text, act = str(b.get("text", "")).strip()[:1000], b.get("action")
            if r["kind"] == "badge":
                if act == "approve":
                    if r["badge"] == "developer" and u["badge"] != "owner": raise Err("Разработчика одобряет только владелец", 403)
                    if t["badge"] == "owner": raise Err("У владельца значок менять нельзя")
                    c.execute("UPDATE users SET badge=? WHERE id=?", (r["badge"], t["id"]))
                    res = "Заявка на галочку «%s» одобрена." % BADGE_REQ[r["badge"]]
                else:
                    res = "Заявка на галочку «%s» отклонена." % BADGE_REQ[r["badge"]]
                if text: res += " Комментарий: " + text
            elif r["kind"] == "support":
                if not text: raise Err("Напиши ответ")
                res = "Ответ поддержки: " + text
            else:
                c.execute("UPDATE requests SET status='done' WHERE id=?", (r["id"],)); return dict(ok=True)
            c.execute("UPDATE requests SET status='done', result=? WHERE id=?", (res, r["id"]))
            bot_say(c, t["id"], res); notify(c, t["id"], "info", res)
            return dict(ok=True)
        tg = c.execute("SELECT * FROM users WHERE username=?", (uname(b.get("username")),)).fetchone()
        if not tg: raise Err("Пользователь не найден", 404)
        if a == "badge":
            v = b.get("badge", "")
            if v not in BADGES or v == "owner": raise Err("Неверный значок")
            if tg["badge"] == "owner": raise Err("Значок владельца менять нельзя")
            if v == "developer" and u["badge"] != "owner": raise Err("Значок разработчика выдаёт только владелец", 403)
            c.execute("UPDATE users SET badge=? WHERE id=?", (v, tg["id"])); return dict(ok=True)
        if a == "rubies":
            c.execute("UPDATE users SET rubies=MAX(0,rubies+?) WHERE id=?", (int(b.get("amount", 0)), tg["id"])); return dict(ok=True)
        if a == "reset_daily":
            c.execute("UPDATE users SET daily=0 WHERE id=?", (tg["id"],)); return dict(ok=True)
        if a == "reset_spin":
            c.execute("UPDATE users SET rare_spin=0 WHERE id=?", (tg["id"],)); return dict(ok=True)
        if a == "give":
            src = CAT["gifts"] if b.get("kind") == "gift" else CAT["cards"] if b.get("kind") == "card" else []
            it = next((x for x in src if x.get("key") == b.get("key")), None)
            if not it: raise Err("Предмет не найден", 404)
            value = it.get("value", it.get("price", 0))
            item = self.give(c, tg["id"], it["name"], it["img"], value, it["tier"])
            notify(c, tg["id"], "gift", "Администратор выдал тебе «%s»" % it["name"])
            return dict(item=item)
        raise Err("Нет такого метода", 404)

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "IP-телефона-или-ПК"

def rime_running(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=0.8) as r:
            return json.loads(r.read()).get("app") == "Rime"
    except Exception:
        return False

def free_port(start):
    for p in range(start, start + 30):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", p)); return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("Нет свободного порта в диапазоне %d-%d" % (start, start + 29))

def open_ui(url):
    if "--no-browser" in sys.argv: return
    if FROZEN or "--open" in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

def main():
    global CAT
    init(); CAT = build_catalog()
    print("Rime запущен | подарки=%d кейсы=%d фоны=%d узоры=%d карточки=%d" % (len(CAT["gifts"]), len(CAT["cases"]), len(CAT["backdrops"]), len(CAT["patterns"]), len(CAT["cards"])))
    if rime_running(PORT):
        url = "http://localhost:%d" % PORT
        print("Rime уже работает: %s — открываю его, второй сервер не нужен." % url)
        open_ui(url); return
    port = free_port(PORT)
    if port != PORT: print("Порт %d занят другой программой — использую %d." % (PORT, port))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    url = "http://localhost:%d" % port
    print("На этом устройстве:  %s" % url)
    print("С телефона/другого ПК в одной Wi-Fi сети:  http://%s:%d" % (lan_ip(), port))
    print("Не закрывай это окно — это и есть сервер. Выход: Ctrl+C.")
    open_ui(url)
    srv.serve_forever()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        msg = traceback.format_exc()
        print(msg)
        try:
            with open(os.path.join(APP_DIR, "rime-error.log"), "a", encoding="utf-8") as f:
                f.write("%s\n%s\n" % (datetime.datetime.now(), msg))
        except Exception:
            pass
        if FROZEN:
            input("Ошибка запуска. Нажми Enter, чтобы закрыть окно...")
        else:
            raise
