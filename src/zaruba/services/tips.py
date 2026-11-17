"""Выбор рекламного оффера для слота (§6.27).

Сервис не знает про `Request`: страница передаёт список из `offers.yaml`,
а решение «что показать» проверяется без HTTP-клиента.
"""

from __future__ import annotations

from datetime import date

from ..config import Settings
from ..content import load_preview_service_tips, load_service_tips
from ..models import ServiceTip, TipSlot


def _best(tips: list[ServiceTip], slot: TipSlot, moment: date) -> ServiceTip | None:
    candidates = [
        tip
        for tip in tips
        if tip.slot is slot and (tip.valid_until is None or tip.valid_until >= moment)
    ]
    return min(candidates, key=lambda tip: (tip.priority, tip.slug), default=None)


def tip_for_slot(
    tips: list[ServiceTip],
    slot: TipSlot,
    *,
    today: date | None = None,
    fallback: list[ServiceTip] | None = None,
) -> ServiceTip | None:
    """Самый приоритетный действующий оффер слота; `None` — показывать нечего.

    `fallback` — демонстрационные офферы: берутся, только если среди основных
    нет действующего. Так боевой оффер всегда важнее примера, а просроченный
    пример не занимает слот.

    `today` — параметр, а не `date.today()` внутри: тест не должен зависеть
    от дня прогона и ломаться 1 января.
    """

    moment = today or date.today()
    chosen = _best(tips, slot, moment)
    if chosen is None and fallback:
        return _best(fallback, slot, moment)
    return chosen


def resolve_tip(
    settings: Settings, slot: TipSlot, *, today: date | None = None
) -> ServiceTip | None:
    """Что показать в слоте: боевой оффер, иначе демо в preview.

    Демо-файл не читается без `preview_enabled`: боевой сайт не должен даже
    открывать файл с придуманным `erid`, не говоря о рендере.
    """

    if not settings.service_tips_visible:
        return None
    demo = load_preview_service_tips(settings) if settings.preview_enabled else None
    return tip_for_slot(load_service_tips(settings), slot, today=today, fallback=demo)
