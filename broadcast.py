import os
import sys
import json
import shutil
import time
import random
import asyncio
import logging
import urllib.parse
import urllib.request

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    SlowModeWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    ChatAdminRequiredError,
)

# ------------------------- НАЛАШТУВАННЯ -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
# Необов'язково: окремий файл з токеном бота для тривог (у Макса — /opt/tg-finder/.env).
if os.getenv("TELEGRAM_REPORT_ENV"):
    load_dotenv(os.getenv("TELEGRAM_REPORT_ENV"), override=False)

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "session")
TARGET_GROUPS = os.getenv("TELEGRAM_TARGET_GROUPS")

MIN_WAIT = int(os.getenv("TELEGRAM_MIN_WAIT", "8"))
MAX_WAIT = int(os.getenv("TELEGRAM_MAX_WAIT", "15"))

# Скільки варіантів оголошень чергувати (A/B = 2).
AB_COUNT = int(os.getenv("TELEGRAM_AB_COUNT", "2"))
# Максимум очікування на ОДНІЙ групі. Довший FloodWait -> групу пропускаємо до дедлайну.
# Це НЕ ліміт акаунта: частина груп має добове вікно (~10 год на добу, у кожної своє),
# і Telegram у ньому віддає FLOOD_WAIT до кінця вікна. Аудит 05.11: дедлайни фіксовані
# на групу (webcam_token 04:41, onlyfans_chaty 10:29, Adults_play_Board 18:05 ...),
# а вже в наступному прогоні акаунт спокійно шле в інші групи.
MAX_FLOOD_WAIT = int(os.getenv("TELEGRAM_MAX_FLOOD_WAIT", "300"))
# Стільки over-cap FloodWait ПІДРЯД від сервера без жодного OK — зупиняємо прогін:
# добові вікна різних груп так не збігаються, це вже схоже на ліміт акаунта.
FLOOD_STREAK_STOP = int(os.getenv("TELEGRAM_FLOOD_STREAK_STOP", "5"))
# Нижче цього числа доставок за прогін — це вже не «погані групи», це проблема акаунта.
LOW_DELIVERY = int(os.getenv("TELEGRAM_LOW_DELIVERY", "25"))
# Крон стартує секунда-в-секунду щодня — надто механічно. Зсуваємо старт випадково.
START_JITTER = int(os.getenv("TELEGRAM_START_JITTER", "240"))
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN", "")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "")
# Кому писати тривоги, якщо бота нема: Telegram id власника (НЕ цього акаунта — інакше
# тривога впаде у «Збережені» і наступний прогін розішле її в групи як оголошення).
OWNER_ID = os.getenv("OWNER_ID", "").strip()
# --dry: підключитись, перевірити Premium, тексти і групи, нічого не надсилати.
DRY = "--dry" in sys.argv
SESSION_PATH = os.path.join(BASE_DIR, SESSION_NAME)
LOG_PATH = os.path.join(BASE_DIR, "cron.log")
STATE_PATH = os.path.join(BASE_DIR, "ab_state.txt")
PREMIUM_STATE_PATH = os.path.join(BASE_DIR, "premium_state.txt")
GROUP_WAIT_PATH = os.path.join(BASE_DIR, "group_wait.json")

# ------------------------- ЛОГУВАННЯ -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("broadcast")

if not API_ID or not API_HASH:
    log.error("TELEGRAM_API_ID та TELEGRAM_API_HASH мають бути в .env")
    raise SystemExit(1)
if not TARGET_GROUPS:
    log.error("TELEGRAM_TARGET_GROUPS має бути в .env")
    raise SystemExit(1)


