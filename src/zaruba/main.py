"""Сборка приложения: настройки, шаблоны, статика, роутеры."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .templating import PACKAGE_DIR, build_footer, build_nav, render


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="ЗАРУБА — фан-сайт чемпионата",
        description="Неофициальный информационный сайт чемпионата по современным танцам в Тюмени",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    from .routers import catalogue, public, schedule

    app.include_router(public.router)
    app.include_router(schedule.router)
    app.include_router(catalogue.router)
    app.state.nav = build_nav(app)
    app.state.footer_links = build_footer(app)

    @app.exception_handler(404)
    def not_found_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        """404 — обычная страница сайта: у пустого состояния обязан быть выход (§6.21)."""

        return render(request, "pages/404.html", status_code=404, page_title="Страница не найдена")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.env}

    return app


app = create_app()
