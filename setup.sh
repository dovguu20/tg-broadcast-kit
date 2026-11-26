#!/usr/bin/env bash
# Майстер установки розсилки. Запускати на сервері (Ubuntu 22.04/24.04) від root:
#   curl -fsSL https://github.com/dovguu20/tg-broadcast-kit/raw/main/setup.sh -o setup.sh && bash setup.sh
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
umask 077   # логи й стан із даними лідів — лише для root
exec 7>/root/.tg-broadcast-setup.lock
flock -n 7 || { echo "Майстер уже запущено в іншому вікні. Закрий зайве вікно і користуйся одним."; exit 1; }

say "=== Установка розсилки ==="
say "Крок 1/6. Ставлю потрібні програми (1–3 хвилини)..."
export DEBIAN_FRONTEND=noninteractive
# Свіжий сервер перші хвилини сам оновлюється і тримає apt — чекаємо до 10 хв, а не падаємо.
for i in $(seq 1 40); do apt-get update -qq >/dev/null 2>&1 && break
  [ "$i" = 1 ] && echo "  Сервер ще сам оновлюється — чекаю (до 10 хвилин)..."; sleep 15; done
apt-get -o DPkg::Lock::Timeout=600 install -y -qq git >/dev/null
if [ -d "$DIR/.git" ]; then
  # Оновлення комплекту. Налаштування, вхід і стан (.env, *.session, *.json) git не чіпає.
  git -C "$DIR" fetch -q origin main && git -C "$DIR" reset -q --hard origin/main \
    || { echo "  Не вдалось оновити комплект з GitHub (інтернет на сервері?). Спробуй ще раз за хвилину."; exit 1; }
else
  rm -rf "$DIR.tmp"   # недокачана копія з минулої спроби
  git clone -q "$REPO" "$DIR.tmp"
  [ -d "$DIR" ] && mv "$DIR" "$DIR.broken.$(date +%s)"   # папка без .git — зберігаємо, не видаляємо
  mv "$DIR.tmp" "$DIR"
fi
cd "$DIR"
# Поки йде прогін розсилки, акаунт не чіпаємо (дві програми на одному акаунті рвуть з'єднання).
# Розклад запускає розсилку під цим самим замком — поки майстер працює, вона просто пропустить свій час.
exec 8>session.lock
if ! flock -n 8; then
  echo "  Зараз іде розсилка (до ~50 хв). Чекаю, поки закінчиться, — вікно не закривай..."
  flock 8
fi
if ! bash install.sh > /tmp/broadcast-install.log 2>&1; then
  echo "  Помилка встановлення. Останні рядки:"; tail -5 /tmp/broadcast-install.log; exit 1
fi
ok "програми встановлено"

