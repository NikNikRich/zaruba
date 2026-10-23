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
    """Возрастные группы выпусков.

    До 2025 включительно сетка была из трёх групп (6–11, 12–17, 18+).
    «Положение 2026» делит участников иначе: 3–6, 7–11, 12–14, 15–17, 10–15,
    14–17, 16+ и 18+, плюс номинация без возрастного ограничения. Старые
    значения остаются, чтобы архивные выпуски читались без переписывания.
    """

    BABY = "3-6"
    KIDS = "6-11"
    KIDS_7_11 = "7-11"
    KIDS_10_15 = "10-15"
    TEENS = "12-17"
    JUNIORS = "12-14"
    TEENS_14_17 = "14-17"
    VARSITY = "15-17"
    ADULTS_15 = "15+"
    ADULTS_16 = "16+"
    ADULTS = "18+"
    ANY = "any"

    @property
    def label(self) -> str:
        return {
            "3-6": "3–6 лет",
            "6-11": "6–11 лет",
            "7-11": "7–11 лет",
            "10-15": "10–15 лет",
            "12-17": "12–17 лет",
            "12-14": "12–14 лет",
            "14-17": "14–17 лет",
            "15-17": "15–17 лет",
            "15+": "15+",
            "16+": "16+",
            "18+": "18+",
            "any": "любой возраст",
        }[self.value]


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


class NominationLevel(StrEnum):
    """Уровень зачёта из «Положения 2026»: стаж команды и участие хореографа."""

    BEG = "beg"
    PRO = "pro"

    @property
    def label(self) -> str:
        return {"beg": "БЕГ", "pro": "ПРО"}[self.value]


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
    """Номинация выпуска.

    У архивных выпусков есть время и зал, но нет публичного названия; у 2026
    наоборот — есть название, возраст, уровни и минимальный состав, а тайминг
    блоков ещё не опубликован. Поэтому `name`, `day` и время необязательны,
    но «время без дня» и «половина тайминга» запрещены.
    """

    slug: Slug
    name: str | None = Field(default=None, min_length=3, max_length=80)
    style: DanceStyle | None = None
    format: DanceFormat
    age_group: AgeGroup
    levels: list[NominationLevel] = Field(default_factory=list, max_length=2)
    min_participants: int | None = Field(default=None, ge=2, le=40)
    max_participants: int | None = Field(default=None, ge=2, le=40)
    day: int | None = Field(default=None, ge=1, le=3)
    starts_at: time | None = None
    ends_at: time | None = None
    stage: str = Field(default="Сцена", max_length=40)
    status: EventStatus = EventStatus.SCHEDULED
    note: str | None = Field(default=None, max_length=240)
    source_id: Slug | None = None

    @model_validator(mode="after")
    def _schedule_shape(self) -> Nomination:
        if self.name is None and self.style is None:
            raise ValueError("нужно поле name или style: номинация без названия не читается")
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("starts_at и ends_at задаются вместе")
        if self.starts_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at должен быть позже starts_at")
        if self.starts_at is not None and self.day is None:
            raise ValueError("время без дня не имеет смысла: укажите day")
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("уровень указан дважды")
        if (
            self.min_participants is not None
            and self.max_participants is not None
            and self.max_participants < self.min_participants
        ):
            raise ValueError("max_participants меньше min_participants")
        return self

    @property
    def title(self) -> str:
        if self.name:
            return self.name
        assert self.style is not None  # гарантировано _schedule_shape
        return f"{self.format.label} · {self.style.label} · {self.age_group.label}"

    @property
    def levels_label(self) -> str:
        return " · ".join(level.label for level in self.levels)

    @property
    def team_size_label(self) -> str | None:
        """«от 6 человек», «3–5 человек» — как в «Положении», без домыслов."""

        if self.min_participants is None:
            return None
        if self.max_participants is None:
            return f"от {self.min_participants} человек"
        return f"{self.min_participants}–{self.max_participants} человек"

    @property
    def has_timing(self) -> bool:
        return self.starts_at is not None and self.ends_at is not None


class Studio(Strict):
    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    city: str = "Тюмень"
    vk_url: str | None = Field(default=None, pattern=r"^https://")
    demo: bool = False


