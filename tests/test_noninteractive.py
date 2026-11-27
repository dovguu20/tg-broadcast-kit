#!/usr/bin/env python3
"""Офлайн-перевірки неінтерактивного встановлення. До Telegram не підключається.

Запуск (з бібліотеками комплекту): venv/bin/python tests/test_noninteractive.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import login_phone  # noqa: E402
from telethon.errors import SessionPasswordNeededError  # noqa: E402

HASH = "a" * 32
API_ID = "424242"
OWNER = "123456789"
PHONE_BAD = "NOTAPHONE-ZXQ-SECRET"
CODE_SECRET = "CODE-ZXQ-918273"
PASSWORD_SECRET = "CLOUD-ZXQ-secret"


def check(name: str, cond: bool) -> None:
    if not cond:
        raise SystemExit("FAIL " + name)
    print("ok  ", name)


def write_env(directory: Path, **overrides: str) -> None:
    values = {
        "TELEGRAM_API_ID": API_ID,
        "TELEGRAM_API_HASH": HASH,
        "OWNER_ID": OWNER,
        "TELEGRAM_SESSION_NAME": "sender",
        "BRAND": "",
    }
    values.update(overrides)
    lines = [f"{key}={value}" for key, value in values.items()]
    (directory / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_env(**extra: str) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "LANG": "C.UTF-8"}
    env.update(extra)
    return env


class BoomClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("TelegramClient не можна створювати на цьому шляху")


def without_client(fn):
    real = login_phone.TelegramClient
    login_phone.TelegramClient = BoomClient
    try:
        return fn()
    finally:
        login_phone.TelegramClient = real


def capture_run(mode: str, base: Path, env_updates: dict, resend: bool = False):
    saved = {key: os.environ.get(key) for key in login_phone.SECRET_KEYS}
    for key in login_phone.SECRET_KEYS:
        os.environ.pop(key, None)
    os.environ.update(env_updates)
    buf_out = []

    class Stream:
        def write(self, text):
            buf_out.append(text)
            return len(text)

        def flush(self):
            pass

    real_stdout = sys.stdout
    sys.stdout = Stream()
    try:
        code = without_client(lambda: asyncio.run(login_phone.run(mode=mode, resend=resend, base=base)))
    finally:
        sys.stdout = real_stdout
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, "".join(buf_out)


def test_decisions() -> None:
    pending_code = {"stage": "code", "phone": "+100000000", "phone_code_hash": "h"}
    pending_pw = {"stage": "password", "phone": "+100000000", "phone_code_hash": "h"}
    check("send: вже авторизований", login_phone.send_code_decision(True, True, pending_code, False) == "already_authorized")
    check("send: код уже висить", login_phone.send_code_decision(False, True, pending_code, False) == "code_already_pending")
    check("send: чекаємо пароль", login_phone.send_code_decision(False, True, pending_pw, False) == "password_needed")
    check("send: --resend все одно шле", login_phone.send_code_decision(False, True, pending_code, True) == "send")
    check("send: без файла сесії старий хеш непридатний", login_phone.send_code_decision(False, False, pending_code, False) == "send")
    check("send: нема очікування", login_phone.send_code_decision(False, True, None, False) == "send")
    check("send: номер виправили — шлемо новий код", login_phone.send_code_decision(False, True, pending_code, False, "+380999999999") == "send")
    check("send: той самий номер — код уже висить", login_phone.send_code_decision(False, True, pending_code, False, pending_code["phone"]) == "code_already_pending")
    check("precheck: нема сесії", login_phone.sign_in_precheck(False, pending_code, "12345", "") == "session_missing")
    check("precheck: пароль ще не дали", login_phone.sign_in_precheck(True, pending_pw, "", "") == "password_needed")
    check("precheck: пароль є — треба підключатись", login_phone.sign_in_precheck(True, pending_pw, "", "secret") is None)
    check("precheck: код є — треба підключатись", login_phone.sign_in_precheck(True, pending_code, "12345", "") is None)
    check("precheck: сесія є, коду нема — перевірити, чи вже увійшли", login_phone.sign_in_precheck(True, None, "", "") is None)


def test_phone_and_pending(tmp: Path) -> None:
    try:
        login_phone.normalize_phone("  ")
        check("порожній номер", False)
    except ValueError as exc:
        check("порожній номер", str(exc) == "phone_missing")
    try:
        login_phone.normalize_phone(PHONE_BAD)
        check("битий номер", False)
    except ValueError as exc:
        check("битий номер", str(exc) == "invalid_phone_format" and PHONE_BAD not in str(exc))
    check("номер з пробілами і дужками", login_phone.normalize_phone("+380 (67) 000-00-00") == "+380670000000")

    path = tmp / "sender.login-pending.json"
    login_phone.save_pending(path, {
        "stage": "code", "phone": "+10000000000", "phone_code_hash": "hash",
        "code": CODE_SECRET, "password": PASSWORD_SECRET, "delivery": "SentCodeTypeApp", "timeout": 60,
    })
    text = path.read_text(encoding="utf-8")
    check("pending без коду і пароля", CODE_SECRET not in text and PASSWORD_SECRET not in text and "hash" in text)
    check("pending права 600", stat.S_IMODE(path.stat().st_mode) == 0o600)


class FakeClient:
    def __init__(self, need_password=False, authorized=False, bad_password=False):
        self.need_password = need_password
        self.authorized = authorized
        self.bad_password = bad_password
        self.calls = []

    async def is_user_authorized(self):
        return self.authorized

    async def sign_in(self, **kwargs):
        self.calls.append(kwargs)
        if "code" in kwargs and self.need_password:
            raise SessionPasswordNeededError(request=None)
        if "password" in kwargs and "code" not in kwargs:
            if self.bad_password:
                raise RuntimeError("bad password")
            self.authorized = True
            return None
        self.authorized = True
        return None

    async def get_me(self):
        class Me:
            premium = True
            id = 5
        return Me()


def test_finish_sign_in(tmp: Path) -> None:
    pending = {"stage": "code", "phone": "+10000000000", "phone_code_hash": "hash", "delivery": "SentCodeTypeApp"}
    saved = []

    def save(data, bucket=saved):
        bucket.append(dict(data))
        login_phone.save_pending(tmp / "pending.json", data)

    event, new_pending, extra = asyncio.run(login_phone.finish_sign_in(
        FakeClient(authorized=True), pending, CODE_SECRET, "", save))
    check("вже увійшли: код не чіпаємо", event == "already_authorized" and new_pending is None and extra.get("premium") is True and not saved)

    saved.clear()
    client = FakeClient(need_password=True)
    event, new_pending, _extra = asyncio.run(login_phone.finish_sign_in(client, dict(pending), CODE_SECRET, "", save))
    blob = json.dumps(saved, ensure_ascii=False)
    check("код прийнято, пароль ще потрібен", event == "password_needed" and new_pending["stage"] == "password")
    check("код не записано у стан", CODE_SECRET not in blob and (tmp / "pending.json").read_text(encoding="utf-8").find(CODE_SECRET) < 0)

    saved.clear()
    client = FakeClient(need_password=True)
    event, new_pending, extra = asyncio.run(login_phone.finish_sign_in(
        client, dict(pending), CODE_SECRET, "right", save))
    check("код і пароль за один крок", event == "authorized" and new_pending is None and extra.get("premium") is True)
    check("було два sign_in", len(client.calls) == 2 and "password" in client.calls[1])

    client = FakeClient(need_password=False)
    event, new_pending, _extra = asyncio.run(login_phone.finish_sign_in(client, dict(pending), CODE_SECRET, "", save))
    check("без 2FA достатньо коду", event == "authorized" and new_pending is None and len(client.calls) == 1)

    client = FakeClient()
    event, _pending, _extra = asyncio.run(login_phone.finish_sign_in(
        client, {"stage": "password", "phone": "+10000000000", "phone_code_hash": "hash"}, "", "right", save))
    check("другий крок лише паролем", event == "authorized" and client.calls == [{"password": "right"}])

    client = FakeClient()
    event, _pending, _extra = asyncio.run(login_phone.finish_sign_in(
        client, {"stage": "password"}, "", "", save))
    check("пароль не передали — до мережі sign_in не дійшло", event == "password_needed" and client.calls == [])

    event, _pending, _extra = asyncio.run(login_phone.finish_sign_in(FakeClient(), {"stage": "code"}, "", "", save))
    check("немає хеша — вхід не готовий", event == "sign_in_not_ready")

    event, _pending, _extra = asyncio.run(login_phone.finish_sign_in(FakeClient(), dict(pending), "", "", save))
    check("немає коду", event == "code_missing")


def test_run_does_not_connect(tmp: Path) -> None:
    write_env(tmp)
    code, out = capture_run("send-code", tmp, {"TELEGRAM_PHONE": PHONE_BAD})
    status = (tmp / "sender.login-status.json").read_text(encoding="utf-8")
    check("битий номер: код 2 і без секрету", code == 2 and "invalid_phone_format" in out and PHONE_BAD not in out and PHONE_BAD not in status)
    check("битий номер не створив сесію", not (tmp / "sender.session").exists())

    code, out = capture_run("send-code", tmp, {})
    check("немає номера", code == 2 and "phone_missing" in out)

    code, out = capture_run("sign-in", tmp, {"TELEGRAM_CODE": CODE_SECRET})
    status = (tmp / "sender.login-status.json").read_text(encoding="utf-8")
    check("вхід без сесії", code == 2 and "session_missing" in out and CODE_SECRET not in out and CODE_SECRET not in status)

    (tmp / "sender.session").write_text("not-a-real-session", encoding="utf-8")
    login_phone.save_pending(tmp / "sender.login-pending.json", {"stage": "password", "phone": "+10000000000", "phone_code_hash": "hash"})
    code, out = capture_run("sign-in", tmp, {})
    check("чекаємо пароль ще до мережі", code == 3 and "password_needed" in out)
    check("файл сесії не підмінили", (tmp / "sender.session").read_text(encoding="utf-8") == "not-a-real-session")


def run_py(directory: Path, args: list, stdin: str = "", env: dict | None = None, timeout: int = 8):
    return subprocess.run(
        [sys.executable, *args],
        cwd=directory,
        input=stdin,
        text=True,
        capture_output=True,
        env=env or clean_env(),
        timeout=timeout,
        check=False,
    )


def test_cli(tmp: Path) -> None:
    for stale in tmp.glob("sender.session*"):
        stale.unlink()
    for stale in tmp.glob("*.login-pending.json"):
        stale.unlink()
    for stale in tmp.glob("*.login-status.json"):
        stale.unlink()
    shutil.copy(ROOT / "login_phone.py", tmp / "login_phone.py")
    shutil.copy(ROOT / "check_account.py", tmp / "check_account.py")
    write_env(tmp, TELEGRAM_API_ID="", TELEGRAM_API_HASH="")
    proc = run_py(tmp, ["check_account.py"])
    combined = proc.stdout + proc.stderr
    check("порожній api_id без трейсбека", proc.returncode == 2 and "Invalid TELEGRAM_API_ID" in combined and "Traceback" not in combined)
    check("порожній api_id не створює сесію", not (tmp / "sender.session").exists())

    write_env(tmp, TELEGRAM_API_HASH="zz")
    proc = run_py(tmp, ["check_account.py"])
    check("битий api_hash", proc.returncode == 2 and "Invalid TELEGRAM_API_HASH" in proc.stdout and "Traceback" not in proc.stderr)

    write_env(tmp, TELEGRAM_SESSION_NAME="customname")
    proc = run_py(tmp, ["check_account.py"], env=clean_env(TELEGRAM_API_ID="", TELEGRAM_SESSION_NAME="sender"))
    check("немає файла сесії", proc.returncode == 0 and "authorized=false" in proc.stdout and "session=missing" in proc.stdout)
    check("перевірка не створює сесію і не бере ім'я з оточення", not (tmp / "customname.session").exists() and not (tmp / "sender.session").exists())

    write_env(tmp)
    proc = run_py(tmp, ["login_phone.py", "--send-code"], stdin=json.dumps({"TELEGRAM_PHONE": PHONE_BAD}))
    combined = proc.stdout + proc.stderr
    check("stdin JSON: битий номер не світиться", proc.returncode == 2 and "invalid_phone_format" in combined and PHONE_BAD not in combined and "Traceback" not in combined)

    proc = run_py(tmp, ["login_phone.py", "--sign-in"], stdin=json.dumps({"TELEGRAM_CODE": CODE_SECRET, "TELEGRAM_2FA_PASSWORD": PASSWORD_SECRET}))
    combined = proc.stdout + proc.stderr
    check("stdin JSON: код і пароль не світяться", proc.returncode == 2 and "session_missing" in combined and CODE_SECRET not in combined and PASSWORD_SECRET not in combined)

    write_env(tmp, TELEGRAM_API_ID="")
    proc = run_py(tmp, ["login_phone.py", "--send-code"], env=clean_env(TELEGRAM_PHONE=PHONE_BAD))
    combined = proc.stdout + proc.stderr
    check("битий api_id раніше за номер", proc.returncode != 0 and "Invalid TELEGRAM_API_ID" in combined and PHONE_BAD not in combined and "Traceback" not in combined)

    proc = run_py(tmp, ["login_phone.py", "--login", "--send-code"])
    check("два режими одразу", proc.returncode == 2)
    proc = run_py(tmp, ["login_phone.py", "--resend"])
    check("--resend сам по собі", proc.returncode == 2)


def test_apply_env_and_install(tmp: Path) -> None:
    for name in ("apply_env.py", "install.sh", ".env.example", "crontab.example"):
        shutil.copy(ROOT / name, tmp / name)
    os.chmod(tmp / "install.sh", 0o755)

    proc = run_py(tmp, ["apply_env.py"], stdin="{")
    check("битий json", proc.returncode == 2 and "invalid json" in proc.stdout and "Traceback" not in proc.stderr)

    secret_id = "424242"
    proc = run_py(
        tmp,
        ["apply_env.py"],
        stdin=json.dumps({
            "TELEGRAM_API_ID": "12",
            "TELEGRAM_API_HASH": "zz",
            "OWNER_ID": OWNER,
            "BRAND": "Агенція",
        }),
    )
    text = (tmp / ".env").read_text(encoding="utf-8")
    combined = proc.stdout + proc.stderr
    api_line_empty = text.startswith("TELEGRAM_API_ID=\n") or "\nTELEGRAM_API_ID=\n" in text
    check("невалідне не записується і не друкується",
          proc.returncode == 2 and "invalid TELEGRAM_API_ID" in proc.stdout
          and "invalid TELEGRAM_API_HASH" in proc.stdout and "ok OWNER_ID" in proc.stdout
          and "12" not in combined and "zz" not in combined and api_line_empty)
    check("валідний owner і назва записані", f"OWNER_ID={OWNER}" in text and "BRAND=Агенція" in text)
    check(".env права 600", stat.S_IMODE((tmp / ".env").stat().st_mode) == 0o600)

    proc = run_py(tmp, ["apply_env.py"], stdin=json.dumps({
        "TELEGRAM_API_ID": secret_id,
        "TELEGRAM_API_HASH": HASH,
    }))
    text = (tmp / ".env").read_text(encoding="utf-8")
    check("повторний запис дописує ключі", proc.returncode == 0 and f"TELEGRAM_API_ID={secret_id}" in text and f"TELEGRAM_API_HASH={HASH}" in text and f"OWNER_ID={OWNER}" in text)
    check("stdout без значень", secret_id not in proc.stdout and HASH not in proc.stdout and "ok TELEGRAM_API_ID" in proc.stdout)

    proc = run_py(tmp, ["apply_env.py"], stdin="{}", env=clean_env())
    text_after = (tmp / ".env").read_text(encoding="utf-8")
    check("порожній повтор нічого не стирає", proc.returncode == 0 and text_after == text and "skip TELEGRAM_API_ID" in proc.stdout)

    env = clean_env(TELEGRAM_SESSION_NAME="sender")
    (tmp / ".env").write_text(
        f"TELEGRAM_API_ID={secret_id}\nTELEGRAM_API_HASH={HASH}\nOWNER_ID={OWNER}\nTELEGRAM_SESSION_NAME=customname\nBRAND=\n",
        encoding="utf-8",
    )
    proc = subprocess.run(["bash", "install.sh", "--status"], cwd=tmp, text=True, capture_output=True, env=env, check=False)
    out = proc.stdout
    check("status код 0", proc.returncode == 0)
    check("status бачить сесію з .env, не з оточення", "session_name=customname" in out and "session=missing" in out)
    check("status не друкує секрети", secret_id not in out and HASH not in out and "api_id=set" in out and "api_hash=set" in out and "owner_id=set" in out)

    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    store = tmp / "cron.capture"
    (bin_dir / "crontab").write_text(
        "#!/bin/bash\n"
        f'store="{store}"\n'
        'if [ "${1:-}" = "-l" ]; then\n'
        '  if [ -s "$store" ]; then cat "$store"; exit 0; fi\n'
        "  exit 1\n"
        "fi\n"
        'cat "$1" > "$store.tmp" && mv "$store.tmp" "$store"\n'  # як справжній crontab: без гонки з «crontab -l |»
        "exit 0\n",
        encoding="utf-8",
    )
    os.chmod(bin_dir / "crontab", 0o755)
    store.write_text("7 * * * * bash /root/broadcast/join_step.sh # tg-join-temp\n", encoding="utf-8")
    cron_env = dict(env)
    cron_env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    proc = subprocess.run(["bash", "install.sh", "--cron"], cwd=tmp, text=True, capture_output=True, env=cron_env, check=False)
    captured = store.read_text(encoding="utf-8")
    check("cron попереджає про ім'я сесії з .env", proc.returncode == 0 and "customname.session" in proc.stdout)
    check("cron лишає join_step і ставить 5 рядків розкладу",
          captured.count("# tg-broadcast-kit") == 5 and "# tg-join-temp" in captured and "DIR" not in captured)
    for schedule in ("0 */2 * * *", "3-58/5 * * * *", "15 * * * *", "45 1 * * *", "5 11 * * 1"):
        check(f"розклад {schedule}", schedule in captured)
    proc = subprocess.run(["bash", "install.sh", "--cron"], cwd=tmp, text=True, capture_output=True, env=cron_env, check=False)
    captured = store.read_text(encoding="utf-8")
    check("повторний --cron не дублює рядки", proc.returncode == 0 and captured.count("# tg-broadcast-kit") == 5 and captured.count("# tg-join-temp") == 1)
    test_several_accounts(tmp, store, cron_env)


def test_several_accounts(tmp: Path, store: Path, cron_env: dict) -> None:
    """Кілька акаунтів на одному сервері (/root/broadcast, /root/broadcast-2): кожна папка
    чіпає лише свої рядки розкладу, свій рядок вступу в групи і бачить лише свій прогін."""
    first, second = tmp / "srv" / "broadcast", tmp / "srv" / "broadcast-2"
    for d in (first, second):
        d.mkdir(parents=True)
        for name in ("install.sh", "crontab.example", "join_step.sh", "common.py"):
            shutil.copy(ROOT / name, d / name)
        (d / ".env").write_text((tmp / ".env").read_text(encoding="utf-8"), encoding="utf-8")
    run = lambda d, *a: subprocess.run(["bash", *a], cwd=d, text=True, capture_output=True, env=cron_env, check=False)
    mine = lambda d: [l for l in store.read_text(encoding="utf-8").splitlines() if f"{d}/" in l]

    run(first, "install.sh", "--cron")
    store.write_text(store.read_text(encoding="utf-8")
                     + f"7 * * * * bash {first}/join_step.sh # tg-join-temp\n"
                     + f"7 * * * * bash {second}/join_step.sh # tg-join-temp\n", encoding="utf-8")
    proc = run(second, "install.sh", "--cron")
    a, b = mine(first), mine(second)
    check("другий акаунт: свої 5 рядків, перший цілий",
          proc.returncode == 0 and sum("# tg-broadcast-kit" in l for l in a) == 5
          and sum("# tg-broadcast-kit" in l for l in b) == 5)
    check("другий акаунт: прогін зсунутий на :40", any(l.startswith("40 0-23/2 * * * ") and "broadcast.py" in l for l in b))
    run(first, "install.sh", "--cron")
    check("повторний --cron першого не чіпає другий", mine(second) == b and len(mine(first)) == len(a))

    # Четвертий акаунт не збігається з першим (:00), номер з нулем не ламає арифметику.
    fourth = tmp / "srv" / "broadcast-04"
    fourth.mkdir()
    for name in ("install.sh", "crontab.example"):
        shutil.copy(ROOT / name, fourth / name)
    (fourth / ".env").write_text((tmp / ".env").read_text(encoding="utf-8"), encoding="utf-8")
    proc = run(fourth, "install.sh", "--cron")
    check("четвертий акаунт: прогін о :20 парних", proc.returncode == 0
          and any(l.startswith("20 0-23/2 * * * ") and "broadcast.py" in l for l in mine(fourth))
          and mine(second) == b)

    # join_step другого акаунта, коли список пройдено, прибирає лише СВІЙ рядок вступу.
    fake = second / "venv" / "bin"
    fake.mkdir(parents=True)
    (fake / "python").write_text("#!/bin/bash\necho 'СПИСОК ПРОЙДЕНО ПОВНІСТЮ'\n", encoding="utf-8")
    os.chmod(fake / "python", 0o755)
    (second / "leads_seen.json").write_text("{}", encoding="utf-8")
    proc = run(second, "join_step.sh")
    cron = store.read_text(encoding="utf-8")
    check("join_step прибирає лише свій рядок вступу",
          proc.returncode == 0 and f"{second}/join_step.sh" not in cron and f"{first}/join_step.sh # tg-join-temp" in cron)

    # Прогін розсилки другого акаунта не «займає» перший.
    if not Path("/proc/self/cwd").exists():
        print("skip  прогін по папках (нема /proc)")
        return
    (fake / "python").write_text("#!/bin/bash\nsleep 30\n", encoding="utf-8")
    proc = subprocess.Popen(["venv/bin/python", "broadcast.py"], cwd=second)
    try:
        import time
        time.sleep(0.5)
        seen = lambda d: subprocess.run([sys.executable, "-c", "import common; print(common.broadcast_running())"],
                                        cwd=d, text=True, capture_output=True).stdout.strip()
        check("прогін бачить лише своя папка", seen(second) == "True" and seen(first) == "False")
        st = lambda d: run(d, "install.sh", "--status").stdout
        check("status: прогін лише у своїй папці",
              "broadcast_running=yes" in st(second) and "broadcast_running=no" in st(first))
    finally:
        proc.kill()
        proc.wait()


def test_bash_syntax() -> None:
    for name in ("install.sh", "setup.sh", "join_step.sh"):
        proc = subprocess.run(["bash", "-n", str(ROOT / name)], check=False)
        check(f"bash -n {name}", proc.returncode == 0)


def test_unsupported_delivery() -> None:
    from telethon.tl import types as tl
    sent = tl.auth.SentCode(type=tl.auth.SentCodeTypeSetUpEmailRequired(), phone_code_hash="h")

    async def fake(_req):
        return sent
    event, pending, _ = asyncio.run(login_phone._send_login_code(fake, 1, HASH, "+380670000000"))
    check("непідтримана доставка: стан коду не пишемо", event == "unsupported_delivery" and pending is None)


def test_owner_messages() -> None:
    """Повідомлення учню в особисті називають номер акаунта (папка) і його ім'я (сирий/139 курсу)."""
    import common
    import wizard_check
    saved = common.BASE_DIR, common.BRAND
    try:
        common.BRAND = "@acc"
        for d, n in (("/root/broadcast", 1), ("/root/broadcast-2", 2), ("/root/broadcast-03", 3), ("/x/broadcast-old", 1)):
            common.BASE_DIR = d
            assert common.label() == f"Акаунт {n} (@acc)", (d, common.label())
        common.BASE_DIR = "/root/broadcast-2"
        for mode in ("joining", "running", "done"):
            assert "Акаунт 2 (@acc)" in wizard_check.MESSAGES[mode](), mode
        with tempfile.TemporaryDirectory() as d:
            common.BASE_DIR = os.path.join(d, "broadcast-2")
            os.mkdir(common.BASE_DIR)
            assert "4 годин" in wizard_check.MESSAGES["joining"]()
            Path(common.BASE_DIR, "join.log").write_text("СПИСОК ПРОЙДЕНО ПОВНІСТЮ\n", encoding="utf-8")
            assert "Вступ у групи пройдено" in wizard_check.MESSAGES["joining"](), "список пройдено — не обіцяти 4 години"
            stop = Path(common.BASE_DIR, "join.stop"); stop.touch()
            soon = time.time() + 1800   # join_step.sh: час зміни join.stop = коли пробувати знову
            os.utime(stop, (soon, soon))
            text = wizard_check.MESSAGES["joining"]()
            assert "паузу" in text and "через 1 год" in text, text
            os.utime(stop, (soon + 86400, soon + 86400))
            assert "через добу" in wizard_check.MESSAGES["joining"](), "PeerFlood — доба"
            os.utime(stop, (soon - 7200, soon - 7200))
            assert "паузу" not in wizard_check.MESSAGES["joining"](), "пауза минула (і старий join.stop від старого коду) — вже не пауза"
        common.BASE_DIR = "/root/broadcast-2"
        from datetime import datetime
        tpl = Path(ROOT, "crontab.example").read_text(encoding="utf-8").splitlines()[0]   # справжній рядок розсилки
        cron = "\n".join(m + tpl.replace("DIR", d)[len("0 */2"):] for m, d in (
            ("0 */2", "/root/broadcast"), ("40 0-23/2", "/root/broadcast-2"), ("20 1-23/2", "/root/broadcast-3")))
        at = datetime(2026, 11, 27, 13, 50)   # непарна година
        for d, want in (("/root/broadcast", "приблизно через 10 хв"), ("/root/broadcast-2", "приблизно через 50 хв"),
                        ("/root/broadcast-3", "приблизно через 1 год 30 хв")):
            common.BASE_DIR = d
            assert wizard_check.next_post(cron, at) == want, (d, wizard_check.next_post(cron, at))
        common.BASE_DIR = "/root/broadcast-4"
        assert wizard_check.next_post(cron, at) == "протягом 2 годин", "нема рядка — загальна фраза"
        common.BASE_DIR = "/root/broadcast-2"
    finally:
        common.BASE_DIR, common.BRAND = saved
    print("ok owner messages")


