#!/usr/bin/env bash
# Майстер установки розсилки для людини в терміналі (читає /dev/tty).
# Помічник без термінала цей файл не запускає: для нього сценарій — GROK.md.
# Запускати на сервері (Ubuntu 22.04/24.04) від root:
#   curl -fsSL https://github.com/dovguu20/tg-broadcast-kit/raw/main/setup.sh -o setup.sh && bash setup.sh
# Повторний запуск безпечний: що вже зроблено — пропускає, налаштування й вхід зберігає.
# Майстер підключає TARGET акаунтів по черзі: після одного сам переходить до наступного.
# Кожен акаунт живе у своїй папці (/root/broadcast, /root/broadcast-2, ...) зі своїм входом,
# текстами й розкладом. Перерване продовжується тією ж командою.
set -euo pipefail
REPO=https://github.com/dovguu20/tg-broadcast-kit
TARGET=3   # скільки акаунтів підключає учень (Макс, 26.11)
SELF=$(readlink -f "$0")
DIR=/root/broadcast
NEW=

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
# ask ЗМІННА РЕГЕКСП — питає, доки відповідь не підійде
ask() { local v; while :; do read -r -p "  > " v </dev/tty; v=${v//[[:space:]]/}
        if [[ $v =~ $2 ]]; then printf -v "$1" '%s' "$v"; return; fi
        echo "  Не схоже на правильне значення. Спробуй ще раз."; done; }
pause() { read -r -p "  $1" _ </dev/tty; }
# Акаунт готовий: якщо підключено менше TARGET — одразу ведемо до наступного.
next_account() {
  local have=0 d a
  for d in /root/broadcast /root/broadcast-[0-9]*; do
    [ -f "$d/.env" ] && { crontab -l 2>/dev/null || true; } | grep -qF "$d/" && have=$((have + 1))
  done
  if [ "$have" -ge "$TARGET" ]; then say "Усі $TARGET акаунти підключені. Це вікно можна закрити."; exit 0; fi
  say "Тепер підключаємо акаунт $((have + 1)) з $TARGET."
  echo "  Для нього потрібне те саме, що для першого: окремий акаунт з Premium, хмарним"
  echo "  паролем і @username, і ЙОГО ВЛАСНІ api_id і api_hash (my.telegram.org з номера саме"
  echo "  цього акаунта). Твій особистий id майстер візьме сам. Перші акаунти працюють далі."
  while :; do
    read -r -p "  Готовий — натисни Enter. Зробиш пізніше — напиши «потім»: " a </dev/tty; a=${a//[[:space:]]/}
    [[ -z $a || $a =~ ^(так|Так|да|Да|y|Y)$ ]] && break
    if [[ $a =~ ^(потім|Потім|потом|Потом|пізніше|позже|later)$ ]]; then
      echo "  Добре. Коли будеш готовий — встав ту саму команду, майстер почне з акаунта $((have + 1))."
      exit 0
    fi
  done
  exec 8>&- 7>&-   # відпускаємо акаунт і замок майстра — новий запуск візьме їх сам
  NEXT=1 exec bash "$SELF"   # NEXT — без повторного питання «Enter — почати»
}

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

# Акаунти, які вже стоять на сервері (є .env).
ACCOUNTS=()
while read -r d; do [ -f "$d/.env" ] && ACCOUNTS+=("$d"); done \
  < <(ls -d /root/broadcast /root/broadcast-[0-9]* 2>/dev/null | grep -E '^/root/broadcast(-[0-9]+)?$' | sort -V)
if [ ${#ACCOUNTS[@]} -gt 0 ]; then
  # Спершу свіжий комплект у ВСІХ папках: старий код не знав про сусідів і міг би
  # стерти розклад нового акаунта. Налаштування, вхід і стан git не чіпає.
  for d in "${ACCOUNTS[@]}"; do
    [ -d "$d/.git" ] && { git -C "$d" fetch -q origin main && git -C "$d" reset -q --hard origin/main \
      || echo "  Не вдалось оновити $d (інтернет на сервері?)."; }
  done
fi
in_cron() { { crontab -l 2>/dev/null || true; } | grep -qF "$1/"; }   # акаунт дійшов до вступу в групи
acct_name() {
  local n; n=$(grep -E '^BRAND=' "$1/.env" | head -1 | cut -d= -f2- || true)
  [ -n "$n" ] || n=$(flock -n "$1/session.lock" timeout 30 "$1/venv/bin/python" "$1/check_account.py" 2>/dev/null \
                     | sed -n 's/.*username=\(@[^ ]*\).*/\1/p' || true)
  printf '%s' "${n:-акаунт у папці $1}"
}
new_dir() {
  local last n=1
  [ ${#ACCOUNTS[@]} = 0 ] && { echo /root/broadcast; return; }
  last=${ACCOUNTS[${#ACCOUNTS[@]}-1]}; [[ $last =~ -([0-9]+)$ ]] && n=$((10#${BASH_REMATCH[1]}))
  echo "/root/broadcast-$((n + 1))"
}
pick_menu() {
  local i c
  say "Акаунти розсилки на цьому сервері:"
  for i in "${!ACCOUNTS[@]}"; do echo "  $((i + 1)) — $(acct_name "${ACCOUNTS[$i]}")"; done
  echo "  Напиши номер акаунта, який перевірити або полагодити,"
  echo "  або «новий», щоб додати ще один акаунт."
  while :; do
    read -r -p "  > " c </dev/tty; c=${c//[[:space:]]/}
    if [[ $c =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le ${#ACCOUNTS[@]} ]; then
      DIR=${ACCOUNTS[$((c - 1))]}; return
    elif [[ $c =~ ^(новий|Новий|новый|Новый|нов|n|N|new|New|ytdbq)$ ]]; then
      DIR=$(new_dir); NEW=1; return
    fi
    echo "  Напиши номер зі списку (1, 2, ...) або «новий»."
  done
}

# Enter — йдемо далі по акаунтах; «перевірити» — меню, щоб полагодити готовий акаунт.
# Інші відповіді не приймаємо: випадкове слово не повинно починати новий акаунт.
go_or_check() {
  local c
  echo "  Enter — $1."
  echo "  Прийшов полагодити розсилку (прийшло ⛔️ чи ⚠️)? Напиши «перевірити»."
  while :; do
    read -r -p "  > " c </dev/tty; c=${c//[[:space:]]/}
    [[ -z $c || $c =~ ^(так|Так|да|Да|y|Y)$ ]] && return 0
    [[ $c =~ ^(перевірити|Перевірити|проверить|Проверить|проверити|check)$ ]] && return 1
    echo "  Натисни Enter або напиши «перевірити»."
  done
}
UNFIN=; DONE=0   # UNFIN — акаунт, який почали, але не довели до кінця; DONE — скільки готових
for d in "${ACCOUNTS[@]+"${ACCOUNTS[@]}"}"; do
  if in_cron "$d"; then DONE=$((DONE + 1)); elif [ -z "$UNFIN" ]; then UNFIN=$d; fi
done
show_done() { say "Уже працюють:"; for d in "${ACCOUNTS[@]}"; do in_cron "$d" && echo "  ✓ $(acct_name "$d")"; done; true; }
if [ ${#ACCOUNTS[@]} = 0 ]; then
  say "Підключаємо акаунт 1 з $TARGET. Після нього майстер сам перейде до наступного."
elif [ -n "$UNFIN" ]; then
  for i in "${!ACCOUNTS[@]}"; do [ "${ACCOUNTS[$i]}" = "$UNFIN" ] && K=$((i + 1)); done
  if [ "$DONE" = 0 ] || [ -n "${NEXT:-}" ]; then
    say "Продовжуємо акаунт $K з $TARGET."; DIR=$UNFIN
  else
    show_done; say "Акаунт $K з $TARGET ще не доставлений до кінця."
    if go_or_check "продовжити його"; then DIR=$UNFIN; else pick_menu; fi
  fi
  [ "$DIR" = "${ACCOUNTS[0]}" ] || [ "$DIR" != "$UNFIN" ] || NEW=1
elif [ ${#ACCOUNTS[@]} -lt $TARGET ]; then
  if [ -n "${NEXT:-}" ]; then
    say "Підключаємо акаунт $(( ${#ACCOUNTS[@]} + 1 )) з $TARGET."; DIR=$(new_dir); NEW=1
  else
    show_done; say "Наступний — акаунт $(( ${#ACCOUNTS[@]} + 1 )) з $TARGET."
    if go_or_check "підключити його"; then DIR=$(new_dir); NEW=1; else pick_menu; fi
  fi
else
  pick_menu
fi
[ "$DIR" = /root/broadcast ] || ok "папка акаунта: $DIR"

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
if [ -n "$NEW" ] && ! filled OWNER_ID; then
  # Твій особистий id той самий для всіх акаунтів — беремо з першого.
  OWNER=$(grep -E '^OWNER_ID=[0-9]{5,15}$' "${ACCOUNTS[0]}/.env" | head -1 | cut -d= -f2- || true)
  [ -n "$OWNER" ] && setenv OWNER_ID "$OWNER"
fi
if filled TELEGRAM_API_ID && filled TELEGRAM_API_HASH && filled OWNER_ID; then
  ok "ключі вже записані раніше"
else
  if ! filled TELEGRAM_API_ID || ! filled TELEGRAM_API_HASH; then
    [ -n "$NEW" ] && echo "  Це НОВИЙ акаунт: ключі беруться на my.telegram.org з номера саме цього акаунта."
    ask_keys
  fi
  filled OWNER_ID || ask_owner
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
filled BRAND || setenv BRAND "$USERNAME"   # ім'я акаунта в сповіщеннях і в меню майстра
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

# Повідомлення учню в особисті. Дійшло — значить і тривоги, і ліди дійдуть.
tell() {
  if venv/bin/python wizard_check.py "$1" >/dev/null 2>&1; then
    ok "повідомлення тобі в Telegram надіслано — сповіщення працюють"
  else
    echo "  Повідомлення в Telegram не надіслалось (інтернет?). Розсилка від цього не зупиняється;"
    echo "  запусти майстер пізніше і напиши «перевірити»."
  fi
}
say "Крок 6/6. Вступ у групи і запуск"
chmod +x join_step.sh
if crontab -l 2>/dev/null | grep -F "$DIR/" | grep -q "broadcast.py.*# tg-broadcast-kit"; then
  bash install.sh --cron >/dev/null   # розклад уже був — просто оновлюємо його до свіжої версії
  ok "розсилка вже працює за розкладом"
  tell running
  say "Готово! Все перевірено, розсилка працює."
  next_account
fi
[ -f join.log ] && { echo "  Як іде вступ у групи (останні рядки):"; grep "вступив\|зупинено\|ПРОЙДЕНО" join.log | tail -2 | sed 's/^/    /' || true; }
exec 6>/tmp/.tg-broadcast-cron.lock; flock -w 60 6 || true
{ crontab -l 2>/dev/null | grep -vF "$DIR/join_step.sh # tg-join-temp" || true
  echo "7 * * * * bash $DIR/join_step.sh # tg-join-temp"; } | crontab -
exec 6>&-
tell joining   # ще під замком акаунта: вступ у групи працює з тим самим входом
flock -u 8; exec 8>&-   # відпускаємо акаунт, щоб вступ у групи міг почати одразу
# 7>&- : вступ у групи йде годинами — він не повинен тримати замок майстра (інакше наступний запуск «уже запущено»).
if [ "${NO_JOIN_NOW:-}" != 1 ]; then nohup bash join_step.sh >/dev/null 2>&1 7>&- & fi
ok "вступ у групи запущено"

say "Готово!"
echo "  Зараз акаунт вступає в групи — партіями, з паузами. Зазвичай це ~4 години;"
echo "  якщо Telegram попросить паузу — до 2–3 днів, майстер почекає сам."
echo "  Коли закінчить, розсилка ввімкнеться сама, а тобі в Telegram прийде"
echo "  повідомлення «✅ Акаунт … вступив у групи, розсилка увімкнена»."
echo "  Далі пости йдуть кожні 2 години."
next_account
