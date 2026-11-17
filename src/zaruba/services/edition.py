"""Производные представления выпуска: готовит роутер, шаблон только рисует (§6.18)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time

from ..models import (
    AgeGroup,
    DanceFormat,
    DanceStyle,
    DataStatus,
    Edition,
    EditionBundle,
    Fee,
    MasterClass,
    Nomination,
    Participant,
    Performance,
    ScheduleBlock,
    Studio,
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

    @property
    def nominations_count(self) -> int:
        return sum(len(block.nominations) for block in self.blocks)


def _rows(bundle: EditionBundle, block: ScheduleBlock) -> list[NominationRow]:
    edition = bundle.edition
    index = edition.nomination_index
    return [
        NominationRow(
            nomination=index[slug], performances=edition.performances_for_nomination(slug)
        )
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


@dataclass(frozen=True)
class DayNominationView:
    """День выпуска без блоков: у 2026 тайминг ещё не опубликован, а номинации уже есть."""

    day: int
    date: date
    nominations: list[Nomination]


def nominations_by_day(bundle: EditionBundle) -> list[DayNominationView]:
    edition = bundle.edition
    views: list[DayNominationView] = []
    for day_index, day_date in enumerate(edition.dates, start=1):
        rows = [item for item in edition.nominations if item.day == day_index]
        views.append(DayNominationView(day=day_index, date=day_date, nominations=rows))
    return views


def catalogue_bundle(bundles: Sequence[EditionBundle]) -> EditionBundle:
    """Выпуск, по которому есть что показать в каталоге.

    Свежий выпуск может быть ещё без заявок (2026: приём до 07.12). Тогда
    каталог показывает предыдущий выпуск с явной пометкой года в шапке,
    а не пустую страницу.
    """

    return next((bundle for bundle in bundles if bundle.edition.performances), bundles[0])


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


@dataclass(frozen=True)
class Price:
    """Тариф «до срока / после»: на странице показываем действующий, а не оба вслепую."""

    early: int
    late: int
    early_until: date
    today: date

    @property
    def early_active(self) -> bool:
        # «До 12 ноября» включает сам день: ранняя цена ещё действует.
        return self.today <= self.early_until

    @property
    def expired(self) -> bool:
        return not self.early_active

    @property
    def current(self) -> int:
        return self.early if self.early_active else self.late


@dataclass(frozen=True)
class FeePrice:
    fee: Fee
    price: Price


@dataclass(frozen=True)
class MasterClassPrice:
    master_class: MasterClass
    price: Price


def _price(early: int, late: int, early_until: date, today: date | None) -> Price:
    return Price(early=early, late=late, early_until=early_until, today=today or date.today())


def fee_prices(edition: Edition, today: date | None = None) -> list[FeePrice]:
    """Взносы с действующей ставкой: шаблон не сравнивает даты сам (§6.13)."""

    return [
        FeePrice(
            fee=fee,
            price=_price(fee.amount_early, fee.amount_late, fee.early_until, today),
        )
        for fee in edition.fees
    ]


def master_class_prices(edition: Edition, today: date | None = None) -> list[MasterClassPrice]:
    return [
        MasterClassPrice(
            master_class=item,
            price=_price(item.price_early, item.price_late, item.early_until, today),
        )
        for item in edition.master_classes
    ]


def master_class_pass(edition: Edition, today: date | None = None) -> Price | None:
    """Абонемент на все классы: срок берётся из самих классов, а не из шаблона."""

    if edition.master_class_pass_early is None or edition.master_class_pass_late is None:
        return None
    deadlines = [item.early_until for item in edition.master_classes]
    if not deadlines:
        return None
    return _price(
        edition.master_class_pass_early,
        edition.master_class_pass_late,
        min(deadlines),
        today,
    )


def master_class_slot(edition: Edition) -> tuple[time, time] | None:
    """Границы мастер-классов: «10:00–16:00» собирается из времени классов."""

    if not edition.master_classes:
        return None
    return (
        min(item.starts_at for item in edition.master_classes),
        max(item.ends_at for item in edition.master_classes),
    )


def master_class_date(edition: Edition) -> date | None:
    """Дата мастер-классов: день первого класса в датах выпуска."""

    if not edition.master_classes:
        return None
    day = min(item.day for item in edition.master_classes)
    return edition.dates[day - 1]


def registration_state(edition: Edition, today: date | None = None) -> str:
    """Статус приёма заявок для пустых состояний: open / closed / unknown."""

    if edition.registration_until is None:
        return "unknown"
    return "open" if (today or date.today()) <= edition.registration_until else "closed"


@dataclass(frozen=True)
class PerformanceView:
    """Выступление вместе с номинацией, участниками и студиями (§6.7)."""

    performance: Performance
    nomination: Nomination
    rows: list[tuple[Participant, Studio]]
    day: int

    @property
    def year(self) -> int:
        return self.performance.scheduled_at.year

    @property
    def participant_names(self) -> str:
        return ", ".join(participant.display_name for participant, _ in self.rows)

    @property
    def studio_names(self) -> str:
        return ", ".join(sorted({studio.name for _, studio in self.rows}))

    @property
    def is_public(self) -> bool:
        """Клип и полное имя — только если все участники публичные (§6.19)."""

        return all(participant.is_public_person for participant, _ in self.rows)


@dataclass(frozen=True)
class ParticipantView:
    """Участник со всеми выступлениями за все загруженные выпуски (§6.8)."""

    participant: Participant
    studio: Studio
    performances: list[PerformanceView]
    year: int

    @property
    def years(self) -> list[int]:
        """Годы, в которых есть выступления; если их нет — год первого упоминания."""

        return sorted({view.year for view in self.performances}, reverse=True) or [self.year]


def _performance_view(bundle: EditionBundle, performance: Performance) -> PerformanceView:
    edition = bundle.edition
    nomination = edition.nomination_index[performance.nomination]
    participants = edition.participant_index
    studios = edition.studio_index
    rows = [
        (participants[slug], studios[participants[slug].studio])
        for slug in performance.participants
    ]
    day = edition.dates.index(performance.scheduled_at.date()) + 1
    return PerformanceView(performance=performance, nomination=nomination, rows=rows, day=day)


def performance_views(bundle: EditionBundle) -> list[PerformanceView]:
    """Все выступления выпуска в порядке сетки: время, затем номер по порядку."""

    edition = bundle.edition
    ordered = sorted(edition.performances, key=lambda item: (item.scheduled_at, item.order_no))
    return [_performance_view(bundle, performance) for performance in ordered]


def filter_performances(
    bundle: EditionBundle,
    *,
    day: int | None = None,
    style: DanceStyle | None = None,
    age: AgeGroup | None = None,
    fmt: DanceFormat | None = None,
    studio: str | None = None,
    video: bool | None = None,
) -> list[PerformanceView]:
    """Фильтры каталога §6.7: день · направление · формат · возраст · студия · клип."""

    result = performance_views(bundle)
    if day is not None:
        result = [view for view in result if view.day == day]
    if style is not None:
        result = [view for view in result if view.nomination.style == style]
    if age is not None:
        result = [view for view in result if view.nomination.age_group == age]
    if fmt is not None:
        result = [view for view in result if view.nomination.format == fmt]
    if studio:
        result = [view for view in result if any(item.studio == studio for item, _ in view.rows)]
    if video is not None:
        result = [view for view in result if (view.performance.video_url is not None) == video]
    return result


def performance_detail(bundle: EditionBundle, slug: str) -> PerformanceView | None:
    for performance in bundle.edition.performances:
        if performance.slug == slug:
            return _performance_view(bundle, performance)
    return None


def siblings(bundle: EditionBundle, view: PerformanceView, limit: int = 4) -> list[PerformanceView]:
    """«Похожие»: ещё выступления той же номинации, без самого себя."""

    same = [
        item
        for item in performance_views(bundle)
        if item.nomination.slug == view.nomination.slug
        and item.performance.slug != view.performance.slug
    ]
    return same[:limit]


def block_for_nomination(bundle: EditionBundle, nomination_slug: str) -> ScheduleBlock | None:
    for block in bundle.edition.blocks:
        if nomination_slug in block.nominations:
            return block
    return None


def participant_view(bundles: Sequence[EditionBundle], slug: str) -> ParticipantView | None:
    """Карточка участника собирается по всем выпускам: годы участия — не выдумка."""

    found: tuple[Participant, Studio, int] | None = None
    for bundle in bundles:
        participant = bundle.edition.participant_index.get(slug)
        if participant is not None:
            found = (participant, bundle.edition.studio_index[participant.studio], bundle.year)
            break
    if found is None:
        return None

    participant, studio, year = found
    performances = [
        _performance_view(bundle, performance)
        for bundle in bundles
        for performance in bundle.edition.performances
        if slug in performance.participants
    ]
    performances.sort(
        key=lambda view: (view.performance.scheduled_at, view.performance.order_no), reverse=True
    )
    return ParticipantView(
        participant=participant, studio=studio, performances=performances, year=year
    )
