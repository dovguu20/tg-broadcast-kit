"""Перевірки для майстра setup.sh. Режими: saved | owner | joining | running | done.
owner: 0 = власник уже писав акаунту, 1 = ще ні, 2 = OWNER_ID це сам акаунт розсилки.
joining / running / done — повідомлення учню в особисті (заодно перевірка, що сповіщення доходять)."""
import os
import sys
import time
import asyncio
import subprocess
from datetime import datetime, timedelta
import common as k


def next_post(cron: str | None = None, now: datetime | None = None) -> str:
    """Скільки до першого прогону розсилки цієї папки — за її рядком у crontab (install.sh --cron)."""
    try:
        if cron is None:
            cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
        line = next(l for l in cron.splitlines() if f"{k.BASE_DIR}/" in l and "broadcast.py" in l)
        minute, hour = line.split()[:2]
        even = int(hour.split("-")[0]) % 2 if hour[0].isdigit() else 0   # «0 */2» або «40 1-23/2»
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        for add in range(3, 125):   # слот за 1–2 хв може зайняти сам join_step (session.lock) — не обіцяємо його
            t = now + timedelta(minutes=add)
            if t.minute == int(minute) and t.hour % 2 == even:
                h, m = divmod(add, 60)
                return f"приблизно через {h} год {m} хв" if h else f"приблизно через {m} хв"
    except Exception:
        pass
    return "протягом 2 годин"


# Вступ: 81 група партіями по 20 щогодини (join_step.sh), між вступами 60–100 с —
# список проходить за 3,5–4 години. Розсилка вмикається лише після нього.
def joining() -> str:
    """Повторний запуск майстра бачить і паузу від Telegram, і вже пройдений список (join_step.sh)."""
    head = f"🟢 {k.label()} підключено, сповіщення доходять. "
    stop = os.path.join(k.BASE_DIR, "join.stop")
    left = os.path.getmtime(stop) - time.time() if os.path.exists(stop) else 0
    if left > 0:
        return head + f"Telegram попросив паузу у вступі в групи — наступна спроба автоматично, {k.wait_text(left)}. " \
                      "Коли закінчить, прийде «✅ … вступив у групи»."
    try:
        passed = "СПИСОК ПРОЙДЕНО ПОВНІСТЮ" in open(os.path.join(k.BASE_DIR, "join.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        passed = False
    if passed:
        return head + "Вступ у групи пройдено, вмикаю розсилку — протягом години прийде «✅ … вступив у групи»."
    return head + "Зараз він вступає в групи — близько 4 годин. " \
                  "Коли закінчить, прийде «✅ … вступив у групи», і розсилка ввімкнеться сама."


MESSAGES = {
    "joining": joining,
    "running": lambda: f"✅ Перевірка пройдена: {k.label()} підключено, розсилка працює за розкладом, кожні 2 години.",
    "done": lambda: f"✅ {k.label()} вступив у групи, розсилка увімкнена. Перша розсилка — {next_post()}, далі кожні 2 години.",
}


async def main(mode: str) -> int:
    cl, cp = k.client("wiz")
    await cl.connect()
    try:
        if mode == "saved":
            msgs = await cl.get_messages("me", limit=2)
            for label, m in zip("AB", msgs):
                print(f"  {label}: «{(m.message or '[медіа без тексту]').replace(chr(10), ' ')[:70]}»")
            if len(msgs) < 2:
                print("  (у «Збережених» менше двох повідомлень)")
            return 0 if len(msgs) == 2 else 1
        if mode == "owner":
            me = await cl.get_me()
            if me.id == k.OWNER:
                return 2
            await cl.get_dialogs()
            try:
                await cl.get_input_entity(k.OWNER)
                return 0
            except ValueError:
                return 1
        if mode in MESSAGES:
            await k.alert(cl, MESSAGES[mode]())
            return 0
        raise SystemExit(f"невідомий режим {mode}")
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1])))
    except SystemExit:
        raise
    except Exception as err:  # мережа / сесія — це не «ти ще не написав»
        print(f"  (перевірка не вдалась: {type(err).__name__})")
        sys.exit(3)
