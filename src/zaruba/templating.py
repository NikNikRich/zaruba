"""Шаблоны, фильтры дат и общий контекст рендера.

Шаблон не считает данные сам (`docs/06` §6.18): всё готовит роутер,
фильтры здесь нужны только для форматирования.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.routing import NoMatchFound

PACKAGE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

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
    {"endpoint": "schedule", "label": "Расписание"},
    {"endpoint": "performances", "label": "Выступления"},
    {"endpoint": "participants", "label": "Участники"},
    {"endpoint": "ratings", "label": "Рейтинги"},
    {"endpoint": "venue_page", "label": "Площадка"},
    {"endpoint": "search", "label": "Поиск"},
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


templates.env.filters.update(
    {
        "fmt_date": fmt_date,
        "fmt_dt": fmt_dt,
        "fmt_time": fmt_time,
        "fmt_dates": fmt_dates,
        "fmt_weekday": fmt_weekday,
        "plural": plural,
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


def render(
    request: Request, template_name: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    # §6.19: canonical на всех страницах — без query, чтобы фильтры не плодили дубли.
    canonical = f"{str(request.base_url).rstrip('/')}{request.url.path}"
    payload: dict[str, Any] = {
        "nav": request.app.state.nav,
        "footer_links": request.app.state.footer_links,
        "settings": request.app.state.settings,
        "current_path": request.url.path,
        "canonical": canonical,
        "robots": None,
    }
    payload.update(context)
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)