def test_join_pause() -> None:
    """join_step.sh чекає стільки, скільки просить Telegram (FloodWait), а не завжди добу (кейс Руслана, сирий/143)."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(ROOT / "join_step.sh", d)
        os.makedirs(f"{d}/venv/bin")
        fake = Path(d, "venv/bin/python")
        fake.write_text('#!/bin/bash\necho call >> calls.txt\n'
                        'echo "Вступ у групи зупинено: FloodWait 600 с"; echo "ПАУЗА 600"\n', encoding="utf-8")
        fake.chmod(0o755)
        run = lambda: subprocess.run(["bash", f"{d}/join_step.sh"], cwd=d, capture_output=True, timeout=30)
        t0 = time.time(); run()
        wait = os.path.getmtime(f"{d}/join.stop") - t0
        assert 590 <= wait <= 610, wait
        run()
        assert Path(d, "calls.txt").read_text().count("call") == 1, "під час паузи вступ не запускається"
        os.utime(f"{d}/join.stop", (t0 - 1, t0 - 1))
        run()
        assert Path(d, "calls.txt").read_text().count("call") == 2, "пауза минула — пробує знову"
        fake.write_text('#!/bin/bash\necho "Вступ у групи зупинено: PeerFlood"\n', encoding="utf-8")
        os.utime(f"{d}/join.stop", (t0 - 1, t0 - 1))
        t0 = time.time(); run()
        assert 86390 <= os.path.getmtime(f"{d}/join.stop") - t0 <= 86410, "без ПАУЗА — доба"
    print("ok join pause")


def main() -> None:
    test_decisions()
    test_join_pause()
    test_owner_messages()
    test_unsupported_delivery()
    with tempfile.TemporaryDirectory(prefix="kit-noninteractive-") as raw:
        tmp = Path(raw)
        test_phone_and_pending(tmp)
        test_finish_sign_in(tmp)
        test_run_does_not_connect(tmp)
        test_cli(tmp)
        test_apply_env_and_install(tmp)
    test_bash_syntax()
    print("\nНЕІНТЕРАКТИВНІ ПЕРЕВІРКИ ПРОЙШЛИ")


if __name__ == "__main__":
    main()
