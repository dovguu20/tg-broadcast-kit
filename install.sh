#!/usr/bin/env bash
# Встановлення комплекту розсилки на чистому Ubuntu 22.04 / 24.04. Запускати від root
# з будь-якого місця: bash /root/broadcast/install.sh
#   без параметрів  — ставить Python, бібліотеки, часовий пояс, створює .env з прикладу
#   --status        — що вже зроблено, БЕЗ секретів (можна запускати скільки завгодно)
#   --apply-env     — записати TELEGRAM_API_ID / TELEGRAM_API_HASH / OWNER_ID / BRAND
#                     зі stdin-JSON або зі змінних оточення; значення не друкує
#   --cron          — вмикає автоматику (5 завдань у crontab); зазвичай це робить
#                     join_step.sh, коли список груп пройдено
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
DIR=$PWD

# Ім'я сесії береться з .env. Порожня змінна оточення його не підміняє.
session_name() {
  local s=""
  if [ -f .env ]; then
    s=$(grep -E '^TELEGRAM_SESSION_NAME=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '[:space:]' || true)
  fi
  if [ -z "$s" ] && [ -n "${TELEGRAM_SESSION_NAME:-}" ]; then
    s=$TELEGRAM_SESSION_NAME
  fi
  if [ -z "$s" ]; then
    s=sender
  fi
  case $s in
    *[!A-Za-z0-9_-]*) s=sender ;;
  esac
  printf '%s' "$s"
}

env_state() {
  # Друкує empty / set / invalid. Саме значення не друкує.
  local key="$1" regex="$2" line val
  if [ ! -f .env ]; then
    printf empty
    return
  fi
  line=$(grep -E "^${key}=" .env | head -1 || true)
  val=${line#*=}
  val=${val#\"}
  val=${val%\"}
  val=${val#\'}
  val=${val%\'}
  val=$(printf '%s' "$val" | tr -d '[:space:]')
  if [ -z "$val" ]; then
    printf empty
  elif printf '%s' "$val" | grep -qE "$regex"; then
    printf set
  else
    printf invalid
  fi
}

if [ "${1:-}" = "--status" ]; then
  emit() { printf '%s=%s\n' "$1" "$2"; }
  emit kit_dir "$DIR"
  if [ -d .git ]; then emit git yes; else emit git no; fi
  if [ -x venv/bin/python ]; then emit venv yes; else emit venv no; fi
  if [ -f .env ]; then emit env_file yes; else emit env_file no; fi
  emit api_id "$(env_state TELEGRAM_API_ID '^[0-9]{4,12}$')"
  emit api_hash "$(env_state TELEGRAM_API_HASH '^[0-9a-fA-F]{32}$')"
  emit owner_id "$(env_state OWNER_ID '^[0-9]{5,15}$')"
  brand=$(grep -E '^BRAND=' .env 2>/dev/null | head -1 | cut -d= -f2- || true)
  brand=${brand#\"}; brand=${brand%\"}
  if [ -n "$(printf '%s' "$brand" | tr -d '[:space:]')" ]; then emit brand set; else emit brand empty; fi
  sess=$(session_name)
  emit session_name "$sess"
  if [ -f "${sess}.session" ]; then emit session present; else emit session missing; fi
  if [ -f "${sess}.login-status.json" ]; then
    ev=$(grep -o '"event"[[:space:]]*:[[:space:]]*"[A-Za-z0-9_]*"' "${sess}.login-status.json" | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//' || true)
    emit login_event "${ev:-unknown}"
  else
    emit login_event none
  fi
  if [ -f "${sess}.login-pending.json" ]; then
    emit login_pending yes
    if grep -q '"stage"[[:space:]]*:[[:space:]]*"password"' "${sess}.login-pending.json"; then
      emit login_stage password
    else
      emit login_stage code
    fi
  else
    emit login_pending no
    emit login_stage none
  fi
  if [ -f joined_done.txt ]; then
    emit join_done "$(grep -c . joined_done.txt || true)"
  else
    emit join_done 0
  fi
  if [ -f .env ]; then
    emit join_total "$(grep -E '^TELEGRAM_TARGET_GROUPS=' .env | head -1 | grep -o '@' | wc -l | tr -d '[:space:]' || true)"
  else
    emit join_total 0
  fi
  if [ -f join.log ] && grep -q 'СПИСОК ПРОЙДЕНО ПОВНІСТЮ' join.log; then
    emit join_complete yes
  else
    emit join_complete no
  fi
  # Прогін саме цієї папки: на сервері можуть крутитися й інші акаунти (/root/broadcast-2, ...).
  running=no
  for p in $(pgrep -f 'venv/bin/python broadcast\.py' 2>/dev/null || true); do
    [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$DIR" ] && running=yes
  done
  emit broadcast_running "$running"
  if ! command -v crontab >/dev/null 2>&1; then
    emit crontab unavailable
    emit join_cron no
    emit broadcast_cron 0
  else
    cron_now=$(crontab -l 2>/dev/null | grep -F "$DIR/" || true)   # лише рядки цієї папки
    emit crontab yes
    if printf '%s\n' "$cron_now" | grep -q '# tg-join-temp'; then emit join_cron yes; else emit join_cron no; fi
    emit broadcast_cron "$(printf '%s\n' "$cron_now" | grep -c '# tg-broadcast-kit' || true)"
  fi
  exit 0
fi

if [ "${1:-}" = "--apply-env" ]; then
  if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    chmod 600 .env
  fi
  if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=python3; fi
  exec "$PY" apply_env.py
fi

if [ "${1:-}" = "--cron" ]; then
  [ -f .env ] || { echo "нема .env — спершу звичайний install.sh і заповнення .env"; exit 1; }
  sess=$(session_name)
  [ -f "${sess}.session" ] || echo "УВАГА: файла сесії ${sess}.session ще нема — спершу вхід (login_phone.py --send-code і --sign-in, або --login у терміналі)"
  tmp=$(mktemp)
  # Кілька акаунтів на одному сервері — кожен у своїй папці: /root/broadcast, /root/broadcast-2, ...
  # Міняємо лише рядки цієї папки, а прогони розносимо на 40 хв (:00, :40, потім :20 непарних годин).
  n=1; [[ ${DIR##*/} =~ ^broadcast-([0-9]+)$ ]] && n=${BASH_REMATCH[1]}
  off=$(( (n - 1) * 40 % 120 )); slot=()
  [ "$off" = 0 ] || slot=(-e "1s#^0 \*/2 #$(( off % 60 )) $(( off / 60 ))-23/2 #")
  { crontab -l 2>/dev/null | awk -v d="$DIR/" '!(index($0, "# tg-broadcast-kit") && index($0, d))' || true; } > "$tmp"
  sed -e "s#DIR#$DIR#g" "${slot[@]}" crontab.example >> "$tmp"
  crontab "$tmp"; rm -f "$tmp"
  echo "автоматику ввімкнено:"; crontab -l | grep -F "$DIR/" | grep "# tg-broadcast-kit"
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
echo "встановлено в $DIR. Далі: bash install.sh --apply-env і вхід (login_phone.py --send-code, потім --sign-in; у терміналі людини — --login)"
