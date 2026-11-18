"""Расписание: дни, блоки, номинации, якоря `#block-...` (§6.6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ..content import load_alerts, load_latest
from ..models import TipSlot
from ..services.alerts import active_alert
from ..services.edition import (
    day_views,
    edition_notice,
    master_class_date,
    master_class_prices,
    master_class_slot,
    nominations_by_day,
)
from ..services.tips import resolve_tip
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

    settings = request.app.state.settings
    service_tip = resolve_tip(settings, TipSlot.SCHEDULE_TOP)

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
        master_classes=master_class_prices(bundle.edition),
        master_class_day=master_class_date(bundle.edition),
        master_class_slot=master_class_slot(bundle.edition),
        active_day=day,
        service_tip=service_tip,
        alert=active_alert(load_alerts(settings)),
        page_title="Расписание",
    )
