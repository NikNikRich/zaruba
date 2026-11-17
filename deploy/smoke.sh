#!/usr/bin/env bash
# Проверка живого сайта после выкладки и после обновления (deploy/README.md §7).
# Использование: SITE_HOST=zaruba72.ru bash deploy/smoke.sh
# (имя переменной именно SITE_HOST: HOST bash задаёт сам — это имя машины)
# Код возврата: 0 — всё сошлось, 1 — есть провалы (они перечислены в выводе).
set -u

HOST="${SITE_HOST:-zaruba72.ru}"
# SCHEME=http — только для локальной отладки скрипта против uvicorn без nginx:
# редирект, www и закрытый снаружи /healthz тогда пропускаются.
SCHEME="${SCHEME:-https}"
BASE="${SCHEME}://${HOST}"
fail=0

ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
head_of() { curl -sI --max-time 10 "$@" | tr -d '\r'; }

if [ "$SCHEME" = "https" ]; then
# 1. http → https
redirect=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "http://${HOST}/")
case "$redirect" in
    "301 ${BASE}/" | "308 ${BASE}/") ok "http → https (${redirect})" ;;
    *) bad "http → https: ожидали 301 на ${BASE}/, получили '${redirect}'" ;;
esac

# 2. www → канонический адрес (иначе два хоста с одним контентом)
# Проверка мягкая: пока у www.<домен> нет A-записи, Caddy не выпустит на него
# сертификат, и требовать редирект нельзя. Отсутствие DNS — warn, а не провал.
www_redirect=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "https://www.${HOST}/")
case "$www_redirect" in
    "301 ${BASE}/" | "308 ${BASE}/") ok "www → https без www (${www_redirect})" ;;
    "000 "*) printf 'warn www.%s не отвечает: A-записи нет — проверка редиректа пропущена\n' "$HOST" ;;
    *) bad "www: ожидали 301 на ${BASE}/, получили '${www_redirect}'" ;;
esac
else
    printf 'warn SCHEME=%s: проверки http→https и www пропущены\n' "$SCHEME"
fi

# 3. Заголовки безопасности на главной
main_headers=$(head_of "${BASE}/")
for header in strict-transport-security content-security-policy \
              x-content-type-options referrer-policy \
              cross-origin-opener-policy cross-origin-resource-policy; do
    if printf '%s\n' "$main_headers" | grep -qi "^${header}:"; then
        ok "заголовок ${header}"
    else
        bad "нет заголовка ${header}"
    fi
done

# 4. Версия nginx не рассказывается
if printf '%s\n' "$main_headers" | grep -qiE '^server: *[a-z]+/'; then
    bad "в Server видна версия ($(printf '%s\n' "$main_headers" | grep -i '^server:'))"
else
    ok "версия сервера скрыта"
fi

# 5. canonical абсолютный и https
event_page=$(curl -s --max-time 10 "${BASE}/event")
if printf '%s\n' "$event_page" | grep -q "rel=\"canonical\" href=\"${BASE}/event\""; then
    ok "canonical = ${BASE}/event"
else
    bad "canonical на /event не ${BASE}/event (проверьте ZARUBA_PUBLIC_BASE_URL)"
fi

# 6. robots.txt ведёт на sitemap
if curl -s --max-time 10 "${BASE}/robots.txt" | grep -q "Sitemap: ${BASE}/sitemap.xml"; then
    ok "robots.txt указывает sitemap"
else
    bad "robots.txt не указывает ${BASE}/sitemap.xml"
fi

# 7. sitemap: 200 и только https-адреса
sitemap_code=$(code "${BASE}/sitemap.xml")
sitemap_body=$(curl -s --max-time 10 "${BASE}/sitemap.xml")
if [ "$sitemap_code" = "200" ]; then ok "sitemap.xml 200"; else bad "sitemap.xml ${sitemap_code}"; fi
if [ "$SCHEME" = "https" ]; then
    if printf '%s\n' "$sitemap_body" | grep -q '<loc>http://'; then
        bad "в sitemap есть http-адреса"
    else
        ok "в sitemap только https"
    fi
fi

# 8. Страница ошибки — HTML с навигацией, а не пустой ответ
notfound=$(code "${BASE}/net-takoy-stranicy")
if [ "$notfound" = "404" ]; then ok "несуществующий адрес → 404"; else bad "404-страница: ${notfound}"; fi

# 9. Чужой Host не подменяет домен сайта
foreign=$(code -H 'Host: evil.example' "${BASE}/")
if [ "$foreign" = "400" ]; then ok "чужой Host → 400"; else bad "чужой Host: ${foreign} вместо 400"; fi

# 10. /healthz снаружи закрыт
if [ "$SCHEME" = "https" ]; then
    healthz=$(code "${BASE}/healthz")
    if [ "$healthz" = "403" ]; then ok "/healthz закрыт снаружи"; else bad "/healthz снаружи: ${healthz} вместо 403"; fi
fi

# 11. Статика отдаётся с кэшем
static_headers=$(head_of "${BASE}/static/style.css")
if printf '%s\n' "$static_headers" | grep -qi '^HTTP/[0-9.]* 200'; then
    ok "static/style.css 200"
else
    bad "static/style.css не 200"
fi
if printf '%s\n' "$static_headers" | grep -qi '^cache-control:.*max-age=3600'; then
    ok "статика кэшируется на час"
else
    bad "у статики нет Cache-Control: max-age=3600"
fi

# 12. Сжатие (мягкая проверка: зависит от размера ответа)
if curl -sI -H 'Accept-Encoding: gzip' --max-time 10 "${BASE}/" | tr -d '\r' | grep -qi '^content-encoding: gzip'; then
    ok "gzip включён"
else
    printf 'warn gzip не увидели (не критично для маленькой страницы)\n'
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
    printf 'OK: %s прошёл все проверки\n' "$BASE"
    exit 0
fi
printf 'FAIL: %s проверок не прошло — сайт считать выложенным нельзя\n' "$fail"
exit 1
