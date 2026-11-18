"""Сборка приложения: настройки, шаблоны, статика, роутеры."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings, get_settings
from .content import ContentError, load_all_results, load_site
from .errors import query_errors
from .middleware import (
    REQUEST_ID_HEADER,
    HeadMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from .templating import PACKAGE_DIR, build_footer, build_legal, build_nav, render

logger = logging.getLogger("zaruba")

# Пауза для робота и uptime-проверки на 503 «данные обновляются»: контент
# чинят минутами, а не секундами, и опрос раз в секунду тут ничего не ускорит.
RETRY_AFTER_SECONDS = "300"


def _request_id(request: Request) -> str:
    """Номер запроса из `RequestIdMiddleware`; пустая строка, если middleware нет."""

    return str(getattr(request.state, "request_id", ""))


def _allowed_hosts(settings: Settings) -> list[str]:
    """Хосты, которым верим: публичный адрес сайта плюс loopback для `/healthz`.

    Заголовок `Host` — это ввод посетителя, а из него собираются `canonical`,
    `robots.txt` и `sitemap.xml`. Без проверки запрос с чужим `Host` заставляет
    сайт объявить своим доменом что угодно, а кэш прокси потом раздаёт эти
    адреса настоящим посетителям. Пустой список — адрес сайта не задан, и
    проверять нечего: так работают локальный запуск и тесты.

    `www.` считается тем же сайтом: редирект на основной домен делает nginx,
    а если его конфигурация другая, посетитель получит страницу с правильным
    `canonical`, а не `400`.
    """

    if not settings.public_base_url:
        return []
    host = urlsplit(settings.public_base_url).hostname or ""
    hosts = {host, "localhost", "127.0.0.1"}
    if host and not host.startswith("www."):
        hosts.add(f"www.{host}")
    # loopback нужен healthcheck-у Docker: он ходит на 127.0.0.1, а не на домен.
    return sorted(hosts - {""})


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
    allowed_hosts = _allowed_hosts(settings)
    if allowed_hosts:
        # Добавлен раньше остальных middleware — значит, работает внутри них:
        # отказ по чужому `Host` уходит с заголовками безопасности и номером.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    elif settings.is_prod:
        logger.warning(
            "ZARUBA_PUBLIC_BASE_URL не задан: canonical, robots.txt и sitemap.xml "
            "собираются из заголовка Host, а подмена Host уводит их на чужой домен"
        )
    app.add_middleware(HeadMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware, hsts=settings.is_prod, miniapp=settings.max_miniapp
    )
    # Последним: `add_middleware` кладёт его снаружи остальных, и номер запроса
    # есть уже в обработчиках ошибок — иначе страница сбоя осталась бы без номера.
    app.add_middleware(RequestIdMiddleware)
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    from .routers import (
        catalogue,
        history,
        legal,
        media,
        public,
        ratings,
        results,
        schedule,
        seo,
    )

    app.include_router(public.router)
    app.include_router(history.router)
    app.include_router(ratings.router)
    app.include_router(results.router)
    app.include_router(media.router)
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
        остаётся живым. `noindex` — чтобы ошибка не попала в поиск,
        `Retry-After` — чтобы робот и uptime-проверка не долбили страницу
        каждую секунду, пока контент чинят.
        """

        request_id = _request_id(request)
        logger.error("контент не прошёл проверку на %s [%s]: %s", request.url.path, request_id, exc)
        response = render(
            request,
            "pages/500.html",
            status_code=503,
            page_title="Данные обновляются",
            robots="noindex, follow",
            request_id=request_id,
        )
        response.headers["Retry-After"] = RETRY_AFTER_SECONDS
        return response

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
            load_all_results(settings)
        except ContentError as exc:
            logger.error("healthz: контент не прошёл проверку: %s", exc)
            response.status_code = 503
            return {"status": "degraded", "version": __version__, "env": settings.env}
        return {"status": "ok", "version": __version__, "env": settings.env}

    return app


app = create_app()
