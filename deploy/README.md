# Развёртывание на сервере

Короткая версия: сайт — один процесс uvicorn за nginx, без базы и без фоновых
задач, поэтому «развернуть» здесь значит «подготовить хост, выдать сертификат,
положить `.env`, запустить контейнер и проверить шесть адресов». Ниже — в том
порядке, в котором это делается, с проверкой после каждого шага: если шаг не
проверен, дальше идти бессмысленно.

Что уже лежит в репозитории и используется здесь:

```
Dockerfile                 образ: python:3.12.15-slim, non-root, healthcheck
docker-compose.yml         публикация только на 127.0.0.1:8000, read_only, tmpfs
.env.example               шаблон окружения (префикс ZARUBA_)
deploy/nginx.conf.example  HTTPS, gzip, /healthz только с loopback, X-Request-ID
deploy/zaruba.service      вариант без Docker: systemd-юнит с минимумом прав
deploy/smoke.sh            проверка живого сайта после выкладки
```

## 1. Что нужно до первого запуска

| Что | Требование | Как проверить |
|---|---|---|
| Домен | `zaruba72.ru` + `www.zaruba72.ru` (на 23.10.2026 домен не резолвится — его нужно зарегистрировать, `docs/01` §1.4) | `dig +short zaruba72.ru` отдаёт IP сервера |
| Сервер | Debian 12/13 или Ubuntu 24.04, 1 vCPU, 1 ГБ ОЗУ, 10 ГБ диска | `free -m`, `lsb_release -a`, `df -h /` |
| Доступ | SSH по ключу, пользователь с `sudo`, пароль по SSH выключен | `ssh -o PreferredAuthentications=publickey ...` |
| Порты | 22, 80, 443 открыты; 8000 — только loopback | `ss -ltnp` |
| Почта | адрес для Let's Encrypt и для писем о сбоях | письмо себе с сервера |
| Аккаунты | Яндекс.Вебмастер, Google Search Console (для домена) | вход без ошибок |

Диск на 1 ГБ ОЗУ: сборка образа на сервере съедает ~600 МБ. Если памяти меньше
гигабайта, добавьте swap (шаг 2) или собирайте образ в CI и тяните готовый из
реестра.

## 2. Подготовка хоста

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git curl ufw fail2ban unattended-upgrades ca-certificates
sudo dpkg-reconfigure -plow unattended-upgrades     # авто-обновления безопасности

# swap: страховка для сборки образа и всплесков
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# firewall: наружу только SSH и веб; 8000 остаётся на loopback
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw --force enable
sudo ufw status verbose
```

Почему именно так: `8000` публикуется в compose как `127.0.0.1:8000`, поэтому
Docker не открывает его наружу в обход `ufw` (известная особенность docker —
он правит iptables мимо ufw; здесь это не страшно только потому, что порт
привязан к loopback). `fail2ban` в Debian 12 включает jail `sshd` сам:
`sudo fail2ban-client status sshd`.

Время и журнал:

```bash
sudo timedatectl set-timezone Asia/Yekaterinburg   # Тюмень, UTC+5
sudo systemctl restart systemd-journald
```

Даты выпусков в контенте хранятся с явным часовым поясом, поэтому смена
пояса хоста на страницы не влияет — только на метки времени в журнале.

## 3. Запуск: Docker (рекомендуемый вариант)

```bash
# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
docker --version && docker compose version

sudo mkdir -p /srv && sudo chown "$USER":"$USER" /srv
git clone <URL-репозитория> /srv/zaruba && cd /srv/zaruba
git log --oneline -1                      # что именно разворачиваем

cp .env.example .env
chmod 600 .env
nano .env                                 # см. шаг 4 — обязательно prod и адрес сайта

docker compose up -d --build
docker compose ps                         # STATUS должен стать (healthy) за ~15 с
```

Проверки этого шага:

```bash
curl -fsS http://127.0.0.1:8000/healthz          # {"status":"ok",...}
docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q web)"
ss -ltnp | grep 8000                              # 127.0.0.1:8000, не 0.0.0.0
docker compose logs --tail=30 web
```

Почему `127.0.0.1:8000` — не небрежность: приложение не умеет TLS, и если оно
смотрит в интернет напрямую, сертификат и HSTS бессмысленны, а `--proxy-headers`
начинает доверять заголовкам любого клиента. Наружу — только nginx.

Если nginx тоже будет в Docker, `127.0.0.1` из контейнера nginx — это он сам:
тогда публикуйте порт в сеть compose и поставьте `FORWARDED_ALLOW_IPS` в адрес
контейнера nginx, а не в `127.0.0.1`. Для одного сайта это лишний слой — nginx
на хосте проще.

## 4. `.env` на сервере

| Переменная | Значение в prod | Почему |
|---|---|---|
| `ZARUBA_ENV` | `prod` | включает HSTS и прод-ветки поведения |
| `ZARUBA_PUBLIC_BASE_URL` | `https://zaruba72.ru` | canonical, robots, sitemap и проверка `Host`; без него чужая подмена `Host` уводит индексацию |
| `ZARUBA_CACHE_TTL_PUBLIC` | `60` | кэш страниц; правка YAML видна максимум через минуту |
| `ZARUBA_CACHE_TTL_SCHEDULE` | `15` | расписание меняется чаще |
| `ZARUBA_RATINGS_ENABLED` | `false` | пока нет механики голосования; `true` открывает раздел для индексации |
| `ZARUBA_FAN_VOTING_ENABLED` | `false` | механики нет (`docs/05`) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | доверяем `X-Forwarded-Proto` только своему nginx |

