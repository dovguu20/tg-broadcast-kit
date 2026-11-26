"""Перевірки для майстра setup.sh. Режими: saved | owner | done.
owner: 0 = власник уже писав акаунту, 1 = ще ні, 2 = OWNER_ID це сам акаунт розсилки."""
import sys
import asyncio
import common as k


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
        if mode == "done":
            await k.alert(cl, f"✅ Акаунт вступив у групи, розсилка увімкнена: {k.BRAND}. "
                              "Перший пост — за розкладом, протягом 2 годин.")
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
