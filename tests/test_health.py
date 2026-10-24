from fastapi.testclient import TestClient

from zaruba import __version__
from zaruba.config import Settings
from zaruba.main import create_app


def test_healthz_returns_app_state(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "env": "local"}


def test_healthz_reports_broken_content(broken_settings: Settings) -> None:
    """Битый YAML — не «здоров»: Docker и мониторинг должны это видеть."""

    with TestClient(create_app(broken_settings)) as test_client:
        response = test_client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "version": __version__, "env": "local"}


def test_static_assets_served(client: TestClient) -> None:
    css = client.get("/static/style.css")
    print_css = client.get("/static/print.css")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert print_css.status_code == 200


def test_api_docs_disabled(client: TestClient) -> None:
    """Служебные схемы наружу не отдаём: сайт информационный, не API."""

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
