"""Щоденна перевірка @SpamBot. Пише власнику ТІЛЬКИ коли є обмеження або перевірка не вдалась.
Чекає до години, поки закінчиться прогін розсилки (дві сесії одночасно — обрив розсилки)."""
import os, time, asyncio
import common as k

STATE = os.path.join(k.BASE_DIR, "spamcheck_state.txt")
# @SpamBot відповідає мовою акаунта — маркери «все чисто» для всіх мов, що бачили.
OK_MARKS = ("no limits are currently applied", "free as a bird",
            "свободен от каких-либо ограничений", "вільний від будь-яких обмежень")


async def main():
    for _ in range(60):
        if not k.broadcast_running():
            break
        await asyncio.sleep(60)
    else:
        print("розсилка не закінчилась за годину, перевірку пропускаю")
        return
    cl, cp = k.client("spam")
    await cl.connect()
    try:
        me = await cl.get_me()
        who = getattr(me, "username", None) or me.first_name
        await cl.send_message("SpamBot", "/start")
        await asyncio.sleep(8)
        msgs = await cl.get_messages("SpamBot", limit=3)
        reply = next((m.message for m in msgs if m.message and not m.out), "").strip()
        clean = any(m in reply.lower() for m in OK_MARKS)
        print(f"{who}: {'чисто' if clean else 'ОБМЕЖЕННЯ'} | {reply[:120]}")
        if clean:
            return
        sig = time.strftime("%Y-%m-%d")  # та сама проблема — не частіше разу на добу
        try:
            if open(STATE).read().strip() == sig:
                print("вже повідомлено сьогодні")
                return
        except Exception:
            pass
        await k.alert(cl, f"⚠️ @SpamBot: обмеження на акаунті {who}\n\n{reply[:600]}\n\n"
                          "Нічого не постити кілька днів і написати @SpamBot, що це помилка.")
        open(STATE, "w").write(sig)  # лише після успішної відправки
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    asyncio.run(main())
