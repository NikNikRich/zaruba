"""Страница результатов: судейские листы, сведённые в таблицы (`docs/06` §6.24).

Сервис не знает про `Request`: он получает выпуск и разобранные листы,
а возвращает всё, что нужно шаблону. Так «место без листа судьи» проверяется
тестом, а не глазами на странице.

Ключевое отличие от `/ratings`: здесь **не рейтинг и не голосование**,
а арифметика по фотографиям листов. Поэтому у каждой таблицы есть состав
жюри и оговорка о том, что место расчётное.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..models import (
    DataStatus,
    EditionBundle,
    Nomination,
    ResultNomination,
    ResultRow,
    Results,
    Source,
)


@dataclass(frozen=True)
class JudgeColumn:
    code: str
    name: str


@dataclass(frozen=True)
class Cell:
    """Клетка «судья × участник»: балл, «н/ч» или «—» (листа нет)."""

    value: float | None
    state: str
    flagged: bool = False


@dataclass(frozen=True)
class Line:
    participant: str
    cells: list[Cell]
    total: float | None
    place: int | None


@dataclass(frozen=True)
class NominationView:
    slug: str
    number: int
    day: int
    title: str
    subtitle: str
    judges: list[JudgeColumn]
    lines: list[Line]
    complete: bool
    missing_sheet: bool
    unreadable: int
    flagged: int
    participants: int


@dataclass(frozen=True)
class DayView:
    day: int
    date: date | None
    nominations: list[NominationView]


@dataclass(frozen=True)
class ResultsView:
    year: int
    title: str
    updated_at: datetime
    status: DataStatus
    source: Source | None
    days: list[DayView]
    nominations: int
    participants: int
    notes: list[str]


def _subtitle(nomination: Nomination) -> str:
    parts = [nomination.format.label, nomination.age_group.label]
    if nomination.levels:
        parts.append(nomination.levels_label)
    return " · ".join(parts)


def _cells(row: ResultRow, codes: list[str]) -> list[Cell]:
    cells = []
    for code in codes:
        if code in row.unreadable:
            cells.append(Cell(value=None, state="unreadable"))
        elif row.scores.get(code) is None:
            cells.append(Cell(value=None, state="missing"))
        else:
            cells.append(Cell(value=row.scores[code], state="value", flagged=code in row.flagged))
    return cells


def _nomination_view(
    item: ResultNomination, edition_nomination: Nomination, judges: dict[str, JudgeColumn]
) -> NominationView:
    codes = list(item.judges)
    missing_sheet = any(value is None for row in item.rows for value in row.scores.values())
    return NominationView(
        slug=item.nomination,
        number=item.number,
        day=item.day,
        title=edition_nomination.title,
        subtitle=_subtitle(edition_nomination),
        judges=[judges[code] for code in codes],
        lines=[
            Line(
                participant=row.participant,
                cells=_cells(row, codes),
                total=row.total,
                place=row.place,
            )
            for row in item.rows
        ],
        complete=item.complete,
        missing_sheet=missing_sheet,
        unreadable=sum(len(row.unreadable) for row in item.rows),
        flagged=sum(len(row.flagged) for row in item.rows),
        participants=len(item.rows),
    )


def results_view(bundle: EditionBundle, results: Results) -> ResultsView:
    """Готовое к рендеру представление: дни, номинации, строки, счётчики."""

    edition = bundle.edition
    judges = {judge.code: JudgeColumn(code=judge.code, name=judge.name) for judge in results.judges}
    nominations = edition.nomination_index
    days: dict[int, list[NominationView]] = {}
    for item in results.nominations:
        edition_nomination = nominations.get(item.nomination)
        if edition_nomination is None:
            # Проверка при загрузке уже это отсекла; страховка для рендера.
            continue
        days.setdefault(item.day, []).append(_nomination_view(item, edition_nomination, judges))
    return ResultsView(
        year=results.edition,
        title=edition.title,
        updated_at=results.updated_at,
        status=results.status,
        source=edition.source_index.get(results.source_id),
        days=[
            DayView(
                day=day,
                date=edition.dates[day - 1] if 0 < day <= len(edition.dates) else None,
                nominations=sorted(items, key=lambda view: view.number),
            )
            for day, items in sorted(days.items())
        ],
        nominations=len(results.nominations),
        participants=results.rows,
        notes=list(results.notes),
    )
