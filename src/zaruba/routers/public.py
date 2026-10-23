"""Публичные информационные страницы: главная (§6.4) и площадка (§6.12)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..services.edition import edition_notice, flat_blocks
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
