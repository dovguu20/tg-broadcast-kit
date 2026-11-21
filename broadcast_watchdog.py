"""Сторож розсилки. Дивиться ЗЗОВНІ: чи був свіжий ФІНІШ у cron.log.
Ловить те, чого сам broadcast.py не зловить — падіння на імпорті, вбитий процес, зламаний крон.
Пише власнику не частіше разу на добу. Запуск: venv/bin/python broadcast_watchdog.py [--dry]"""
import os, re, sys, time, asyncio
import common as k

MAX_SILENCE_H = 4  # прогін кожні 2 год; 4 — це вже два пропущені поспіль
STATE = os.path.join(k.BASE_DIR, "watchdog_state.txt")
DRY = "--dry" in sys.argv


def last_finish(path):
    """Останній рядок ФІНІШ у лозі -> unix-час. Читаємо з хвоста, лог великий."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 300_000))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    stamps = re.findall(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*ФІНІШ", tail, re.M)
    if not stamps:
        return None
    return time.mktime(time.strptime(stamps[-1], "%Y-%m-%d %H:%M:%S"))


def status(now=None):
    """None = все гаразд, інакше текст проблеми."""
    now = now or time.time()
    ts = last_finish(os.path.join(k.BASE_DIR, "cron.log"))
    if ts is None:
        return "у лозі взагалі немає завершених прогонів"
    if now - ts > MAX_SILENCE_H * 3600:
        return (f"останній прогін {int((now - ts) / 3600)} год тому "
                f"({time.strftime('%d.%m %H:%M', time.localtime(ts))})")
    print(f"ok, останній прогін {int((now - ts) / 60)} хв тому")
    return None


async def main():
    problem = status()
    if not problem:
        return
    if k.broadcast_running():
        print("прогін іде саме зараз — не тривога:", problem)
        return
    sig = time.strftime("%Y-%m-%d")
    try:
        if open(STATE).read().strip() == sig:
            print("вже повідомлено сьогодні:", problem)
            return
    except Exception:
        pass
    text = f"⛔️ Розсилка {k.BRAND} стоїть: {problem}.\nСкажи агенту: «подивись cron.log і полагодь»."
    if DRY:
        print("[dry] надіслав би:", text)
        return
    cl, cp = k.client("wd")
    await cl.connect()
    try:
        await k.alert(cl, text)
        open(STATE, "w").write(sig)  # лише після успішної відправки
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    asyncio.run(main())
