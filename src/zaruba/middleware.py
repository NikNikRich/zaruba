"""Заголовки безопасности, кэш статики и семантика HEAD (`docs/06` §6.20).

Сайт не содержит исполняемого JS: единственный `<script>` на странице — блок
JSON-LD, а он не исполняется. Поэтому CSP закрыт почти полностью, а не «как
получится»: если завтра появится скрипт, его придётся добавить в политику явно.

HSTS включается только в prod: на локальном `http://127.0.0.1:8000` заголовок
бесполезен и мешает проверять сайт по http.
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# `frame-src`, `object-src` и `worker-src` перечислены явно, хотя `default-src`
# их и так покрывает: часть браузеров проверяет эти директивы по отдельности,
# а плагин, фрейм или воркер — ровно тот случай, когда нужен прямой запрет,
# а не вывод из умолчания.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "worker-src 'none'; "
    "manifest-src 'self'; "
    "base-uri 'self'"
)

# Origin MAX Bridge: единственный внешний скрипт сайта, и только в режиме
# Mini App (docs/09). Адрес взят из документации MAX
# (dev.max.ru/docs/webapps/bridge): `max-web-app.js` отдаётся оттуда и
# определяет `window.WebApp`.
MAX_BRIDGE_ORIGIN = "https://st.max.ru"

# Отличия от обычной политики ровно два, и оба следуют из способа запуска:
#   * `script-src` разрешает MAX Bridge — без него `BackButton` и `openLink`
#     не работают, а инлайновый инициализатор потребовал бы `unsafe-inline`;
#   * `frame-ancestors` разрешает клиенту MAX встроить страницу. При этом
#     `X-Frame-Options: DENY` в этом режиме не отправляется: старые браузеры
#     проверяют его раньше CSP и закрыли бы Mini App.
CSP_MAX_MINIAPP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    f"script-src 'self' {MAX_BRIDGE_ORIGIN}; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self' https://max.ru https://*.max.ru; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "worker-src 'none'; "
    "manifest-src 'self'; "
    "base-uri 'self'"
)

STATIC_CACHE = "public, max-age=3600"
# HTML без `max-age`: браузер обязан спросить сервер. Без этого прокси и браузеры
# кэшируют страницы эвристически, и после правки YAML человек видит старую афишу.
PAGE_CACHE = "no-cache"
HSTS = "max-age=31536000; includeSubDomains"

# Идентификатор запроса: `X-Request-ID` от прокси принимаем только в предсказуемом
# виде — он попадает в журнал и в ответ, поэтому переводы строк и мусор отсекаем.
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")


SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", CSP),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
    # Чужая страница не должна получить ссылку на наше окно через `window.opener`:
    # через неё подменяют адрес в исходной вкладке (таб-нэппинг, фишинг).
    ("Cross-Origin-Opener-Policy", "same-origin"),
    # Наши CSS и картинки нельзя вставить ресурсом на чужой странице.
    ("Cross-Origin-Resource-Policy", "same-origin"),
    # Наследие Flash и PDF-клиентов: кросс-доменные файлы сайта — только свои.
    ("X-Permitted-Cross-Domain-Policies", "none"),
)


def security_headers(*, miniapp: bool = False) -> tuple[tuple[str, str], ...]:
    """Заголовки ответа; в режиме Mini App меняются политика и запрет фрейма.

    Остальные строки одинаковы: `nosniff`, реферальная политика и запреты
    плагина не зависят от того, где открыта страница.
    """

    if not miniapp:
        return SECURITY_HEADERS
    return tuple(
        ("Content-Security-Policy", CSP_MAX_MINIAPP)
        if name == "Content-Security-Policy"
        else (name, value)
        for name, value in SECURITY_HEADERS
        if name != "X-Frame-Options"
    )


class SecurityHeadersMiddleware:
    """Заголовки на каждом ответе; `Cache-Control` — час для статики, ревалидация для HTML."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False, miniapp: bool = False) -> None:
        self.app = app
        self.hsts = hsts
        self.headers = security_headers(miniapp=miniapp)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = scope["path"].startswith("/static/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers:
                    headers.setdefault(name, value)
                headers.setdefault("Cache-Control", STATIC_CACHE if is_static else PAGE_CACHE)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class HeadMiddleware:
    """`HEAD` отдаёт те же заголовки, что `GET`, но без тела.

    FastAPI не добавляет `HEAD` к `APIRoute`, поэтому `curl -I` и часть
    аптайм-проверок получали 405 на странице, которая открывается обычным
    переходом. Здесь запрос выполняется как `GET`, а тело отбрасывается —
    `Content-Length` при этом остаётся честным.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


class RequestIdMiddleware:
    """`X-Request-ID` на каждом ответе: по нему запрос находят в журнале.

    Номер приходит от прокси (`nginx` умеет `$request_id`) или генерируется
    здесь. Чужой заголовок принимается только в безопасном виде: подставленный
    клиентом мусор сделал бы журнал бесполезным, а перевод строки — подделкой
    записи. Тот же номер кладётся в `scope["state"]`, поэтому страница сбоя
    может показать его человеку, а обработчик ошибки — записать в журнал.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
