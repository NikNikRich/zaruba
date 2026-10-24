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

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
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
)


class SecurityHeadersMiddleware:
    """Заголовки на каждом ответе; `Cache-Control` — час для статики, ревалидация для HTML."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = scope["path"].startswith("/static/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
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
