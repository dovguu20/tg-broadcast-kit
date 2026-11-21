"""Офлайн-перевірка комплекту без Telegram: підроблений клієнт, тимчасова копія папки.
Запуск: venv/bin/python selfcheck.py   → в кінці має бути «ВСЕ ГАРАЗД»."""
import asyncio, datetime, json, os, re, shutil, subprocess, sys, tempfile, time

KIT = os.path.dirname(os.path.abspath(__file__))
OWNER = 777
checks = []


def ok(name, cond):
    checks.append((name, bool(cond)))
    print(("ok   " if cond else "FAIL ") + name)


# ---------- статичні перевірки ----------
from dotenv import dotenv_values
env = dotenv_values(os.path.join(KIT, ".env.example"))
groups = [g.strip() for g in env["TELEGRAM_TARGET_GROUPS"].split(",") if g.strip()]
ok(f".env.example: {len(groups)} груп, усі виду @name", len(groups) == 81 and all(re.fullmatch(r"@[A-Za-z0-9_]{4,32}", g) for g in groups))
ok(".env.example: LEAD_REPLY з переносами рядків", "\n" in env["LEAD_REPLY"] and "\\n" not in env["LEAD_REPLY"])
ok(".env.example: три порожні поля для учня", all(env.get(k) == "" for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "OWNER_ID")))
cron = [l for l in open(os.path.join(KIT, "crontab.example")).read().splitlines() if l.strip()]
ok("crontab.example: 5 завдань з маркером", len(cron) == 5 and all(l.endswith("# tg-broadcast-kit") and len(l.split()) >= 7 for l in cron))
ok("install.sh: синтаксис bash", subprocess.run(["bash", "-n", os.path.join(KIT, "install.sh")]).returncode == 0)
for s in ("broadcast.py", "lead_reply.py", "leads_report.py", "spamcheck.py", "broadcast_watchdog.py", "join_groups.py", "login_phone.py", "check_account.py", "common.py"):
    ok(f"{s} існує", os.path.exists(os.path.join(KIT, s)))

# ---------- пісочниця ----------
tmp = tempfile.mkdtemp(prefix="kitcheck_")
for f in os.listdir(KIT):
    if f.endswith(".py"):
        shutil.copy(os.path.join(KIT, f), tmp)
open(os.path.join(tmp, ".env"), "w").write(
    "TELEGRAM_API_ID=12345\nTELEGRAM_API_HASH=" + "a" * 32 + f"\nOWNER_ID={OWNER}\nBRAND=Test\n"
    "TELEGRAM_SESSION_NAME=sender\nTELEGRAM_MIN_WAIT=0\nTELEGRAM_MAX_WAIT=0\nTELEGRAM_START_JITTER=0\n"
    'LEAD_REPLY="Привет! Спасибо за сообщение"\nTELEGRAM_TARGET_GROUPS=@g1,@g2,@g3\n')
open(os.path.join(tmp, "sender.session"), "w").write("fake")
sys.path.insert(0, tmp)
os.chdir(tmp)

from telethon.errors import FloodWaitError
from telethon.tl.types import User


class Msg:
    def __init__(self, id, message, out=False, date=None):
        self.id, self.message, self.out = id, message, out
        self.date = date or datetime.datetime.now(datetime.timezone.utc)


class Me:
    id, username, first_name, premium = 111, "testacc", "T", True


class Fake:
    forwards, sent, flood = [], [], {}

    def __init__(self, session, api_id, api_hash, receive_updates=False):
        self.session = session

    async def connect(self): pass
    async def disconnect(self): pass
    def is_connected(self): return True
    async def is_user_authorized(self): return True
    async def get_me(self): return Me
    async def get_input_entity(self, t): return t
    async def get_messages(self, peer, limit=None): return [Msg(295, "A text"), Msg(294, "B text")][:limit]
    async def send_message(self, peer, text): Fake.sent.append((peer, text))

    async def forward_messages(self, target, msg):
        if target in Fake.flood:
            raise FloodWaitError(request=None, capture=Fake.flood[target])
        Fake.forwards.append((target, msg.id))


import broadcast
broadcast.TelegramClient = Fake


def run():
    Fake.forwards.clear(); Fake.sent.clear()
    asyncio.run(broadcast.main())
    return open(os.path.join(tmp, "cron.log"), encoding="utf-8").read()


log = run()
ok("прогін 1: усі 3 групи, варіант A id=295", [t for t, _ in Fake.forwards] and len(Fake.forwards) == 3 and all(i == 295 for _, i in Fake.forwards))
ok("прогін 1: ФІНІШ у лозі, лічильник A/B = 1", "ФІНІШ" in log and open(os.path.join(tmp, "ab_state.txt")).read() == "1")
run()
ok("прогін 2: варіант B id=294", len(Fake.forwards) == 3 and all(i == 294 for _, i in Fake.forwards))
Fake.flood = {"@g2": 100000}
log = run()
gw = json.load(open(os.path.join(tmp, "group_wait.json")))
ok("FloodWait понад ліміт: @g2 пропущена, дедлайн записано, інші 2 доставлені", len(Fake.forwards) == 2 and "@g2" in gw and "вікно групи" in log)
Fake.flood = {}
log = run()
ok("наступний прогін: @g2 ще у вікні, не чіпаємо", len(Fake.forwards) == 2 and "У добовому вікні ще 1" in log)
Me.premium = False
run()
ok("без Premium: нічого не надіслано, тривога власнику в ЛС", not Fake.forwards and any(p == OWNER and "Premium" in t for p, t in Fake.sent))
Me.premium = True
broadcast.DRY = True
log = run()
ok("--dry: нічого не надіслано, групи перевірені", not Fake.forwards and "DRY | варіанти" in log and "груп знайдено: 3" in log)
broadcast.DRY = False
Me.id = OWNER
try:
    run(); ok("OWNER_ID = сам акаунт → зупинка", False)
