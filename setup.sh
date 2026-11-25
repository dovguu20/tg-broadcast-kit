#!/usr/bin/env bash
# Майстер установки розсилки. Запускати на сервері (Ubuntu 22.04/24.04) від root:
#   curl -fsSL https://raw.githubusercontent.com/dovguu20/tg-broadcast-kit/main/setup.sh -o setup.sh && bash setup.sh
# Повторний запуск безпечний: що вже зроблено — пропускає, налаштування й вхід зберігає.
set -euo pipefail
REPO=https://github.com/dovguu20/tg-broadcast-kit
DIR=/root/broadcast

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
# ask ЗМІННА РЕГЕКСП — питає, доки відповідь не підійде
ask() { local v; while :; do read -r -p "  > " v </dev/tty; v=${v//[[:space:]]/}
        if [[ $v =~ $2 ]]; then printf -v "$1" '%s' "$v"; return; fi
        echo "  Не схоже на правильне значення. Спробуй ще раз."; done; }
pause() { read -r -p "  $1" _ </dev/tty; }

[ "$(id -u)" = 0 ] || { echo "Запусти від root: спочатку ssh root@IP_сервера"; exit 1; }
# Поки йде прогін розсилки, акаунт не чіпаємо (дві програми на одному акаунті рвуть з'єднання).
while pgrep -f 'broadcast\.py' >/dev/null; do
  echo "  Зараз іде розсилка (до ~50 хв). Чекаю, поки закінчиться, — вікно не закривай..."; sleep 60
done

say "=== Установка розсилки ==="
say "Крок 1/6. Ставлю потрібні програми (1–3 хвилини)..."
export DEBIAN_FRONTEND=noninteractive
# Свіжий сервер перші хвилини сам оновлюється і тримає apt — чекаємо до 10 хв, а не падаємо.
apt-get -o DPkg::Lock::Timeout=600 update -qq >/dev/null && apt-get -o DPkg::Lock::Timeout=600 install -y -qq git >/dev/null
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only 2>/dev/null || echo "  (оновлення комплекту пропущено, працюю з тим, що є)"
else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"
if ! bash install.sh > /tmp/broadcast-install.log 2>&1; then
  echo "  Помилка встановлення. Останні рядки:"; tail -5 /tmp/broadcast-install.log; exit 1
fi
ok "програми встановлено"

setenv() { venv/bin/python - "$1" "$2" <<'PY'
import re, sys
key, val = sys.argv[1], sys.argv[2]
s = open(".env", encoding="utf-8").read()
s = re.sub(rf"(?m)^{key}=.*$", lambda m: f"{key}={val}", s)
open(".env", "w", encoding="utf-8").write(s)
PY
}
filled() { grep -qE "^$1=.+" .env; }

ask_keys() {
  echo "  Встав api_id — число з my.telegram.org (API development tools):"
  ask API_ID '^[0-9]{4,12}$'
  echo "  Встав api_hash — довгий рядок звідти ж (32 символи):"
  ask API_HASH '^[0-9a-fA-F]{32}$'
  setenv TELEGRAM_API_ID "$API_ID"; setenv TELEGRAM_API_HASH "$API_HASH"; chmod 600 .env
}
ask_owner() {
  echo "  Твій ОСОБИСТИЙ Telegram id — число від бота @userinfobot (не акаунта розсилки):"
  ask OWNER '^[0-9]{5,15}$'
  setenv OWNER_ID "$OWNER"
}

say "Крок 2/6. Ключі Telegram"
if filled TELEGRAM_API_ID && filled TELEGRAM_API_HASH && filled OWNER_ID; then
  ok "ключі вже записані раніше"
else
  ask_keys; ask_owner
  ok "ключі записано"
fi

say "Крок 3/6. Вхід в акаунт розсилки"
if venv/bin/python check_account.py 2>/dev/null | grep -q "authorized=true"; then
  ok "вхід уже виконано"
else
  echo "  Зараз попросить: номер акаунта розсилки (+380...), потім код, який прийде"
  echo "  в сам Telegram (не SMS), потім хмарний пароль. Символи не видно — це нормально."
  until venv/bin/python login_phone.py --login </dev/tty >/tmp/broadcast-login.log 2>&1 \
        && venv/bin/python check_account.py 2>/dev/null | grep -q "authorized=true"; do
    echo "  Вхід не вдався. Причина:"
    { grep -o '"error": "[A-Za-z]*"\|retry_after_seconds": [0-9]*\|Invalid TELEGRAM_[A-Z_]*\|вже запущено' \
        /tmp/broadcast-login.log || tail -2 /tmp/broadcast-login.log; } | tail -3 | sed 's/^/    /' || true
    echo "  ApiIdInvalid / Invalid TELEGRAM_API — помилка в api_id чи api_hash."
    echo "  retry_after_seconds — Telegram просить почекати стільки секунд."
    read -r -p "  Enter — спробувати ще раз;  напиши 'ключі' — ввести api_id і api_hash заново: " a </dev/tty
    [[ $a == ключі || $a == ключи || $a == keys ]] && ask_keys
  done
fi
ACCT=$(venv/bin/python check_account.py)
USERNAME=$(sed -n 's/.*username=\(@[^ ]*\).*/\1/p' <<<"$ACCT")
ok "вхід виконано: $USERNAME"
if ! grep -q "premium=True" <<<"$ACCT"; then
  say "На цьому акаунті нема Telegram Premium — без нього розсилка не працює."
  echo "  Увімкни Premium і запусти майстер ще раз:  bash $DIR/setup.sh"; exit 1
fi
ok "Telegram Premium є"

say "Крок 4/6. Зв'язок з тобою"
echo "  Зі свого ОСОБИСТОГО акаунта напиши акаунту розсилки ($USERNAME) будь-яке слово,"
echo "  наприклад «старт». Без цього Telegram не дасть йому надсилати тобі сповіщення."
while :; do
  pause "Коли написав — натисни Enter..."
  rc=0; venv/bin/python wizard_check.py owner || rc=$?
  [ $rc = 0 ] && { ok "бачу твоє повідомлення"; break; }
  [ $rc = 2 ] && { echo "  Ти вказав id самого акаунта розсилки, а треба свій особистий."
                   echo "  Введи правильний — свій особистий id від @userinfobot:"
                   ask OWNER '^[0-9]{5,15}$'; setenv OWNER_ID "$OWNER"; continue; }
  echo "  Ще не бачу повідомлення від тебе. Перевір, що пишеш саме $USERNAME,"
  echo "  і що твій id від @userinfobot записаний правильно."
  read -r -p "  Enter — перевірити ще раз;  напиши 'id' — ввести свій id заново: " a </dev/tty
  [[ $a == id || $a == ід || $a == ID ]] && ask_owner
done

say "Крок 5/6. Тексти оголошення"
echo "  З телефону відкрий у акаунті розсилки чат «Збережені» (Saved Messages) і надішли"
echo "  собі СПОЧАТКУ текст B, ПОТІМ текст A — двома окремими повідомленнями."
echo "  Зразки: $REPO/tree/main/texts"
while :; do
  pause "Коли надіслав — натисни Enter..."
  echo "  Ось що розсилка надсилатиме (два найновіші повідомлення):"
  venv/bin/python wizard_check.py saved || true
  read -r -p "  Це твої оголошення? (так/ні) " yn </dev/tty
  [[ $yn =~ ^(так|Так|ТАК|да|Да|y|Y|yes|Yes|т|д)$ ]] && break
  echo "  Тоді надішли собі у «Збережені» правильні тексти (спочатку B, потім A)."
done
ok "тексти на місці"
if venv/bin/python lead_reply.py --seed >/dev/null 2>&1 && [ -f leads_seen.json ]; then
  ok "старі чати запам'ятовано (авто-відповідь їх не чіпатиме)"
else
  echo "  (старі чати запам'ятаю пізніше, автоматично)"
fi

say "Крок 6/6. Вступ у групи і запуск"
chmod +x join_step.sh
{ crontab -l 2>/dev/null | grep -v "# tg-join-temp" || true
  echo "7 * * * * bash $DIR/join_step.sh # tg-join-temp"; } | crontab -
if [ "${NO_JOIN_NOW:-}" != 1 ]; then nohup bash join_step.sh >/dev/null 2>&1 & fi
ok "вступ у групи запущено"

say "Готово!"
echo "  Зараз акаунт вступає в групи — партіями, з паузами, приблизно 5 годин."
echo "  Коли закінчить, розсилка ввімкнеться сама, а тобі в Telegram прийде"
echo "  повідомлення «✅ Акаунт вступив у групи, розсилка увімкнена»."
echo "  Далі пости йдуть кожні 2 години. Це вікно можна закрити."
