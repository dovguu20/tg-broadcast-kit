#!/usr/bin/env python3
"""Explicit, single-request Telegram onboarding; default is a read-only check."""
import argparse
import asyncio
import fcntl
import json
import os
import platform
import re
import time
from getpass import getpass
from pathlib import Path

import telethon
from dotenv import dotenv_values
from telethon import TelegramClient, errors, functions, types
from telethon.tl.alltlobjects import LAYER

BASE = Path(__file__).resolve().parent


def configuration(base):
    # Read this project's file, without inheriting another project's variables.
    config = dotenv_values(base / ".env")
    api_id = config.get("TELEGRAM_API_ID", "") or ""
    api_hash = config.get("TELEGRAM_API_HASH", "") or ""
    session = config.get("TELEGRAM_SESSION_NAME", "") or ""
    if not api_id.isascii() or not api_id.isdigit() or int(api_id) <= 0:
        raise ValueError("Invalid TELEGRAM_API_ID")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("Invalid TELEGRAM_API_HASH")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", session):
        raise ValueError("Invalid TELEGRAM_SESSION_NAME")
    return int(api_id), api_hash, session


def record(base, session, event, **details):
    # Only explicitly supplied diagnostics belong here; never dump RPC objects.
    value = {"time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "event": event, "telethon": telethon.__version__, "layer": LAYER,
             **details}
    path = base / (session + ".login-status.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream)
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def run(login=False, base=BASE):
    os.umask(0o077)
    api_id, api_hash, session = configuration(base)
    session_file = base / (session + ".session")
    if session_file.exists():
        session_file.chmod(0o600)
    with (base / (session + ".login.lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Інший вхід у цю сесію на цьому комп’ютері вже запущено.")
            return 2
        client = TelegramClient(
            str(base / session), api_id, api_hash,
            device_model="broadcast kit",
            system_version=f"{platform.system()} {platform.release()}",
            app_version="1.0", request_retries=0,
            connection_retries=1, flood_sleep_threshold=0,
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=25)
            authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=20)
            if not login or authorized:
                record(base, session, "check", connected=True, authorized=authorized)
                if not authorized:
                    print("Код не запитувався. Для одного запиту входу додай --login.")
                return 0
            phone = re.sub(r"[\s()-]", "", await asyncio.to_thread(
                getpass, "Номер у форматі +кодкраїни… (прихований): "))
            if not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
                record(base, session, "invalid_phone_format")
                return 2
            try:
                # One explicit SendCode RPC: no automatic resends/AuthRestart loop.
                sent = await asyncio.wait_for(client(functions.auth.SendCodeRequest(
                    phone_number=phone, api_id=api_id, api_hash=api_hash,
                    settings=types.CodeSettings(),
                )), timeout=30)
                if isinstance(sent, types.auth.SentCodeSuccess):
                    me = await client.get_me()
                    record(base, session, "authorized" if me else "login_not_confirmed",
                           premium=bool(me and me.premium))
                    return 0 if me else 2
                if not isinstance(sent, types.auth.SentCode):
                    record(base, session, "unsupported_auth_response", response=type(sent).__name__)
                    return 2
                record(base, session, "delivery_selected", delivery=type(sent.type).__name__,
                       next_delivery=type(sent.next_type).__name__ if sent.next_type else None,
                       timeout=sent.timeout)
                print("Це обраний спосіб доставки, а не підтвердження отримання.")
                if isinstance(sent.type, types.auth.SentCodeTypeApp):
                    print("Код очікується у перевіреному службовому чаті Telegram цього акаунта.")
                supported = ("SentCodeTypeApp", "SentCodeTypeSms", "SentCodeTypeCall",
                             "SentCodeTypeSmsWord", "SentCodeTypeSmsPhrase")
                if type(sent.type).__name__ not in supported:
                    print("Цей спосіб потребує окремого підтримуваного процесу входу; зупинено.")
                    return 2
                code = (await asyncio.to_thread(
                    getpass, "Код (прихований; Enter без коду — завершити): ")).strip()
                if not code:
                    record(base, session, "code_not_received", delivery=type(sent.type).__name__)
                    return 2
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except errors.SessionPasswordNeededError:
                await client.sign_in(password=await asyncio.to_thread(
                    getpass, "Пароль 2FA (прихований): "))
            me = await client.get_me()
            record(base, session, "authorized" if me else "login_not_confirmed",
                   premium=bool(me and me.premium))
            return 0 if me else 2
        except errors.FloodWaitError as exc:
            record(base, session, "rate_limited", error=type(exc).__name__,
                   retry_after_seconds=exc.seconds)
            return 2
        except errors.RPCError as exc:
            record(base, session, "telegram_error", error=type(exc).__name__)
            return 2
        except (OSError, asyncio.TimeoutError) as exc:
            record(base, session, "connection_error", error=type(exc).__name__)
            return 2
        finally:
            await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="explicitly request one login code")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(login=args.login)))
    except (KeyboardInterrupt, EOFError):
        print("Вхід зупинено.")
    except ValueError as exc:
        raise SystemExit(str(exc))