`http://` в адресе вместо `https://` — самая дорогая опечатка: сайт сам объявит
себя небезопасным, а `sitemap.xml` уведёт робота на редирект.

## 5. nginx и сертификат

Порядок важен: сначала nginx отвечает на 80-м порту, потом Let's Encrypt, и
только потом конфигурация с `ssl_certificate` — иначе nginx не стартует, потому
что файлов сертификата ещё нет.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx

# 1) заглушка на 80-м порту, чтобы прошёл ACME-challenge
sudo tee /etc/nginx/sites-available/zaruba >/dev/null <<'EOF'
server {
    listen 80;
    server_name zaruba72.ru www.zaruba72.ru;
    location / { return 200 "ok\n"; }
}
EOF
sudo ln -sf /etc/nginx/sites-available/zaruba /etc/nginx/sites-enabled/zaruba
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
dig +short zaruba72.ru                       # уже должен отдавать IP сервера

# 2) сертификат на оба имени: без www в SAN браузер ругается на https://www...
sudo certbot --nginx -d zaruba72.ru -d www.zaruba72.ru \
     --agree-tos -m admin@zaruba72.ru --redirect --no-eff-email
sudo certbot renew --dry-run                 # проверка автопродления

# 3) боевая конфигурация из репозитория
sudo cp /srv/zaruba/deploy/nginx.conf.example /etc/nginx/sites-available/zaruba
sudo sed -i 's/example\.ru/zaruba72.ru/g' /etc/nginx/sites-available/zaruba
sudo nginx -t && sudo systemctl reload nginx
```

Проверки:

```bash
curl -sI http://zaruba72.ru | head -3          # 301 → https://zaruba72.ru/
curl -s -o /dev/null -w '%{http_code}\n' https://zaruba72.ru/healthz   # 403: наружу не торчит
curl -sI https://zaruba72.ru | grep -i 'strict-transport\|content-security'
echo | openssl s_client -connect zaruba72.ru:443 -servername zaruba72.ru 2>/dev/null | grep 'Protocol'
```

Продление: `certbot.timer` включён пакетом (`systemctl list-timers | grep certbot`).
После продления nginx должен перечитать конфигурацию — проверьте, что в
`/etc/letsencrypt/renewal/zaruba72.ru.conf` есть `renew_hook = systemctl reload nginx`.


## 6. Вариант без Docker (systemd)

Нужен, когда на хосте уже есть Python 3.12 или Docker нежелателен. Юнит
`deploy/zaruba.service` рассчитан на `/srv/zaruba` и пользователя `zaruba`.

```bash
# Python >= 3.12: в Ubuntu 24.04 и Debian 13 он системный. В Debian 12 системный
# 3.11 — там либо Docker-вариант (шаг 3), либо python3.12 из backports.
sudo apt install -y python3.12 python3.12-venv
python3.12 --version                       # должно быть 3.12.x
sudo useradd --system --create-home --home-dir /srv/zaruba --shell /usr/sbin/nologin zaruba
sudo git clone <URL-репозитория> /srv/zaruba
sudo chown -R zaruba:zaruba /srv/zaruba

sudo -u zaruba python3.12 -m venv /srv/zaruba/.venv
sudo -u zaruba /srv/zaruba/.venv/bin/pip install --require-hashes -r /srv/zaruba/requirements.lock
sudo -u zaruba /srv/zaruba/.venv/bin/pip install --no-deps /srv/zaruba

sudo install -m 600 -o zaruba -g zaruba /srv/zaruba/.env.example /srv/zaruba/.env
sudo -u zaruba nano /srv/zaruba/.env        # prod + адрес сайта

