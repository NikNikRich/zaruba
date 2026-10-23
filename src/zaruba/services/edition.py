"""Производные представления выпуска: готовит роутер, шаблон только рисует (§6.18)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import (
    DataStatus,
    EditionBundle,
    Nomination,
    Performance,
    ScheduleBlock,
)


@dataclass(frozen=True)
class NominationRow:
    nomination: Nomination
    performances: list[Performance]


@dataclass(frozen=True)
class BlockView:
    day: int
    date: date
    block: ScheduleBlock
    nominations: list[NominationRow]

    @property
    def performances_count(self) -> int:
        return sum(len(row.performances) for row in self.nominations)


@dataclass(frozen=True)
class DayView:
    day: int
    date: date
    blocks: list[BlockView]


def _rows(bundle: EditionBundle, block: ScheduleBlock) -> list[NominationRow]:
    edition = bundle.edition
    index = edition.nomination_index
    return [
        NominationRow(nomination=index[slug], performances=edition.performances_for_nomination(slug))
        for slug in block.nominations
    ]


def day_views(bundle: EditionBundle) -> list[DayView]:
    edition = bundle.edition
    days: list[DayView] = []
    for day_index, day_date in enumerate(edition.dates, start=1):
        blocks = [
            BlockView(day=day_index, date=day_date, block=block, nominations=_rows(bundle, block))
            for block in edition.blocks_for_day(day_index)
        ]
        days.append(DayView(day=day_index, date=day_date, blocks=blocks))
    return days


def flat_blocks(bundle: EditionBundle) -> list[BlockView]:
    return [block for day in day_views(bundle) for block in day.blocks]


def edition_notice(bundle: EditionBundle, today: date | None = None) -> str | None:
    """Жёлтая полоса §6.3: честное предупреждение вместо «всё подтверждено»."""

    edition = bundle.edition
    today = today or date.today()
    if edition.status is not DataStatus.CONFIRMED:
        return "Ключевые факты не подтверждены организаторами: проверяйте по ссылкам на источники."
    if edition.dates[-1] < today:
        return (
            f"Выпуск {edition.edition} завершён. Даты следующего сезона не объявлены — "
            f"ниже архив {edition.edition} года."
        )
    return None
