"""Хронология выпусков: архив до 2025 + загруженные выпуски (`docs/06` §6.5).

Год без подтверждённых данных не появляется: пустая строка в таблице читается
как «пропустили», а не «не было». Поэтому 2020 и 2021 отсутствуют — их нет
ни в архиве сообщества, ни в наших выпусках.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from ..models import EditionBundle, HistoryEntry, Source


@dataclass(frozen=True)
class HistoryRow:
    year: int
    title: str
    dates: list[date]
    note: str | None
    source: Source | None
    live: bool
    # Куда ведёт год: текущий — на /event, архив с выступлениями — в каталог.
    link: str | None
    # Реальные номинации выпуска: каркас каталога (`demo: true`) сюда не входит.
    nominations: list[str] = field(default_factory=list)
    nomination_source: Source | None = None
    # Страница результатов существует только для разобранного выпуска и только
    # одна: `/results` показывает самый свежий. Ссылка «результаты» у года,
    # которого там нет, вела бы на чужие баллы.
    results_url: str | None = None


def _edition_row(
    bundle: EditionBundle, live_year: int, results_year: int | None = None
) -> HistoryRow:
    edition = bundle.edition
    live = edition.edition == live_year
    real_nominations = [item for item in edition.nominations if not item.demo]
    # Ссылка в каталог честна только там, где есть настоящие выступления:
    # демонстрационный каркас не повод звать зрителя «смотреть выступления».
    has_real_performances = any(not item.demo for item in edition.performances)
    if live:
        link = "/event"
    elif has_real_performances:
        link = "/performances"
    else:
        link = None
    source_id = next(
        (item.source_id for item in real_nominations if item.source_id is not None), None
    )
    return HistoryRow(
        year=edition.edition,
        title=edition.title,
        dates=list(edition.dates),
        note=edition.note,
        source=edition.sources[0] if edition.sources else None,
        live=live,
        link=link,
        nominations=[item.title for item in real_nominations],
        nomination_source=edition.source_index.get(source_id) if source_id else None,
        results_url="/results" if edition.edition == results_year else None,
    )


def history_rows(
    bundles: Sequence[EditionBundle],
    archive: Sequence[HistoryEntry],
    results_year: int | None = None,
) -> list[HistoryRow]:
    """Свежие выпуски сверху; архивные записи — ниже и без ссылок в никуда."""

    if not bundles:
        return []

    live_year = bundles[0].year
    loaded_years = {bundle.year for bundle in bundles}
    rows = [_edition_row(bundle, live_year, results_year) for bundle in bundles]
    rows += [
        HistoryRow(
            year=entry.year,
            title=entry.title,
            dates=list(entry.dates),
            note=entry.note,
            source=entry.source,
            live=False,
            link=None,
        )
        for entry in archive
        # Если год появился в content/editions, главнее выпуск: архив не дублирует.
        if entry.year not in loaded_years
    ]
    return sorted(rows, key=lambda row: row.year, reverse=True)
