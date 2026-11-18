"""Загрузка контента из YAML: пока нет БД, контент живёт в репозитории (`docs/06` §6.17)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError

from .config import Settings, get_settings
from .models import (
    INITIALS_RE,
    AgeGroup,
    Alert,
    AlertFeed,
    DanceFormat,
    Edition,
    EditionBundle,
    FaqItem,
    GlossaryEntry,
    HistoryEntry,
    Nomination,
    Photo,
    ResultNomination,
    Results,
    ServiceTip,
    Venue,
)

VENUES_ADAPTER = TypeAdapter(list[Venue])
GLOSSARY_ADAPTER = TypeAdapter(list[GlossaryEntry])
FAQ_ADAPTER = TypeAdapter(list[FaqItem])
HISTORY_ADAPTER = TypeAdapter(list[HistoryEntry])
PHOTOS_ADAPTER = TypeAdapter(list[Photo])
RESULTS_ADAPTER = TypeAdapter(Results)
SERVICE_TIPS_ADAPTER = TypeAdapter(list[ServiceTip])


class ContentError(Exception):
    """Ошибка данных: сообщение всегда содержит файл и путь до поля."""


def _read_yaml(path: Path, expect: str = "полями выпуска") -> dict:
    if not path.exists():
        raise ContentError(f"{path}: файл не найден")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContentError(f"{path}: не разобрать YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise ContentError(f"{path}: ожидался YAML-объект с {expect}")
    return raw


def _validation_error(path: Path, exc: ValidationError) -> ContentError:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<корень>"
        lines.append(f"  {location}: {error['msg']}")
    return ContentError(f"{path}: данные не прошли проверку\n" + "\n".join(lines))


# Overlay `content/editions/<год>.d/*.yaml` — презентационный слой, а не второй
# выпуск: строки дополняют опубликованный YAML и патчатся по slug. Так выдуманные
# блоки и участники не попадают в базовый файл, который проверяют тесты и релиз.
OVERLAY_SECTIONS = frozenset(
    {
        "sources",
        "studios",
        "nominations",
        "blocks",
        "participants",
        "performances",
        "judges",
        "fees",
        "tickets",
        "master_classes",
        # Медиа и коммерция тоже приходят из таблиц: презентационные карточки
        # не должны попадать в базовый YAML, который проверяют релиз и тесты.
        "media",
        "commerce",
    }
)
OVERLAY_KEY = {"sources": "id"}
DEFAULT_OVERLAY_KEY = "slug"


def overlay_dir(path: Path) -> Path:
    """`2026.yaml` → `2026.d`: соседняя папка с презентационными списками."""

    return Path(path).with_suffix(".d")


def merge_overlay(
    raw: dict[str, object],
    overlays: Iterable[tuple[Path, Mapping[str, object]]],
) -> dict[str, object]:
    """Дополняет выпуск overlay-строками; совпадение по slug — точечный патч.

    Патч нужен таймингу номинаций: в «Положении» времени нет, а расписание
    предпросмотра строится из выступлений. Патч меняет только переданные поля,
    поэтому реальные название, возраст и источник остаются из базового YAML.
    """

    merged = dict(raw)
    for path, data in overlays:
        for section, rows in data.items():
            if section not in OVERLAY_SECTIONS:
                raise ContentError(f"{path}: раздел «{section}» нельзя добавить из overlay")
            if not isinstance(rows, list):
                raise ContentError(f"{path}: раздел «{section}» должен быть списком")
            base = merged.get(section, [])
            if not isinstance(base, list):
                raise ContentError(f"{path}: в выпуске раздел «{section}» — не список")
            # Копия: overlay не должен менять базовый словарь, который вызывающий
            # ещё использует (например, сверяет real-строки с публикацией).
            base = list(base)
            merged[section] = base

            key = OVERLAY_KEY.get(section, DEFAULT_OVERLAY_KEY)
            index: dict[str, int] = {}
            for position, item in enumerate(base):
                if isinstance(item, dict) and isinstance(item.get(key), str):
                    index[item[key]] = position

            seen: set[str] = set()
            for item in rows:
                if not isinstance(item, dict) or not isinstance(item.get(key), str):
                    raise ContentError(f"{path}: {section}: у каждой строки нужен {key}")
                value = item[key]
                if value in seen:
                    raise ContentError(f"{path}: {section}.{value}: строка повторяется")
                seen.add(value)
                if value not in index:
                    base.append(item)
                    continue
                current = base[index[value]]
                if not isinstance(current, dict):
                    raise ContentError(f"{path}: {section}.{value}: в выпуске не объект")
                base[index[value]] = {**current, **item}
    return merged


def _read_overlay(directory: Path) -> list[tuple[Path, dict[str, object]]]:
    if not directory.is_dir():
        return []
    return [
        (file, _read_yaml(file, "списками выпуска")) for file in sorted(directory.glob("*.yaml"))
    ]


# Корень сообщества и витрины билетов — это лента, а не публикация: клик по
# такому источнику не показывает факт, ради которого он поставлен. Правило
# `docs/06` §6.17: источник — конкретный пост, документ или страница события.
SOURCE_ROOT_URLS = frozenset(
    {
        "https://vk.com/zaruba72champ",
        "https://t.me/zaruba72champ",
        "https://tyumen.kassy.ru",
    }
)


def _check_source_link(path: Path, url: str, where: str) -> None:
    root = url.rstrip("/")
    if root in SOURCE_ROOT_URLS:
        raise ContentError(
            f"{path}: {where}: ссылка ведёт на корень «{root}» — нужен адрес "
            "конкретного поста, документа или страницы события"
        )


def load_venues(path: Path) -> dict[str, Venue]:
    raw = _read_yaml(path)
    try:
        venues = VENUES_ADAPTER.validate_python(raw.get("venues"))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    index: dict[str, Venue] = {}
    for venue in venues:
        if venue.slug in index:
            raise ContentError(f"{path}: площадка «{venue.slug}» описана дважды")
        index[venue.slug] = venue
    return index


def load_edition(
    path: Path, venues: dict[str, Venue] | None = None, *, preview: bool = False
) -> EditionBundle:
    path = Path(path)
    raw = _read_yaml(path)
    if preview:
        raw = merge_overlay(raw, _read_overlay(overlay_dir(path)))
    try:
        edition = Edition.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    for source in edition.sources:
        _check_source_link(path, source.url, f"sources.{source.id}")

    if venues is None:
        venues = load_venues(path.parent.parent / "venues.yaml")

    venue = venues.get(edition.venue)
    if venue is None:
        raise ContentError(f"{path}: площадки «{edition.venue}» нет в content/venues.yaml")

    return EditionBundle(edition=edition, venue=venue)


def load_editions(
    content_dir: Path, venues_file: Path | None = None, *, preview: bool = False
) -> list[EditionBundle]:
    content_dir = Path(content_dir)
    if not content_dir.is_dir():
        raise ContentError(f"{content_dir}: папка с выпусками не найдена")

    files = sorted(content_dir.glob("*.yaml"))
    if not files:
        raise ContentError(f"{content_dir}: нет ни одного файла выпуска")

    venues = load_venues(Path(venues_file) if venues_file else content_dir.parent / "venues.yaml")
    bundles = [load_edition(path, venues, preview=preview) for path in files]

    years = [bundle.year for bundle in bundles]
    duplicates = sorted({year for year in years if years.count(year) > 1})
    if duplicates:
        raise ContentError(f"{content_dir}: выпуск {duplicates} описан в двух файлах")

    return sorted(bundles, key=lambda bundle: bundle.year, reverse=True)


def _content_fingerprint(settings: Settings) -> tuple[tuple[str, int, int], ...]:
    """Путь, mtime и размер каждого файла контента — ключ кэша вместо TTL.

    TTL означает «до минуты показываем вчерашние данные»: на расписании в день
    события это худший вид ошибки. Отпечаток всех файлов (а не максимальный mtime)
    ловит и правку в `content/`, смонтированном в Docker, и копирование с
    сохранением даты: размер в ключе закрывает `git checkout`/`rsync -a`.
    """

    paths = [
        settings.venues_file,
        settings.alerts_file,
        settings.glossary_file,
        settings.faq_file,
        settings.history_file,
        settings.photos_file,
        settings.offers_file,
        *settings.content_dir.glob("*.yaml"),
        # Overlay — тоже контент: правка презентационного блока должна сбрасывать
        # кэш, иначе страница покажет старый тайминг до рестарта.
        *settings.content_dir.glob("*.d/*.yaml"),
    ]
    items = []
    for path in paths:
        if path.exists():
            stat = path.stat()
            items.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(items))


@lru_cache(maxsize=8)
def _site_cached(
    content_dir: str,
    venues_file: str,
    preview: bool,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> tuple[EditionBundle, ...]:
    return tuple(load_editions(Path(content_dir), Path(venues_file), preview=preview))


def load_site(settings: Settings | None = None) -> list[EditionBundle]:
    """Все выпуски. Разбор YAML — по разу на версию контента, а не на запрос."""

    settings = settings or get_settings()
    return list(
        _site_cached(
            str(settings.content_dir),
            str(settings.venues_file),
            settings.preview_enabled,
            _content_fingerprint(settings),
        )
    )


def load_glossary(path: Path) -> list[GlossaryEntry]:
    """Глоссарий: 9 направлений и термины чемпионата (`docs/06` §6.15)."""

    path = Path(path)
    raw = _read_yaml(path, "полем entries")
    try:
        entries = GLOSSARY_ADAPTER.validate_python(raw.get("entries"))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    slugs = [entry.slug for entry in entries]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ContentError(f"{path}: статьи повторяются — {duplicates}")

    styles = [entry.style for entry in entries if entry.style is not None]
    repeated_styles = sorted({style.value for style in styles if styles.count(style) > 1})
    if repeated_styles:
        raise ContentError(f"{path}: направление описано дважды — {repeated_styles}")
    return entries


def load_faq(path: Path, source_ids: set[str] | None = None) -> list[FaqItem]:
    path = Path(path)
    raw = _read_yaml(path, "полем items")
    try:
        items = FAQ_ADAPTER.validate_python(raw.get("items"))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    slugs = [item.slug for item in items]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ContentError(f"{path}: вопросы повторяются — {duplicates}")

    if source_ids is not None:
        for item in items:
            if item.source_id and item.source_id not in source_ids:
                raise ContentError(
                    f"{path}: faq.{item.slug}: неизвестный source_id «{item.source_id}»"
                )
    return items


def load_offers(path: Path, *, allow_demo: bool = False) -> list[ServiceTip]:
    """Рекламные офферы нативных слотов (§6.27).

    Отсутствие файла — законное состояние «рекламы нет», а не авария: сайт
    работает без него. А вот битая строка — ошибка с файлом и полем: реклама
    без `erid` или с неизвестным слотом не должна доехать до страницы.

    `allow_demo` открывает токены `DEMO-*` только для презентационного файла:
    маркировка ОРД не выдаётся с таким префиксом, и в боевом `offers.yaml`
    он означал бы рекламу без настоящего `erid`.
    """

    path = Path(path)
    if not path.exists():
        return []
    raw = _read_yaml(path, "полем tips")
    try:
        tips = SERVICE_TIPS_ADAPTER.validate_python(raw.get("tips", []))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    slugs = [tip.slug for tip in tips]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ContentError(f"{path}: офферы повторяются — {duplicates}")
    if not allow_demo:
        for tip in tips:
            if tip.erid.startswith("DEMO-"):
                raise ContentError(
                    f"{path}: tips.{tip.slug}.erid: токен DEMO-* допустим только "
                    "в content/offers.preview.yaml"
                )
    return tips


@lru_cache(maxsize=8)
def _offers_cached(path: str, fingerprint: tuple[int, int, int]) -> tuple[ServiceTip, ...]:
    return tuple(load_offers(Path(path)))


def load_service_tips(settings: Settings | None = None) -> list[ServiceTip]:
    """Офферы с кэшем по mtime и размеру файла — как у результатов."""

    settings = settings or get_settings()
    path = settings.offers_file
    if not path.exists():
        return []
    stat = path.stat()
    return list(_offers_cached(str(path), (stat.st_mtime_ns, stat.st_size, len(str(path)))))


@lru_cache(maxsize=4)
def _preview_offers_cached(path: str, fingerprint: tuple[int, int, int]) -> tuple[ServiceTip, ...]:
    return tuple(load_offers(Path(path), allow_demo=True))


def load_preview_service_tips(settings: Settings | None = None) -> list[ServiceTip]:
    """Демо-офферы для показа (docs/07 §7.8).

    Файл читается страницами только при `preview_enabled`, но `content check`
    проверяет его всегда: сломанный пример должен ломать проверку до показа,
    а не в момент демонстрации. Отсутствие файла — «примеров нет».
    """

    settings = settings or get_settings()
    path = settings.offers_preview_file
    if not path.exists():
        return []
    stat = path.stat()
    return list(_preview_offers_cached(str(path), (stat.st_mtime_ns, stat.st_size, len(str(path)))))


@dataclass(frozen=True)
class Knowledge:
    """Не-выпускной контент: глоссарий и FAQ, общий для всех выпусков."""

    glossary: list[GlossaryEntry]
    faq: list[FaqItem]


ALERTS_ADAPTER = TypeAdapter(AlertFeed)


@lru_cache(maxsize=4)
def _alerts_cached(path: str, fingerprint: tuple[int, int, int]) -> tuple[Alert, ...]:
    """Оповещения по отпечатку файла: плашка не должна отставать от таблицы."""

    raw = _read_yaml(Path(path), "списком alerts")
    try:
        feed = ALERTS_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise _validation_error(Path(path), exc) from exc

    ids = [item.id for item in feed.alerts]
    duplicates = sorted({slug for slug in ids if ids.count(slug) > 1})
    if duplicates:
        raise ContentError(f"{path}: оповещение описано дважды — {duplicates}")
    return tuple(feed.alerts)


def load_alerts(settings: Settings | None = None) -> list[Alert]:
    """Срочные оповещения (`docs/09` §9.9). Нет файла — нет оповещений.

    Пустое состояние — норма: до чемпионата плашке нечего сообщать, и страница
    не должна из-за этого падать. Битый файл — наоборот, авария: он ломает
    главную и расписание, поэтому ошибка называется файлом и полем.
    """

    settings = settings or get_settings()
    path = Path(settings.alerts_file)
    if not path.is_file():
        return []
    stat = path.stat()
    return list(_alerts_cached(str(path), (stat.st_mtime_ns, stat.st_size, len(str(path)))))


def load_history(path: Path) -> list[HistoryEntry]:
    """Архив до 2025: страница `/history` дополняет его загруженными выпусками."""

    path = Path(path)
    raw = _read_yaml(path, "полем entries")
    try:
        entries = HISTORY_ADAPTER.validate_python(raw.get("entries"))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    years = [entry.year for entry in entries]
    duplicates = sorted({year for year in years if years.count(year) > 1})
    if duplicates:
        raise ContentError(f"{path}: год описан дважды — {duplicates}")
    for entry in entries:
        _check_source_link(path, entry.source.url, f"entries.{entry.year}.source")
    return sorted(entries, key=lambda entry: entry.year, reverse=True)


def load_photos(path: Path) -> list[Photo]:
    """Фотоархив (`docs/06` §6.5): карточка без файла — законная, без прав — нет.

    Порядок в файле не важен: страница сортирует по году сама. Дубли года
    разрешены — у выпуска может быть несколько кадров, — а вот дубль `slug`
    ломает ссылки и файлы, поэтому это ошибка.
    """

    path = Path(path)
    raw = _read_yaml(path, "полем photos")
    try:
        photos = PHOTOS_ADAPTER.validate_python(raw.get("photos"))
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    seen: set[str] = set()
    for photo in photos:
        if photo.slug in seen:
            raise ContentError(f"{path}: фото «{photo.slug}» описано дважды")
        seen.add(photo.slug)
        _check_source_link(path, photo.source.url, f"photos.{photo.slug}.source")
    return list(photos)


@lru_cache(maxsize=8)
def _results_cached(path: str, fingerprint: tuple[int, int, int]) -> Results:
    return load_results(Path(path))


def load_results(path: Path, bundle: EditionBundle | None = None) -> Results:
    """Разобранные судейские листы выпуска (`docs/06` §6.24).

    Файл проверяется дважды: сам по себе (суммы, места, состав судей) и против
    выпуска. Вторая проверка не формальность: номер номинации в листах должен
    указывать на ту же номинацию того же дня, что в `content/editions`. Иначе
    таблица «SOLO KIDS» однажды покажет баллы «BABY», и на странице это будет
    выглядеть правдоподобно.
    """

    path = Path(path)
    raw = _read_yaml(path, "полями выпуска и номинаций")
    try:
        results = RESULTS_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc
    if bundle is not None:
        _check_results(results, bundle, path)
    return results


def _check_results(results: Results, bundle: EditionBundle, path: Path) -> None:
    edition = bundle.edition
    if results.edition != edition.edition:
        raise ContentError(
            f"{path}: в файле выпуск {results.edition}, а ожидался {edition.edition}"
        )
    if results.source_id not in edition.source_index:
        raise ContentError(f"{path}: неизвестный source_id «{results.source_id}»")

    judge_slugs = {judge.slug for judge in edition.judges}
    for judge in results.judges:
        if judge.judge is not None and judge.judge not in judge_slugs:
            raise ContentError(
                f"{path}: судья «{judge.code}» ссылается на неизвестного «{judge.judge}»"
            )

    nominations = edition.nomination_index
    # Порядок номинаций внутри дня = порядок строк в файле выпуска: по нему
    # листы и нумеровались. Демо-каркас каталога в этот порядок не входит.
    order: dict[int, list[str]] = {}
    for nomination in edition.nominations:
        if nomination.demo or nomination.day is None:
            continue
        order.setdefault(nomination.day, []).append(nomination.slug)

    for item in results.nominations:
        found = nominations.get(item.nomination)
        if found is None:
            raise ContentError(
                f"{path}: номинации «{item.nomination}» нет в выпуске {edition.edition}"
            )
        if found.demo:
            raise ContentError(
                f"{path}: «{item.nomination}» — каркас каталога, а не номинация выпуска"
            )
        if found.day != item.day:
            raise ContentError(
                f"{path}: {item.nomination}: день {item.day}, а в выпуске — {found.day}"
            )
        day_slugs = order.get(item.day, [])
        if not 1 <= item.number <= len(day_slugs):
            raise ContentError(
                f"{path}: {item.nomination}: номера {item.number} нет в дне {item.day} "
                f"(номинаций в дне: {len(day_slugs)})"
            )
        expected = day_slugs[item.number - 1]
        if expected != item.nomination:
            raise ContentError(
                f"{path}: {item.nomination} в листах идёт под номером {item.number}, "
                f"а в выпуске на этом месте «{expected}»"
            )
        _check_participant_names(path, item, found)


def _check_participant_names(path: Path, item: ResultNomination, nomination: Nomination) -> None:
    """Детское соло и дуэт — инициалы или сценическое имя, без фамилии (§6.1 п.6).

    Проверка стоит на загрузке, а не только в импортёре: YAML правят руками,
    и одно полное имя, вернувшееся из CSV, не должно доехать до страницы.
    Дуэт в листах записан как «А. Б. и В. Г.» — поэтому проверяется каждая часть.

    Одно слово без пробела разрешено осознанно: в листах есть сценические имена
    («Лисичка», «Emily», «NUTS»), и превращать их в «Л.» значило бы выдумать
    инициал. Фамилия без имени в листах не встречается — там всегда «Фамилия Имя».
    """

    if nomination.age_group is AgeGroup.ADULTS or nomination.format not in (
        DanceFormat.SOLO,
        DanceFormat.DUET,
    ):
        return
    for row in item.rows:
        parts = row.participant.split(" и ")
        if not all(INITIALS_RE.match(part) or " " not in part for part in parts):
            raise ContentError(
                f"{path}: {item.nomination}: «{row.participant}» — детская номинация, "
                "в ней публикуются только инициалы или сценическое имя"
            )


def load_results_file(path: Path, bundle: EditionBundle | None = None) -> Results:
    """То же, что `load_results`, но с кэшем по mtime и размеру файла."""

    path = Path(path)
    if not path.exists():
        raise ContentError(f"{path}: файл не найден")
    stat = path.stat()
    results = _results_cached(str(path), (stat.st_mtime_ns, stat.st_size, len(str(path))))
    if bundle is not None:
        _check_results(results, bundle, path)
    return results


def result_years(settings: Settings | None = None) -> list[int]:
    """Годы, для которых листы разобраны: файл есть — ссылка честная.

    Содержимое здесь не читается: страница проверит его сама, а `/history`
    нужен только ответ «есть ли куда вести».
    """

    settings = settings or get_settings()
    if not settings.results_dir.is_dir():
        return []
    years = [
        int(path.stem)
        for path in settings.results_dir.glob("*.yaml")
        if path.stem.isdigit() and 2010 <= int(path.stem) <= 2100
    ]
    return sorted(years, reverse=True)


def load_results_for(settings: Settings, year: int) -> Results | None:
    """Результаты конкретного выпуска; `None` — файла нет, это не авария."""

    path = settings.results_dir / f"{year}.yaml"
    if not path.exists():
        return None
    bundle = next((item for item in load_site(settings) if item.year == year), None)
    if bundle is None:
        raise ContentError(f"{path}: выпуска {year} нет в content/editions")
    return load_results_file(path, bundle)


def load_all_results(settings: Settings | None = None) -> list[Results]:
    """Все разобранные выпуски: проверка целостности — часть загрузки."""

    settings = settings or get_settings()
    loaded: list[Results] = []
    for year in result_years(settings):
        results = load_results_for(settings, year)
        if results is not None:
            loaded.append(results)
    return loaded


@lru_cache(maxsize=8)
def _knowledge_cached(
    glossary_file: str,
    faq_file: str,
    source_ids: frozenset[str],
    fingerprint: tuple[tuple[str, int, int], ...],
) -> Knowledge:
    return Knowledge(
        glossary=load_glossary(Path(glossary_file)),
        faq=load_faq(Path(faq_file), set(source_ids)),
    )


def load_knowledge(settings: Settings | None = None) -> Knowledge:
    settings = settings or get_settings()
    # FAQ общий для сайта: источник может быть из любого выпуска, не только из свежего.
    source_ids = frozenset(
        source_id for bundle in load_site(settings) for source_id in bundle.edition.source_index
    )
    return _knowledge_cached(
        str(settings.glossary_file),
        str(settings.faq_file),
        source_ids,
        _content_fingerprint(settings),
    )


def load_latest(settings: Settings | None = None) -> EditionBundle:
    """Свежий выпуск — то, что показываем на главной и в расписании."""

    return load_site(settings)[0]


def _summary(bundle: EditionBundle) -> str:
    edition = bundle.edition
    demo = sum(1 for participant in edition.participants if participant.demo)
    parts = [
        edition.title,
        f"{edition.days} дн.",
        f"{len(edition.blocks)} блока",
        f"{len(edition.nominations)} номинаций",
        f"{len(edition.participants)} участников",
        f"{len(edition.performances)} выступлений",
    ]
    if demo:
        parts.append(f"{demo} из них демонстрационные")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.content",
        description="Проверка YAML-выпусков «ЗАРУБА»",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser("check", help="проверить один файл или весь content/")
    check.add_argument("paths", nargs="*", type=Path, help="пути к файлам выпусков")

    import_cmd = subcommands.add_parser(
        "import", help="проверить файл и показать, что попадёт в базу"
    )
    import_cmd.add_argument("path", type=Path, help="путь к файлу выпуска")

    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            if args.paths:
                bundles = [load_edition(path) for path in args.paths]
            else:
                settings = get_settings()
                bundles = load_site(settings)
                archive = load_history(settings.history_file)
                photos = load_photos(settings.photos_file)
                results = load_all_results(settings)
                knowledge = load_knowledge(settings)
                alerts = load_alerts(settings)
                # Офферы проверяются тем же прогоном: битый `offers.yaml` должен
                # ломать pre-commit, а не первую же страницу в проде. Демо-файл
                # проверяется всегда, даже без preview: показ не должен падать.
                tips = load_service_tips(settings)
                demo_tips = load_preview_service_tips(settings)
                print(f"OK  история: {len(archive)} записей до 2025 года")
                print(f"OK  глоссарий: {len(knowledge.glossary)} статей")
                print(f"OK  FAQ: {len(knowledge.faq)} вопросов")
                print(f"OK  рекламные слоты: {len(tips)} офферов")
                urgent = sum(1 for item in alerts if item.in_feed)
                print(f"OK  оповещения: {len(alerts)} строк, срочных к показу {urgent}")
                print(f"OK  демо-офферы: {len(demo_tips)} (только preview)")
                own = sum(1 for photo in photos if photo.file)
                cited = sum(1 for photo in photos if photo.is_citation)
                print(
                    f"OK  фотоархив: {len(photos)} карточек, из них с файлом {own} "
                    f"(цитат по ст. 1274 ГК РФ: {cited})"
                )
                for item in results:
                    placed = sum(1 for n in item.nominations for row in n.rows if row.place)
                    print(
                        f"OK  результаты {item.edition}: {len(item.nominations)} номинаций, "
                        f"{item.rows} строк, мест присвоено {placed}"
                    )
            for bundle in bundles:
                print(f"OK  {bundle.year}: {_summary(bundle)}")
            print(f"проверено выпусков: {len(bundles)}")
            return 0
        bundle = load_edition(args.path)
    except ContentError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(f"OK  {bundle.year}: {_summary(bundle)}")
    print("импорт идемпотентен по slug; БД появится на этапе M4, сейчас файл только проверен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