sudo cp /srv/zaruba/deploy/zaruba.service /etc/systemd/system/zaruba.service
sudo systemctl daemon-reload && sudo systemctl enable --now zaruba
systemctl status zaruba --no-pager
curl -fsS http://127.0.0.1:8000/healthz
journalctl -u zaruba -n 30 --no-pager
```

Отличия от Docker-варианта, о которых стоит помнить:

- код и `.env` лежат в `/srv/zaruba`, права — у пользователя `zaruba`, `.env` 600:
  в юните нет `User=root`, и читать секреты больше некому;
- `ProtectSystem=strict` делает `/usr`, `/etc`, `/boot` только для чтения —
  приложению этого достаточно, оно ничего не пишет (кэш — в памяти);
- обновление: `sudo git -C /srv/zaruba pull --ff-only`, затем
  `pip install --require-hashes -r ...` (если менялся лок) и
  `sudo systemctl restart zaruba`;
- `requirements.lock` ставится с `--require-hashes`: подмена пакета в индексе
  не пройдёт, как и в образе.

## 7. Проверка после запуска

`deploy/smoke.sh` — те же проверки, что делает CI в контейнере, но на живом
домене: редирект, HSTS, CSP, canonical, robots, sitemap, 404, отказ по чужому
`Host` и закрытый снаружи `/healthz`.

```bash
SITE_HOST=zaruba72.ru bash deploy/smoke.sh
```

Скрипт печатает по строке на проверку и завершается ненулевым кодом, если хоть
одна провалилась, — им же проверяется обновление. Ожидаемый вывод — все `ok`.
Ручной минимум, если скрипт недоступен:

| Адрес | Что должно быть |
|---|---|
| `http://zaruba72.ru/` | 301 на `https://zaruba72.ru/` |
| `https://www.zaruba72.ru/` | 301 на `https://zaruba72.ru/` |
| `https://zaruba72.ru/` | 200, `strict-transport-security`, `content-security-policy`, `cross-origin-opener-policy` |
| `https://zaruba72.ru/event` | `canonical` равен `https://zaruba72.ru/event` |
| `https://zaruba72.ru/robots.txt` | `Sitemap: https://zaruba72.ru/sitemap.xml` |
| `https://zaruba72.ru/sitemap.xml` | 200, только `https`-адреса |
| `https://zaruba72.ru/нет-такой` | 404 и HTML-страница с навигацией |
| `https://zaruba72.ru/healthz` | 403 (наружу не торчит) |
| `https://zaruba72.ru/` с `Host: evil.example` | 400 |

## 8. Обновление и откат

```bash
cd /srv/zaruba
git pull --ff-only
docker compose up -d --build          # или: systemctl restart zaruba
docker compose ps && docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q web)"
SITE_HOST=zaruba72.ru bash deploy/smoke.sh
```

Откат — это откат коммита, а не «поправить на сервере»:

```bash
git log --oneline -5
git checkout <предыдущий-коммит>
docker compose up -d --build
SITE_HOST=zaruba72.ru bash deploy/smoke.sh
git checkout main
```

Перед первым обновлением поставьте тег (`git tag -a v0.1.0 -m 'первый прод'`):
возвращаться к «вчерашнему рабочему» проще по имени, чем по хешу из истории.

Правка контента — отдельный случай: `content/` смонтирован только для чтения,
поэтому достаточно `git pull` (или `scp` файла) и
`docker compose restart web`; пересборка образа не нужна.

## 9. Бэкап и восстановление

Единственные данные вне git — `.env` и текущее состояние `content/`, причём
`content/` и так в репозитории. Поэтому:

```bash
sudo mkdir -p /var/backups/zaruba
# ежедневно в 03:20: архив контента и .env, хранить 14 дней
echo '20 3 * * * root tar czf /var/backups/zaruba/$(date +\%F).tar.gz -C /srv/zaruba content .env && find /var/backups/zaruba -name "*.tar.gz" -mtime +14 -delete' \
  | sudo tee /etc/cron.d/zaruba-backup
```

`.env` дополнительно храните в менеджере паролей: в нём нет секретов, но есть
адрес сайта и лимиты, а восстанавливать его «по памяти» — лишний риск.

Восстановление с нуля: новый хост по шагам 2–5, `git clone`, `.env` из менеджера,
`content/` из архива (если в git его ещё нет), `docker compose up -d --build`,
`smoke.sh`. Время — около получаса, базы данных нет.


## 10. Мониторинг, логи, приватность

`/healthz` снаружи закрыт намеренно (он называет версию и окружение), поэтому
внешняя проверка «жив ли сайт» смотрит на главную:

- **Внешний аптайм** — UptimeRobot или healthchecks.io на `https://zaruba72.ru/`,
  ожидание: 200 и подстрока из заголовка страницы. Раз в 5 минут достаточно.
- **Внутренняя проверка** — по cron, потому что `/healthz` читает контент и
  ловит битый YAML, которого снаружи не видно:

```bash
sudo tee /etc/cron.d/zaruba-health >/dev/null <<'EOF'
# каждые 5 минут: /healthz локально → пинг во внешний сервис проверок
*/5 * * * * root curl -fsS --max-time 5 http://127.0.0.1:8000/healthz >/dev/null && curl -fsS --max-time 5 https://hc-ping.com/<uuid> >/dev/null
EOF
```

