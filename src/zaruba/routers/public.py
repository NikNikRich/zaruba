"""Публичные информационные страницы: главная (§6.4), событие (§6.5) и площадка (§6.12)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_knowledge, load_latest
from ..models import AgeGroup, DanceStyle, FaqGroup, GlossaryKind
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
        {"value": style.value, "label": style.label, "count": len(matches)}
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


@router.get("/judges", response_class=HTMLResponse)
def judges_page(request: Request) -> HTMLResponse:
    """§6.11: кто судит и что вообще оценивают. Пустые поля — честное «уточняется»."""

    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    blocks = {block.slug: block for block in edition.blocks}
    cards = [
        {
            "judge": judge,
            "blocks": [blocks[slug] for slug in judge.blocks if slug in blocks],
            "source": edition.source_index.get(judge.source_id) if judge.source_id else None,
        }
        for judge in edition.judges
    ]
    return render(
        request,
        "pages/judges.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        cards=cards,
        page_title="Жюри",
    )


@router.get("/tickets", response_class=HTMLResponse)
def tickets_page(request: Request) -> HTMLResponse:
    """§6.13: мы не продаём билеты. Цена — только из официального анонса."""

    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    ticket_source = next(
        (source for source in edition.sources if "kassy" in source.url),
        edition.sources[0] if edition.sources else None,
    )
    return render(
        request,
        "pages/tickets.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        ticket_source=ticket_source,
        sales_closed=edition.dates[-1] < date.today(),
        page_title="Билеты",
    )


@router.get("/glossary", response_class=HTMLResponse)
def glossary_page(request: Request) -> HTMLResponse:
    """§6.15: девять направлений и термины чемпионата — SEO и материал для викторины."""

    bundle = load_latest(request.app.state.settings)
    glossary = load_knowledge(request.app.state.settings).glossary
    return render(
        request,
        "pages/glossary.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        styles=[entry for entry in glossary if entry.kind is GlossaryKind.STYLE],
        terms=[entry for entry in glossary if entry.kind is GlossaryKind.TERM],
        page_title="Глоссарий",
    )


@router.get("/faq", response_class=HTMLResponse)
def faq_page(request: Request) -> HTMLResponse:
    """§6.15: 12 вопросов в четырёх группах; ответ «уточняется» помечен статусом."""

    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    knowledge = load_knowledge(request.app.state.settings)
    groups = [
        {
            "key": group.value,
            "label": group.label,
            "questions": [item for item in knowledge.faq if item.group is group],
        }
        for group in FaqGroup
        if any(item.group is group for item in knowledge.faq)
    ]
    return render(
        request,
        "pages/faq.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        groups=groups,
        sources=edition.source_index,
        total=len(knowledge.faq),
        page_title="Вопросы и ответы",
    )
