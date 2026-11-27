"""Перевірки для майстра setup.sh. Режими: saved | owner | joining | running | done.
owner: 0 = власник уже писав акаунту, 1 = ще ні, 2 = OWNER_ID це сам акаунт розсилки.
joining / running / done — повідомлення учню в особисті (заодно перевірка, що сповіщення доходять)."""
import os
import re
import sys
import time
import asyncio
import common as k


def acct() -> str:
    """«Акаунт 2 (name)»: номер — з назви папки (broadcast-2), як у розкладі install.sh."""
    m = re.fullmatch(r"broadcast-(\d+)", os.path.basename(k.BASE_DIR))
    return f"Акаунт {int(m.group(1)) if m else 1} ({k.BRAND})"


# Вступ: 81 група партіями по 20 щогодини (join_step.sh), між вступами 60–100 с —
# список проходить за 3,5–4 години. Розсилка вмикається лише після нього.
def joining() -> str:
    """Повторний запуск майстра бачить і паузу від Telegram, і вже пройдений список (join_step.sh)."""
    head = f"🟢 {acct()} підключено, сповіщення доходять. "
    stop = os.path.join(k.BASE_DIR, "join.stop")
    if os.path.exists(stop) and time.time() - os.path.getmtime(stop) < 86400:
        return head + "Telegram попросив паузу у вступі в групи — наступна спроба автоматично через добу. " \
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
    "running": lambda: f"✅ Перевірка пройдена: {acct()} підключено, розсилка працює за розкладом, кожні 2 години.",
    "done": lambda: f"✅ {acct()} вступив у групи, розсилка увімкнена. Перший пост — за розкладом, протягом 2 годин.",
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
