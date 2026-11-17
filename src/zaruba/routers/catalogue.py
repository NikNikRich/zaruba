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

from ..content import load_knowledge, load_latest, load_site
from ..models import AgeGroup, DanceFormat, DanceStyle
from ..services.catalogs import (
    filter_participants,
    filter_studios,
    participant_cities,
    participant_views,
    studio_view,
    studio_views,
)
from ..services.edition import (
    block_for_nomination,
    catalogue_bundle,
    edition_notice,
    filter_performances,
    participant_view,
    performance_detail,
    registration_state,
    siblings,
)
from ..services.search import MIN_QUERY, search_site
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
    bundle = catalogue_bundle(load_site(request.app.state.settings))
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
        registration_state=registration_state(edition),
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
        catalogue_year=bundle.year,
        live_year=load_latest(request.app.state.settings).year,
    )


@router.get("/performances/{slug}", response_class=HTMLResponse)
def performance_page(request: Request, slug: str) -> HTMLResponse:
    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    found = next(
        (
            (bundle, view)
            for bundle in bundles
            if (view := performance_detail(bundle, slug)) is not None
        ),
        None,
    )
    if found is None:
        raise HTTPException(status_code=404, detail=f"выступление «{slug}» не найдено")
    bundle, view = found
    edition = bundle.edition

    source = (
        edition.source_index.get(view.performance.source_id)
        if view.performance.source_id is not None
        else None
    )
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
        live_year=latest.year,
        page_title=f"{view.nomination.title} — {view.participant_names}",
    )


@router.get("/participants/{slug}", response_class=HTMLResponse)
def participant_page(request: Request, slug: str) -> HTMLResponse:
    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    view = participant_view(bundles, slug)
    if view is None:
        raise HTTPException(status_code=404, detail=f"участник «{slug}» не найден")

    # Шапка и предупреждение — по выпуску, в котором человек выступал, а не по свежему.
    bundle = next((item for item in bundles if item.year == view.year), latest)
    return render(
        request,
        "pages/participant.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        view=view,
        # §6.8: ребёнок без согласия — только инициалы и закрытая от индексации страница.
        robots=None if view.participant.is_public_person else "noindex, nofollow",
        page_title=view.participant.display_name,
    )


@router.get("/participants", response_class=HTMLResponse)
def participants(
    request: Request,
    q: Annotated[str | None, Query(max_length=80)] = None,
    studio: str | None = None,
    city: str | None = None,
    age: AgeGroup | None = None,
    style: DanceStyle | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    """§6.8: каталог участников — поиск, фильтры, алфавит вместо фан-рейтинга."""

    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    catalogue = catalogue_bundle(bundles)
    edition = catalogue.edition

    everyone = participant_views(bundles)
    views = filter_participants(everyone, q=q, studio=studio, city=city, age=age, style=style)
    total = len(views)
    pages = max(1, ceil(total / PAGE_SIZE))
    current_page = min(page, pages)
    start = (current_page - 1) * PAGE_SIZE
    filtered = any(value for value in (q, studio, city, age, style))
    query = {key: value for key, value in request.query_params.items() if key != "page"}

    return render(
        request,
        "pages/participants.html",
        bundle=catalogue,
        edition=edition,
        venue=catalogue.venue,
        notice=edition_notice(catalogue),
        views=views[start : start + PAGE_SIZE],
        total=total,
        filtered=filtered,
        registration_state=registration_state(edition),
        robots="noindex, follow" if filtered or current_page > 1 else None,
        current={
            "q": q or "",
            "studio": studio,
            "city": city,
            "age": age,
            "style": style,
        },
        filters={
            "studios": _options(
                [(item.slug, item.name) for item in sorted(edition.studios, key=lambda s: s.name)],
                studio,
            ),
            "cities": _options([(value, value) for value in participant_cities(everyone)], city),
            "ages": _options(
                [
                    (item.value, item.label)
                    for item in AgeGroup
                    if any(view.participant.age_group is item for view in everyone)
                ],
                age.value if age else None,
            ),
            "styles": _options(
                [
                    (item.value, item.label)
                    for item in DanceStyle
                    if any(nomination.style is item for nomination in edition.nominations)
                ],
                style.value if style else None,
            ),
        },
        pager=(
            {
                "page": current_page,
                "pages": pages,
                "links": [
                    {"page": number, "url": _page_url("/participants", query, number)}
                    for number in range(1, pages + 1)
                ],
            }
            if pages > 1
            else None
        ),
        page_title="Участники",
        catalogue_year=edition.edition,
        live_year=latest.year,
    )


@router.get("/studios", response_class=HTMLResponse)
def studios(
    request: Request,
    q: Annotated[str | None, Query(max_length=80)] = None,
    city: str | None = None,
) -> HTMLResponse:
    """§6.9: каталог студий. Ссылки на студии — только с `nofollow`."""

    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    everyone = studio_views(bundles)
    views = filter_studios(everyone, q=q, city=city)
    filtered = bool(q or city)

    return render(
        request,
        "pages/studios.html",
        bundle=latest,
        edition=latest.edition,
        venue=latest.venue,
        notice=edition_notice(latest),
        views=views,
        total=len(views),
        filtered=filtered,
        registration_state=registration_state(latest.edition),
        robots="noindex, follow" if filtered else None,
        current={"q": q or "", "city": city},
        filters={
            "cities": _options(
                [(value, value) for value in sorted({item.studio.city for item in everyone})],
                city,
            )
        },
        page_title="Студии",
    )


@router.get("/studios/{slug}", response_class=HTMLResponse)
def studio_page(request: Request, slug: str) -> HTMLResponse:
    bundles = load_site(request.app.state.settings)
    latest = bundles[0]
    view = studio_view(bundles, slug)
    if view is None:
        raise HTTPException(status_code=404, detail=f"студия «{slug}» не найдена")

    # Предупреждение — по выпуску, где студия заявлена, а не по свежему.
    bundle = next((item for item in bundles if slug in item.edition.studio_index), latest)
    shows = sorted(
        view.performances,
        key=lambda item: (item.performance.scheduled_at, item.performance.order_no),
        reverse=True,
    )
    return render(
        request,
        "pages/studio.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        view=view,
        performances=shows,
        page_title=view.studio.name,
    )


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: Annotated[str, Query(max_length=80)] = "",
) -> HTMLResponse:
    """§6.16: один input, результаты по группам, пустой ответ — с выходом."""

    settings = request.app.state.settings
    bundles = load_site(settings)
    latest = bundles[0]
    glossary = load_knowledge(settings).glossary
    query = q.strip()
    groups = search_site(latest, bundles, query, glossary)

    return render(
        request,
        "pages/search.html",
        bundle=latest,
        edition=latest.edition,
        venue=latest.venue,
        notice=edition_notice(latest),
        q=query,
        groups=groups,
        too_short=0 < len(query) < MIN_QUERY,
        total=sum(group.total for group in groups),
        # §6.19: выдача поиска не индексируется — иначе дубли каталогов.
        robots="noindex, follow" if query else None,
        page_title="Поиск",
    )
