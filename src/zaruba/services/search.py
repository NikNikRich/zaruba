"""Поиск по сайту (`docs/06` §6.16): пока нет БД — проход по загруженным данным.

Правила: результаты сгруппированы по типу, по `limit` в группе; пустой
результат обязан содержать выход, поэтому каталоги принимают `?q=`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode

from ..models import EditionBundle, GlossaryEntry
from .catalogs import participant_views, studio_views
from .edition import block_for_nomination, performance_views

MIN_QUERY = 2


def normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _score(text: str, needle: str) -> int:
    """0 — нет совпадения, 2 — начало слова, 1 — внутри слова."""

    haystack = normalize(text)
    if not needle:
        return 0
    if haystack.startswith(needle) or f" {needle}" in haystack:
        return 2
    return 1 if needle in haystack else 0


@dataclass(frozen=True)
class SearchHit:
    title: str
    subtitle: str | None
    url: str
    badge: str | None = None


@dataclass(frozen=True)
class SearchGroup:
    key: str
    label: str
    hits: list[SearchHit] = field(default_factory=list)
    total: int = 0
    more_url: str | None = None


def _ranked(pairs: list[tuple[int, SearchHit]], limit: int) -> list[SearchHit]:
    ordered = sorted(pairs, key=lambda pair: (-pair[0], pair[1].title))
    return [hit for _, hit in ordered][:limit]


def _more_url(path: str, query: str) -> str:
    return f"{path}?{urlencode({'q': query})}"


def search_site(
    bundle: EditionBundle,
    bundles: Sequence[EditionBundle],
    query: str,
    glossary: Sequence[GlossaryEntry],
    limit: int = 5,
) -> list[SearchGroup]:
    needle = normalize(query)
    if len(needle) < MIN_QUERY:
        return []

    groups: list[SearchGroup] = []

    people: list[tuple[int, SearchHit]] = []
    for person in participant_views(bundles):
        text = " ".join(
            [
                person.participant.display_name,
                person.studio.name,
                person.participant.city,
                person.participant.age_group.label,
                " ".join(item.nomination.title for item in person.performances),
            ]
        )
        score = _score(text, needle)
        if score:
            people.append(
                (
                    score,
                    SearchHit(
                        title=person.participant.display_name,
                        subtitle=f"{person.studio.name} · {person.participant.city}",
                        url=f"/participants/{person.participant.slug}",
                        badge="пример" if person.participant.demo else None,
                    ),
                )
            )
    groups.append(
        SearchGroup(
            key="participants",
            label="Участники",
            hits=_ranked(people, limit),
            total=len(people),
            more_url=_more_url("/participants", query),
        )
    )

    shows: list[tuple[int, SearchHit]] = []
    for edition_bundle in bundles:
        for show in performance_views(edition_bundle):
            text = " ".join(
                [
                    show.performance.title,
                    show.nomination.title,
                    show.participant_names,
                    show.studio_names,
                ]
            )
            score = _score(text, needle)
            if score:
                shows.append(
                    (
                        score,
                        SearchHit(
                            title=show.performance.title,
                            subtitle=(
                                f"День {show.day} · {show.nomination.title} · "
                                f"{show.participant_names}"
                            ),
                            url=f"/performances/{show.performance.slug}",
                            badge="пример" if show.performance.demo else None,
                        ),
                    )
                )
    groups.append(
        SearchGroup(
            key="performances",
            label="Выступления",
            hits=_ranked(shows, limit),
            total=len(shows),
        )
    )

    nominations: list[tuple[int, SearchHit]] = []
    show_years = {bundle.year for bundle in bundles}
    for edition_bundle in bundles:
        for nomination in edition_bundle.edition.nominations:
            score = _score(f"{nomination.title} {nomination.stage}", needle)
            if not score:
                continue
            block = block_for_nomination(edition_bundle, nomination.slug)
            starts_at, ends_at = nomination.starts_at, nomination.ends_at
            when = (
                f"{starts_at.strftime('%H:%M')}–{ends_at.strftime('%H:%M')}"
                if starts_at is not None and ends_at is not None
                else "время уточняется"
            )
            year = f" · {edition_bundle.year}" if len(show_years) > 1 else ""
            nominations.append(
                (
                    score,
                    SearchHit(
                        title=nomination.title,
                        subtitle=f"День {nomination.day or '—'} · {when}{year}",
                        url=f"/schedule#block-{block.slug}" if block else "/schedule",
                    ),
                )
            )
    groups.append(
        SearchGroup(
            key="nominations",
            label="Номинации",
            hits=_ranked(nominations, limit),
            total=len(nominations),
        )
    )

    studios: list[tuple[int, SearchHit]] = []
    for studio_card in studio_views(bundles):
        text = " ".join(
            [
                studio_card.studio.name,
                studio_card.studio.city,
                " ".join(style.label for style in studio_card.styles),
            ]
        )
        score = _score(text, needle)
        if score:
            studios.append(
                (
                    score,
                    SearchHit(
                        title=studio_card.studio.name,
                        subtitle=(
                            f"{studio_card.studio.city} · "
                            f"участников: {len(studio_card.participants)} · "
                            f"выступлений: {len(studio_card.performances)}"
                        ),
                        url=f"/studios/{studio_card.studio.slug}",
                        badge="пример" if studio_card.studio.demo else None,
                    ),
                )
            )
    groups.append(
        SearchGroup(
            key="studios",
            label="Студии",
            hits=_ranked(studios, limit),
            total=len(studios),
            more_url=_more_url("/studios", query),
        )
    )

    terms: list[tuple[int, SearchHit]] = []
    for entry in glossary:
        score = _score(" ".join([entry.term, entry.what, entry.example or ""]), needle)
        if score:
            terms.append(
                (
                    score,
                    SearchHit(
                        title=entry.term,
                        subtitle=entry.kind.label,
                        url=f"/glossary#{entry.anchor}",
                    ),
                )
            )
    groups.append(
        SearchGroup(
            key="glossary",
            label="Глоссарий",
            hits=_ranked(terms, limit),
            total=len(terms),
        )
    )

    return [group for group in groups if group.hits]
