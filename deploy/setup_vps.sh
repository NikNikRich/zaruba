#!/usr/bin/env bash
# Первичная подготовка чистого Ubuntu/Debian под сайт «ЗАРУБА».
#
#   sudo bash deploy/setup_vps.sh            # каталог по умолчанию /opt/zaruba
#   sudo APP_DIR=/srv/zaruba bash deploy/setup_vps.sh
#
# Скрипт идемпотентен: повторный запуск ничего не ломает и только досоздаёт
# недостающее. Он ставит Docker, открывает 22/80/443 и создаёт каталог
# приложения; сайт, домен и сертификат — следующие шаги (docs/08).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/zaruba}"
DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-root}}"

log() { printf '\n== %s\n' "$1"; }
die() { printf 'ОШИБКА: %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "нужны права root: sudo bash deploy/setup_vps.sh"
command -v apt-get >/dev/null 2>&1 || die "нужен Ubuntu или Debian: apt-get не найден"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
    ubuntu | debian) log "система: ${PRETTY_NAME:-$ID}" ;;
    *) die "поддерживаются Ubuntu и Debian, а здесь ${ID:-неизвестная система}" ;;
esac

# -qq и DEBIAN_FRONTEND: без них apt ждёт подтверждения и вешает неинтерактивный
# прогон (в том числе запуск по SSH из CI).
log "Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw unattended-upgrades >/dev/null

log "Docker Engine и compose"
if command -v docker >/dev/null 2>&1; then
    printf 'уже установлен: %s\n' "$(docker --version)"
else
    # Официальный скрипт Docker ставит Engine и плагин compose.
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null
docker compose version

log "Firewall (22, 80, 443)"
# 22 остаётся открытым всегда: закрыв его, до сервера больше не дотянуться.
ufw allow 22/tcp
# 80 нужен не только для редиректа, но и для ACME-проверки Let's Encrypt.
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
ufw status

log "Пользователь деплоя"
if [ "$DEPLOY_USER" != root ] && id "$DEPLOY_USER" >/dev/null 2>&1; then
    # Под этим пользователем ходит GitHub Actions: ему нужен docker без sudo.
    usermod -aG docker "$DEPLOY_USER"
    printf 'пользователь %s добавлен в группу docker (перезайдите в сессию)\n' "$DEPLOY_USER"
else
    printf 'деплой пойдёт от root — для одноразового VPS допустимо\n'
fi

log "Каталог приложения"
install -d -m 0755 "$APP_DIR"
if [ "$DEPLOY_USER" != root ] && id "$DEPLOY_USER" >/dev/null 2>&1; then
    chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
fi
printf 'готово: %s\n' "$APP_DIR"

log "Автообновления безопасности"
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || \
    printf 'warn: unattended-upgrades не настроился — проверьте вручную\n'

cat <<NEXT

Дальше (подробно — docs/08-deploy-and-sheets.md):
  1. A-запись домена на IP этого сервера; проверка: dig +short <домен>
  2. git clone <репозиторий> $APP_DIR && cd $APP_DIR
  3. cp .env.example .env && nano .env   # ZARUBA_ENV=prod, DOMAIN, ZARUBA_PUBLIC_BASE_URL
  4. docker compose up -d --build
  5. SITE_HOST=<домен> bash deploy/smoke.sh
NEXT
