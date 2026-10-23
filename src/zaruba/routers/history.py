"""Архив по годам: `/history` (`docs/06` §6.5)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_history, load_site
from ..services.history import history_rows
from ..templating import render

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    """Хронология собирается из двух источников: архив YAML + загруженные выпуски."""

    settings = request.app.state.settings
    bundles = load_site(settings)
    archive = load_history(settings.history_file)
    latest = bundles[0]

    return render(
        request,
        "pages/history.html",
        bundle=latest,
        edition=latest.edition,
        venue=latest.venue,
        rows=history_rows(bundles, archive),
        page_title="История",
    )
