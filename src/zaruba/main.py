"""Сборка приложения: настройки, шаблоны, статика, роутеры."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .content import ContentError, load_site
from .errors import query_errors
from .middleware import (
    REQUEST_ID_HEADER,
    HeadMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from .templating import PACKAGE_DIR, build_footer, build_legal, build_nav, render

logger = logging.getLogger("zaruba")


def _request_id(request: Request) -> str:
    """Номер запроса из `RequestIdMiddleware`; пустая строка, если middleware нет."""

    return str(getattr(request.state, "request_id", ""))


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
    app.add_middleware(HeadMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_prod)
    # Последним: `add_middleware` кладёт его снаружи остальных, и номер запроса
    # есть уже в обработчиках ошибок — иначе страница сбоя осталась бы без номера.
    app.add_middleware(RequestIdMiddleware)
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

    @app.exception_handler(405)
    def method_not_allowed_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        """405 — тоже страница: `POST` на адрес каталога не должен отдавать JSON."""

        return render(
            request,
            "pages/405.html",
            status_code=405,
            page_title="Так нельзя",
            robots="noindex, follow",
        )

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

    @app.exception_handler(ContentError)
    def content_error_page(request: Request, exc: ContentError) -> HTMLResponse:
        """Битый YAML — авария, но посетителю нужна страница, а не `text/plain`.

        503, а не 500: данные исправят и выпуск перезапустят, поэтому адрес
        остаётся живым. `noindex` — чтобы ошибка не попала в поиск.
        """

        request_id = _request_id(request)
        logger.error("контент не прошёл проверку на %s [%s]: %s", request.url.path, request_id, exc)
        return render(
            request,
            "pages/500.html",
            status_code=503,
            page_title="Данные обновляются",
            robots="noindex, follow",
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    def unexpected_error_page(request: Request, exc: Exception) -> HTMLResponse:
        """Сбой в коде не должен выглядеть как пустое окно браузера.

        Обработчик стоит снаружи пользовательских middleware (так устроен
        Starlette), поэтому `X-Request-ID` здесь ставится вручную: иначе номер
        был бы в журнале, но не в ответе, и человеку нечего было бы назвать.
        """

        request_id = _request_id(request)
        logger.error("необработанная ошибка на %s [%s]", request.url.path, request_id, exc_info=exc)
        response = render(
            request,
            "pages/500.html",
            status_code=500,
            page_title="Сбой на сервере",
            robots="noindex, follow",
            request_id=request_id,
        )
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz(response: Response) -> dict[str, str]:
        """Живость включает контент: битый YAML — это не «здоров».

        Иначе Docker и мониторинг видят 200, пока все страницы отвечают 503.
        """

        try:
            load_site(settings)
        except ContentError as exc:
            logger.error("healthz: контент не прошёл проверку: %s", exc)
            response.status_code = 503
            return {"status": "degraded", "version": __version__, "env": settings.env}
        return {"status": "ok", "version": __version__, "env": settings.env}

    return app


app = create_app()
