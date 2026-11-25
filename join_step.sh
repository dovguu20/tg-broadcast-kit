#!/usr/bin/env bash
# Одна партія вступу в групи (крон щогодини, ставить setup.sh). Коли список пройдено —
# вмикає розклад розсилки, пише власнику і лише тоді прибирає себе з крону
# (не вдалось повідомити — спробує ще раз наступної години).
cd "$(dirname "$(readlink -f "$0")")"
umask 077
exec 9>session.lock
flock -n 9 || exit 0   # з акаунтом зараз працює майстер чи інша задача — наступної години
# Telegram зупинив вступ (FloodWait/PeerFlood) — добу не пробуємо, щоб не злити тривогами.
if [ -f join.stop ] && [ -n "$(find join.stop -mmin -1440)" ]; then exit 0; fi
out=$(KIT_SESSION_LOCKED=1 venv/bin/python join_groups.py 20 2>&1); echo "$out" >> join.log
if grep -q "Вступ у групи зупинено" <<<"$out"; then touch join.stop; exit 0; fi
grep -q "СПИСОК ПРОЙДЕНО ПОВНІСТЮ" <<<"$out" || exit 0
[ -f leads_seen.json ] || venv/bin/python lead_reply.py --seed >> join.log 2>&1
[ -f leads_seen.json ] || { echo "запам'ятати старі чати не вдалось — повторю наступної години" >> join.log; exit 1; }
bash install.sh --cron >> join.log 2>&1 || { echo "install.sh --cron не вдався" >> join.log; exit 1; }
venv/bin/python wizard_check.py done >> join.log 2>&1 || exit 1
{ crontab -l 2>/dev/null | grep -v "# tg-join-temp" || true; } | crontab -
