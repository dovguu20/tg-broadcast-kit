#!/usr/bin/env python3
"""Вхід у Telegram. За замовчуванням лише перевірка.

--login       інтерактивно в терміналі (майстер setup.sh): номер, код і пароль 2FA
              читаються через getpass, символи не видно.
--send-code   без термінала: надіслати код. Номер — змінна TELEGRAM_PHONE
              або JSON зі stdin {"TELEGRAM_PHONE": "+..."}.
--sign-in     без термінала: завершити вхід. Код — TELEGRAM_CODE, хмарний пароль —
              TELEGRAM_2FA_PASSWORD (або ті самі ключі в JSON зі stdin).
--resend      разом із --send-code: надіслати новий код, навіть якщо попередній ще висить.

Секрети не приймаються аргументами командного рядка і ніколи не друкуються.
Між --send-code і --sign-in на диску лишаються файл сесії та phone_code_hash
(файл *.login-pending.json, права 600). Це має бути той самий каталог на VPS.
"""
import argparse
import asyncio
import fcntl
import json
import os
import platform
import re
import sys
import time
from getpass import getpass
from pathlib import Path

import telethon
from dotenv import dotenv_values
from telethon import TelegramClient, errors, functions, types
from telethon.tl.alltlobjects import LAYER

BASE = Path(__file__).resolve().parent
SECRET_KEYS = ("TELEGRAM_PHONE", "TELEGRAM_CODE", "TELEGRAM_2FA_PASSWORD")
PENDING_KEYS = ("stage", "phone", "phone_code_hash", "delivery", "timeout", "sent_at")
SUPPORTED_DELIVERY = (
    "SentCodeTypeApp",
    "SentCodeTypeSms",
    "SentCodeTypeCall",
    "SentCodeTypeSmsWord",
    "SentCodeTypeSmsPhrase",
)


class LoginInputError(Exception):
    """Немає термінала для getpass. Не плутати з помилкою мережі."""


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


def write_private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False)


