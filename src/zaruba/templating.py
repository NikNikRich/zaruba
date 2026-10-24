"""Шаблоны, фильтры дат и общий контекст рендера.

Шаблон не считает данные сам (`docs/06` §6.18): всё готовит роутер,
фильтры здесь нужны только для форматирования.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.routing import NoMatchFound

from .services.seo import breadcrumbs_json_ld, json_ld_script

PACKAGE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

# Хеш CSS в ссылке: статика кэшируется на час, и без версии после релиза
# часть посетителей остаётся со старым файлом.
ASSET_VERSION = hashlib.sha1(
    b"".join((PACKAGE_DIR / "static" / name).read_bytes() for name in ("style.css", "print.css"))
).hexdigest()[:8]

MONTHS: tuple[str, ...] = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Пункты шапки из §6.3. В меню попадает только то, что уже реализовано:
# ссылка в 404 хуже, чем отсутствие пункта. Ключ — имя эндпоинта,
# а не путь: в FastAPI 0.161 включённые роутеры не отдают `route.path`.
NAV_ITEMS: tuple[dict[str, str], ...] = (
    {"endpoint": "event_page", "label": "О событии"},
    {"endpoint": "history_page", "label": "История"},
    {"endpoint": "schedule", "label": "Расписание"},
    {"endpoint": "performances", "label": "Выступления"},
    {"endpoint": "participants", "label": "Участники"},
    {"endpoint": "ratings_page", "label": "Рейтинги"},
    {"endpoint": "venue_page", "label": "Площадка"},
    {"endpoint": "search", "label": "Поиск"},
)

# Служебные страницы (§6.2 п.18, §6.3): отдельный ряд футера, не в шапке.
LEGAL_ITEMS: tuple[dict[str, str], ...] = (
    {"endpoint": "about_page", "label": "О сайте"},
    {"endpoint": "rules_page", "label": "Правила"},
    {"endpoint": "privacy_page", "label": "Данные"},
    {"endpoint": "takedown_page", "label": "Удаление данных"},
)

# Второй ряд ссылок в футере: каталоги P1 (§6.9–6.15). Тоже только реализованное.
FOOTER_ITEMS: tuple[dict[str, str], ...] = (
    {"endpoint": "studios", "label": "Студии"},
    {"endpoint": "judges_page", "label": "Жюри"},
    {"endpoint": "tickets_page", "label": "Билеты"},
    {"endpoint": "glossary_page", "label": "Глоссарий"},
    {"endpoint": "faq_page", "label": "Вопросы и ответы"},
    {"endpoint": "search", "label": "Поиск"},
)


def fmt_date(value: date | datetime | None) -> str:
    """«6 декабря» — без года, для блоков внутри выпуска."""

    if value is None:
        return ""
    return f"{value.day} {MONTHS[value.month]}"


def fmt_dt(value: date | datetime | None) -> str:
    """«6 декабря 2025»."""

    if value is None:
        return ""
    return f"{value.day} {MONTHS[value.month]} {value.year}"


def fmt_time(value: time | datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%H:%M")


def fmt_dates(values: list[date] | None) -> str:
    """«6–7 декабря 2025» или «30 ноября — 1 декабря 2025»."""

    if not values:
        return ""
    first, last = values[0], values[-1]
    if len(values) == 1:
        return fmt_dt(first)
    if first.month == last.month and first.year == last.year:
        return f"{first.day}–{last.day} {MONTHS[last.month]} {last.year}"
    return f"{fmt_date(first)} — {fmt_dt(last)}"


def fmt_weekday(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return WEEKDAYS[value.weekday()]


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 выступление, 2 выступления, 5 выступлений."""

    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def money(value: int | None) -> str:
    """«1 000» — разряды неразрывным пробелом, чтобы цена не рвалась по строкам.

    Цены приходят из «Положения» целыми рублями, поэтому копеек и валюты здесь
    нет: знак «₽» ставит шаблон.
    """

    if value is None:
        return ""
    return f"{value:,}".replace(",", "\u00a0")


templates.env.filters.update(
    {
        "fmt_date": fmt_date,
        "fmt_dt": fmt_dt,
        "fmt_time": fmt_time,
        "fmt_dates": fmt_dates,
        "fmt_weekday": fmt_weekday,
        "plural": plural,
        "money": money,
    }
)


def _links(app: FastAPI, items: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    """Только существующие эндпоинты: `url_path_for` сам скажет, чего нет."""

    links: list[dict[str, str]] = []
    for item in items:
        try:
            url = app.url_path_for(item["endpoint"])
        except NoMatchFound:
            continue
        links.append({"url": url, "label": item["label"]})
    return links


def build_nav(app: FastAPI) -> list[dict[str, str]]:
    return _links(app, NAV_ITEMS)


def build_footer(app: FastAPI) -> list[dict[str, str]]:
    return _links(app, FOOTER_ITEMS)


def build_legal(app: FastAPI) -> list[dict[str, str]]:
    return _links(app, LEGAL_ITEMS)


def base_url(request: Request) -> str:
    """Публичный адрес сайта: настройка важнее заголовков прокси.

    `request.base_url` зависит от `--proxy-headers` и `X-Forwarded-Proto`:
    забыли флаг — и canonical, `robots.txt` и `sitemap.xml` молча уезжают на
    `http://127.0.0.1:8000`. Явный адрес снимает эту зависимость, а на локальном
    запуске и в тестах остаётся адрес запроса.
    """

    configured: str = request.app.state.settings.public_base_url
    return (configured or str(request.base_url)).rstrip("/")


def _json_ld_blocks(request: Request, context: dict[str, Any], status_code: int) -> list[str]:
    """JSON-LD страницы: то, что передал роутер, плюс крошки (§6.19).

    Крошки строятся здесь, а не в каждом роутере: иначе половина страниц
    осталась бы без `BreadcrumbList`. На страницах ошибок разметки нет —
    адрес там заведомо несуществующий.
    """

    blocks: list[dict[str, Any]] = list(context.get("json_ld") or [])
    if status_code >= 400:
        return [json_ld_script(block) for block in blocks]

    if not any(block.get("@type") == "BreadcrumbList" for block in blocks):
        crumbs = breadcrumbs_json_ld(
            base_url(request),
            request.url.path,
            context.get("page_title"),
            [
                *request.app.state.nav,
                *request.app.state.footer_links,
                *request.app.state.legal_links,
            ],
        )
        if crumbs is not None:
            blocks.append(crumbs)
    return [json_ld_script(block) for block in blocks]


def render(
    request: Request, template_name: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    # §6.19: canonical — на успешных страницах и без query, чтобы фильтры не
    # плодили дубли. На 404/405/422/503 адрес — это адрес самой ошибки:
    # canonical там подсказывал бы роботу «главную версию» несуществующей
    # страницы и уводил бы её в индекс вместо страницы с объяснением.
    canonical = f"{base_url(request)}{request.url.path}" if status_code < 400 else None
    payload: dict[str, Any] = {
        "nav": request.app.state.nav,
        "footer_links": request.app.state.footer_links,
        "legal_links": request.app.state.legal_links,
        "settings": request.app.state.settings,
        "current_path": request.url.path,
        "canonical": canonical,
        "asset_v": ASSET_VERSION,
        "robots": None,
    }
    payload.update(context)
    # json_ld ставим после update: роутер передаёт словари, шаблон ждёт строки.
    payload["json_ld"] = _json_ld_blocks(request, context, status_code)
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)
