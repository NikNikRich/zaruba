"""Сборка приложения: настройки, шаблоны, статика, роутеры."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .errors import query_errors
from .middleware import SecurityHeadersMiddleware
from .templating import PACKAGE_DIR, build_footer, build_legal, build_nav, render


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
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_prod)
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    from .routers import catalogue, history, legal, public, ratings, schedule, seo

    app.include_router(public.router)
    app.include_router(history.router)
    app.include_router(ratings.router)
    app.include_router(legal.router)
    app.include_router(schedule.router)
    app.include_router(catalogue.router)
    app.include_router(seo.router)
    app.state.nav = build_nav(app)
    app.state.footer_links = build_footer(app)
    app.state.legal_links = build_legal(app)

    @app.exception_handler(404)
    def not_found_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        """404 — обычная страница сайта: у пустого состояния обязан быть выход (§6.21)."""

        return render(request, "pages/404.html", status_code=404, page_title="Страница не найдена")

    @app.exception_handler(RequestValidationError)
    def invalid_query_page(request: Request, exc: RequestValidationError) -> HTMLResponse:
        """422 — тоже страница сайта.

        Неверный фильтр (`/schedule?day=9`, `/participants?age=abc`) — обычная
        ситуация: ссылку могли собрать руками. JSON с английским текстом pydantic
        в этом месте нарушает §6.21 «у пустого состояния есть выход», поэтому
        отвечаем HTML-ом с объяснением и ссылками.
        """

        return render(
            request,
            "pages/422.html",
            status_code=422,
            page_title="Проверьте параметры",
            errors=query_errors(exc),
            robots="noindex, follow",
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.env}

    return app


app = create_app()
