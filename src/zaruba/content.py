"""Загрузка контента из YAML: пока нет БД, контент живёт в репозитории (`docs/06` §6.17)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError

from .config import Settings, get_settings
from .models import Edition, EditionBundle, FaqItem, GlossaryEntry, Venue

VENUES_ADAPTER = TypeAdapter(list[Venue])
GLOSSARY_ADAPTER = TypeAdapter(list[GlossaryEntry])
FAQ_ADAPTER = TypeAdapter(list[FaqItem])


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


def load_edition(path: Path, venues: dict[str, Venue] | None = None) -> EditionBundle:
    path = Path(path)
    raw = _read_yaml(path)
    try:
        edition = Edition.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(path, exc) from exc

    if venues is None:
        venues = load_venues(path.parent.parent / "venues.yaml")

    venue = venues.get(edition.venue)
    if venue is None:
        raise ContentError(f"{path}: площадки «{edition.venue}» нет в content/venues.yaml")

    return EditionBundle(edition=edition, venue=venue)


def load_editions(content_dir: Path, venues_file: Path | None = None) -> list[EditionBundle]:
    content_dir = Path(content_dir)
    if not content_dir.is_dir():
        raise ContentError(f"{content_dir}: папка с выпусками не найдена")

    files = sorted(content_dir.glob("*.yaml"))
    if not files:
        raise ContentError(f"{content_dir}: нет ни одного файла выпуска")

    venues = load_venues(Path(venues_file) if venues_file else content_dir.parent / "venues.yaml")
    bundles = [load_edition(path, venues) for path in files]

    years = [bundle.year for bundle in bundles]
    duplicates = sorted({year for year in years if years.count(year) > 1})
    if duplicates:
        raise ContentError(f"{content_dir}: выпуск {duplicates} описан в двух файлах")

    return sorted(bundles, key=lambda bundle: bundle.year, reverse=True)


def load_site(settings: Settings | None = None) -> list[EditionBundle]:
    settings = settings or get_settings()
    return load_editions(settings.content_dir, settings.venues_file)


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


@dataclass(frozen=True)
class Knowledge:
    """Не-выпускной контент: глоссарий и FAQ, общий для всех выпусков."""

    glossary: list[GlossaryEntry]
    faq: list[FaqItem]


def load_knowledge(settings: Settings | None = None) -> Knowledge:
    settings = settings or get_settings()
    source_ids = set(load_latest(settings).edition.source_index)
    return Knowledge(
        glossary=load_glossary(settings.glossary_file),
        faq=load_faq(settings.faq_file, source_ids),
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
            bundles = (
                [load_edition(path) for path in args.paths]
                if args.paths
                else load_site()
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
