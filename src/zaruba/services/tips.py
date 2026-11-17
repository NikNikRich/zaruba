"""Выбор рекламного оффера для слота (§6.27).

Сервис не знает про `Request`: страница передаёт список из `offers.yaml`,
а решение «что показать» проверяется без HTTP-клиента.
"""

from __future__ import annotations

from datetime import date

from ..models import ServiceTip, TipSlot


def tip_for_slot(
    tips: list[ServiceTip], slot: TipSlot, *, today: date | None = None
) -> ServiceTip | None:
    """Самый приоритетный действующий оффер слота; `None` — показывать нечего.

    `today` — параметр, а не `date.today()` внутри: тест не должен зависеть
    от дня прогона и ломаться 1 января.
    """

    moment = today or date.today()
    candidates = [
        tip
        for tip in tips
        if tip.slot is slot and (tip.valid_until is None or tip.valid_until >= moment)
    ]
    return min(candidates, key=lambda tip: (tip.priority, tip.slug), default=None)