class JudgeRole(StrEnum):
    JUDGE = "judge"
    MC = "mc"

    @property
    def label(self) -> str:
        return {"judge": "судья", "mc": "ведущий"}[self.value]


class Judge(Strict):
    """Человек за судейским столом или на сцене как ведущий (`docs/06` §6.11).

    Пустое поле значит «публично не подтверждено»: страница так и пишет,
    вместо того чтобы подставлять «Тюмень» или «универсал».
    """

    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    role: JudgeRole = JudgeRole.JUDGE
    city: str | None = Field(default=None, max_length=60)
    regalia: str | None = Field(default=None, max_length=120)
    styles: list[DanceStyle] = Field(default_factory=list, max_length=9)
    blocks: list[Slug] = Field(default_factory=list, max_length=12)
    source_id: Slug | None = None
    demo: bool = False

    @model_validator(mode="after")
    def _unique_marks(self) -> Judge:
        if len(set(self.styles)) != len(self.styles):
            raise ValueError("направление указано дважды")
        if len(set(self.blocks)) != len(self.blocks):
            raise ValueError("блок указан дважды")
        return self


class GlossaryKind(StrEnum):
    STYLE = "style"
    TERM = "term"

    @property
    def label(self) -> str:
        return {"style": "направление", "term": "термин чемпионата"}[self.value]


class GlossaryEntry(Strict):
    """Статья глоссария: «что это», «как отличить», «пример» (§6.15)."""

    slug: Slug
    term: str = Field(min_length=2, max_length=80)
    kind: GlossaryKind = GlossaryKind.STYLE
    what: str = Field(min_length=20, max_length=400)
    signs: list[str] = Field(min_length=1, max_length=4)
    example: str | None = Field(default=None, max_length=160)
    style: DanceStyle | None = None

    @model_validator(mode="after")
    def _style_link(self) -> GlossaryEntry:
        if self.kind is GlossaryKind.STYLE and self.style is None:
            raise ValueError("для направления нужно поле style из справочника")
        if self.kind is GlossaryKind.TERM and self.style is not None:
            raise ValueError("у термина чемпионата не бывает style")
        if any(len(sign) > 160 for sign in self.signs):
            raise ValueError("признак длиннее 160 символов — это уже абзац")
        return self

    @property
    def anchor(self) -> str:
        return f"{self.kind.value}-{self.slug}"


class FaqGroup(StrEnum):
    SPECTATOR = "spectator"
    PARTICIPANT = "participant"
    PARENT = "parent"
    SITE = "site"

    @property
    def label(self) -> str:
        return {
            "spectator": "Зрителю",
            "participant": "Участнику",
            "parent": "Родителям",
            "site": "Сайту",
        }[self.value]


class FaqItem(Strict):
    """Вопрос-ответ: статус нужен, чтобы ответ «уточняется» не читался как факт."""

    slug: Slug
    group: FaqGroup
    question: str = Field(min_length=5, max_length=200)
    answer: str = Field(min_length=10, max_length=600)
    status: DataStatus = DataStatus.UNCONFIRMED
    source_id: Slug | None = None


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
            raise ValueError("для несовершеннолетнего без согласия display_name — только инициалы")
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


class FeeAudience(StrEnum):
    CHILDREN = "children"
    ADULTS = "adults"

    @property
    def label(self) -> str:
        return {"children": "Детские", "adults": "Взрослые"}[self.value]


class Fee(Strict):
    """Взнос за участие: ставка до и после даты, из «Положения»."""

    slug: Slug
    audience: FeeAudience
    format: DanceFormat
    amount_early: int = Field(ge=1, le=100_000)
    amount_late: int = Field(ge=1, le=100_000)
    early_until: date
    unit: str = Field(default="с участника", max_length=40)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _price_order(self) -> Fee:
        if self.amount_late < self.amount_early:
            raise ValueError("ставка после раннего срока не может быть ниже ранней")
        return self


class Ticket(Strict):
    """Зрительский билет: `day` пустой — билет на оба дня."""

    slug: Slug
    title: str = Field(min_length=3, max_length=80)
    price: int = Field(ge=1, le=100_000)
    day: int | None = Field(default=None, ge=1, le=3)
    block: str | None = Field(default=None, max_length=60)
    source_id: Slug | None = None


