"""Спільне для допоміжних скриптів комплекту: конфіг з .env, перевірка «іде прогін»,
клієнт на копії сесії, тривога власнику. broadcast.py цього файла не використовує."""
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

from dotenv import dotenv_values
from telethon import TelegramClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = dotenv_values(os.path.join(BASE_DIR, ".env"))
SESSION = CFG.get("TELEGRAM_SESSION_NAME") or "sender"
OWNER = int(CFG["OWNER_ID"]) if (CFG.get("OWNER_ID") or "").strip().isdigit() else 0
BRAND = CFG.get("BRAND") or os.path.basename(BASE_DIR)
TOKEN = CFG.get("REPORT_BOT_TOKEN") or ""
CHAT = CFG.get("REPORT_CHAT_ID") or ""


def broadcast_running() -> bool:
    """Поки іде прогін, сесію не чіпає більше ніхто: дві сесії на одному auth key —
    Telegram рве з'єднання розсилки («account is likely misusing the session»)."""
    # Саме процес розсилки (а не редактор чи tail, у яких у рядку теж є «broadcast.py»)
    # і саме ЦІЄЇ папки: інші акаунти на сервері (/root/broadcast-2, ...) нам не заважають.
    out = subprocess.run(["pgrep", "-f", r"venv/bin/python broadcast\.py"], capture_output=True, text=True).stdout
    here = os.path.realpath(BASE_DIR)
    for pid in out.split():
        try:
            if os.path.realpath(f"/proc/{pid}/cwd") == here:
                return True
        except OSError:
            pass
    return False


def require_api():
    """Перевірити ключі ДО копії файла сесії, щоб порожній api_id не лишав трейсбек."""
    api_id = (CFG.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (CFG.get("TELEGRAM_API_HASH") or "").strip()
    if not api_id.isascii() or not api_id.isdigit() or int(api_id) <= 0:
        raise SystemExit("Invalid TELEGRAM_API_ID")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise SystemExit("Invalid TELEGRAM_API_HASH")
    return int(api_id), api_hash


def client(tag: str, live: bool = False):
    """Клієнт на КОПІЇ файла сесії -> (client, копія). live=True — на самому файлі сесії
    (лише під session.lock, коли розсилка точно не йде: так Telegram-кеш груп лишається
    в сесії і перший прогін не резолвить 81 юзернейм заново)."""
    api_id, api_hash = require_api()  # до копії: щоб не лишати її при поганому .env
    if live:
        return TelegramClient(os.path.join(BASE_DIR, SESSION), api_id, api_hash, receive_updates=False), None
    cp = os.path.join(BASE_DIR, f".{tag}_{SESSION}")
    shutil.copy(os.path.join(BASE_DIR, SESSION + ".session"), cp + ".session")
    return TelegramClient(cp, api_id, api_hash, receive_updates=False), cp


def save_json(path: str, data) -> None:
    """Атомарний запис: обрив чи перезавантаження посеред запису не лишить порожній файл."""
    import json
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def cleanup(cp) -> None:
    if not cp:
        return
    for suffix in (".session", ".session-journal"):
        try:
            os.remove(cp + suffix)
        except OSError:
            pass


async def send_owner(cl, text: str) -> None:
    """ЛС власнику. Числовий id Telethon знає лише після того, як бачив цього користувача:
    тому власник має один раз написати акаунту розсилки, а ми при промаху кешу читаємо
    список діалогів і пробуємо ще раз."""
    me = await cl.get_me()
    if me.id == OWNER:
        raise RuntimeError("OWNER_ID = id цього акаунта: повідомлення впало б у «Збережені» "
                           "і пішло б у групи як оголошення. Вкажи id ОСОБИСТОГО акаунта.")
    try:
        await cl.send_message(OWNER, text)
    except ValueError:
        await cl.get_dialogs()
        await cl.send_message(OWNER, text)


async def alert(cl, text: str) -> None:
    """Через бота, якщо він є; інакше в ЛС власнику з цього акаунта; інакше лише друк."""
    if TOKEN and CHAT:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": CHAT, "text": text}).encode())
        urllib.request.urlopen(req, timeout=30).read()
    elif OWNER:
        await send_owner(cl, text)
    else:
        print("ТРИВОГА не надіслана (OWNER_ID порожній):", text)
        return
    print("тривогу надіслано власнику")
