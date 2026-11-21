#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient


async def main() -> None:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))  # сесія лежить біля скрипта
    load_dotenv()
    client = TelegramClient(
        os.getenv("TELEGRAM_SESSION_NAME", "session"),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
        if not authorized:
            print("authorized=false")
            return
        me = await client.get_me()
        print(
            f"authorized=true username=@{getattr(me, 'username', None) or 'no_username'} "
            f"premium={bool(getattr(me, 'premium', False))}"
        )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
