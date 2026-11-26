#!/usr/bin/env python3
"""Перевірка, чи сесія вже увійшла в Telegram.

Порожній або битий api_id дає рядок «Invalid TELEGRAM_API_ID» і код 2,
без трейсбека. Якщо файла сесії ще нема — нічого не створює і не підключається.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

from dotenv import dotenv_values


def prepare():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Лише файл цього комплекту. Порожня змінна оточення не має маскувати заповнений .env.
    cfg = dotenv_values(".env")
    api_id = (cfg.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (cfg.get("TELEGRAM_API_HASH") or "").strip()
    session = (cfg.get("TELEGRAM_SESSION_NAME") or "sender").strip()
    if not api_id.isascii() or not api_id.isdigit() or int(api_id) <= 0:
        print("Invalid TELEGRAM_API_ID")
        return 2
    if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        print("Invalid TELEGRAM_API_HASH")
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        print("Invalid TELEGRAM_SESSION_NAME")
        return 2
    if not os.path.exists(session + ".session"):
        print("authorized=false")
        print("session=missing")
        return 0
    return (int(api_id), api_hash, session)


async def main(api_id: int, api_hash: str, session: str) -> None:
    from telethon import TelegramClient

    client = TelegramClient(session, api_id, api_hash)
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
    ready = prepare()
    if isinstance(ready, int):
        raise SystemExit(ready)
    asyncio.run(main(*ready))
