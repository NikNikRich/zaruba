"""Рейтинги и методика: `/ratings` (`docs/06` §6.10).

Раздел закрыт флагом `ZARUBA_RATINGS_ENABLED`: пока механики голосования нет,
страница объясняет, почему таблиц не существует, и показывает методику.
Ссылку в шапку не ставим до включения — но страница уже существует, чтобы
в 404 не вело ничего.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..services.ratings import ratings_view
from ..templating import render

router = APIRouter()


@router.get("/ratings", response_class=HTMLResponse)
def ratings_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    bundle = load_latest(settings)

    return render(
        request,
        "pages/ratings.html",
        bundle=bundle,
        edition=bundle.edition,
        view=ratings_view(settings, bundle.edition),
        page_title="Рейтинги",
        # Раздел не запущен — индексировать нечего: методика есть, цифр нет.
        robots=None if settings.ratings_enabled else "noindex, follow",
    )
