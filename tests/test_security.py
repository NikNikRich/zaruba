"""§6.20: заголовки безопасности, кэш статики и HSTS только в проде."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app
from zaruba.middleware import CSP, REQUEST_ID_HEADER, REQUEST_ID_RE


@contextmanager
def env_client(env: str) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(env=env))) as test_client:
        yield test_client


def test_security_headers_are_sent_with_every_page(client: TestClient) -> None:
    for path in ("/", "/event", "/ratings", "/privacy", "/sitemap.xml", "/nope"):
        response = client.get(path)
        headers = response.headers

        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin", path
        assert headers["x-frame-options"] == "DENY", path
        assert headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()", path
        assert headers["content-security-policy"] == CSP, path


def test_static_is_cacheable_but_pages_are_not(client: TestClient) -> None:
    """Статика живёт час; HTML — `no-cache`, чтобы правка YAML не ждала TTL."""

    static = client.get("/static/style.css")
    page = client.get("/")
    sitemap = client.get("/sitemap.xml")

    assert static.headers["cache-control"] == "public, max-age=3600"
    assert static.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-cache"
    assert sitemap.headers["cache-control"] == "public, max-age=3600", (
        "robots.txt и sitemap.xml кэшируются на час: они собираются из того же контента"
    )


def test_stylesheet_links_carry_content_hash(client: TestClient) -> None:
    """`?v=хеш` — иначе после правки CSS час отдаётся старая копия из кэша."""

    page = client.get("/").text

    assert re.search(r'/static/style\.css\?v=[0-9a-f]{8}"', page), page[:600]
    assert re.search(r'/static/print\.css\?v=[0-9a-f]{8}"', page)


def test_hsts_only_in_prod() -> None:
    with env_client("local") as client:
        assert "strict-transport-security" not in client.get("/").headers

    with env_client("prod") as client:
        assert (
            client.get("/").headers["strict-transport-security"]
            == "max-age=31536000; includeSubDomains"
        )


def test_csp_stays_strict_because_there_is_no_inline_js_or_css(client: TestClient) -> None:
    """`unsafe-inline` не нужен: стили в файлах, скриптов нет вовсе.

    Единственный `<script>` — блок JSON-LD: он не исполняется, поэтому CSP его
    не касается. Инлайновый `style="..."` в разметке сломал бы `style-src 'self'`,
    поэтому проверяем, что его нет (ссылки вида `?style=hip-hop` — не в счёт).
    """

    for path in ("/", "/glossary", "/schedule", "/ratings", "/about"):
        html = client.get(path).text
        assert not re.search(r"<script[^>]+src=", html), path
        assert not re.search(r"\sstyle=\"", html), path


def test_request_id_is_generated_and_echoed(client: TestClient) -> None:
    """`X-Request-ID` есть на каждом ответе; номер от прокси сохраняется как есть."""

    first = client.get("/").headers[REQUEST_ID_HEADER]
    second = client.get("/").headers[REQUEST_ID_HEADER]

    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert re.fullmatch(r"[0-9a-f]{32}", second)
    assert first != second, "номер запроса уникален, иначе по нему не найти запись"

    echoed = client.get("/", headers={REQUEST_ID_HEADER: "trace-proof-42"})
    assert echoed.headers[REQUEST_ID_HEADER] == "trace-proof-42"
    assert client.get("/nope").headers[REQUEST_ID_HEADER]


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    """Чужой заголовок принимается только безопасным: иначе он подделывает журнал."""

    for bad in ("short", "id with spaces", "x" * 65):
        got = client.get("/", headers={REQUEST_ID_HEADER: bad}).headers[REQUEST_ID_HEADER]
        assert got != bad
        assert re.fullmatch(r"[0-9a-f]{32}", got), bad


def test_request_id_pattern_blocks_header_injection() -> None:
    """Перевод строки в номере — это уже подделка записи журнала, а не номер."""

    assert REQUEST_ID_RE.fullmatch("trace-0123456789")
    assert not REQUEST_ID_RE.fullmatch("trace-0123456789\nX-Evil: 1")
    assert not REQUEST_ID_RE.fullmatch("")
    assert not REQUEST_ID_RE.fullmatch("x" * 65)
