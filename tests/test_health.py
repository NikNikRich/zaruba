from fastapi.testclient import TestClient

from zaruba import __version__


def test_healthz_returns_app_state(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "env": "local"}


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
