"""Вступ у групи зі списку TELEGRAM_TARGET_GROUPS партіями, з паузами 60–100 с.
Зупиняється при FloodWait / PeerFlood / ліміті груп — і пише про це власнику.
Прогрес у joined_done.txt: повторний запуск продовжує з того ж місця.
Запуск: venv/bin/python join_groups.py [скільки за раз, типово 20]"""
import os, sys, random, asyncio
from telethon import functions
from telethon.errors import (FloodWaitError, PeerFloodError, UserAlreadyParticipantError,
                             InviteRequestSentError, ChannelsTooMuchError, RPCError)
from telethon.errors.common import TypeNotFoundError
import common as k

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20
TARGETS = [t.strip() for t in (k.CFG.get("TELEGRAM_TARGET_GROUPS") or "").split(",") if t.strip()]
DONE = os.path.join(k.BASE_DIR, "joined_done.txt")


async def main():
    if k.broadcast_running():
        print("іде прогін розсилки — вступ у групи зараз не запускаємо, спробуй за годину")
        return
    done = set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    todo = [t for t in TARGETS if t.lower() not in done]
    cl, cp = k.client("join")
    await cl.connect()
    try:
        me = await cl.get_me()
        who = getattr(me, "username", None) or me.first_name
        print(f"{who}: у списку {len(TARGETS)}, лишилось {len(todo)}, за цей раз до {LIMIT}")
        joined = already = req = fail = 0
        stop = None
        with open(DONE, "a") as log:
            for i, t in enumerate(todo[:LIMIT], 1):
                try:
                    await cl(functions.channels.JoinChannelRequest(t))
                    joined += 1; tag = "ВСТУПИВ"
                except UserAlreadyParticipantError:
                    already += 1; tag = "вже там"
                except InviteRequestSentError:
                    req += 1; tag = "заявка на модерацію"
                except FloodWaitError as e:
                    stop = f"FloodWait {e.seconds} с"; break
                except PeerFloodError:
                    stop = "PeerFlood — Telegram вважає дії спамом"; break
                except ChannelsTooMuchError:
                    stop = "вичерпано ліміт груп на акаунті"; break
                except TypeNotFoundError:
                    tag = "вступ ок (відповідь не розпізнана)"  # вступ проходить, не падаємо
                except (ValueError, TypeError):
                    fail += 1; tag = "нема такої групи"
                except RPCError as e:
                    fail += 1; tag = type(e).__name__
                print(f"  [{i}] {tag} -> {t}")
                log.write(t.lower() + "\n"); log.flush()
                if i < min(LIMIT, len(todo)):
                    await asyncio.sleep(random.randint(60, 100))
        left = len(todo) - min(LIMIT, len(todo))
        summary = f"вступив {joined} | вже був {already} | заявок {req} | помилок {fail} | лишилось {left}"
        print("\n" + summary)
        if stop:
            print("Вступ у групи зупинено:", stop)
            await k.alert(cl, f"⚠️ Вступ у групи зупинено: {who}\nПричина: {stop}\n{summary}\n"
                              "Telegram просить паузу. Наступна спроба — автоматично через добу. "
                              "Якщо повторюється кілька днів — напиши Максу.")
        elif left == 0:
            print("СПИСОК ПРОЙДЕНО ПОВНІСТЮ")
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    asyncio.run(main())
