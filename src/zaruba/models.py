"""Модели выпуска: контракт из `docs/06` §6.17 и правила целостности YAML-контента."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]

# Публичное имя несовершеннолетнего без согласия — только инициалы («А. К.»).
INITIALS_RE = re.compile(r"^[А-ЯЁA-Z]\.\s?(?:[А-ЯЁA-Z]\.)?$")


class Strict(BaseModel):
    """Лишнее поле — ошибка: опечатка в YAML не должна молча теряться."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgeGroup(StrEnum):
    KIDS = "6-11"
    TEENS = "12-17"
    ADULTS = "18+"

    @property
    def label(self) -> str:
        return {"6-11": "6–11 лет", "12-17": "12–17 лет", "18+": "18+"}[self.value]


STYLE_LABELS: dict[str, str] = {
    "hip-hop": "Hip-Hop",
    "heels-girly": "Heels / Girly",
    "jazz-funk": "Jazz-Funk",
    "contemporary": "Contemporary",
    "k-pop": "K-Pop",
    "experimental": "Experimental",
    "breaking": "Breaking",
    "waacking-vogue": "Waacking / Vogue",
    "dancehall": "Dancehall",
}


class DanceStyle(StrEnum):
    """Девять стилевых дивизионов из `docs/03`."""

    HIP_HOP = "hip-hop"
    HEELS_GIRLY = "heels-girly"
    JAZZ_FUNK = "jazz-funk"
    CONTEMPORARY = "contemporary"
    K_POP = "k-pop"
    EXPERIMENTAL = "experimental"
    BREAKING = "breaking"
    WAACKING_VOGUE = "waacking-vogue"
    DANCEHALL = "dancehall"

    @property
    def label(self) -> str:
        return STYLE_LABELS[self.value]


class DanceFormat(StrEnum):
    SOLO = "solo"
    DUET = "duet"
    SMALL_GROUP = "small-group"
    TEAM = "team"
    SHOW = "show"

    @property
    def label(self) -> str:
        return {
            "solo": "Соло",
            "duet": "Дуэт",
            "small-group": "Мини-группа",
            "team": "Команда",
            "show": "Шоу",
        }[self.value]


class EventStatus(StrEnum):
    ANNOUNCED = "announced"
    SCHEDULED = "scheduled"
    LIVE = "live"
    DONE = "done"
    MOVED = "moved"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "announced": "анонс",
            "scheduled": "по расписанию",
            "live": "сейчас",
            "done": "прошло",
            "moved": "перенесено",
            "cancelled": "отменено",
        }[self.value]


