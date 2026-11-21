"""Тижневий звіт власнику: ліди за N днів — скільки, скільком не відповіли, як довго чекали.
Крон понеділок. Чекає до години, поки закінчиться прогін розсилки. --dry лише друкує."""
import sys, asyncio, datetime, statistics
from telethon.tl.types import User
import common as k

DAYS = int(next((a for a in sys.argv[1:] if a.isdigit()), 7))
DRY = "--dry" in sys.argv
AUTO_PREFIX = (k.CFG.get("LEAD_REPLY") or "Привет! Спасибо за сообщение")[:25]


async def report(cl) -> str:
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)
    leads, lags, lines = 0, [], []
    async for dl in cl.iter_dialogs():
        e = dl.entity
        if not isinstance(e, User) or e.bot or e.is_self or e.id == k.OWNER or dl.date < since:
            continue
        msgs = [m async for m in cl.iter_messages(e, reverse=True, limit=40)]
        if not msgs or msgs[0].out or msgs[0].date < since:
            continue
        leads += 1
        human = next((m for m in msgs if m.out and m.message is not None
                      and not m.message.startswith(AUTO_PREFIX)), None)
        if human:
            lags.append((human.date - msgs[0].date).total_seconds() / 3600)
        name = f"{e.first_name or ''} {e.last_name or ''}".strip()
        lines.append(f"  {msgs[0].date:%d.%m} {name}{' @' + e.username if e.username else ''} — "
                     f"{'відп. ' + format(lags[-1], '.1f') + ' год' if human else 'БЕЗ ВІДПОВІДІ'}")
    head = f"{k.BRAND}: лідів {leads}, без відповіді {leads - len(lags)}"
    if lags:
        head += f", медіана відповіді {statistics.median(lags):.1f} год"
    return (f"📊 Ліди за {DAYS} дн (без авто-відповідей, лише живі)\n\n" + head
            + ("\n" + "\n".join(lines) if lines else ""))


async def main():
    for _ in range(60):
        if not k.broadcast_running():
            break
        await asyncio.sleep(60)
    else:
        print("розсилка не закінчилась за годину, звіт пропущено")
        return
    cl, cp = k.client("rep")
    await cl.connect()
    try:
        text = await report(cl)
        print(text)
        if not DRY and k.OWNER:
            await k.send_owner(cl, text[:4000])
    finally:
        await cl.disconnect()
        k.cleanup(cp)


if __name__ == "__main__":
    asyncio.run(main())
