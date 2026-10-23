"""Каталог выступлений и карточки: `/performances`, `/performances/{slug}`, `/participants/{slug}`
(`docs/06` §6.7, §6.8). Шаблоны ничего не считают — все данные готовит роутер.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import ceil
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest, load_site
from ..models import AgeGroup, DanceFormat, DanceStyle
from ..services.edition import (
    block_for_nomination,
    edition_notice,
    filter_performances,
    participant_view,
    performance_detail,
    siblings,
)
from ..templating import render

router = APIRouter()

# §6.7: пагинация по 24, бесконечной ленты нет — её нельзя доскроллить и проиндексировать.
PAGE_SIZE = 24


def _options(items: Iterable[tuple[str, str]], current: str | None) -> list[dict[str, object]]:
    return [
        {"value": value, "label": label, "selected": value == current} for value, label in items
    ]


def _page_url(path: str, params: dict[str, str], page: int) -> str:
    """Ссылка на страницу каталога с сохранением фильтров; первую — без `page`."""

    query = dict(params)
    if page > 1:
        query["page"] = str(page)
    return f"{path}?{urlencode(query)}" if query else path


@router.get("/performances", response_class=HTMLResponse)
def performances(
    request: Request,
    day: Annotated[int | None, Query(ge=1, le=3)] = None,
    style: DanceStyle | None = None,
    age: AgeGroup | None = None,
    fmt: Annotated[DanceFormat | None, Query(alias="format")] = None,
    studio: str | None = None,
    video: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition

    views = filter_performances(
        bundle, day=day, style=style, age=age, fmt=fmt, studio=studio, video=video
    )
    total = len(views)
    pages = max(1, ceil(total / PAGE_SIZE))
    current_page = min(page, pages)
    start = (current_page - 1) * PAGE_SIZE
    filtered = any(value is not None for value in (day, style, age, fmt, studio, video))
    query = {key: value for key, value in request.query_params.items() if key != "page"}

    return render(
        request,
        "pages/performances.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        views=views[start : start + PAGE_SIZE],
        total=total,
        filtered=filtered,
        # §6.19: фильтры и пагинация закрыты от индексации, canonical — на чистый каталог.
        robots="noindex, follow" if filtered or current_page > 1 else None,
        current={
            "day": day,
            "style": style,
            "age": age,
            "format": fmt,
            "studio": studio,
            "video": video,
        },
        filters={
            "days": _options(
                [(str(number), f"День {number}") for number in range(1, edition.days + 1)],
                str(day) if day else None,
            ),
            "styles": _options(
                [
                    (item.value, item.label)
                    for item in DanceStyle
                    if any(nomination.style == item for nomination in edition.nominations)
                ],
                style.value if style else None,
            ),
            "ages": _options(
                [
                    (item.value, item.label)
                    for item in AgeGroup
                    if any(nomination.age_group == item for nomination in edition.nominations)
                ],
                age.value if age else None,
            ),
            "formats": _options(
                [
                    (item.value, item.label)
                    for item in DanceFormat
                    if any(nomination.format == item for nomination in edition.nominations)
                ],
                fmt.value if fmt else None,
            ),
            "studios": _options(
                [(item.slug, item.name) for item in sorted(edition.studios, key=lambda s: s.name)],
                studio,
            ),
        },
        pager={
            "page": current_page,
            "pages": pages,
            "links": [
                {"page": number, "url": _page_url("/performances", query, number)}
                for number in range(1, pages + 1)
            ],
        }
        if pages > 1
        else None,
        page_title="Выступления",
    )


@router.get("/performances/{slug}", response_class=HTMLResponse)
def performance_page(request: Request, slug: str) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    view = performance_detail(bundle, slug)
    if view is None:
        raise HTTPException(status_code=404, detail=f"выступление «{slug}» не найдено")

    source = edition.source_index.get(view.performance.source_id)
    return render(
        request,
        "pages/performance.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        view=view,
        block=block_for_nomination(bundle, view.nomination.slug),
        more=siblings(bundle, view),
        source=source,
        page_title=f"{view.nomination.title} — {view.participant_names}",
    )


@router.get("/participants/{slug}", response_class=HTMLResponse)
def participant_page(request: Request, slug: str) -> HTMLResponse:
    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    view = participant_view(bundles, slug)
    if view is None:
        raise HTTPException(status_code=404, detail=f"участник «{slug}» не найден")

    return render(
        request,
        "pages/participant.html",
        bundle=latest,
        edition=latest.edition,
        venue=latest.venue,
        notice=edition_notice(latest),
        view=view,
        # §6.8: ребёнок без согласия — только инициалы и закрытая от индексации страница.
        robots=None if view.participant.is_public_person else "noindex, nofollow",
        page_title=view.participant.display_name,
    )