class DataStatus(StrEnum):
    """Сквозной статус факта, `docs/06` §6.3: подтверждено / не подтверждено / устарело."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    OUTDATED = "outdated"

    @property
    def label(self) -> str:
        return {
            "confirmed": "подтверждено",
            "unconfirmed": "не подтверждено",
            "outdated": "устарело",
        }[self.value]


class ConsentStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    GRANTED = "granted"
    DECLINED = "declined"


class SourceTrust(StrEnum):
    OFFICIAL = "official"
    PUBLIC = "public"
    RUMOR = "rumor"

    @property
    def label(self) -> str:
        return {"official": "официальный", "public": "публичный", "rumor": "обсуждение"}[self.value]


class Source(Strict):
    """Источник факта: без него факт не может считаться подтверждённым (§6.17)."""

    id: Slug
    title: str = Field(min_length=3, max_length=120)
    url: str = Field(pattern=r"^https://")
    retrieved_at: date
    trust: SourceTrust = SourceTrust.OFFICIAL


class Venue(Strict):
    slug: Slug
    name: str = Field(min_length=3, max_length=120)
    city: str = "Тюмень"
    address: str = Field(min_length=3, max_length=160)
    capacity: int | None = Field(default=None, ge=1)
    entrance_note: str | None = Field(default=None, max_length=300)
    parking_note: str | None = Field(default=None, max_length=300)
    map_url: str | None = Field(default=None, pattern=r"^https://")
    status: DataStatus = DataStatus.CONFIRMED

    @property
    def address_line(self) -> str:
        return f"{self.city}, {self.address}"


class Nomination(Strict):
    slug: Slug
    style: DanceStyle
    format: DanceFormat
    age_group: AgeGroup
    day: int = Field(ge=1, le=3)
    starts_at: time
    ends_at: time
    stage: str = Field(default="Зал A", max_length=40)
    status: EventStatus = EventStatus.SCHEDULED
    source_id: Slug | None = None

    @model_validator(mode="after")
    def _time_order(self) -> Nomination:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at должен быть позже starts_at")
        return self

    @property
    def title(self) -> str:
        return f"{self.format.label} · {self.style.label} · {self.age_group.label}"


class Studio(Strict):
    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    city: str = "Тюмень"
    vk_url: str | None = Field(default=None, pattern=r"^https://")
    demo: bool = False


class Participant(Strict):
    slug: Slug
    display_name: str = Field(min_length=2, max_length=60)
    age_group: AgeGroup
    studio: Slug
    city: str = "Тюмень"
    consent_status: ConsentStatus = ConsentStatus.NOT_REQUESTED
    consent_at: date | None = None
    demo: bool = False

    @property
    def is_minor(self) -> bool:
        return self.age_group is not AgeGroup.ADULTS

    @property
    def is_public_person(self) -> bool:
        """Фото, клип и индексация — только для 18+ или при согласии (§6.19, §6.21)."""

        return not self.is_minor or self.consent_status is ConsentStatus.GRANTED

    @model_validator(mode="after")
    def _privacy(self) -> Participant:
        if (
            self.is_minor
            and self.consent_status is not ConsentStatus.GRANTED
            and not INITIALS_RE.match(self.display_name)
        ):
            raise ValueError(
                "для несовершеннолетнего без согласия display_name — только инициалы"
            )
        if self.consent_status is ConsentStatus.GRANTED and self.consent_at is None:
            raise ValueError("consent_at обязателен при consent_status=granted")
        return self


class Performance(Strict):
    """Выступление: то, что реально стоит в расписании и имеет свою страницу."""

    slug: Slug
    title: str = Field(min_length=3, max_length=120)
    nomination: Slug
    participants: list[Slug] = Field(min_length=1, max_length=40)
    scheduled_at: datetime
    order_no: int = Field(ge=1)
    stage: str | None = Field(default=None, max_length=40)
    status: EventStatus = EventStatus.SCHEDULED
    video_url: str | None = Field(default=None, pattern=r"^https://")
    source_id: Slug | None = None
    demo: bool = False

    @model_validator(mode="after")
    def _unique_participants(self) -> Performance:
        if len(set(self.participants)) != len(self.participants):
            raise ValueError("участник указан дважды")
        return self


class ScheduleBlock(Strict):
    slug: Slug
    day: int = Field(ge=1, le=3)
    title: str = Field(min_length=3, max_length=80)
    starts_at: time
    ends_at: time
    stage: str = Field(default="Зал A", max_length=40)
    nominations: list[Slug] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _time_order(self) -> ScheduleBlock:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at должен быть позже starts_at")
        return self


class Edition(Strict):
    """Один выпуск: всё, что нужно страницам, в одном файле (`docs/06` §6.17)."""

    edition: int = Field(ge=2010, le=2100)
    title: str = Field(min_length=3, max_length=120)
    dates: list[date] = Field(min_length=1, max_length=3)
    venue: Slug
    status: DataStatus = DataStatus.UNCONFIRMED
    timezone: str = "Asia/Yekaterinburg"
    age_rating: str = Field(default="6+", max_length=8)
    updated_at: datetime
    note: str | None = Field(default=None, max_length=300)

    sources: list[Source] = Field(default_factory=list)
    studios: list[Studio] = Field(default_factory=list)
    nominations: list[Nomination] = Field(default_factory=list)
    blocks: list[ScheduleBlock] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    performances: list[Performance] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> Edition:
        if self.dates != sorted(set(self.dates)):
            raise ValueError("dates должны идти по возрастанию и без повторов")

        groups = {
            "sources": [s.id for s in self.sources],
            "studios": [s.slug for s in self.studios],
            "nominations": [n.slug for n in self.nominations],
            "blocks": [b.slug for b in self.blocks],
            "participants": [p.slug for p in self.participants],
            "performances": [p.slug for p in self.performances],
        }
        for name, slugs in groups.items():
            duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
            if duplicates:
                raise ValueError(f"{name}: повторяются slug {duplicates}")

        source_ids = {s.id for s in self.sources}
        studio_slugs = {s.slug for s in self.studios}
        nomination_index = self.nomination_index
        participant_slugs = {p.slug for p in self.participants}

        for nomination in self.nominations:
            self._check_day(nomination.day, "nominations", nomination.slug)
            if nomination.source_id and nomination.source_id not in source_ids:
                raise ValueError(
                    f"nominations.{nomination.slug}: неизвестный source_id «{nomination.source_id}»"
                )

        for block in self.blocks:
            self._check_day(block.day, "blocks", block.slug)
            missing = [slug for slug in block.nominations if slug not in nomination_index]
            if missing:
                raise ValueError(f"blocks.{block.slug}: нет номинаций {missing}")
            wrong_day = [
                slug for slug in block.nominations if nomination_index[slug].day != block.day
            ]
            if wrong_day:
                raise ValueError(f"blocks.{block.slug}: номинации другого дня — {wrong_day}")

        for participant in self.participants:
            if participant.studio not in studio_slugs:
                raise ValueError(
                    f"participants.{participant.slug}: неизвестная студия «{participant.studio}»"
                )

        for performance in self.performances:
            if performance.nomination not in nomination_index:
                raise ValueError(
                    f"performances.{performance.slug}: "
                    f"неизвестная номинация «{performance.nomination}»"
                )
            missing = [slug for slug in performance.participants if slug not in participant_slugs]
            if missing:
                raise ValueError(f"performances.{performance.slug}: нет участников {missing}")
            if performance.scheduled_at.date() not in self.dates:
                raise ValueError(
                    f"performances.{performance.slug}: дата {performance.scheduled_at.date()} "
                    f"вне дат выпуска"
                )
            if performance.source_id and performance.source_id not in source_ids:
                raise ValueError(
                    f"performances.{performance.slug}: "
                    f"неизвестный source_id «{performance.source_id}»"
                )
        return self

    def _check_day(self, day: int, field: str, slug: str) -> None:
        if day > len(self.dates):
            raise ValueError(f"{field}.{slug}: день {day}, а в выпуске {len(self.dates)} дат")

    @property
    def days(self) -> int:
        return len(self.dates)

    @property
    def source_index(self) -> dict[str, Source]:
        return {source.id: source for source in self.sources}

    @property
    def studio_index(self) -> dict[str, Studio]:
        return {studio.slug: studio for studio in self.studios}

    @property
    def nomination_index(self) -> dict[str, Nomination]:
        return {nomination.slug: nomination for nomination in self.nominations}

    @property
    def participant_index(self) -> dict[str, Participant]:
        return {participant.slug: participant for participant in self.participants}

    def blocks_for_day(self, day: int) -> list[ScheduleBlock]:
        return sorted((block for block in self.blocks if block.day == day), key=lambda b: b.starts_at)

    def performances_for_nomination(self, slug: str) -> list[Performance]:
        return sorted(
            (item for item in self.performances if item.nomination == slug),
            key=lambda item: (item.scheduled_at, item.order_no),
        )


class EditionBundle(Strict):
    """Выпуск вместе с разрешённой площадкой — то, что получают роутеры."""

    edition: Edition
    venue: Venue

    @property
    def year(self) -> int:
        return self.edition.edition



