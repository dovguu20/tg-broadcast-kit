"""Спільне для допоміжних скриптів комплекту: конфіг з .env, перевірка «іде прогін»,
клієнт на копії сесії, тривога власнику. broadcast.py цього файла не використовує."""
import os
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
    return subprocess.run(["pgrep", "-f", r"broadcast\.py"], capture_output=True).returncode == 0


def client(tag: str):
    """Клієнт на КОПІЇ файла сесії (живий файл відкриває лише broadcast.py). -> (client, копія)."""
    api_id, api_hash = int(CFG["TELEGRAM_API_ID"]), CFG["TELEGRAM_API_HASH"]  # до копії: щоб не лишати її при поганому .env
    cp = os.path.join(BASE_DIR, f".{tag}_{SESSION}")
    shutil.copy(os.path.join(BASE_DIR, SESSION + ".session"), cp + ".session")
    return TelegramClient(cp, api_id, api_hash, receive_updates=False), cp


def cleanup(cp: str) -> None:
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
