"""Нештатные запросы и аварии: страница вместо «пустого браузера» или JSON (§6.21)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app
from zaruba.middleware import REQUEST_ID_HEADER


def test_broken_content_is_a_page_not_plain_text(broken_settings: Settings) -> None:
    """Битый YAML: 503 HTML с выходом, а не `text/plain` от Starlette."""

    with TestClient(create_app(broken_settings)) as test_client:
        response = test_client.get("/")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers[REQUEST_ID_HEADER], "номер есть и на странице аварии"
    assert response.headers["retry-after"] == "300", "робот не должен долбить страницу ежесекундно"
    assert "Страница временно недоступна" in response.text
    assert 'name="robots" content="noindex, follow"' in response.text


def test_unexpected_error_is_a_page() -> None:
    """Сбой в коде — тоже страница: иначе человек видит пустое окно."""

    app: FastAPI = create_app()

    @app.get("/boom", include_in_schema=False)
    def boom() -> None:
        raise RuntimeError("тестовая авария")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "Страница временно недоступна" in response.text
    assert 'name="robots" content="noindex, follow"' in response.text


def test_error_page_names_the_request_id() -> None:
    """Номер запроса виден и в заголовке, и на странице: человеку есть что назвать.

    Обработчик 500 стоит снаружи пользовательских middleware, поэтому заголовок
    там ставится вручную — этот тест и проверяет, что он не потерялся.
    """

    app: FastAPI = create_app()

    @app.get("/boom", include_in_schema=False)
    def boom() -> None:
        raise RuntimeError("тестовая авария")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom", headers={REQUEST_ID_HEADER: "trace-boom-42"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "trace-boom-42"
    assert "trace-boom-42" in response.text


def test_error_page_has_no_json_ld(broken_settings: Settings) -> None:
    """На странице ошибки разметка не нужна: адрес заведомо нерабочий."""

    with TestClient(create_app(broken_settings)) as test_client:
        response = test_client.get("/")

    assert "application/ld+json" not in response.text


def test_head_is_allowed_and_returns_no_body(client: TestClient) -> None:
    """`curl -I` на страницу не должен получать 405 — там тот же GET, но без тела."""

    response = client.head("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-security-policy"]
    assert response.content == b""


def test_post_to_a_page_is_a_page_not_json(client: TestClient) -> None:
    """`POST` на адрес каталога — не повод отдавать машинный JSON."""

    response = client.post("/participants")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith("text/html")
    assert "Так нельзя" in response.text
    assert 'name="robots" content="noindex, follow"' in response.text
