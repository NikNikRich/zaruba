"""Модели выпуска: контракт из `docs/06` §6.17 и правила целостности YAML-контента."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

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
    # Сетка 2025 из судейских листов: границы не совпадают ни с 2019–2024,
    # ни с 2026. Подгонять 3–9 под «3–6» значило бы соврать в источнике.
    KIDS_3_9 = "3-9"
    BABY_3_7 = "3-7"
    KIDS_8_12 = "8-12"
    JUNIORS_10_14 = "10-14"
    JUNIORS_13_15 = "13-15"
    VARSITY_14_18 = "14-18"
    UP_TO_14 = "up-to-14"
    # В части номинаций 2025 (SHOW, EX, MINI GROUP, STRIP, HEELS) возраст
    # в судейских листах не назван: «любой возраст» был бы домыслом.
    UNSPECIFIED = "not-specified"
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
            "3-9": "3–9 лет",
            "3-7": "3–7 лет",
            "8-12": "8–12 лет",
            "10-14": "10–14 лет",
            "13-15": "13–15 лет",
            "14-18": "14–18 лет",
            "up-to-14": "до 14 лет",
            "not-specified": "не указан",
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


class ContactKind(StrEnum):
    """Вид официального контакта: ссылка строится по нему, а не по тексту."""

    PHONE = "phone"
    EMAIL = "email"

    @property
    def label(self) -> str:
        return {"phone": "телефон", "email": "e-mail"}[self.value]


class EditionContact(Strict):
    """Официальный контакт оргкомитета: без `source_id` на сайте не публикуется.

    Это не личные контакты участников и не контакты редакции: телефон и почта
    попадают в контент только вместе со ссылкой на «Положение» или официальный
    пост, иначе контакт — выдумка.
    """

    kind: ContactKind
    label: str = Field(min_length=3, max_length=80)
    value: str = Field(min_length=5, max_length=120)
    source_id: Slug

    @model_validator(mode="after")
    def _value_matches_kind(self) -> EditionContact:
        if self.kind is ContactKind.PHONE:
            digits = re.sub(r"\D", "", self.value)
            if len(digits) != 11 or not digits.startswith(("7", "8")):
                raise ValueError("телефон — 11 цифр, начиная с 7 или 8")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.value):
            raise ValueError("e-mail должен содержать имя, @ и домен")
        return self

    @property
    def url(self) -> str:
        """`tel:` собирается из цифр, `mailto:` — из значения: шаблон без схемы."""

        if self.kind is ContactKind.PHONE:
            digits = re.sub(r"\D", "", self.value)
            if digits.startswith("8"):
                digits = "7" + digits[1:]
            return f"tel:+{digits}"
        return f"mailto:{self.value}"


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
    # Каркас страниц каталога: номинация-пример не является фактом выпуска
    # и в архивный список на `/history` не попадает.
    demo: bool = False

    @model_validator(mode="after")
    def _schedule_shape(self) -> Nomination:
        if self.name is None and self.style is None:
            raise ValueError("нужно поле name или style: номинация без названия не читается")
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("starts_at и ends_at задаются вместе")
        if self.starts_at is not None and self.ends_at is not None:
            if self.ends_at <= self.starts_at:
                raise ValueError("ends_at должен быть позже starts_at")
            if self.day is None:
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


class PhotoNature(StrEnum):
    """Что именно на картинке: снимок или нарисованная нами иллюстрация.

    Разделение не косметическое. Подпись «Фото: …» под рисунком — обман,
    а `alt` без слова «иллюстрация» читается незрячим как описание снимка.
    Поэтому тип едет в данных, а не угадывается по имени файла.
    """

    PHOTO = "photo"
    ILLUSTRATION = "illustration"

    @property
    def label(self) -> str:
        return {"photo": "фото", "illustration": "иллюстрация"}[self.value]


CITATION_LAW = "ст. 1274 ГК РФ"


class PhotoUse(StrEnum):
    """Основание, по которому снимок вообще попал на сайт.

    Законных вариантов два, и они не взаимозаменяемы:

    - `own` — кадр наш или есть разрешение автора (правообладателя);
    - `citation` — цитирование по п. 1 ст. 1274 ГК РФ: информационная, научная,
      учебная или культурная цель **и** имя автора **и** источник заимствования.

    «Нашлось в интернете» основанием не является, поэтому значения всего два:
    третье пришлось бы объяснять в каждом втором споре.
    """

    OWN = "own"
    CITATION = "citation"

    @property
    def label(self) -> str:
        return {
            "own": "свой кадр или разрешение автора",
            "citation": "цитирование, ст. 1274 ГК РФ",
        }[self.value]


class CitationPurpose(StrEnum):
    """Цель цитирования из п. 1 ст. 1274 ГК РФ — списком, а не свободным текстом.

    Свободное поле рано или поздно заполнят словом «интересно», и проверить,
    что это законная цель, будет нельзя. Четыре значения — ровно те, что в норме.
    """

    INFO = "info"
    SCIENCE = "science"
    EDUCATION = "education"
    CULTURE = "culture"

    @property
    def label(self) -> str:
        return {
            "info": "информационная",
            "science": "научная",
            "education": "учебная",
            "culture": "культурная",
        }[self.value]


def photo_credit_line(
    *,
    author: str,
    use: PhotoUse = PhotoUse.OWN,
    license: str | None = None,
    purpose: CitationPurpose | None = None,
    nature: PhotoNature = PhotoNature.PHOTO,
) -> str:
    """Подпись под картинкой: имя автора и основание публикации.

    Одна функция на архив и на портреты — чтобы «Фото: …» с одним набором прав
    не отличалось от «Фото: …» с другим. Для цитирования подпись прямо называет
    норму: читатель видит, что снимок взят не с разрешения, а по закону.
    """

    kind = "Иллюстрация" if nature is PhotoNature.ILLUSTRATION else "Фото"
    if use is PhotoUse.CITATION:
        basis = f"цитирование, {CITATION_LAW}"
        if purpose is not None:
            basis += f", {purpose.label} цель"
        return f"{kind}: {author} · {basis}"
    return f"{kind}: {author}" + (f" · {license}" if license else "")


def check_photo_rights(
    *,
    use: PhotoUse,
    author: str | None,
    license: str | None,
    purpose: CitationPurpose | None,
    source_url: str | None,
) -> None:
    """Проверяет два условия цитирования и не даёт смешать их с разрешением.

    Ошибки нарочно говорят, чего не хватает, а не «неверные права»: YAML правит
    человек, и ему нужен следующий шаг, а не приговор. `author` обязателен в
    обоих режимах — п. 1 ст. 1274 ГК РФ требует имени и при цитировании, и
    собственная лицензия без автора всё равно неполная.
    """

    if not author:
        raise ValueError(
            "нужен автор: имя обязательно и при цитировании (п. 1 ст. 1274 ГК РФ), "
            "и при своей лицензии"
        )
    if use is PhotoUse.CITATION:
        if purpose is None:
            raise ValueError(
                "для цитирования нужна purpose (info, science, education, culture): "
                "без законной цели цитата не цитата"
            )
        if not source_url:
            raise ValueError("для цитирования нужен source_url — источник заимствования")
        if license:
            raise ValueError(
                "при цитировании license не пишут: если разрешение есть, это use: own, "
                "а не ст. 1274 ГК РФ"
            )
        return
    if not license:
        raise ValueError(
            "нужен license (разрешение автора или лицензия): без него остаётся "
            "только цитирование по ст. 1274 ГК РФ"
        )
    if purpose is not None:
        raise ValueError("purpose относится к цитированию: уберите его или поставьте use: citation")


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
    # Портрет: кроп 3:4 по лицу (480×640), автор и права.
    # Пусто — карточка показывает инициалы, а не серый прямоугольник.
    photo: ImagePath | None = None
    photo_author: str | None = Field(default=None, min_length=2, max_length=80)
    photo_license: str | None = Field(default=None, min_length=2, max_length=80)
    # Основание публикации: своё разрешение или цитирование по ст. 1274 ГК РФ.
    # Портрет человека — тоже «фото», поэтому правила те же, что в фотоархиве.
    photo_use: PhotoUse = PhotoUse.OWN
    photo_purpose: CitationPurpose | None = None
    photo_source_url: str | None = Field(default=None, pattern=r"^https://", max_length=300)
    # Отличает снимок от рисунка `tools/avatars.py`: на странице это видно.
    photo_nature: PhotoNature = PhotoNature.PHOTO
    demo: bool = False

    @model_validator(mode="after")
    def _unique_marks(self) -> Judge:
        if len(set(self.styles)) != len(self.styles):
            raise ValueError("направление указано дважды")
        if len(set(self.blocks)) != len(self.blocks):
            raise ValueError("блок указан дважды")
        rights = (self.photo_author, self.photo_license, self.photo_purpose, self.photo_source_url)
        if self.photo is None:
            if any(rights) or self.photo_use is not PhotoUse.OWN:
                raise ValueError(
                    "права на снимок указывают вместе с photo: у карточки без файла их нет"
                )
            return self
        if self.photo_nature is PhotoNature.ILLUSTRATION and self.photo_use is not PhotoUse.OWN:
            raise ValueError(
                "иллюстрация — наша работа: для неё только use: own, цитировать нечего"
            )
        check_photo_rights(
            use=self.photo_use,
            author=self.photo_author,
            license=self.photo_license,
            purpose=self.photo_purpose,
            source_url=self.photo_source_url,
        )
        return self

    @property
    def photo_credit(self) -> str | None:
        """Подпись собирается из полей, а не пишется руками: имя автора не потеряется."""

        if self.photo is None or not self.photo_author:
            return None
        return photo_credit_line(
            author=self.photo_author,
            use=self.photo_use,
            license=self.photo_license,
            purpose=self.photo_purpose,
            nature=self.photo_nature,
        )


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


class ResultRow(Strict):
    """Строка итоговой таблицы: участник и баллы судей по одной номинации.

    Балл судьи — сумма пяти критериев из его листа, а не напечатанный «общий
    балл»: сумму можно пересчитать. Если напечатанный итог с ней не сошёлся,
    судья попадает в `flagged` — на странице это звёздочка «итог на листе
    расходится с суммой критериев», а не повод выбросить балл.

    Число в `scores` — балл судьи; `None` — листа этого судьи в источнике нет
    (клетка «—»); судья в `unreadable` — цифра есть, но не читается («н/ч»).
    Разница принципиальная: без листа сравнивать суммы нельзя ни у кого
    в номинации, а нечитаемая клетка выбивает из рейтинга только своего
    участника (`docs/06` §6.24).
    """

    participant: str = Field(min_length=2, max_length=80)
    scores: dict[Slug, float | None] = Field(max_length=6)
    unreadable: list[Slug] = Field(default_factory=list, max_length=6)
    total: float | None = Field(default=None, ge=0, le=800)
    place: int | None = Field(default=None, ge=1, le=100)
    flagged: list[Slug] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _consistent(self) -> ResultRow:
        if not self.scores and not self.unreadable:
            raise ValueError("нет ни баллов, ни пометки о нечитаемой клетке")
        both = sorted(set(self.scores) & set(self.unreadable))
        if both:
            raise ValueError(f"судья не может быть и с баллом, и нечитаемым: {both}")
        if len(set(self.unreadable)) != len(self.unreadable):
            raise ValueError("unreadable: судья указан дважды")
        if len(set(self.flagged)) != len(self.flagged):
            raise ValueError("flagged: судья указан дважды")
        unknown = sorted(set(self.flagged) - set(self.scores))
        if unknown:
            raise ValueError(f"flagged: судьи без балла — {unknown}")
        if self.place is not None:
            if self.total is None:
                raise ValueError("место без итогового балла не присваивается")
            if self.unreadable or any(value is None for value in self.scores.values()):
                raise ValueError("при неполной строке место не присваивается")
        values = [value for value in self.scores.values() if value is not None]
        if self.total is not None and abs(round(sum(values), 1) - self.total) > 0.05:
            raise ValueError("total не равен сумме баллов судей")
        return self

    @property
    def complete(self) -> bool:
        """У всех судей есть читаемый балл — только тогда строка участвует в местах."""

        return not self.unreadable and all(value is not None for value in self.scores.values())


class ResultNomination(Strict):
    """Итоги одной номинации: листы всех судей, сведённые в одну таблицу."""

    nomination: Slug
    day: int = Field(ge=1, le=3)
    number: int = Field(ge=1, le=30)
    judges: list[Slug] = Field(min_length=1, max_length=6)
    rows: list[ResultRow] = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _shape(self) -> ResultNomination:
        if len(set(self.judges)) != len(self.judges):
            raise ValueError("судья указан дважды")
        for row in self.rows:
            missing = sorted(set(self.judges) - set(row.scores) - set(row.unreadable))
            extra = sorted((set(row.scores) | set(row.unreadable)) - set(self.judges))
            if missing or extra:
                raise ValueError(
                    f"{row.participant}: состав судей строки не совпадает с номинацией "
                    f"(нет {missing}, лишние {extra})"
                )
        # Нет листа хотя бы у одного судьи — суммы несопоставимы, и места
        # не присваиваются никому: иначе участник с меньшим числом судей
        # получил бы преимущество или, наоборот, несправедливый штраф.
        if any(value is None for row in self.rows for value in row.scores.values()):
            if any(row.place is not None for row in self.rows):
                raise ValueError("нет листа судьи: места в номинации не присваиваются")
            return self
        places = [row.place for row in self.rows if row.place is not None]
        if len(set(places)) != len(places):
            raise ValueError("место присвоено дважды")
        if places and sorted(places) != list(range(1, len(places) + 1)):
            raise ValueError("места идут не подряд: пропуск читается как ошибка расчёта")
        return self

    @property
    def complete(self) -> bool:
        """Места можно считать: у всех судей есть листы и все клетки читаются."""

        return all(row.complete for row in self.rows)


class ResultJudge(Strict):
    """Код судьи в листах и его имя: код — `mor`, имя — «Андрей Морыженков»."""

    code: Slug
    name: str = Field(min_length=2, max_length=60)
    judge: Slug | None = None


class Results(Strict):
    """Разобранные судейские листы выпуска (`docs/06` §6.24).

    Это не протокол организаторов: места здесь расчётные, поэтому у файла
    есть статус (`unconfirmed`, пока организаторы не подтвердят) и источник.
    """

    edition: int = Field(ge=2010, le=2100)
    updated_at: datetime
    source_id: Slug
    status: DataStatus = DataStatus.UNCONFIRMED
    judges: list[ResultJudge] = Field(min_length=1, max_length=8)
    nominations: list[ResultNomination] = Field(default_factory=list, max_length=40)
    notes: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _integrity(self) -> Results:
        codes = [judge.code for judge in self.judges]
        if len(set(codes)) != len(codes):
            raise ValueError("судья указан дважды")
        slugs = [item.nomination for item in self.nominations]
        if len(set(slugs)) != len(slugs):
            raise ValueError("номинация описана дважды")
        numbers = [(item.day, item.number) for item in self.nominations]
        if len(set(numbers)) != len(numbers):
            raise ValueError("номер номинации повторяется в одном дне")
        for item in self.nominations:
            unknown = sorted(set(item.judges) - set(codes))
            if unknown:
                raise ValueError(f"{item.nomination}: неизвестные коды судей {unknown}")
        return self

    @property
    def rows(self) -> int:
        return sum(len(item.rows) for item in self.nominations)


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


class MediaKind(StrEnum):
    """Что открывает карточка: видео, альбом фото или эфир.

    `badge` — надпись на крупной лаймовой кнопке превью. Это действие
    («Play», «Альбом»), а не тип файла: посетитель должен понимать по клику,
    что произойдёт, без иконок и скриптов.
    """

    VIDEO = "video"
    PHOTO = "photo"
    STREAM = "stream"

    @property
    def label(self) -> str:
        return {"video": "видео", "photo": "фото", "stream": "трансляция"}[self.value]

    @property
    def badge(self) -> str:
        return {"video": "Play", "photo": "Альбом", "stream": "Эфир"}[self.value]


class MediaPlatform(StrEnum):
    """Внешние хостинги, на которых лежит материал.

    Тяжёлые iframe не встраиваем: CSP (`frame-src 'none'`) и zero-JS оставляют
    только ссылку, поэтому платформа — это подпись карточки, а не плеер.
    """

    VK_VIDEO = "VK Видео"
    RUTUBE = "Rutube"
    YANDEX_DISK = "Яндекс Диск"
    MAIL_CLOUD = "Облако Mail.ru"
    YOUTUBE = "YouTube"
    OTHER = "Другое"


# Путь превью — от `static/`: только свой файл и только в известных папках.
# Шаблон строит `url_for('static', path=...)`, поэтому `../`, `http://` и
# произвольный каталог отсекаются моделью, а не проверкой в шаблоне.
MEDIA_IMAGE_RE = r"^img/(?:history|media|people)/[a-z0-9]+(?:-[a-z0-9]+)*\.webp$"
MediaImage = Annotated[str, Field(pattern=MEDIA_IMAGE_RE, max_length=120)]


class MediaItem(Strict):
    """Карточка предпросмотра: ссылка на внешний хостинг, без встраивания.

    `preview_image` не обязателен: без файла рисуется типографская заглушка,
    поэтому блок можно показать сразу, не выдумывая и не копируя чужой постер.
    `nomination_slug` привязывает материал к номинации на `/results`.
    """

    slug: Slug
    title: str = Field(min_length=3, max_length=120)
    kind: MediaKind
    platform: MediaPlatform
    url: HttpUrl
    preview_image: MediaImage | None = None
    meta: str | None = Field(default=None, max_length=40)
    nomination_slug: Slug | None = None


class OfferCategory(StrEnum):
    """Тип коммерческого предложения: товар, услуга или партнёрская ссылка."""

    MERCH = "merch"
    PHOTO_PACK = "photo_pack"
    VIP = "vip"
    PARTNER = "partner"

    @property
    def label(self) -> str:
        return {
            "merch": "мерч",
            "photo_pack": "фотопакет",
            "vip": "VIP",
            "partner": "партнёр",
        }[self.value]


class OfferStatus(StrEnum):
    """Наличие: «распродано» — честное состояние, а не спрятанная карточка."""

    AVAILABLE = "available"
    PREORDER = "preorder"
    SOLD_OUT = "sold_out"

    @property
    def label(self) -> str:
        return {"available": "в наличии", "preorder": "предзаказ", "sold_out": "распродано"}[
            self.value
        ]


# Токен маркировки рекламы по 38-ФЗ: латиница, цифры, `_` и `-`. Проверяем
# форму, а не подлинность: токен выдаёт ОРД, и придуманный «пример» должен
# ломать `content check`, а не попадать в плашку «Реклама».
ERID_RE = r"^[A-Za-z0-9_-]{6,64}$"


class CommerceOffer(Strict):
    """Мерч, фотопакет, VIP или партнёрская ссылка.

    Цена есть у товара и услуги: без неё карточка обещала бы покупку без
    цены. У партнёра цены нет — это ссылка на чужой сайт, поэтому `price`
    запрещён, а не «просто пустой». `erid` добавляет плашку «Реклама» и
    `rel="sponsored"`: маркировка нужна только там, где она указана.
    """

    slug: Slug
    title: str = Field(min_length=3, max_length=120)
    category: OfferCategory
    price: int | None = Field(default=None, ge=1, le=1_000_000)
    status: OfferStatus = OfferStatus.AVAILABLE
    description: str = Field(min_length=10, max_length=300)
    cta_label: str = Field(min_length=3, max_length=40)
    url: HttpUrl
    erid: str | None = Field(default=None, pattern=ERID_RE, max_length=64)

    @model_validator(mode="after")
    def _price_follows_category(self) -> CommerceOffer:
        if self.category is OfferCategory.PARTNER:
            if self.price is not None:
                raise ValueError("партнёрская ссылка — не товар: price не задаётся")
            return self
        if self.price is None:
            raise ValueError(
                f"{self.category.value}: нужна цена в рублях — «ориентировочно» не пишем"
            )
        return self


class MasterClass(Strict):
    """Мастер-класс: время и педагог из афиши, цена до и после срока."""

    slug: Slug
    day: int = Field(ge=1, le=3)
    starts_at: time
    ends_at: time
    teacher: str = Field(min_length=2, max_length=80)
    price_early: int = Field(ge=1, le=100_000)
    price_late: int = Field(ge=1, le=100_000)
    early_until: date
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
    # Контакты оргкомитета — со ссылкой на источник. Личных контактов участников
    # и редакции здесь нет и быть не должно.
    contacts: list[EditionContact] = Field(default_factory=list, max_length=8)

    sources: list[Source] = Field(default_factory=list)
    studios: list[Studio] = Field(default_factory=list)
    nominations: list[Nomination] = Field(default_factory=list)
    blocks: list[ScheduleBlock] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    performances: list[Performance] = Field(default_factory=list)
    judges: list[Judge] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list, max_length=12)
    tickets: list[Ticket] = Field(default_factory=list, max_length=12)
    # Медиа и коммерция расширяемы: лишняя строка в YAML не ломает модель,
    # пока проходит перечисление. Ограничение по числу — защита от опечатки
    # «скопировал список дважды», а не продуктовое решение.
    media: list[MediaItem] = Field(default_factory=list, max_length=24)
    commerce: list[CommerceOffer] = Field(default_factory=list, max_length=12)
    master_classes: list[MasterClass] = Field(default_factory=list, max_length=12)
    master_class_pass_early: int | None = Field(default=None, ge=1, le=100_000)
    master_class_pass_late: int | None = Field(default=None, ge=1, le=100_000)
    criteria: list[Criterion] = Field(default_factory=list, max_length=8)
    rules: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        """Опечатка в зоне ломает `content check`, а не главную страницу.

        `zoneinfo` берёт базу из системы: на Windows и в slim-образах её нет,
        пока не установлен пакет `tzdata` (он в зависимостях ради этого).
        """

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"неизвестная зона «{value}»: проверьте написание или поставьте tzdata"
            ) from exc
        return value

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
            "media": [m.slug for m in self.media],
            "commerce": [o.slug for o in self.commerce],
        }
        for name, slugs in groups.items():
            duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
            if duplicates:
                raise ValueError(f"{name}: повторяются slug {duplicates}")

        source_ids = {s.id for s in self.sources}
        for contact in self.contacts:
            if contact.source_id not in source_ids:
                raise ValueError(
                    f"contacts.{contact.kind}: неизвестный source_id «{contact.source_id}»"
                )
        studio_slugs = {s.slug for s in self.studios}
        nomination_index = self.nomination_index
        participant_slugs = {p.slug for p in self.participants}

        for nomination in self.nominations:
            self._check_day(nomination.day, "nominations", nomination.slug)
            if nomination.source_id and nomination.source_id not in source_ids:
                raise ValueError(
                    f"nominations.{nomination.slug}: неизвестный source_id «{nomination.source_id}»"
                )

        # Привязка к номинации — ссылка на сетку выпуска, а не на строку-опечатку:
        # иначе блок «Фото и видео» на `/results` молча не показал бы материал.
        for item in self.media:
            if item.nomination_slug and item.nomination_slug not in nomination_index:
                raise ValueError(
                    f"media.{item.slug}: неизвестная номинация «{item.nomination_slug}»"
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
            if master_class.early_until > self.dates[0]:
                raise ValueError(
                    f"master_classes.{master_class.slug}: ранний срок "
                    f"{master_class.early_until} позже начала выпуска"
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

    def _check_day(self, day: int | None, field: str, slug: str) -> None:
        """День сверяется с датами выпуска, только когда он задан.

        У выпуска 2026 тайминг блоков ещё не опубликован: `day` там пустой,
        и проверять нечего — `None` не должен превращаться в TypeError.
        """

        if day is None:
            return
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


class HistoryEntry(Strict):
    """Выпуск до 2025: только то, что подтверждено публичными источниками.

    2025 и 2026 в этом файле не дублируются — они берутся из `content/editions`.
    Пустых дат не боимся: у 2019 и 2022 публично известны не все подробности,
    и страница честно пишет «даты не подтверждены», а не придумывает день.
    """

    year: int = Field(ge=2010, le=2100)
    title: str = Field(min_length=3, max_length=120)
    dates: list[date] = Field(default_factory=list, max_length=3)
    note: str = Field(min_length=10, max_length=300)
    source: Source

    @model_validator(mode="after")
    def _dates_in_year(self) -> HistoryEntry:
        if self.dates and self.dates != sorted(set(self.dates)):
            raise ValueError("dates должны идти по возрастанию и без повторов")
        if any(value.year != self.year for value in self.dates):
            raise ValueError("дата выпуска не совпадает с годом записи")
        return self


class PhotoKind(StrEnum):
    """Формат превью: размеры заданы кодом, а не аккуратностью автора снимка.

    Тот же пресет читает `tools/prepare_photos.py`, поэтому фото с камеры,
    телефона и выгрузки из альбома получаются одного размера и одной рамки.
    """

    GALLERY = "gallery"
    PORTRAIT = "portrait"

    @property
    def size(self) -> tuple[int, int]:
        return {"gallery": (960, 720), "portrait": (480, 640)}[self.value]

    @property
    def label(self) -> str:
        return {"gallery": "кадр 4:3", "portrait": "портрет 3:4"}[self.value]


# Путь — от `static/`: только свои файлы и только в двух папках. Шаблон строит
# `src="/static/{{ photo.file }}"`, поэтому `../`, `http://` и произвольный
# каталог отсекаются здесь, а не проверкой в трёх местах.
IMAGE_PATH_RE = r"^img/(?:history|people)/[a-z0-9]+(?:-[a-z0-9]+)*\.webp$"
ImagePath = Annotated[str, Field(pattern=IMAGE_PATH_RE, max_length=120)]


class Photo(Strict):
    """Снимок архива: файл (если он у нас есть), права и первоисточник.

    Файла нет — карточка остаётся ссылкой на публикацию: чужой снимок без
    основания мы не копируем (`/takedown`), а показываем превью-заглушку
    с переходом в источник. Как только файл появляется, нужны `alt`, `author`
    и одно из двух оснований: разрешение (`license`, `use: own`) или цитирование
    по ст. 1274 ГК РФ (`use: citation` + `purpose`). Источник заимствования —
    всегда поле `source`, поэтому при цитировании он уже есть и виден на странице.
    """

    slug: Slug
    year: int = Field(ge=2010, le=2100)
    title: str = Field(min_length=3, max_length=120)
    caption: str = Field(min_length=10, max_length=200)
    kind: PhotoKind = PhotoKind.GALLERY
    file: ImagePath | None = None
    alt: str | None = Field(default=None, min_length=5, max_length=160)
    author: str | None = Field(default=None, min_length=2, max_length=80)
    license: str | None = Field(default=None, min_length=2, max_length=80)
    use: PhotoUse = PhotoUse.OWN
    purpose: CitationPurpose | None = None
    source: Source

    @model_validator(mode="after")
    def _file_needs_credit(self) -> Photo:
        if self.file is None:
            if self.use is not PhotoUse.OWN or self.purpose is not None:
                raise ValueError(
                    "use и purpose задаются только с файлом: карточка без файла — это ссылка, "
                    "а не иллюстрация"
                )
            return self
        missing = [
            name
            for name, value in (
                ("alt", self.alt),
                ("author", self.author),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "у фото с файлом обязательны " + ", ".join(missing) + ": без прав не публикуем"
            )
        check_photo_rights(
            use=self.use,
            author=self.author,
            license=self.license,
            purpose=self.purpose,
            source_url=str(self.source.url),
        )
        return self

    @property
    def width(self) -> int:
        return self.kind.size[0]

    @property
    def height(self) -> int:
        return self.kind.size[1]

    @property
    def is_citation(self) -> bool:
        """Снимок взят по ст. 1274 ГК РФ, а не с разрешения: на странице это видно."""

        return self.file is not None and self.use is PhotoUse.CITATION

    @property
    def credit(self) -> str:
        """«Фото: автор · лицензия» или «Фото: автор · цитирование, ст. 1274 ГК РФ»."""

        if not self.author:
            return ""
        return photo_credit_line(
            author=self.author,
            use=self.use,
            license=self.license,
            purpose=self.purpose,
        )


class EditionBundle(Strict):
    """Выпуск вместе с разрешённой площадкой — то, что получают роутеры."""

    edition: Edition
    venue: Venue

    @property
    def year(self) -> int:
        return self.edition.edition