def read_counter() -> int:
    """Номер запуску — для чергування A/B між запусками."""
    try:
        with open(STATE_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def write_counter(value: int) -> None:
    try:
        with open(STATE_PATH, "w") as f:
            f.write(str(value))
    except Exception as e:
        log.warning("Не вдалось записати лічильник A/B: %s", e)


def read_premium_state() -> str:
    try:
        with open(PREMIUM_STATE_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def write_premium_state(value: str) -> None:
    try:
        with open(PREMIUM_STATE_PATH, "w") as f:
            f.write(value)
    except Exception as error:
        log.warning("Не вдалось записати стан Premium: %s", error)


async def alert(client, text: str) -> None:
    """Тривога власнику: через бота, якщо він налаштований, інакше в ЛС з цього акаунта."""
    if REPORT_BOT_TOKEN and REPORT_CHAT_ID:
        await asyncio.to_thread(post_report_alert, text)
    elif OWNER_ID:
        await send_owner(client, text)
    else:
        raise RuntimeError("Тривоги не налаштовані: вкажи OWNER_ID або REPORT_BOT_TOKEN+REPORT_CHAT_ID у .env")


async def send_owner(client, text: str) -> None:
    """Числовий id Telethon знає лише після того, як бачив цього користувача — тому власник
    один раз пише акаунту розсилки, а при промаху кешу читаємо діалоги і пробуємо ще раз."""
    try:
        await client.send_message(int(OWNER_ID), text)
    except ValueError:
        await client.get_dialogs()
        await client.send_message(int(OWNER_ID), text)


def post_report_alert(text: str) -> None:
    if not REPORT_BOT_TOKEN or not REPORT_CHAT_ID:
        raise RuntimeError("Report bot is not configured")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage",
        data=urllib.parse.urlencode({"chat_id": REPORT_CHAT_ID, "text": text}).encode(),
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"ReportBotHttp{response.status}")


async def record_premium_status(client, me, premium: bool) -> None:
    previous = read_premium_state()
    current = "active" if premium else "missing"
    if previous == current:
        return
    if premium and previous != "missing":
        write_premium_state(current)
        return

    identity = getattr(me, "username", None) or me.first_name or str(me.id)
    if premium:
        text = (
            f"✅ Telegram Premium відновлено: {identity}. "
            "Нові розсилки знову дозволені; пропущені автоматично не надсилалися."
        )
    else:
        text = (
            f"⚠️ Telegram Premium закінчився: {identity}. "
            "Розсилку цього акаунта автоматично зупинено. Поновіть Premium."
        )
    delivered = False
    try:
        await alert(client, text)
        delivered = True
    except Exception:
        # Свідомо БЕЗ запасного send_message("me"): запис у «Збережені» став би
        # найновішим повідомленням і пішов би в групи як варіант A.
        log.exception("Не вдалось надіслати Premium-сповіщення")
    if delivered:
        write_premium_state(current)


async def main() -> None:
    targets = [t.strip() for t in TARGET_GROUPS.split(",") if t.strip()]
    log.info("=== СТАРТ розсилки | груп у списку: %d ===", len(targets))

    # receive_updates=False: розсилці апдейти не потрібні, а їх обробка — єдине, що
    # рвало з'єднання посеред прогону (06–08.11: відповідь @SpamBot іншій сесії
    # приходила сюди апдейтом з невідомим Telethon типом → «misusing the session» →
    # disconnect → решта груп «Cannot send requests while disconnected»).
    # Бонус: мінус десятки GetDifference-запитів на прогін.
    client = TelegramClient(SESSION_PATH, int(API_ID), API_HASH, receive_updates=False)
    await client.connect()

    if not await client.is_user_authorized():
        log.error("Сесія невалідна або відсутня. Потрібен повторний вхід (session-файл).")
        await client.disconnect()
        raise SystemExit(1)

    me = await client.get_me()
    log.info("Акаунт: %s (id=%s)", getattr(me, "username", None) or me.first_name, me.id)
    if OWNER_ID and str(me.id) == OWNER_ID:
        log.error("OWNER_ID збігається з id цього акаунта: тривоги падали б у «Збережені» і йшли б "
                  "у групи як оголошення. Вкажи у .env id СВОГО особистого акаунта, не цього.")
        await client.disconnect()
        raise SystemExit(1)
    premium_active = bool(getattr(me, "premium", False))
    await record_premium_status(client, me, premium_active)
    if not premium_active:
        log.error("Telegram Premium відсутній — розсилку зупинено до поновлення підписки.")
        await client.disconnect()
        # Для сторожа: прогін відпрацював штатно (про Premium власник уже отримав окреме повідомлення).
        log.info("=== ФІНІШ | пропущено: нема Telegram Premium ===")
        return
    # Беремо останні AB_COUNT повідомлень зі 'Збережених'.
    # limit повертає від найновішого до старішого: [0]=найновіше (A), [1]=наступне (B)...
    saved = await client.get_messages("me", limit=AB_COUNT)
    if len(saved) < AB_COUNT:
        log.error("У 'Збережених' %d повідомлень, а треба %d (A і B) — розсилку пропущено.", len(saved), AB_COUNT)
        try:
            who = f"@{me.username}" if getattr(me, "username", None) else getattr(me, "first_name", "?")
            await alert(client, f"⚠️ Розсилка: у «Збережених» акаунта {who} {len(saved)} повідомлень, а треба {AB_COUNT}. "
                                "Надішли собі туди оголошення: спочатку B, потім A.")
        except Exception:
            log.exception("Не вдалось надіслати тривогу про «Збережені»")
        await client.disconnect()
        log.info("=== ФІНІШ | пропущено: у «Збережених» нема двох оголошень ===")
        return

    variants = list(saved)  # variants[0]=A (найновіше), variants[1]=B, ...

    # Який варіант на цей запуск — чергуємо за лічильником.
    counter = read_counter()
    idx = counter % len(variants)
    msg = variants[idx]
    label = chr(ord("A") + idx)  # 0->A, 1->B
    log.info("Цей запуск: варіант %s (повідомлення id=%s, усього варіантів: %d)",
             label, msg.id, len(variants))

    if DRY:
        ok = bad = 0
        for t in targets:
            try:
                await client.get_input_entity(t)
                ok += 1
            except Exception as e:
                bad += 1
                log.warning("[dry] не знаходжу групу %s: %s", t, e)
            await asyncio.sleep(random.uniform(1, 2))  # ResolveUsername має свій флуд-ліміт
        log.info("=== DRY | варіанти: %s | груп знайдено: %d | не знайдено: %d | нічого не надсилав ===",
                 ", ".join(f"id={m.id} «{(m.message or '[медіа]').replace(chr(10), ' ')[:40]}»" for m in variants), ok, bad)
        await client.disconnect()
        return

    if START_JITTER > 0:
        delay = random.randint(0, START_JITTER)
        log.info("Джитер старту: чекаю %d с.", delay)
        await asyncio.sleep(delay)

    # Порядок груп різний щоразу.
    random.shuffle(targets)

    # Групи, що ще в своєму добовому вікні (дедлайн з минулого FloodWait), не чіпаємо:
    # кожна спроба туди — гарантований FLOOD_WAIT і зайвий запит.
    try:
        group_wait = {k: float(v) for k, v in json.load(open(GROUP_WAIT_PATH)).items()}
    except Exception:
        group_wait = {}
    group_wait = {k: v for k, v in group_wait.items() if v > time.time()}
    waiting = [t for t in targets if t in group_wait]
    if waiting:
        log.info("У добовому вікні ще %d груп, пропускаю: %s", len(waiting), ", ".join(waiting))
        targets = [t for t in targets if t not in group_wait]

    sent = 0
    failed = 0
    streak = 0  # over-cap FloodWait підряд від сервера

    for i, target in enumerate(targets, 1):
        # ponytail: Premium перевіряємо лише на старті прогону (вище). Перевірка перед
        # КОЖНОЮ групою коштувала get_me() на ітерацію (+107 запитів/прогін) і живила
        # account-level FloodWait. Стеля: якщо Premium злетить посеред прогону, решта
        # піде без нього — наступний прогін це зловить на старті.
        try:
            if not client.is_connected():
                log.warning("З'єднання втрачено — перепідключаюсь.")
                await client.connect()
            await client.forward_messages(target, msg)
            sent += 1
            streak = 0
            log.info("[%d/%d] OK (%s) -> %s", i, len(targets), label, target)

        except FloodWaitError as e:
            wait = getattr(e, "seconds", 0) or 30
            # Telethon запам'ятовує FloodWait за ТИПОМ запиту і далі кидає його сам,
            # без сервера, для всіх наступних груп — так одна група з вікном гасила
            # решту прогону (30.10: 0-12 доставок зі 107). Ліміт на групу, кеш чистимо.
            # ponytail: приватне поле Telethon; якщо його перейменують — getattr дасть
            # порожній dict і ми просто повернемось до старої поведінки.
            getattr(client, "_flood_waited_requests", {}).clear()
            if wait > MAX_FLOOD_WAIT:
                failed += 1
                streak += 1
                group_wait[target] = time.time() + wait
                log.warning("[%d/%d] FloodWait %d сек на %s — вікно групи до %s, пропускаю.",
                            i, len(targets), wait, target,
                            time.strftime("%H:%M", time.localtime(group_wait[target])))
                if streak >= FLOOD_STREAK_STOP:
                    log.warning("%d over-cap FloodWait підряд без жодного OK — схоже на ліміт "
                                "акаунта, зупиняю цей прогін.", streak)
                    break
                continue
            log.warning("[%d/%d] FloodWait %d сек на %s — чекаю.", i, len(targets), wait, target)
            await asyncio.sleep(wait + 5)
            try:
                await client.forward_messages(target, msg)
                sent += 1
                log.info("[%d/%d] OK після паузи (%s) -> %s", i, len(targets), label, target)
            except Exception as e2:
                failed += 1
                log.error("[%d/%d] Не вдалось після паузи -> %s: %s", i, len(targets), target, e2)

        except SlowModeWaitError as e:
            failed += 1
            log.warning("[%d/%d] SlowMode %s сек -> %s, пропускаю.", i, len(targets), e.seconds, target)

        except (ChatWriteForbiddenError, UserBannedInChannelError, ChatAdminRequiredError):
            failed += 1
            log.warning("[%d/%d] Немає прав писати -> %s, пропускаю.", i, len(targets), target)

        except ChannelPrivateError:
            failed += 1
            log.warning("[%d/%d] Немає доступу (приват/кік) -> %s, пропускаю.", i, len(targets), target)

        except Exception as e:
            failed += 1
            log.error("[%d/%d] Помилка -> %s: %s", i, len(targets), target, e)

        if i < len(targets):
            await asyncio.sleep(random.randint(MIN_WAIT, MAX_WAIT))

    try:
        json.dump({k: v for k, v in group_wait.items() if v > time.time()},
                  open(GROUP_WAIT_PATH, "w"))
    except Exception:
        log.warning("Не вдалось записати group_wait.json")

    # Зберігаємо лічильник для наступного запуску (наступного разу буде інший варіант).
    if premium_active or sent > 0:
        write_counter(counter + 1)

    # Нуль доставок зі 100+ цілей = акаунт забанений або сесія злетіла. Алерт один раз,
    # поки стан не зміниться: інакше кожні 2 години летів би однаковий крик.
    zero_path = os.path.join(BASE_DIR, "zero_state.txt")
    try:
        was_zero = open(zero_path).read().strip() == "zero"
    except Exception:
        was_zero = False
    now_zero = sent < LOW_DELIVERY and len(targets) >= 20
    if now_zero != was_zero:
        who = getattr(me, "username", None) or getattr(me, "first_name", "?")
        text = (f"\u26a0\ufe0f Розсилка {who}: лише {sent} доставок зі {len(targets)} груп "
                f"(норма від {LOW_DELIVERY}). Схоже на флуд-таймер або бан — перевір @SpamBot."
                if now_zero else
                f"\u2705 Розсилка {who} відновилась: {sent} зі {len(targets)}.")
        try:
            await alert(client, text)
            open(zero_path, "w").write("zero" if now_zero else "ok")  # лише після успішної тривоги
        except Exception:
            log.exception("Не вдалось надіслати алерт про нульову доставку")

    await client.disconnect()
    log.info("=== ФІНІШ | варіант: %s | надіслано: %d | помилок: %d | усього: %d | у вікні: %d ===",
             label, sent, failed, len(targets), len(waiting))


def _alert_crash(e: BaseException) -> None:
    """Мовчазна смерть скрипта — найгірший сценарій. Дедуп: раз на добу на тип помилки."""
    sig = f"{time.strftime('%Y-%m-%d')} {type(e).__name__}"
    path = os.path.join(BASE_DIR, "crash_state.txt")
    try:
        if open(path).read().strip() == sig:
            return
    except Exception:
        pass
    _send_crash(e)
    try:
        open(path, "w").write(sig)  # лише після успішної відправки — інакше без повтору
    except Exception:
        pass


def _send_crash(e: BaseException) -> None:
    text = f"\u26d4\ufe0f Розсилка {os.path.basename(BASE_DIR)} УПАЛА: {type(e).__name__}: {e}"
    if REPORT_BOT_TOKEN and REPORT_CHAT_ID:
        post_report_alert(text)
        return
    if not OWNER_ID:
        raise RuntimeError("Тривоги не налаштовані (OWNER_ID порожній)")
    # Основний клієнт уже мертвий разом зі своїм event loop — відкриваємо нову сесію з КОПІЇ
    # файла (той самий файл під двома клієнтами = «database is locked»).
    cp = os.path.join(BASE_DIR, f".crash_{SESSION_NAME}")
    shutil.copy(SESSION_PATH + ".session", cp + ".session")

    async def send():
        c = TelegramClient(cp, int(API_ID), API_HASH, receive_updates=False)
        await c.connect()
        try:
            await send_owner(c, text)
        finally:
            await c.disconnect()
    try:
        asyncio.run(send())
    finally:
        for suffix in (".session", ".session-journal"):
            try:
                os.remove(cp + suffix)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except BaseException as e:
        log.exception("Розсилка впала")
        try:
            _alert_crash(e)
        except Exception:
            log.exception("Не вдалось надіслати алерт про падіння")
        raise
