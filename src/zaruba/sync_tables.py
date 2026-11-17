"""Таблицы предпросмотра → overlay-слой выпуска.

Вход — `docs/templates/*.csv` (UTF-8 BOM, разделитель `;`): одна строка — одна
сущность модели, колонка `data_tag` отделяет подтверждённые факты (`real`) от
презентационных (`preview`). Выход — `content/editions/<год>.d/*.yaml`, который
загрузчик подмешивает только при `ZARUBA_PREVIEW_ENABLED=1`.

`real`-строки не перезаписывают опубликованный YAML: скрипт сверяет их с ним и
падает при расхождении. Подтверждённый факт меняется отдельной правкой базового
файла, а не импортом таблицы. `preview`-строки живут в overlay и удаляются
командой `--drop-preview`.

    .venv\\Scripts\\python.exe -m zaruba.sync_tables --check
    .venv\\Scripts\\python.exe -m zaruba.sync_tables
    .venv\\Scripts\\python.exe -m zaruba.sync_tables --drop-preview
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from .config import BASE_DIR
from .content import ContentError, merge_overlay, overlay_dir
from .models import Edition, Judge, Nomination, Participant, Performance, ScheduleBlock, Studio

TAG_REAL = "real"
TAG_PREVIEW = "preview"
TAGS = frozenset({TAG_REAL, TAG_PREVIEW})


class TableError(Exception):
    """Ошибка таблицы: сообщение называет файл, строку и колонку."""


# UTF-16 (LE/BE) начинается с BOM: такой файл мы не разбираем, но и молча
# превращать его в «кракозябры» через cp1251 нельзя — ошибка должна быть
# понятной. Остальные два BOM (`utf-8-sig`) снимает сам кодек.
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _read_table_text(path: Path) -> str:
    """Читает CSV в UTF-8 (с BOM или без) либо Windows-1251.

    Русский Excel сохраняет таблицы в cp1251, «Блокнот» и Google Sheets — в
    UTF-8. Порядок важен: cp1251 декодирует почти любые байты, поэтому её
    пробуем последней, иначе UTF-8 с кириллицей молча стал бы мусором.
    """

    raw = path.read_bytes()
    if raw.startswith(UTF16_BOMS):
        raise TableError(
            f"{path}: файл сохранён в UTF-16 — пересохраните таблицу в UTF-8 или Windows-1251"
        )
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TableError(
        f"{path}: не удалось определить кодировку — нужен UTF-8 (с BOM или без) или Windows-1251"
    )


def _text(row: dict[str, str], key: str) -> str | None:
    value = row.get(key, "").strip()
    return value or None


def _number(row: dict[str, str], key: str) -> int | None:
    value = _text(row, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise TableError(f"колонка {key}: «{value}» — не целое число") from exc


def _items(row: dict[str, str], key: str) -> list[str]:
    value = _text(row, key)
    if value is None:
        return []
    return [part.strip() for part in value.replace(",", "|").split("|") if part.strip()]


def _present(**values: object) -> dict[str, Any]:
    """Только заданные поля: пустая клетка оставляет значение модели по умолчанию."""

    return {key: value for key, value in values.items() if value is not None and value != []}


def _studio(row: dict[str, str], preview: bool) -> Studio:
    return Studio(
        **_present(
            slug=row["slug"],
            name=row["name"],
            city=_text(row, "city"),
            vk_url=_text(row, "vk_url"),
            demo=True if preview else None,
        )
    )


def _participant(row: dict[str, str], preview: bool) -> Participant:
    return Participant(
        **_present(
            slug=row["slug"],
            display_name=row["display_name"],
            age_group=row["age_group"],
            studio=row["studio"],
            city=_text(row, "city"),
            consent_status=_text(row, "consent_status"),
            consent_at=_text(row, "consent_at"),
            demo=True if preview else None,
        )
    )


def _block(row: dict[str, str], preview: bool) -> ScheduleBlock:
    # У блока нет поля demo: презентационность видна по номинациям и баннеру.
    del preview
    return ScheduleBlock(
        **_present(
            slug=row["slug"],
            day=_number(row, "day"),
            title=row["title"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            stage=_text(row, "stage"),
            nominations=_items(row, "nominations"),
            note=_text(row, "note"),
        )
    )


def _performance(row: dict[str, str], preview: bool) -> Performance:
    return Performance(
        **_present(
            slug=row["slug"],
            title=row["title"],
            nomination=row["nomination"],
            participants=_items(row, "participants"),
            scheduled_at=row["scheduled_at"],
            order_no=_number(row, "order_no"),
            stage=_text(row, "stage"),
            status=_text(row, "status"),
            video_url=_text(row, "video_url"),
            source_id=_text(row, "source_id"),
            demo=True if preview else None,
        )
    )


def _nomination(row: dict[str, str], preview: bool) -> Nomination:
    return Nomination(
        **_present(
            slug=row["slug"],
            name=_text(row, "name"),
            style=_text(row, "style"),
            format=row["format"],
            age_group=row["age_group"],
            levels=_items(row, "levels"),
            min_participants=_number(row, "min_participants"),
            max_participants=_number(row, "max_participants"),
            day=_number(row, "day"),
            starts_at=_text(row, "starts_at"),
            ends_at=_text(row, "ends_at"),
            stage=_text(row, "stage"),
            status=_text(row, "status"),
            note=_text(row, "note"),
            source_id=_text(row, "source_id"),
            demo=True if preview else None,
        )
    )


def _judge(row: dict[str, str], preview: bool) -> Judge:
    photo = _text(row, "photo")
    return Judge(
        **_present(
            slug=row["slug"],
            name=row["name"],
            role=_text(row, "role"),
            city=_text(row, "city"),
            regalia=_text(row, "regalia"),
            styles=_items(row, "styles"),
            blocks=_items(row, "blocks"),
            source_id=_text(row, "source_id"),
            photo=photo,
            photo_author=_text(row, "photo_author") if photo else None,
            photo_license=_text(row, "photo_license") if photo else None,
            photo_use=_text(row, "photo_use") if photo else None,
            photo_purpose=_text(row, "photo_purpose") if photo else None,
            photo_source_url=_text(row, "photo_source_url") if photo else None,
            photo_nature=_text(row, "photo_nature") if photo else None,
            demo=True if preview else None,
        )
    )


@dataclass(frozen=True)
class TableSpec:
    section: str
    filename: str
    columns: tuple[str, ...]
    model: type[BaseModel]
    build: Callable[[dict[str, str], bool], BaseModel]


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "studios",
        "studios.csv",
        ("slug", "name", "city", "vk_url", "demo", "data_tag"),
        Studio,
        _studio,
    ),
    TableSpec(
        "participants",
        "participants.csv",
        (
            "slug",
            "display_name",
            "age_group",
            "studio",
            "city",
            "consent_status",
            "consent_at",
            "demo",
            "data_tag",
        ),
        Participant,
        _participant,
    ),
    TableSpec(
        "blocks",
        "blocks.csv",
        (
            "slug",
            "day",
            "title",
            "starts_at",
            "ends_at",
            "stage",
            "nominations",
            "note",
            "data_tag",
        ),
        ScheduleBlock,
        _block,
    ),
)


TABLE_SPECS += (
    TableSpec(
        "performances",
        "performances.csv",
        (
            "slug",
            "title",
            "nomination",
            "participants",
            "scheduled_at",
            "order_no",
            "stage",
            "status",
            "video_url",
            "source_id",
            "demo",
            "data_tag",
        ),
        Performance,
        _performance,
    ),
    TableSpec(
        "nominations",
        "nominations.csv",
        (
            "slug",
            "name",
            "style",
            "format",
            "age_group",
            "levels",
            "min_participants",
            "max_participants",
            "day",
            "starts_at",
            "ends_at",
            "stage",
            "status",
            "note",
            "source_id",
            "demo",
            "data_tag",
        ),
        Nomination,
        _nomination,
    ),
    TableSpec(
        "judges",
        "judges.csv",
        (
            "slug",
            "name",
            "role",
            "city",
            "regalia",
            "styles",
            "blocks",
            "source_id",
            "photo",
            "photo_author",
            "photo_license",
            "photo_use",
            "photo_purpose",
            "photo_source_url",
            "photo_nature",
            "demo",
            "data_tag",
        ),
        Judge,
        _judge,
    ),
)


@dataclass(frozen=True)
class Summary:
    real: dict[str, int]
    preview: dict[str, int]

    def line(self) -> str:
        real = sum(self.real.values())
        preview = sum(self.preview.values())
        parts = [f"{section}: {count}" for section, count in self.preview.items() if count]
        detail = f" ({', '.join(parts)})" if parts else ""
        return f"real: {real}, preview: {preview}{detail}"


def _validation_message(path: Path, exc: ValidationError) -> str:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<корень>"
        details.append(f"{location}: {error['msg']}")
    return f"{path}: " + "; ".join(details)


def read_table(spec: TableSpec, tables_dir: Path) -> list[dict[str, str]]:
    """Строки таблицы с проверкой шапки, тега и повторов slug."""

    path = tables_dir / spec.filename
    if not path.is_file():
        raise TableError(f"{path}: файл таблицы не найден")
    raw = _read_table_text(path)
    if not raw.strip():
        raise TableError(f"{path}: таблица пуста")

    try:
        delimiter = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(raw.splitlines(), delimiter=delimiter)
    fields = tuple((name or "").strip() for name in (reader.fieldnames or ()))
    missing = [name for name in spec.columns if name not in fields]
    unknown = [name for name in fields if name not in spec.columns]
    if missing or unknown:
        raise TableError(f"{path}: шапка не совпадает с моделью (нет {missing}, лишние {unknown})")

    rows: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    for raw_row in reader:
        row = {
            (key or "").strip(): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(row.values()):
            continue
        line = reader.line_num
        tag = row.get("data_tag", "")
        if tag not in TAGS:
            raise TableError(f"{path}:{line}: data_tag «{tag}» — нужен real или preview")
        slug = row.get("slug", "")
        if not slug:
            raise TableError(f"{path}:{line}: пустой slug")
        if slug in seen:
            raise TableError(
                f"{path}:{line}: slug «{slug}» повторяется — впервые в строке {seen[slug]}"
            )
        seen[slug] = line
        rows.append(row)
    if not rows:
        raise TableError(f"{path}: нет ни одной строки")
    return rows


def _base_rows(base: dict[str, object], section: str, path: Path) -> list[dict[str, object]]:
    value = base.get(section, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise TableError(f"{path}: раздел «{section}» в выпуске — не список")
    return [item for item in value if isinstance(item, dict)]


def _check_real_rows(
    spec: TableSpec,
    rows: list[dict[str, str]],
    base_rows: list[dict[str, object]],
    table_path: Path,
    base_path: Path,
) -> None:
    """`real`-строки — зеркало опубликованного YAML, а не источник записи."""

    truth = {str(row.get("slug")): row for row in base_rows if isinstance(row.get("slug"), str)}
    real_rows = [row for row in rows if row["data_tag"] == TAG_REAL]
    real_slugs = {row["slug"] for row in real_rows}
    if real_slugs != set(truth):
        missing = sorted(set(truth) - real_slugs)
        extra = sorted(real_slugs - set(truth))
        raise TableError(
            f"{table_path}: real-строки не совпадают с {base_path.name}: "
            f"нет в таблице {missing}, нет в YAML {extra}"
        )

    for row in real_rows:
        slug = row["slug"]
        try:
            from_table = spec.build(row, False)
            from_base = spec.model.model_validate(truth[slug])
        except ValidationError as exc:
            raise TableError(_validation_message(table_path, exc)) from exc
        left = from_table.model_dump()
        right = from_base.model_dump()
        different = sorted(key for key in left if left[key] != right[key])
        if different:
            raise TableError(
                f"{table_path}: {spec.section}.{slug}: таблица расходится с YAML "
                f"по полям {different} — правьте опубликованный файл, а не таблицу"
            )


def _preview_rows(spec: TableSpec, rows: list[dict[str, str]], path: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for row in rows:
        if row["data_tag"] != TAG_PREVIEW:
            continue
        try:
            model = spec.build(row, True)
        except ValidationError as exc:
            raise TableError(_validation_message(path, exc)) from exc
        payload = model.model_dump(mode="json", exclude_none=True)
        if "demo" in type(model).model_fields:
            payload["demo"] = True
        prepared.append(payload)
    return prepared


def _at(value: object, where: str) -> time:
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise TableError(f"{where}: «{value}» — не время ЧЧ:ММ") from exc


def _moment(value: object, where: str) -> time:
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError as exc:
        raise TableError(f"{where}: «{value}» — не дата-время ISO 8601") from exc


def timing_patches(
    base: dict[str, object],
    overlay: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Время номинации из её выступлений: «Положение» тайминга не даёт.

    Патч не трогает название, возраст и источник: в базе остаётся подтверждённый
    факт, а в overlay — только вычисленные границы для страницы расписания.
    """

    published = {
        str(row.get("slug"))
        for row in _base_rows(base, "nominations", Path("<base>"))
        if row.get("starts_at") is not None
    }
    shows: dict[str, list[dict[str, Any]]] = {}
    for row in overlay.get("performances", []):
        shows.setdefault(str(row["nomination"]), []).append(row)

    patches: list[dict[str, Any]] = []
    patched: set[str] = set()
    for block in overlay.get("blocks", []):
        block_start = _at(block["starts_at"], f"blocks.{block['slug']}.starts_at")
        block_end = _at(block["ends_at"], f"blocks.{block['slug']}.ends_at")
        for slug in block.get("nominations", []):
            slug = str(slug)
            if slug in published or slug in patched:
                continue
            rows = shows.get(slug, [])
            if not rows:
                raise TableError(
                    f"blocks.{block['slug']}: у номинации «{slug}» нет выступлений — "
                    "время в расписании вывести не из чего"
                )
            moments = []
            for row in rows:
                moment = _moment(row["scheduled_at"], f"performances.{row['slug']}.scheduled_at")
                if moment < block_start or moment > block_end:
                    raise TableError(
                        f"performances.{row['slug']}: {moment:%H:%M} вне блока "
                        f"{block_start:%H:%M}–{block_end:%H:%M}"
                    )
                moments.append(moment)
            first = max(min(moments), block_start)
            finish = min(
                datetime.combine(date.min, max(moments)) + timedelta(minutes=10),
                datetime.combine(date.min, block_end),
            ).time()
            if finish <= first:
                raise TableError(f"blocks.{block['slug']}: у номинации «{slug}» пустое окно")
            patches.append(
                {
                    "slug": slug,
                    "starts_at": first.strftime("%H:%M"),
                    "ends_at": finish.strftime("%H:%M"),
                }
            )
            patched.add(slug)
    return patches


