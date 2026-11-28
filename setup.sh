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
    if [[ $a =~ ^(потім|Потім|потом|Потом|пізніше|позже|later|ні|Ні|нет|Нет|no|No)$ ]]; then
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
echo "  Вийти будь-коли — Ctrl+C. Потім та сама команда продовжить з того ж місця."
say "Крок 1/6. Ставлю потрібні програми (1–3 хвилини)..."
export DEBIAN_FRONTEND=noninteractive
# Свіжий сервер перші хвилини сам оновлюється і тримає apt — чекаємо до 10 хв, а не падаємо.
dpkg --configure -a >/dev/null 2>&1 || true   # минулого разу вікно закрили посеред встановлення
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
    elif [[ $c =~ ^(перев|Перев|пров|Пров|пер|Пер|check|Check) ]]; then
      echo "  Напиши номер акаунта, який перевірити (1, 2, ...)."; continue
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
    # перевірити / проверить / перевирити …; літерали, не [Пп]: у C-локалі сервера кирилиця в [..] ламається
    [[ $c =~ ^(перев|Перев|пров|Пров|пер|Пер|check|Check) ]] && return 1
    echo "  Натисни Enter або напиши «перевірити»."
  done
}
UNFIN=; DONE=0   # UNFIN — акаунт, який почали, але не довели до кінця; DONE — скільки готових
for d in "${ACCOUNTS[@]+"${ACCOUNTS[@]}"}"; do
  if in_cron "$d"; then DONE=$((DONE + 1)); elif [ -z "$UNFIN" ]; then UNFIN=$d; fi
done
joining() { { crontab -l 2>/dev/null || true; } | grep -qF "$1/join_step.sh # tg-join-temp"; }
show_done() { say "Уже підключені:"; for d in "${ACCOUNTS[@]}"; do in_cron "$d" || continue
  if joining "$d"; then echo "  ✓ $(acct_name "$d") — ще вступає в групи"; else echo "  ✓ $(acct_name "$d")"; fi; done; true; }