Почему не «просто UptimeRobot на `/healthz`»: адрес закрыт снаружи (`deny all`),
и это правильно — он называет версию и окружение. Внешний сервис видит главную,
а битый YAML ловит локальная проверка, которая знает, что 503 — это авария.

Журналы:

```bash
docker compose logs -f --tail=100 web      # или journalctl -u zaruba -f
sudo journalctl --disk-usage               # journald не должен расти бесконечно
```

Ограничьте журнал, иначе 10 ГБ диска кончатся на ровном месте:

```bash
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=200M/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald
```

Приватность и логи. В `/privacy` написано, что запросы поиска не сохраняются,
и это правда: приложение не пишет в журнал ни строку запроса, ни `?q=`. Но
nginx по умолчанию пишет access-log с IP-адресом посетителя, а это уже
персональные данные в смысле 152-ФЗ. Два допустимых варианта — выберите и
зафиксируйте в политике:

1. **Не хранить**: в `server`-блоке `access_log off;` — для сайта без форм
   разбор access-log нужен только при инциденте, а `X-Request-ID` в журнале
   приложения остаётся.
2. **Хранить, но недолго**: оставить access-log и добавить в `/privacy` абзац
   «журналы обращений храним 14 дней для диагностики, IP не связываем с
   человеком; аналитики и cookies нет». Тогда `logrotate` настраивается на 14
   дней (`/etc/logrotate.d/nginx`, `rotate 14`, `daily`).

Второй вариант требует правки текста политики — это решение организатора, а не
техники.

## 11. Перед открытием для поиска

Сайт технически готов отдавать роботам, но «проиндексирован» — это отдельные
действия, и делать их нужно один раз:

| Шаг | Где | Зачем |
|---|---|---|
| Подтвердить домен | Яндекс.Вебмастер, Google Search Console (DNS TXT или meta) | без подтверждения не видно запросов и ошибок |
| Добавить `sitemap.xml` | там же, раздел «Индексирование» | 30+ страниц находятся сразу, а не «когда-нибудь» |
| Проверить `robots.txt` | `https://zaruba72.ru/robots.txt` | должен разрешать `/` и указывать sitemap |
| Проверить 404 и 422 | вручную | страницы ошибок не должны попадать в индекс |
| Проверить сниппет | Вебмастер → «Мои страницы» | заголовок и описание из шаблона, а не «Заруба» без контекста |
| Аналитика (по желанию) | Яндекс.Метрика | **только** вместе с правкой `/privacy`: сейчас там написано, что счётчиков нет |

Метрика — единственный пункт, который может сделать сайт хуже: счётчик ставит
cookies и внешний скрипт, а CSP в `middleware.py` запрещает сторонние
источники. Если счётчик появится, понадобится и правка CSP, и абзац в политике.

## 12. Чего в репозитории нет (осознанно)

- **CD (автодеплой из CI).** Нужны секреты SSH и доверие к ветке `main`;
  ручной `git pull && docker compose up -d --build` с `smoke.sh` для одного
  хоста дешевле, чем поддерживать workflow с ключами.
- **Реестра образов.** Образ собирается на сервере; если это станет медленно,
  следующий шаг — `docker/build-push-action` в GHCR и `image:` вместо `build:`
  в compose.
- **Второго хоста и балансировки.** Сайт читает YAML, состояния нет: при падении
  хоста достаточно развернуть его заново по шагам 2–5.
- **Алертов на диск и память.** Для одного VPS это `df -h` вручную и внешний
  аптайм; полноценный мониторинг (node_exporter, Grafana) не окупается.
- **WAF и лимита частоты в приложении.** `limit_req` — в примере nginx
  (`deploy/nginx.conf.example`), включается раскомментированием двух строк.

## 13. Порядок в один экран

```text
1. домен куплен, A-запись на IP сервера, dig подтверждает
2. apt upgrade, ufw (22/80/443), fail2ban, unattended-upgrades, swap
3. git clone в /srv/zaruba, .env (prod + https-адрес), chmod 600
4. docker compose up -d --build → (healthy), curl 127.0.0.1:8000/healthz
5. nginx: сначала 80 + заглушка → certbot на домен и www → боевой конфиг
6. SITE_HOST=zaruba72.ru bash deploy/smoke.sh → все ok
7. cron: бэкап в 03:20, /healthz каждые 5 минут, journald 200 МБ
8. Вебмастер/GSC: подтвердить домен, отдать sitemap
9. тег v0.1.0 в git; обновление — pull → build → smoke → откат по коммиту
```

Пункты 1–2 зависят от владельца домена и сервера, 3–9 выполняются на хосте за
один заход. После шага 6 сайт считается развёрнутым, после шага 8 — найденным.