def build_overlay(
    tables_dir: Path, base_path: Path
) -> tuple[dict[str, list[dict[str, Any]]], Summary]:
    """Читает таблицы, сверяет real-строки и собирает overlay в память."""

    if not base_path.is_file():
        raise TableError(f"{base_path}: файл выпуска не найден")
    loaded = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TableError(f"{base_path}: ожидался YAML-объект выпуска")
    base: dict[str, object] = loaded

    overlay: dict[str, list[dict[str, Any]]] = {}
    real: dict[str, int] = {}
    preview: dict[str, int] = {}
    for spec in TABLE_SPECS:
        path = tables_dir / spec.filename
        rows = read_table(spec, tables_dir)
        _check_real_rows(spec, rows, _base_rows(base, spec.section, base_path), path, base_path)
        real[spec.section] = sum(1 for row in rows if row["data_tag"] == TAG_REAL)
        prepared = _preview_rows(spec, rows, path)
        preview[spec.section] = len(prepared)
        if prepared:
            overlay[spec.section] = prepared

    patches = timing_patches(base, overlay)
    if patches:
        overlay.setdefault("nominations", []).extend(patches)

    merged = merge_overlay(base, [(base_path, overlay)])
    try:
        Edition.model_validate(merged)
    except ValidationError as exc:
        raise TableError(_validation_message(base_path, exc)) from exc
    return overlay, Summary(real=real, preview=preview)