if [ ${#ACCOUNTS[@]} = 0 ]; then
  say "Підключаємо акаунт 1 з $TARGET. Після нього майстер сам перейде до наступного."
elif [ -n "$UNFIN" ]; then
  for i in "${!ACCOUNTS[@]}"; do [ "${ACCOUNTS[$i]}" = "$UNFIN" ] && K=$((i + 1)); done
  if [ "$DONE" = 0 ] || [ -n "${NEXT:-}" ]; then
    say "Продовжуємо акаунт $K з $TARGET."; DIR=$UNFIN
  else
    show_done; say "Акаунт $K з $TARGET ще не доведений до кінця."
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
echo "  Ставлю програми для цього акаунта (1–3 хвилини). Вікно не закривай..."
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
  echo "  Зараз попросить: номер акаунта розсилки (+380...), потім код, потім хмарний пароль"
  echo "  (рос.: облачный пароль). Код приходить у Telegram на ТЕЛЕФОНІ з акаунтом розсилки —"
  echo "  тримай його під рукою. Код і пароль при вводі не видно — це нормально."
  echo "  Код не приходить — замість коду напиши qr: увійдеш QR-кодом з телефона, без коду."
  # Підказки й запити login_phone.py пише прямо в термінал; stdout (технічний журнал) — у файл для розбору помилки.
  until venv/bin/python login_phone.py --login </dev/tty >/tmp/broadcast-login.log 2>&1 \
        && venv/bin/python check_account.py 2>/dev/null | grep -q "authorized=true"; do
    L=/tmp/broadcast-login.log
    echo "  Вхід не вдався."
    if grep -q 'ApiIdInvalid\|Invalid TELEGRAM_API' $L; then echo "  Помилка в api_id чи api_hash — введи їх заново (напиши 'ключі')."
    elif grep -q 'invalid_phone_format' $L; then echo "  Номер треба вводити з плюсом і кодом країни, без пробілів: +380501234567."
    elif grep -q 'PhoneNumberInvalid\|PhoneNumberBanned\|PhoneNumberUnoccupied' $L; then echo "  Telegram не приймає цей номер. Перевір номер акаунта розсилки."
    elif grep -q 'code_not_received' $L; then
      echo "  Код не прийшов. Це відомий збій Telegram: коди для таких програм часто не доходять."
      echo "  Увійди без коду, QR-кодом з телефона: напиши qr."
    elif grep -q 'qr_timeout' $L; then echo "  QR-код не відсканували (5 хвилин) або хмарний пароль тричі не той. Напиши qr ще раз."
    elif grep -q 'PhoneCodeInvalid\|PhoneCodeExpired' $L; then echo "  Код неправильний або застарів. Спробуй ще раз — прийде новий."
    elif grep -q 'PasswordHashInvalid\|password_failed' $L; then echo "  Хмарний пароль (рос.: облачный пароль) не той. Перевір його в Telegram: Налаштування → Конфіденційність."
    elif grep -q 'rate_limited\|PhoneNumberFlood' $L; then
      s=$(grep -o 'retry_after_seconds": [0-9]*' $L | grep -o '[0-9]*$' | tail -1)
      if [ -n "$s" ] && [ "$s" -lt 3600 ]; then echo "  Telegram просить почекати з кодами ~$(( (s + 59) / 60 )) хв."
      elif [ -n "$s" ]; then echo "  Telegram просить почекати з кодами ~$(( (s + 3599) / 3600 )) год."; else echo "  Telegram тимчасово не надсилає коди на цей номер (забагато спроб)."; fi
      echo "  Не чекай: увійди QR-кодом — напиши qr. Ліміт на коди його не стосується."
    elif grep -q 'unsupported_auth_response' $L; then echo "  Telegram вибрав спосіб входу, який майстер не вміє. Увійди QR-кодом: напиши qr."
    elif grep -q 'connection_error' $L; then echo "  Немає зв'язку з Telegram. Спробуй ще раз за хвилину (Enter)."
    elif grep -q 'вже запущено' $L; then echo "  Вхід уже запущено в іншому вікні — закрий його."
    else echo "  Деталі:"; tail -2 $L | sed 's/^/    /'; fi
    read -r -p "  Enter — спробувати ще раз;  'qr' — увійти QR-кодом з телефона;  'ключі' (або k) — ввести api_id і api_hash заново: " a </dev/tty
    a=${a//[[:space:]]/}
    [[ $a == ключі || $a == ключи || $a == keys || $a == k || $a == K ]] && ask_keys
    # QR малюється прямо в термінал (/dev/tty). Удалось — наступний --login бачить вхід і пропускає.
    if [[ $a =~ ^(qr|QR|Qr|кр|КР|йк|ЙК)$ ]]; then
      until venv/bin/python login_phone.py --qr </dev/tty >/tmp/broadcast-login.log 2>&1; do
        read -r -p "  QR не спрацював. Enter — новий QR-код;  'код' — назад до входу кодом: " b </dev/tty
        [[ ${b//[[:space:]]/} =~ ^(код|Код|code|kod)$ ]] && break
      done
    fi
  done
fi
acct() { until ACCT=$(venv/bin/python check_account.py 2>&1); do
           echo "  Немає зв'язку з Telegram."; pause "Enter — спробувати ще раз..."; done; }
acct
USERNAME=$(sed -n 's/.*username=\(@[^ ]*\).*/\1/p' <<<"$ACCT")
for d in "${ACCOUNTS[@]+"${ACCOUNTS[@]}"}"; do
  # лише для акаунта, який ще підключаємо: готові (у розкладі) не чіпаємо
  [ "$d" != "$DIR" ] && ! in_cron "$DIR" && [ -n "$USERNAME" ] && grep -qxF "BRAND=$USERNAME" "$d/.env" 2>/dev/null || continue
  say "$USERNAME уже підключений в іншій папці ($d). Два підключення одного акаунта — подвійні пости і бан."
  echo "  Тут потрібен ІНШИЙ акаунт розсилки. Виходжу з цього входу — запусти майстер ще раз"
  echo "  і увійди в інший акаунт (на телефоні перемкнись на нього перед QR-кодом)."
  venv/bin/python login_phone.py --logout >/dev/null 2>&1 || true
  exit 1
done
while [ -z "$USERNAME" ] || [ "$USERNAME" = "@no_username" ]; do
  echo "  У акаунта розсилки нема імені користувача (@...). Воно потрібне, щоб ти його знайшов."
  echo "  На телефоні в акаунті розсилки: Налаштування → Ім'я користувача (рос.: Настройки → Имя пользователя) → придумай і збережи."
  pause "Коли зберіг — натисни Enter..."
  acct
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
rc=0; venv/bin/python wizard_check.py owner >/dev/null 2>&1 || rc=$?   # повторний запуск: уже писав — не просимо знову
[ $rc = 0 ] || {
  echo "  У Telegram (не в цьому вікні!) зі свого ОСОБИСТОГО акаунта напиши акаунту розсилки"
  echo "  $USERNAME будь-яке слово, наприклад «старт»: https://t.me/${USERNAME#@}"
  echo "  Без цього Telegram не дасть йому надсилати тобі сповіщення."; }
while [ $rc != 0 ]; do
  pause "Коли написав — натисни Enter..."
  rc=0; venv/bin/python wizard_check.py owner || rc=$?
  [ $rc = 0 ] && { ok "бачу твоє повідомлення"; break; }
  [ $rc = 3 ] && { echo "  Не вдалось перевірити (зв'язок із Telegram). Спробуй ще раз через хвилину."; continue; }
  if [ $rc = 2 ]; then
    echo "  Твій особистий id збігається з акаунтом, у який увійшов майстер."
    if ! in_cron "$DIR"; then   # готовий акаунт з розкладу не розлогінюємо
      echo "  Якщо на кроці входу ти увійшов у СВІЙ ОСОБИСТИЙ акаунт замість акаунта розсилки —"
      echo "  напиши «вийти»: майстер вийде з нього, і ти увійдеш в акаунт розсилки заново."
    fi
    echo "  Якщо ти ввів id акаунта розсилки — напиши «id» і введи свій особистий id від @userinfobot."
    read -r -p "  > " a </dev/tty; a=${a//[[:space:]]/}
    if ! in_cron "$DIR" && [[ $a =~ ^(вийти|Вийти|выйти|Выйти|logout)$ ]]; then
      venv/bin/python login_phone.py --logout >/dev/null 2>&1 || true
      setenv BRAND ""   # ім'я особистого акаунта не має лишитись у сповіщеннях
      echo "  Вийшов. Запускаю вхід заново."
      exec 8>&- 7>&-; NEXT=1 exec bash "$SELF"
    fi
    [[ $a =~ ^(id|Id|ID|ід|Ід|ид|Ид|шв)$ ]] && ask_owner
    rc=1; continue
  fi
  echo "  Ще не бачу повідомлення від тебе. Перевір, що пишеш у Telegram саме $USERNAME"
  echo "  і саме зі свого особистого акаунта, а твій id від @userinfobot записаний правильно."
  read -r -p "  Enter — перевірити ще раз;  напиши 'id' — ввести свій id заново: " a </dev/tty
  a=${a//[[:space:]]/}
  [[ $a =~ ^(id|Id|ID|ід|Ід|ид|Ид|шв)$ ]] && ask_owner
  rc=1
done

say "Крок 5/6. Тексти оголошення"
SAVED="«Збережені» (рос.: «Избранное», Saved Messages)"
first=1
while :; do
  rc=0; out=$(venv/bin/python wizard_check.py saved 2>&1) || rc=$?
  if [ $rc = 0 ]; then
    echo "  Ось що розсилка надсилатиме (два найновіші повідомлення в $SAVED):"; echo "$out"
    read -r -p "  Це твої оголошення? (так/ні) " yn </dev/tty; yn=${yn//[[:space:]]/}
    [[ $yn =~ ^(так|Так|ТАК|да|Да|ДА|y|Y|yes|Yes|YES|т|Т|д|Д|1|nfr|lf|ок|Ок|ОК|ok|Ok|OK|\+)$ ]] && break
    echo "  Тоді надішли собі в $SAVED правильні тексти: спочатку B, потім A."
  elif [ $rc = 3 ]; then echo "  Немає зв'язку з Telegram — спробуємо ще раз."
  elif [ $first = 1 ]; then
    echo "  З телефону відкрий в акаунті розсилки чат $SAVED і надішли собі"
    echo "  СПОЧАТКУ текст B, ПОТІМ текст A — двома окремими повідомленнями."
    echo "  Зразки: $REPO/tree/main/texts"
  else echo "  Поки бачу менше двох повідомлень у $SAVED. Надішли тексти (спочатку B, потім A)."; fi
  [ $rc = 3 ] || first=0
  pause "Коли надіслав — натисни Enter..."
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
  # акаунти вступають у різні хвилини (7, 27, 47), а не всі разом з однієї адреси
  n=1; [[ ${DIR##*/} =~ ^broadcast-([0-9]+)$ ]] && n=$((10#${BASH_REMATCH[1]}))
  echo "$(( (7 + (n - 1) * 20) % 60 )) * * * * bash $DIR/join_step.sh # tg-join-temp"; } | crontab -
exec 6>&-
tell joining   # ще під замком акаунта: вступ у групи працює з тим самим входом
flock -u 8; exec 8>&-   # відпускаємо акаунт, щоб вступ у групи міг почати одразу
# 7>&- : вступ у групи йде годинами — він не повинен тримати замок майстра (інакше наступний запуск «уже запущено»).
# повторна перевірка під час вступу: партія цієї години вже була — не додаємо зайву (вдвічі швидший вступ = ризик)
if [ "${NO_JOIN_NOW:-}" != 1 ] && [ -z "$(find join.log -mmin -60 2>/dev/null)" ]; then nohup bash join_step.sh >/dev/null 2>&1 7>&- & fi
ok "вступ у групи запущено"

say "Готово!"
echo "  Зараз акаунт вступає в групи — партіями, з паузами. Зазвичай це ~4 години;"
echo "  якщо Telegram попросить паузу — до 2–3 днів, майстер почекає сам."
echo "  Коли закінчить, розсилка ввімкнеться сама, а тобі в Telegram прийде"
echo "  повідомлення «✅ Акаунт … вступив у групи, розсилка увімкнена»."
echo "  Далі пости йдуть кожні 2 години."
next_account
