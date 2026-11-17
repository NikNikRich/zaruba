"""Результаты по судейским листам: `/results` (`docs/06` §6.24).

Одна страница — самый свежий разобранный выпуск. Архива результатов по годам
нет намеренно: файл появляется только там, где листы действительно разобраны,
а страница «2024 — данных нет» обещает архив, которого не будет.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_results_for, load_site, result_years
from ..services.edition import media_by_nomination
from ..services.results import results_view
from ..templating import render

router = APIRouter()


@router.get("/results", response_class=HTMLResponse)
def results_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    bundles = load_site(settings)
    latest = bundles[0]
    known = {bundle.year for bundle in bundles}
    year = next((item for item in result_years(settings) if item in known), None)
    bundle = next((item for item in bundles if item.year == year), latest)
    results = load_results_for(settings, year) if year is not None else None

    return render(
        request,
        "pages/results.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        view=results_view(bundle, results) if results is not None else None,
        # Привязка медиа к номинациям — из того же выпуска, чьи листы разобраны:
        # карточка 2026 года не должна «висеть» под таблицей 2025-го.
        media_for_nominations=media_by_nomination(bundle.edition),
        page_title="Результаты",
    )
