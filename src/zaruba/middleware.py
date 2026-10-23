"""Заголовки безопасности и кэш статики (`docs/06` §6.20).

Сайт не содержит исполняемого JS: единственный `<script>` на странице — блок
JSON-LD, а он не исполняется. Поэтому CSP закрыт почти полностью, а не «как
получится»: если завтра появится скрипт, его придётся добавить в политику явно.

HSTS включается только в prod: на локальном `http://127.0.0.1:8000` заголовок
бесполезен и мешает проверять сайт по http.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
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
HSTS = "max-age=31536000; includeSubDomains"

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", CSP),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
)


class SecurityHeadersMiddleware:
    """Заголовки на каждом ответе; `Cache-Control` — только для `/static`."""

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
                if is_static:
                    headers.setdefault("Cache-Control", STATIC_CACHE)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_wrapper)
