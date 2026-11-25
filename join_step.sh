#!/usr/bin/env bash
# Одна партія вступу в групи (крон щогодини, ставить setup.sh). Коли список пройдено —
# прибирає себе з крону, вмикає розклад розсилки і пише власнику.
cd "$(dirname "$(readlink -f "$0")")"
exec 9>join.lock
flock -n 9 || exit 0
# Telegram зупинив вступ (FloodWait/PeerFlood) — добу не пробуємо, щоб не злити тривогами.
if [ -f join.stop ] && [ -n "$(find join.stop -mmin -1440)" ]; then exit 0; fi
out=$(venv/bin/python join_groups.py 20 2>&1); echo "$out" >> join.log
if grep -q "Вступ у групи зупинено" <<<"$out"; then touch join.stop; exit 0; fi
if grep -q "СПИСОК ПРОЙДЕНО ПОВНІСТЮ" <<<"$out"; then
  [ -f leads_seen.json ] || venv/bin/python lead_reply.py --seed >> join.log 2>&1
  # Спершу постійний розклад; тимчасовий рядок прибираємо і пишемо «увімкнено» лише після успіху.
  bash install.sh --cron >> join.log 2>&1 || { echo "install.sh --cron не вдався" >> join.log; exit 1; }
  { crontab -l 2>/dev/null | grep -v "# tg-join-temp" || true; } | crontab -
  venv/bin/python wizard_check.py done >> join.log 2>&1
fi
