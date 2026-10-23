"""Публичные информационные страницы: главная (§6.4), событие (§6.5) и площадка (§6.12)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..models import AgeGroup, DanceStyle
from ..services.edition import day_views, edition_notice, flat_blocks
from ..templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    return render(
        request,
        "pages/home.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        blocks=flat_blocks(bundle)[:3],
        participants=edition.participants[:8],
        studios=edition.studio_index,
    )


@router.get("/event", response_class=HTMLResponse)
def event_page(request: Request) -> HTMLResponse:
    """§6.5: короткий плотный текст, форматы, направления, цифры и хронология."""

    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition

    directions = [
        {"label": style.label, "count": len(matches)}
        for style in DanceStyle
        if (matches := [item for item in edition.nominations if item.style == style])
    ]
    ages = [
        {
            "label": group.label,
            "count": sum(1 for item in edition.participants if item.age_group == group),
        }
        for group in AgeGroup
        if any(item.age_group == group for item in edition.nominations)
    ]
    # §6.5: год без подтверждённых данных не выдумываем — строка появляется только по факту.
    chronology = [
        {
            "year": edition.edition,
            "dates": edition.dates,
            "feature": edition.note or "—",
            "source": edition.sources[0] if edition.sources else None,
        }
    ]

    return render(
        request,
        "pages/event.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        days=day_views(bundle),
        directions=directions,
        ages=ages,
        cities=sorted({item.city for item in edition.participants}),
        studios=sorted(edition.studios, key=lambda item: item.name),
        demo={
            "participants": sum(1 for item in edition.participants if item.demo),
            "performances": sum(1 for item in edition.performances if item.demo),
            "studios": sum(1 for item in edition.studios if item.demo),
        },
        chronology=chronology,
        page_title="О событии",
    )


@router.get("/venue", response_class=HTMLResponse)
def venue_page(request: Request) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    return render(
        request,
        "pages/venue.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        page_title="Площадка",
    )