except SystemExit:
    ok("OWNER_ID = сам акаунт → зупинка", True)
Me.id = 111
Fake.sent.clear()
broadcast._alert_crash(RuntimeError("boom"))
ok("падіння: тривога власнику через копію сесії, копію прибрано", any(p == OWNER and "УПАЛА" in t for p, t in Fake.sent) and not os.path.exists(os.path.join(tmp, ".crash_sender.session")))

# ---------- тривога власнику, якого сесія ще не бачила ----------
import common
common.TelegramClient = Fake


class ColdFake(Fake):
    """Як справжній Telethon: числовий id невідомий, доки не прочитали діалоги."""
    known = False

    async def get_dialogs(self): ColdFake.known = True

    async def send_message(self, peer, text):
        if peer == OWNER and not ColdFake.known:
            raise ValueError("Could not find the input entity for PeerUser")
        Fake.sent.append((peer, text))


Fake.sent.clear()
asyncio.run(common.send_owner(ColdFake("x", 1, "h"), "тест"))
ok("send_owner: id не в кеші → читає діалоги і доставляє", Fake.sent == [(OWNER, "тест")])
ColdFake.known = False
Me.id = OWNER
try:
    asyncio.run(common.send_owner(ColdFake("x", 1, "h"), "тест")); ok("send_owner: OWNER = сам акаунт → відмова", False)
except RuntimeError:
    ok("send_owner: OWNER = сам акаунт → відмова", True)
Me.id = 111

# ---------- lead_reply ----------
import lead_reply


class Dlg:
    def __init__(self, entity, date=None):
        self.entity = entity
        self.date = date or datetime.datetime.now(datetime.timezone.utc)


def user(id, name, **kw):
    return User(id=id, first_name=name, username=name.lower(), bot=kw.get("bot", False), is_self=kw.get("is_self", False))


class LeadFake(Fake):
    history = {}

    async def iter_dialogs(self):
        for e, _ in self.history:
            yield Dlg(e)

    def _hist(self, peer):
        return next(h for e, h in self.history if e.id == peer.id)

    async def get_messages(self, peer, limit=None):
        return self._hist(peer)

    async def iter_messages(self, peer, reverse=False, limit=None):
        for m in reversed(self._hist(peer)):
            yield m


u_new, u_ans, u_ours, u_owner, u_bot = user(1, "Anna"), user(2, "Bella"), user(3, "Cara"), user(OWNER, "Owner"), user(5, "Bot", bot=True)
LeadFake.history = [
    (u_new, [Msg(3, "Здравствуйте, по вакансии")]),                      # написала перша, ніхто не відповів
    (u_ans, [Msg(5, "ок", out=True), Msg(4, "Привет")]),                  # написала, ми вже відповіли самі
    (u_ours, [Msg(6, "hi"), Msg(2, "Привіт, ми перші", out=True)]),        # ми написали першими — не лід
    (u_owner, [Msg(7, "власник")]),
    (u_bot, [Msg(8, "бот")]),
]
Fake.sent.clear()
seen = {}
new = asyncio.run(lead_reply.handle(LeadFake("x", 1, "h"), seen))
ok("lead_reply: авто-відповідь лише тій, що написала перша без відповіді", len(Fake.sent) == 1 and Fake.sent[0][0].id == 1 and Fake.sent[0][1] == "Привет! Спасибо за сообщение")
ok("lead_reply: 2 сповіщення (новий лід + «вже відповіли»), власник/бот/наш-перший пропущені", len(new) == 2 and any("Anna" in n and "надіслана" in n for n in new) and any("Bella" in n and "вже відповіли" in n for n in new) and str(OWNER) not in seen)
Fake.sent.clear()
new2 = asyncio.run(lead_reply.handle(LeadFake("x", 1, "h"), seen))
ok("lead_reply: повторний запуск нічого не шле і не дублює", not Fake.sent and not new2)
lead_reply.SEED = True
Fake.sent.clear()
new3 = asyncio.run(lead_reply.handle(LeadFake("x", 1, "h"), {}))
ok("lead_reply --seed: лише запам'ятовує, нічого не шле", not Fake.sent and not new3)

# ---------- leads_report ----------
import leads_report
text = asyncio.run(leads_report.report(LeadFake("x", 1, "h")))
ok("leads_report: 2 ліди, 1 без відповіді, медіана є", "лідів 2" in text and "без відповіді 1" in text and "медіана" in text)

# ---------- watchdog ----------
import broadcast_watchdog as wd
stamp = lambda ago_s: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - ago_s))
open(os.path.join(tmp, "cron.log"), "w").write(f"{stamp(5 * 3600)},000 | INFO | === ФІНІШ | варіант: A ===\n")
ok("watchdog: прогін 5 год тому → тривога", wd.status() and "5 год тому" in wd.status())
open(os.path.join(tmp, "cron.log"), "w").write(f"{stamp(600)},000 | INFO | === ФІНІШ | варіант: A ===\n")
ok("watchdog: прогін 10 хв тому → тихо", wd.status() is None)
ok("watchdog/common: pgrep не бачить прогону в пісочниці", not common.broadcast_running())

os.chdir(KIT)
shutil.rmtree(tmp)
failed = [n for n, c in checks if not c]
print(f"\n{len(checks) - len(failed)}/{len(checks)} перевірок пройшло")
print("ВСЕ ГАРАЗД" if not failed else "ПРОВАЛЕНО: " + "; ".join(failed))
sys.exit(1 if failed else 0)
