"""Фото и видео: ссылки на внешние хостинги без встроенных плееров (`docs/06` §6.25).

Страница отдельная, а не блок на главной: карточек может быть много, а главная
обязана оставаться ответом «что, когда, где» без длинной ленты. Материала нет —
страница объясняет почему (§6.21), а не показывает пустую сетку.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..services.edition import edition_notice, media_groups
from ..templating import render

router = APIRouter()


@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    groups = media_groups(bundle.edition)
    return render(
        request,
        "pages/media.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        groups=groups,
        total=len(bundle.edition.media),
        page_title="Фото и видео",
    )
