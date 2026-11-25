#!/usr/bin/env python3
"""Записати в .env лише передані поля. Значення ніколи не друкує.

Джерело: JSON-об'єкт зі stdin, якщо він непорожній; інакше змінні оточення.
Порожнє значення означає «це поле не чіпати» — повторний запуск нічого не затирає.
Секрети не приймаються аргументами командного рядка: їх видно в списку процесів.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
KEYS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "OWNER_ID", "BRAND")


def read_input(stdin=None) -> dict:
    """Повернути словник ключ → рядок. Не друкує вміст."""
    stream = sys.stdin if stdin is None else stdin
    raw = ""
    try:
        tty = stream.isatty()
    except Exception:
        tty = False
    if not tty:
        raw = stream.read()
    if raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise SystemExit("invalid json")
        if not isinstance(data, dict):
            raise SystemExit("invalid json")
        out = {}
        for key in KEYS:
            if key not in data or data[key] is None:
                out[key] = ""
            else:
                out[key] = str(data[key])
        return out
    return {key: os.environ.get(key, "") for key in KEYS}


def valid(key: str, value: str) -> bool:
    if key == "TELEGRAM_API_ID":
        return bool(re.fullmatch(r"[0-9]{4,12}", value)) and int(value) > 0
    if key == "TELEGRAM_API_HASH":
        return bool(re.fullmatch(r"[0-9a-fA-F]{32}", value))
    if key == "OWNER_ID":
        return bool(re.fullmatch(r"[0-9]{5,15}", value))
    if key == "BRAND":
        return 0 < len(value) <= 80 and "\n" not in value and "\r" not in value and "#" not in value and '"' not in value
    return False


def ensure_text(base: Path) -> str:
    env = base / ".env"
    if env.exists():
        return env.read_text(encoding="utf-8")
    example = base / ".env.example"
    if example.exists():
        return example.read_text(encoding="utf-8")
    return "".join(f"{key}=\n" for key in KEYS)


def apply_text(text: str, updates: dict) -> str:
    for key, value in updates.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if pattern.search(text):
            text = pattern.sub(lambda _m, line=line: line, text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    return text


def apply(base: Path, values: dict) -> int:
    """Записати валідні поля. Повернути 0, або 2 якщо якесь передане значення невалідне."""
    updates = {}
    invalid = []
    for key in KEYS:
        raw = values.get(key, "")
        value = raw.strip() if key != "BRAND" else raw.strip()
        if value == "":
            continue
        if not valid(key, value):
            invalid.append(key)
            continue
        updates[key] = value
    if updates:
        env = base / ".env"
        text = apply_text(ensure_text(base), updates)
        tmp = base / ".env.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
        os.replace(tmp, env)
        os.chmod(env, 0o600)
    for key in KEYS:
        if key in updates:
            print(f"ok {key}")
        elif key in invalid:
            print(f"invalid {key}")
        else:
            print(f"skip {key}")
    return 2 if invalid else 0


def main() -> int:
    try:
        values = read_input()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(exc.code if isinstance(exc.code, str) else "invalid json")
            return 2
        raise
    return apply(BASE, values)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