class MasterClass(Strict):
    """Мастер-класс: время и педагог из афиши, цена до и после срока."""

    slug: Slug
    day: int = Field(ge=1, le=3)
    starts_at: time
    ends_at: time
    teacher: str = Field(min_length=2, max_length=80)
    price_early: int = Field(ge=1, le=100_000)
    price_late: int = Field(ge=1, le=100_000)
    source_id: Slug | None = None

    @model_validator(mode="after")
    def _time_order(self) -> MasterClass:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at должен быть позже starts_at")
        if self.price_late < self.price_early:
            raise ValueError("цена после срока не может быть ниже ранней")
        return self


class Criterion(Strict):
    """Критерий судейства с максимальным баллом — только из «Положения»."""

    title: str = Field(min_length=3, max_length=80)
    max_score: int = Field(ge=1, le=10)
    applies_to: str | None = Field(default=None, max_length=80)


class Edition(Strict):
    """Один выпуск: всё, что нужно страницам, в одном файле (`docs/06` §6.17)."""

    edition: int = Field(ge=2010, le=2100)
    title: str = Field(min_length=3, max_length=120)
    dates: list[date] = Field(min_length=1, max_length=3)
    venue: Slug
    status: DataStatus = DataStatus.UNCONFIRMED
    timezone: str = "Asia/Yekaterinburg"
    # Ценз берём с афиши; если его там нет — поля нет, а сайт пишет «не объявлен».
    age_rating: str | None = Field(default=None, max_length=8)
    updated_at: datetime
    note: str | None = Field(default=None, max_length=300)
    organizers: list[str] = Field(default_factory=list, max_length=6)
    registration_until: date | None = None

    sources: list[Source] = Field(default_factory=list)
    studios: list[Studio] = Field(default_factory=list)
    nominations: list[Nomination] = Field(default_factory=list)
    blocks: list[ScheduleBlock] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    performances: list[Performance] = Field(default_factory=list)
    judges: list[Judge] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list, max_length=12)
    tickets: list[Ticket] = Field(default_factory=list, max_length=12)
    master_classes: list[MasterClass] = Field(default_factory=list, max_length=12)
    master_class_pass_early: int | None = Field(default=None, ge=1, le=100_000)
    master_class_pass_late: int | None = Field(default=None, ge=1, le=100_000)
    criteria: list[Criterion] = Field(default_factory=list, max_length=8)
    rules: list[str] = Field(default_factory=list, max_length=12)

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
            "judges": [j.slug for j in self.judges],
            "fees": [f.slug for f in self.fees],
            "tickets": [t.slug for t in self.tickets],
            "master_classes": [m.slug for m in self.master_classes],
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

        block_slugs = {block.slug for block in self.blocks}
        for judge in self.judges:
            missing_blocks = [slug for slug in judge.blocks if slug not in block_slugs]
            if missing_blocks:
                raise ValueError(f"judges.{judge.slug}: нет блоков {missing_blocks}")
            if judge.source_id and judge.source_id not in source_ids:
                raise ValueError(f"judges.{judge.slug}: неизвестный source_id «{judge.source_id}»")

        for ticket in self.tickets:
            if ticket.day is not None:
                self._check_day(ticket.day, "tickets", ticket.slug)
            if ticket.source_id and ticket.source_id not in source_ids:
                raise ValueError(
                    f"tickets.{ticket.slug}: неизвестный source_id «{ticket.source_id}»"
                )

        for master_class in self.master_classes:
            self._check_day(master_class.day, "master_classes", master_class.slug)
            if master_class.source_id and master_class.source_id not in source_ids:
                raise ValueError(
                    f"master_classes.{master_class.slug}: "
                    f"неизвестный source_id «{master_class.source_id}»"
                )

        for fee in self.fees:
            if fee.early_until > self.dates[0]:
                raise ValueError(
                    f"fees.{fee.slug}: ранний срок {fee.early_until} позже начала выпуска"
                )

        for rule in self.rules:
            if len(rule) > 300:
                raise ValueError("rules: правило длиннее 300 символов — это уже абзац")
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
        return sorted(
            (block for block in self.blocks if block.day == day), key=lambda b: b.starts_at
        )

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