def record(base, session, event, **details):
    # Only explicitly supplied diagnostics belong here; never dump RPC objects
    # and never include phone, code, password or phone_code_hash.
    value = {"time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "event": event, "telethon": telethon.__version__, "layer": LAYER,
             **details}
    path = base / (session + ".login-status.json")
    write_private_json(path, value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def pending_path(base, session):
    return base / (session + ".login-pending.json")


def save_pending(path, data):
    clean = {key: data[key] for key in PENDING_KEYS if key in data and data[key] is not None}
    write_private_json(path, clean)


def load_pending(path):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def delete_pending(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def normalize_phone(raw):
    phone = re.sub(r"[\s()-]", "", raw or "")
    if not phone:
        raise ValueError("phone_missing")
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
        raise ValueError("invalid_phone_format")
    return phone


def code_from_env():
    return os.environ.get("TELEGRAM_CODE", "").strip()


def password_from_env():
    # Не обрізати пробіли всередині пароля. Порожній рядок = пароль не передали.
    if "TELEGRAM_2FA_PASSWORD" not in os.environ:
        return ""
    return os.environ.get("TELEGRAM_2FA_PASSWORD") or ""


def absorb_stdin_secrets(stdin=None):
    """JSON зі stdin кладе секрети в оточення процесу. У чат і логи нічого не пише."""
    stream = sys.stdin if stdin is None else stdin
    try:
        tty = stream.isatty()
    except Exception:
        tty = False
    if tty:
        return
    raw = stream.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("invalid json")
        raise SystemExit(2)
    if not isinstance(data, dict):
        print("invalid json")
        raise SystemExit(2)
    for key in SECRET_KEYS:
        if key not in data or data[key] is None:
            continue
        os.environ[key] = str(data[key])


def send_code_decision(authorized, session_exists, pending, resend):
    """Чи слати новий код. Чиста функція, без мережі."""
    if authorized:
        return "already_authorized"
    if not session_exists:
        # Без файла сесії збережений phone_code_hash непридатний.
        return "send"
    if pending and pending.get("stage") == "password" and not resend:
        return "password_needed"
    if pending and not resend:
        return "code_already_pending"
    return "send"


def sign_in_precheck(session_exists, pending, code, password):
    """Подія, з якою можна зупинитись ДО мережі, або None — треба підключатись."""
    if not session_exists:
        return "session_missing"
    if pending and pending.get("stage") == "password" and not password:
        return "password_needed"
    return None


async def hidden(prompt):
    try:
        return await asyncio.to_thread(getpass, prompt)
    except (EOFError, OSError):
        print("Немає термінала для прихованого вводу. "
              "Без термінала: --send-code, потім --sign-in; секрети лише зі змінних оточення.")
        raise LoginInputError()


async def finish_sign_in(client, pending, code, password, save_pending_fn):
    """Повернути (event, pending_or_None, extra). pending_or_None=None — файл більше не потрібен.

    SessionPasswordNeededError зберігає стан «чекаємо пароль» ДО спроби пароля,
    щоб обірваний крок не вимагав нового коду.
    """
    if await client.is_user_authorized():
        me = await client.get_me()
        return "already_authorized", None, {"authorized": True, "premium": bool(me and me.premium)}
    stage = (pending or {}).get("stage")
    if stage == "password":
        if not password:
            return "password_needed", pending, {}
        await client.sign_in(password=password)
    else:
        if not pending or not pending.get("phone") or not pending.get("phone_code_hash"):
            return "sign_in_not_ready", pending, {}
        if not code:
            return "code_missing", pending, {}
        try:
            await client.sign_in(
                phone=pending["phone"], code=code, phone_code_hash=pending["phone_code_hash"])
        except errors.SessionPasswordNeededError:
            updated = {key: pending[key] for key in PENDING_KEYS if key in pending}
            updated["stage"] = "password"
            save_pending_fn(updated)
            if not password:
                return "password_needed", updated, {}
            await client.sign_in(password=password)
    me = await client.get_me()
    event = "authorized" if me else "login_not_confirmed"
    return event, None, {"authorized": bool(me), "premium": bool(me and me.premium)}


def _exit_for(event):
    if event == "password_needed":
        return 3
    if event in ("already_authorized", "authorized", "code_sent", "code_already_pending"):
        return 0
    return 2


async def _send_login_code(client, api_id, api_hash, phone):
    sent = await asyncio.wait_for(client(functions.auth.SendCodeRequest(
        phone_number=phone, api_id=api_id, api_hash=api_hash,
        settings=types.CodeSettings(),
    )), timeout=30)
    if isinstance(sent, types.auth.SentCodeSuccess):
        me = await client.get_me()
        return ("authorized" if me else "login_not_confirmed"), None, {
            "authorized": bool(me), "premium": bool(me and me.premium)}
    if not isinstance(sent, types.auth.SentCode):
        return "unsupported_auth_response", None, {"response": type(sent).__name__}
    delivery = type(sent.type).__name__
    pending = {
        "stage": "code",
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
        "delivery": delivery,
        "timeout": sent.timeout,
        "sent_at": int(time.time()),
        "next_delivery": type(sent.next_type).__name__ if sent.next_type else None,
    }
    if delivery not in SUPPORTED_DELIVERY:
        return "unsupported_delivery", pending, {"delivery": delivery}
    return "code_sent", pending, {
        "delivery": delivery,
        "next_delivery": pending["next_delivery"],
        "timeout": sent.timeout,
    }


async def run(mode="check", resend=False, base=BASE):
    os.umask(0o077)
    api_id, api_hash, session = configuration(base)
    session_file = base / (session + ".session")
    pend = pending_path(base, session)
    phone = ""

    if mode == "send-code":
        try:
            phone = normalize_phone(os.environ.get("TELEGRAM_PHONE", ""))
        except ValueError as exc:
            record(base, session, str(exc))
            return 2
        # Поки файла сесії нема, мережа не потрібна лише якщо номер уже відхилено вище.
    elif mode == "sign-in":
        pre = sign_in_precheck(
            session_file.exists(), load_pending(pend), code_from_env(), password_from_env())
        if pre:
            record(base, session, pre)
            return _exit_for(pre)

    had_session = session_file.exists()
    if had_session:
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
            if mode == "check":
                record(base, session, "check", connected=True, authorized=authorized)
                if not authorized:
                    print("Код не запитувався. Інтерактивно: --login. "
                          "Без термінала: --send-code, потім --sign-in.")
                return 0
            if mode == "send-code":
                # had_session — до connect(). Новий файл, який Telethon створив щойно,
                # ще не робить старий phone_code_hash придатним.
                decision = send_code_decision(authorized, had_session, load_pending(pend), resend)
                if decision == "already_authorized":
                    delete_pending(pend)
                    me = await client.get_me()
                    record(base, session, "already_authorized", authorized=True,
                           premium=bool(me and me.premium))
                    return 0
                if decision != "send":
                    record(base, session, decision)
                    return _exit_for(decision)
                event, new_pending, extra = await _send_login_code(client, api_id, api_hash, phone)
                # У файл стану не кладемо next_delivery — його вже є в extra для друку.
                if new_pending is not None:
                    save_pending(pend, new_pending)
                elif event in ("authorized", "already_authorized"):
                    delete_pending(pend)
                record(base, session, event, **{k: v for k, v in extra.items() if v is not None})
                return _exit_for(event)
            if mode == "sign-in":
                def _save(data, path=pend):
                    save_pending(path, data)

                event, new_pending, extra = await finish_sign_in(
                    client, load_pending(pend), code_from_env(), password_from_env(), _save)
                if new_pending is None:
                    delete_pending(pend)
                record(base, session, event, **extra)
                return _exit_for(event)

            # --login: інтерактивний шлях майстра. Секрети тільки через getpass.
            if not authorized:
                phone = re.sub(r"[\s()-]", "", await hidden(
                    "Номер у форматі +кодкраїни… (прихований): "))
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
                        record(base, session, "unsupported_auth_response",
                               response=type(sent).__name__)
                        return 2
                    record(base, session, "delivery_selected", delivery=type(sent.type).__name__,
                           next_delivery=type(sent.next_type).__name__ if sent.next_type else None,
                           timeout=sent.timeout)
                    print("Це обраний спосіб доставки, а не підтвердження отримання.")
                    if isinstance(sent.type, types.auth.SentCodeTypeApp):
                        print("Код очікується у перевіреному службовому чаті Telegram цього акаунта.")
                    if type(sent.type).__name__ not in SUPPORTED_DELIVERY:
                        print("Цей спосіб потребує окремого підтримуваного процесу входу; зупинено.")
                        return 2
                    code = (await hidden(
                        "Код (прихований; Enter без коду — завершити): ")).strip()
                    if not code:
                        record(base, session, "code_not_received",
                               delivery=type(sent.type).__name__)
                        return 2
                    await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
                except errors.SessionPasswordNeededError:
                    await client.sign_in(password=await hidden("Пароль 2FA (прихований): "))
                me = await client.get_me()
                record(base, session, "authorized" if me else "login_not_confirmed",
                       premium=bool(me and me.premium))
                return 0 if me else 2
            record(base, session, "check", connected=True, authorized=True)
            return 0
        except LoginInputError:
            return 2
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true",
                        help="інтерактивний вхід у терміналі (getpass)")
    parser.add_argument("--send-code", action="store_true",
                        help="надіслати код; номер у TELEGRAM_PHONE або JSON зі stdin")
    parser.add_argument("--sign-in", action="store_true",
                        help="увійти кодом TELEGRAM_CODE і, якщо треба, TELEGRAM_2FA_PASSWORD")
    parser.add_argument("--resend", action="store_true",
                        help="разом із --send-code: надіслати новий код, навіть якщо попередній ще висить")
    args = parser.parse_args(argv)
    chosen = sum(1 for flag in (args.login, args.send_code, args.sign_in) if flag)
    if chosen > 1:
        print("Обери один режим: --login або --send-code або --sign-in.")
        return 2
    if args.resend and not args.send_code:
        print("--resend лише разом із --send-code.")
        return 2
    if args.login:
        mode = "login"
    elif args.send_code:
        mode = "send-code"
    elif args.sign_in:
        mode = "sign-in"
    else:
        mode = "check"
    if mode in ("send-code", "sign-in"):
        absorb_stdin_secrets()
    return asyncio.run(run(mode=mode, resend=args.resend))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("Вхід зупинено.")
    except ValueError as exc:
        raise SystemExit(str(exc))
