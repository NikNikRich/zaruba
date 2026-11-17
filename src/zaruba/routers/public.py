"""Публичные информационные страницы: главная (§6.4), событие (§6.5) и площадка (§6.12)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_history, load_knowledge, load_latest, load_site
from ..models import AgeGroup, DanceFormat, DanceStyle, FaqGroup, GlossaryKind, NominationLevel
from ..services.edition import (
    catalogue_bundle,
    day_views,
    edition_notice,
    fee_prices,
    flat_blocks,
    master_class_date,
    master_class_pass,
    master_class_prices,
    nominations_by_day,
    registration_state,
)
from ..services.history import history_rows
from ..services.seo import event_json_ld, faq_json_ld, glossary_json_ld
from ..templating import base_url, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    bundle = load_latest(settings)
    edition = bundle.edition
    archive = catalogue_bundle(load_site(settings))
    return render(
        request,
        "pages/home.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        blocks=flat_blocks(bundle)[:3],
        days=nominations_by_day(bundle),
        nominations=edition.nominations,
        master_classes=master_class_prices(edition),
        master_class_day=master_class_date(edition),
        fees=fee_prices(edition),
        tickets=edition.tickets,
        judges=edition.judges,
        participants=edition.participants[:8],
        studios=edition.studio_index,
        registration_state=registration_state(edition),
        archive=archive if archive.year != edition.edition else None,
        # §6.19: Event на главной — даты, место и ссылка на билеты.
        json_ld=[event_json_ld(bundle, base_url(request))],
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
    formats = [
        {"value": item.value, "label": item.label, "count": len(matches)}
        for item in DanceFormat
        if (matches := [row for row in edition.nominations if row.format == item])
    ]
    levels = [
        {"value": item.value, "label": item.label, "count": len(matches)}
        for item in NominationLevel
        if (matches := [row for row in edition.nominations if item in row.levels])
    ]
    ages = [
        {
            "label": group.label,
            "count": sum(1 for item in edition.nominations if item.age_group == group),
        }
        for group in AgeGroup
        if any(item.age_group == group for item in edition.nominations)
    ]
    # §6.5: хронология = архив до 2025 + загруженные выпуски; год без фактов не появляется.
    settings = request.app.state.settings
    chronology = history_rows(load_site(settings), load_history(settings.history_file))

    return render(
        request,
        "pages/event.html",
        bundle=bundle,
        edition=edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        days=day_views(bundle),
        nominations_days=nominations_by_day(bundle),
        directions=directions,
        formats=formats,
        levels=levels,
        ages=ages,
        cities=sorted({item.city for item in edition.participants}),
        studios=sorted(edition.studios, key=lambda item: item.name),
        demo={
            "participants": sum(1 for item in edition.participants if item.demo),
            "performances": sum(1 for item in edition.performances if item.demo),
            "studios": sum(1 for item in edition.studios if item.demo),
        },
        chronology=chronology,
        rules=edition.rules,
        page_title="О событии",
        json_ld=[event_json_ld(bundle, base_url(request))],
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
        criteria=edition.criteria,
        master_classes=master_class_prices(edition),
        master_class_day=master_class_date(edition),
        pass_price=master_class_pass(edition),
        page_title="Жюри",
    )


@router.get("/tickets", response_class=HTMLResponse)
def tickets_page(request: Request) -> HTMLResponse:
    """§6.13: мы не продаём билеты. Цена — только из официального анонса."""

    bundle = load_latest(request.app.state.settings)
    edition = bundle.edition
    first_ticket = edition.tickets[0] if edition.tickets else None
    fee_views = fee_prices(edition)
    ticket_source = (
        edition.source_index.get(first_ticket.source_id)
        if first_ticket is not None and first_ticket.source_id is not None
        else None
    )
    if ticket_source is None:
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
        # Ссылка на форму участия — из YAML, а не строка в шаблоне.
        registration_source=edition.source_index.get("registration-form"),
        tickets=edition.tickets,
        fees=fee_views,
        fee_early_until=min((view.price.early_until for view in fee_views), default=None),
        fee_early_active=any(view.price.early_active for view in fee_views),
        registration_until=edition.registration_until,
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
        # §6.19: DefinedTerm для глоссария.
        json_ld=[glossary_json_ld(base_url(request), glossary)],
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
        # §6.19: FAQPage — ровно те вопросы, что видит посетитель.
        json_ld=[faq_json_ld(base_url(request), knowledge.faq)],
    )
