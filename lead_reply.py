"""Нові вхідні в особисті на акаунті розсилки: авто-перша відповідь + сповіщення власнику.
Крон кожні 5 хв. Поки іде прогін розсилки — пропускає (одна сесія за раз).
Стан leads_seen.json: user_id → коли побачили. Де вже є наша вихідна — не відповідає.
--seed лише запам'ятовує наявні діалоги (обов'язково один раз перед кроном). --dry нічого не шле."""
import os, sys, json, time, asyncio
from telethon.tl.types import User
import common as k

REPLY = k.CFG.get("LEAD_REPLY") or "Привет! Спасибо за сообщение 🙌 Менеджер скоро ответит."
SEED = "--seed" in sys.argv
DRY = "--dry" in sys.argv
STATE = os.path.join(k.BASE_DIR, "leads_seen.json")


async def handle(cl, seen: dict) -> list:
    """Оновлює seen, шле авто-відповіді, повертає тексти сповіщень власнику."""
    new = []
    async for dl in cl.iter_dialogs():
        e = dl.entity
        if not isinstance(e, User) or e.bot or e.is_self or e.id == k.OWNER or str(e.id) in seen:
            continue
        msgs = await cl.get_messages(e, limit=30)
        if not msgs:
            continue
        first = msgs[-1]
        seen[str(e.id)] = {"t": time.time(), "name": f"{e.first_name or ''} {e.last_name or ''}".strip(),
                           "user": e.username, "our_first": bool(first.out)}
        if SEED or first.out:
            continue  # старий діалог або ми написали першими — не лід
        answered = any(m.out for m in msgs)
        text = (first.message or "[медіа]").replace("\n", " ")[:120]
        if not answered and not DRY:
            await cl.send_message(e, REPLY)
            await asyncio.sleep(1)
        seen[str(e.id)]["auto_replied"] = not answered
        tag = "вже відповіли" if answered else ("DRY" if DRY else "авто-відповідь надіслана")
        who = f"{seen[str(e.id)]['name']} @{e.username}" if e.username else f"{seen[str(e.id)]['name']} (id {e.id})"
        new.append(f"🔔 Новий лід на {k.BRAND}: {who}\n«{text}»\n{tag}")
    return new


async def main():
    if k.broadcast_running():
        print("іде прогін розсилки — пропускаю цей раз")
        return
    try:
        seen = json.load(open(STATE))
    except Exception:
        seen = {}
    if not os.path.exists(STATE) and not SEED:
        print("нема leads_seen.json — спершу запусти з --seed")
        return
    cl, cp = k.client("lr")
    await cl.connect()
    try:
        me = await cl.get_me()
        if k.OWNER and me.id == k.OWNER:
            print("OWNER_ID = id цього акаунта: сповіщення падали б у «Збережені» і йшли б у групи. Виправ .env.")
            return
        new = await handle(cl, seen)
        for n in new:
            print(n.replace("\n", " | "))
            if not DRY and k.OWNER:
                await k.send_owner(cl, n)
                await asyncio.sleep(1)
        json.dump(seen, open(STATE, "w"), ensure_ascii=False)
        if SEED:
            print(f"запам'ятовано {len(seen)} діалогів")
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    asyncio.run(main())
