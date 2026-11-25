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
apt-get -o DPkg::Lock::Timeout=600 update -qq
apt-get -o DPkg::Lock::Timeout=600 install -y -qq python3 python3-venv python3-pip git cron tzdata >/dev/null
# Час у логах — київський; крон читає системний час.
timedatectl set-timezone Europe/Kyiv 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Kyiv /etc/localtime
systemctl restart cron 2>/dev/null || true   # інакше крон живе зі старим часовим поясом до перезавантаження
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
chmod 600 .env
chmod 600 ./*.session 2>/dev/null || true
venv/bin/python -c "import telethon, dotenv; print('бібліотеки ok: Telethon', telethon.__version__)"
venv/bin/python -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" ./*.py
echo "встановлено в $DIR. Далі: заповнити .env (3 поля) і venv/bin/python login_phone.py --login"