setenv() { venv/bin/python - "$1" "$2" <<'PY'
import re, sys
key, val = sys.argv[1], sys.argv[2]
s = open(".env", encoding="utf-8").read()
if re.search(rf"(?m)^{key}=", s):
    s = re.sub(rf"(?m)^{key}=.*$", lambda m: f"{key}={val}", s)
else:
    s = s.rstrip("\n") + f"\n{key}={val}\n"   # стара .env без цього ключа
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
  echo "  Зараз попросить: номер акаунта розсилки (+380...), потім код, потім хмарний пароль."
  echo "  Код зазвичай приходить у сам Telegram на телефоні з цим акаунтом; куди саме —"
  echo "  програма напише нижче (Telegram / SMS / дзвінок). Символи не видно — це нормально."
  until venv/bin/python login_phone.py --login </dev/tty 2>&1 | tee /tmp/broadcast-login.log \
          | { grep --line-buffered '"delivery_selected"' || true; } | sed -u \
            -e 's/.*"delivery": "SentCodeTypeApp".*/  → Код прийде в Telegram (чат «Telegram» на телефоні)./' \
            -e 's/.*"delivery": "SentCodeTypeSms.*/  → Код прийде в SMS./' \
            -e 's/.*"delivery": "SentCodeTypeCall".*/  → Код продиктують дзвінком./' \
            -e '/"delivery"/d' \
        && venv/bin/python check_account.py 2>/dev/null | grep -q "authorized=true"; do
    L=/tmp/broadcast-login.log
    echo "  Вхід не вдався."
    if grep -q 'ApiIdInvalid\|Invalid TELEGRAM_API' $L; then echo "  Помилка в api_id чи api_hash — введи їх заново (напиши 'ключі')."
    elif grep -q 'invalid_phone_format' $L; then echo "  Номер треба вводити з плюсом і кодом країни, без пробілів: +380501234567."
    elif grep -q 'PhoneNumberInvalid\|PhoneNumberBanned\|PhoneNumberUnoccupied' $L; then echo "  Telegram не приймає цей номер. Перевір номер акаунта розсилки."
    elif grep -q 'PhoneCodeInvalid\|PhoneCodeExpired\|code_not_received' $L; then echo "  Код неправильний або застарів. Спробуй ще раз — прийде новий."
    elif grep -q 'PasswordHashInvalid' $L; then echo "  Неправильний хмарний пароль акаунта розсилки."
    elif grep -q 'rate_limited' $L; then echo "  Telegram просить почекати $(grep -o 'retry_after_seconds": [0-9]*' $L | grep -o '[0-9]*$' | tail -1) секунд перед новою спробою."
    elif grep -q 'окремого підтримуваного\|unsupported_auth_response' $L; then
      echo "  Telegram вибрав спосіб входу, який майстер не вміє (пошта, дзвінок-скидання тощо)."
      echo "  Зроби скрін цього вікна і надішли Максу."; exit 1
    elif grep -q 'вже запущено' $L; then echo "  Вхід уже запущено в іншому вікні — закрий його."
    else echo "  Деталі:"; tail -2 $L | sed 's/^/    /'; fi
    read -r -p "  Enter — спробувати ще раз;  'ключі' (або k) — ввести api_id і api_hash заново: " a </dev/tty
    [[ $a == ключі || $a == ключи || $a == keys || $a == k || $a == K ]] && ask_keys
  done
fi
ACCT=$(venv/bin/python check_account.py)
USERNAME=$(sed -n 's/.*username=\(@[^ ]*\).*/\1/p' <<<"$ACCT")
while [ -z "$USERNAME" ] || [ "$USERNAME" = "@no_username" ]; do
  echo "  У акаунта розсилки нема імені користувача (@...). Воно потрібне, щоб ти його знайшов."
  echo "  На телефоні в акаунті розсилки: Налаштування → Ім'я користувача → придумай і збережи."
  pause "Коли зберіг — натисни Enter..."
  ACCT=$(venv/bin/python check_account.py)
  USERNAME=$(sed -n 's/.*username=\(@[^ ]*\).*/\1/p' <<<"$ACCT")
done
ok "вхід виконано: $USERNAME"
if ! grep -q "premium=True" <<<"$ACCT"; then
  say "На цьому акаунті нема Telegram Premium — без нього розсилка не працює."
  echo "  Увімкни Premium і запусти майстер ще раз:  bash setup.sh"; exit 1
fi
ok "Telegram Premium є"

say "Крок 4/6. Зв'язок з тобою"
echo "  Зі свого ОСОБИСТОГО акаунта напиши акаунту розсилки ($USERNAME) будь-яке слово,"
echo "  наприклад «старт». Без цього Telegram не дасть йому надсилати тобі сповіщення."
while :; do
  pause "Коли написав — натисни Enter..."
  rc=0; venv/bin/python wizard_check.py owner || rc=$?
  [ $rc = 0 ] && { ok "бачу твоє повідомлення"; break; }
  [ $rc = 3 ] && { echo "  Не вдалось перевірити (зв'язок із Telegram). Спробуй ще раз через хвилину."; continue; }
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
  if ! venv/bin/python wizard_check.py saved; then
    echo "  Потрібно два повідомлення в «Збережених». Надішли тексти (спочатку B, потім A)."; continue
  fi
  read -r -p "  Це твої оголошення? (так/ні, або y/n) " yn </dev/tty
  [[ $yn =~ ^(так|Так|ТАК|да|Да|y|Y|yes|Yes|т|д|1|nfr)$ ]] && break
  echo "  Тоді надішли собі у «Збережені» правильні тексти (спочатку B, потім A)."
done
ok "тексти на місці"
if [ -f leads_seen.json ]; then
  ok "сповіщення про лідів уже налаштовані"   # повторний seed проковтнув би нових лідів
elif venv/bin/python lead_reply.py --seed >/dev/null 2>&1 && [ -f leads_seen.json ]; then
  ok "старі чати запам'ятовано (сповіщень про них не буде)"
else
  echo "  (старі чати запам'ятаю пізніше, автоматично)"
fi

say "Крок 6/6. Вступ у групи і запуск"
chmod +x join_step.sh
if crontab -l 2>/dev/null | grep -q "broadcast.py.*# tg-broadcast-kit"; then
  bash install.sh --cron >/dev/null   # розклад уже був — просто оновлюємо його до свіжої версії
  ok "розсилка вже працює за розкладом"
  say "Готово! Все перевірено, розсилка працює. Це вікно можна закрити."
  exit 0
fi
[ -f join.log ] && { echo "  Як іде вступ у групи (останні рядки):"; grep "вступив\|зупинено\|ПРОЙДЕНО" join.log | tail -2 | sed 's/^/    /' || true; }
{ crontab -l 2>/dev/null | grep -v "# tg-join-temp" || true
  echo "7 * * * * bash $DIR/join_step.sh # tg-join-temp"; } | crontab -
flock -u 8; exec 8>&-   # відпускаємо акаунт, щоб вступ у групи міг почати одразу
if [ "${NO_JOIN_NOW:-}" != 1 ]; then nohup bash join_step.sh >/dev/null 2>&1 & fi
ok "вступ у групи запущено"

say "Готово!"
echo "  Зараз акаунт вступає в групи — партіями, з паузами. Зазвичай це ~5 годин;"
echo "  якщо Telegram попросить паузу — до 2–3 днів, майстер почекає сам."
echo "  Коли закінчить, розсилка ввімкнеться сама, а тобі в Telegram прийде"
echo "  повідомлення «✅ Акаунт вступив у групи, розсилка увімкнена»."
echo "  Далі пости йдуть кожні 2 години. Це вікно можна закрити."
