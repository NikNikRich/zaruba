"""Каталоги этапа 2: участники и студии (`docs/06` §6.8, §6.9).

Шаблон ничего не фильтрует и не сортирует — он получает готовые карточки.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models import AgeGroup, DanceStyle, EditionBundle, Studio
from .edition import ParticipantView, PerformanceView, participant_view, performance_views


def normalize(text: str) -> str:
    """Поиск не должен зависеть от регистра, «ё» и лишних пробелов."""

    return " ".join(text.casefold().replace("ё", "е").split())


def matches(haystack: str, needle: str) -> bool:
    return normalize(needle) in normalize(haystack)


def styles_in(performances: Sequence[PerformanceView]) -> list[DanceStyle]:
    """Направления в порядке справочника `DanceStyle`, а не в порядке появления."""

    return [
        style
        for style in DanceStyle
        if any(view.nomination.style is style for view in performances)
    ]


def participant_views(bundles: Sequence[EditionBundle]) -> list[ParticipantView]:
    """Все участники всех выпусков, по алфавиту; свежий выпуск даёт карточку."""

    views: dict[str, ParticipantView] = {}
    for bundle in bundles:
        for participant in bundle.edition.participants:
            if participant.slug in views:
                continue
            view = participant_view(bundles, participant.slug)
            if view is not None:
                views[participant.slug] = view
    return sorted(views.values(), key=lambda view: normalize(view.participant.display_name))


def filter_participants(
    views: Sequence[ParticipantView],
    *,
    q: str | None = None,
    studio: str | None = None,
    city: str | None = None,
    age: AgeGroup | None = None,
    style: DanceStyle | None = None,
) -> list[ParticipantView]:
    """Фильтры каталога §6.8: имя, студия, город, направление, возраст."""

    result = list(views)
    if q:
        result = [
            view
            for view in result
            if matches(
                " ".join(
                    [
                        view.participant.display_name,
                        view.studio.name,
                        view.participant.city,
                        " ".join(item.nomination.title for item in view.performances),
                    ]
                ),
                q,
            )
        ]
    if studio:
        result = [view for view in result if view.participant.studio == studio]
    if city:
        result = [view for view in result if normalize(view.participant.city) == normalize(city)]
    if age is not None:
        result = [view for view in result if view.participant.age_group is age]
    if style is not None:
        result = [
            view
            for view in result
            if any(item.nomination.style is style for item in view.performances)
        ]
    return result


def participant_cities(views: Sequence[ParticipantView]) -> list[str]:
    return sorted({view.participant.city for view in views})


@dataclass(frozen=True)
class StudioView:
    """Студия вместе с её участниками и выступлениями за все выпуски (§6.9)."""

    studio: Studio
    participants: list[ParticipantView]
    performances: list[PerformanceView]

    @property
    def years(self) -> list[int]:
        return sorted({view.year for view in self.performances}, reverse=True)

    @property
    def styles(self) -> list[DanceStyle]:
        return styles_in(self.performances)

    @property
    def participant_names(self) -> str:
        return ", ".join(view.participant.display_name for view in self.participants)


def studio_views(bundles: Sequence[EditionBundle]) -> list[StudioView]:
    people = participant_views(bundles)
    all_performances = [view for bundle in bundles for view in performance_views(bundle)]

    studios: dict[str, Studio] = {}
    for bundle in bundles:
        for studio in bundle.edition.studios:
            studios.setdefault(studio.slug, studio)

    result: list[StudioView] = []
    for slug, studio in studios.items():
        members = [view for view in people if view.participant.studio == slug]
        shows = [
            view
            for view in all_performances
            if any(member_studio.slug == slug for _, member_studio in view.rows)
        ]
        result.append(StudioView(studio=studio, participants=members, performances=shows))
    return sorted(result, key=lambda view: normalize(view.studio.name))


def filter_studios(
    views: Sequence[StudioView], *, q: str | None = None, city: str | None = None
) -> list[StudioView]:
    result = list(views)
    if q:
        result = [
            view
            for view in result
            if matches(
                " ".join(
                    [
                        view.studio.name,
                        view.studio.city,
                        " ".join(style.label for style in view.styles),
                        view.participant_names,
                    ]
                ),
                q,
            )
        ]
    if city:
        result = [view for view in result if normalize(view.studio.city) == normalize(city)]
    return result


def studio_view(bundles: Sequence[EditionBundle], slug: str) -> StudioView | None:
    for view in studio_views(bundles):
        if view.studio.slug == slug:
            return view
    return None
