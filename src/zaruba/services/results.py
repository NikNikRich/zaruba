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
    ScoreFeed,
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


# --- Подтверждённые оценки судей (`docs/06` §6.28) --------------------------


@dataclass(frozen=True)
class ScoreLine:
    participant: str
    studio: str | None
    performance: str | None
    score: float
    place: int | None


@dataclass(frozen=True)
class ScoreNomination:
    slug: str
    title: str
    day: int | None
    number: int | None
    lines: list[ScoreLine]


@dataclass(frozen=True)
class ScoreBoard:
    edition: int
    updated_at: datetime
    nominations: list[ScoreNomination]
    rows: int


def scores_view(bundle: EditionBundle, feed: ScoreFeed) -> ScoreBoard | None:
    """Подтверждённые оценки, сгруппированные по номинациям.

    `None` — ни одной подтверждённой строки: страница не рисует пустую таблицу
    и не обещает протокол, которого ещё нет. Порядок строк — место, затем балл:
    так таблица читается сверху вниз, а строки без места не рвут её середину.
    """

    published = feed.published
    if not published:
        return None
    edition = bundle.edition
    index = edition.nomination_index
    # Номер номинации внутри дня — порядок строк выпуска, как в результатах.
    order: dict[str, int] = {}
    per_day: dict[int, int] = {}
    for nomination in edition.nominations:
        if nomination.demo or nomination.day is None:
            continue
        per_day[nomination.day] = per_day.get(nomination.day, 0) + 1
        order[nomination.slug] = per_day[nomination.day]

    grouped: dict[str, list[ScoreLine]] = {}
    for row in published:
        found = index.get(row.nomination)
        if found is None:
            # Проверка при загрузке это уже отсекла; страховка для рендера.
            continue
        grouped.setdefault(row.nomination, []).append(
            ScoreLine(
                participant=row.participant,
                studio=row.studio,
                performance=row.performance,
                score=row.score,
                place=row.place,
            )
        )

    nominations: list[ScoreNomination] = []
    for slug, lines in grouped.items():
        found = index[slug]
        lines.sort(key=lambda line: (line.place is None, line.place or 0, -line.score))
        nominations.append(
            ScoreNomination(
                slug=slug,
                title=found.title,
                day=found.day,
                number=order.get(slug),
                lines=lines,
            )
        )
    nominations.sort(key=lambda item: (item.day or 0, item.number or 0))
    return ScoreBoard(
        edition=feed.edition,
        updated_at=feed.updated_at,
        nominations=nominations,
        rows=sum(len(item.lines) for item in nominations),
    )
