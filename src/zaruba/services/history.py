"""Хронология выпусков: архив до 2025 + загруженные выпуски (`docs/06` §6.5).

Год без подтверждённых данных не появляется: пустая строка в таблице читается
как «пропустили», а не «не было». Поэтому 2020, 2021 и 2023 отсутствуют —
их нет ни в архиве сообщества, ни в наших выпусках.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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


def _edition_row(bundle: EditionBundle, live_year: int) -> HistoryRow:
    edition = bundle.edition
    live = edition.edition == live_year
    link = "/event" if live or not edition.performances else "/performances"
    return HistoryRow(
        year=edition.edition,
        title=edition.title,
        dates=list(edition.dates),
        note=edition.note,
        source=edition.sources[0] if edition.sources else None,
        live=live,
        link=link,
    )


def history_rows(
    bundles: Sequence[EditionBundle], archive: Sequence[HistoryEntry]
) -> list[HistoryRow]:
    """Свежие выпуски сверху; архивные записи — ниже и без ссылок в никуда."""

    if not bundles:
        return []

    live_year = bundles[0].year
    loaded_years = {bundle.year for bundle in bundles}
    rows = [_edition_row(bundle, live_year) for bundle in bundles]
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
