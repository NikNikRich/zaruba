"""Расписание: дни, блоки, номинации, якоря `#block-...` (§6.6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..services.edition import day_views, edition_notice, nominations_by_day
from ..templating import render

router = APIRouter()


@router.get("/schedule", response_class=HTMLResponse)
def schedule(
    request: Request,
    day: Annotated[int | None, Query(ge=1, le=3)] = None,
) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)
    days = day_views(bundle)
    all_days = days
    if day is not None:
        days = [item for item in days if item.day == day]

    nominations_days = nominations_by_day(bundle)
    if day is not None:
        nominations_days = [item for item in nominations_days if item.day == day]

    return render(
        request,
        "pages/schedule.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        notice=edition_notice(bundle),
        days=days,
        all_days=all_days,
        nominations_days=nominations_days,
        master_classes=bundle.edition.master_classes,
        active_day=day,
        page_title="Расписание",
    )
