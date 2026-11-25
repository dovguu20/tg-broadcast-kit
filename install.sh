#!/usr/bin/env bash
# Встановлення комплекту розсилки на чистому Ubuntu 22.04 / 24.04. Запускати від root
# з будь-якого місця: bash /root/broadcast/install.sh
#   без параметрів  — ставить Python, бібліотеки, часовий пояс, створює .env з прикладу
#   --cron          — вмикає автоматику (5 завдань у crontab); запускати ПІСЛЯ успішного тесту
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
DIR=$PWD

if [ "${1:-}" = "--cron" ]; then
  [ -f .env ] || { echo "нема .env — спершу звичайний install.sh і заповнення .env"; exit 1; }
  [ -f "${TELEGRAM_SESSION_NAME:-sender}.session" ] || echo "УВАГА: файла сесії ще нема — спершу login_phone.py --login"
  tmp=$(mktemp)
  { crontab -l 2>/dev/null | grep -v "# tg-broadcast-kit" || true; } > "$tmp"
  sed "s#DIR#$DIR#g" crontab.example >> "$tmp"
  crontab "$tmp"; rm -f "$tmp"
  echo "автоматику ввімкнено:"; crontab -l | grep "# tg-broadcast-kit"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
umask 077
# Свіжий сервер перші хвилини сам оновлюється: update чекаємо циклом (Lock::Timeout діє лише на install).
for i in $(seq 1 40); do apt-get update -qq && break; sleep 15; done
apt-get -o DPkg::Lock::Timeout=600 install -y -qq python3 python3-venv python3-pip git cron tzdata >/dev/null
# Час у логах — київський; крон читає системний час.
timedatectl set-timezone Europe/Kyiv 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Kyiv /etc/localtime
if [ -d /run/systemd/system ]; then
  systemctl enable -q cron 2>/dev/null || true
  systemctl restart cron   # інакше крон живе зі старим часовим поясом до перезавантаження
  systemctl is-active -q cron || { echo "cron не запустився — розклад не працюватиме"; exit 1; }
fi
# Перерваний минулий запуск міг лишити напівстворений venv — тоді створюємо заново.
if ! venv/bin/python -m pip --version >/dev/null 2>&1; then
  [ -d venv ] && mv venv "venv.broken.$(date +%s)"
  python3 -m venv venv
fi
venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
# Стара .env після оновлення комплекту: дописуємо ключі, яких у ній ще нема (значення з прикладу).
venv/bin/python - <<'PY'
from dotenv import dotenv_values
have, example = dotenv_values(".env"), open(".env.example", encoding="utf-8").read().splitlines()
add = [l for l in example if "=" in l and not l.startswith("#") and l.split("=", 1)[0] not in have]
if add:
    s = open(".env", encoding="utf-8").read().rstrip("\n")
    open(".env", "w", encoding="utf-8").write(s + "\n" + "\n".join(add) + "\n")
    print("дописано в .env:", ", ".join(l.split("=", 1)[0] for l in add))
PY
chmod 600 .env
chmod 600 ./*.session 2>/dev/null || true
venv/bin/python -c "import telethon, dotenv; print('бібліотеки ok: Telethon', telethon.__version__)"
venv/bin/python -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" ./*.py
echo "встановлено в $DIR. Далі: заповнити .env (3 поля) і venv/bin/python login_phone.py --login"