def _overlay_yaml(section: str, rows: list[dict[str, Any]]) -> str:
    return yaml.safe_dump({section: rows}, allow_unicode=True, sort_keys=False, width=100)


def _check_overlay(overlay: dict[str, list[dict[str, Any]]], out_dir: Path) -> list[str]:
    """Сверяет файлы overlay с тем, что дали таблицы: `--check` не должен врать."""

    expected = {
        out_dir / f"{section}.yaml": _overlay_yaml(section, rows)
        for section, rows in overlay.items()
        if rows
    }
    problems: list[str] = []
    for path, text in expected.items():
        if not path.is_file():
            problems.append(f"нет файла {path.name}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"устарел {path.name}")
    problems.extend(
        f"лишний файл {path.name}"
        for path in sorted(out_dir.glob("*.yaml"))
        if path not in expected
    )
    return problems


def write_overlay(
    overlay: dict[str, list[dict[str, Any]]], out_dir: Path, *, dry_run: bool = False
) -> list[Path]:
    """Пишет только непустые разделы и убирает устаревшие файлы overlay."""

    if dry_run:
        return [out_dir / f"{section}.yaml" for section, rows in overlay.items() if rows]
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for section, rows in overlay.items():
        if not rows:
            continue
        path = out_dir / f"{section}.yaml"
        path.write_text(_overlay_yaml(section, rows), encoding="utf-8")
        written.append(path)
    for stale in sorted(out_dir.glob("*.yaml")):
        if stale not in written:
            stale.unlink()
    return sorted(written)


def drop_preview(out_dir: Path) -> list[Path]:
    """Удаляет overlay: презентационные строки исчезают без правки базового YAML."""

    if not out_dir.is_dir():
        return []
    removed = sorted(out_dir.glob("*.yaml"))
    for path in removed:
        path.unlink()
    with contextlib.suppress(OSError):
        out_dir.rmdir()
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.sync_tables",
        description="CSV-таблицы предпросмотра → content/editions/<год>.d",
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=BASE_DIR / "docs" / "templates",
        help="папка с CSV (по умолчанию docs/templates)",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=BASE_DIR / "content" / "editions" / "2026.yaml",
        help="опубликованный выпуск, с которым сверяются real-строки",
    )
    parser.add_argument("--out-dir", type=Path, help="папка overlay (по умолчанию <год>.d)")
    parser.add_argument("--check", action="store_true", help="только проверить, не писать")
    parser.add_argument("--drop-preview", action="store_true", help="удалить overlay и выйти")
    args = parser.parse_args(argv)
    out_dir = args.out_dir or overlay_dir(args.base)

    try:
        if args.drop_preview:
            removed = drop_preview(out_dir)
            print(f"OK: удалено файлов overlay: {len(removed)}")
            return 0
        overlay, summary = build_overlay(args.tables_dir, args.base)
        if args.check:
            problems = _check_overlay(overlay, out_dir)
            if problems:
                print(
                    f"ОШИБКА: overlay не совпадает с таблицами: {', '.join(problems)}",
                    file=sys.stderr,
                )
                return 1
            print(f"OK  таблицы: {summary.line()}")
            print(f"OK  overlay совпадает: {out_dir}")
            return 0
        written = write_overlay(overlay, out_dir)
        print(f"OK  таблицы: {summary.line()}")
        print(f"OK  записано файлов overlay: {len(written)} → {out_dir}")
        return 0
    except (TableError, ContentError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
