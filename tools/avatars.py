"""Постеры для карточек жюри и ведущего: 3:4, 480×640, WebP + JPEG.

Это иллюстрации, а не портреты: лиц не рисуем, чужие снимки не берём.
На `/judges` они подписаны «иллюстрация», поэтому подмены фотографии нет —
в отличие от «сгенерированного портрета», который выдавал бы рисунок за снимок
человека.

Когда появятся настоящие кадры — свои или с разрешения автора, — их кладут
в `assets/photos/people/`, прогоняют `tools/prepare_photos.py --preset portrait`,
а в YAML ставят `photo_nature: photo` и основание: `photo_license` (use: own)
либо `photo_use: citation` + `photo_purpose` + `photo_source_url` (ст. 1274 ГК РФ).

    python tools/avatars.py --check
    python tools/avatars.py
    python tools/avatars.py --slug fara --edition 2025
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_photos import SLUG_RE  # noqa: E402  (путь к tools задан выше)

from zaruba.models import PhotoKind  # noqa: E402

DEFAULT_OUT = ROOT / "src" / "zaruba" / "static" / "img"
EDITIONS = ROOT / "content" / "editions"
# Автор и лицензия рисунков: подпись собирается из этих двух полей (models.Judge).
AUTHOR = "проект «ЗАРУБА»"
LICENSE = "CC BY 4.0"


def _force_utf8() -> None:
    """Консоль Windows по умолчанию cp1251: без этого печать «×» роняет скрипт."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def latest_edition(editions: Path = EDITIONS) -> Path:
    """Тот выпуск, который показывает `/judges`: последний по номеру года."""

    found = sorted(path for path in editions.glob("*.yaml") if path.stem.isdigit())
    if not found:
        raise SystemExit(f"Нет выпусков в {editions}")
    return found[-1]


def judge_slugs(edition: Path) -> list[str]:
    """Slug'и из самого YAML: список людей не дублируется в скрипте."""

    data = yaml.safe_load(edition.read_text(encoding="utf-8")) or {}
    judges = data.get("judges") or []
    return [str(item["slug"]) for item in judges if isinstance(item, dict) and item.get("slug")]


def _load_pillow() -> tuple[object, object]:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - зависит от окружения, не от кода
        print(
            "Нужен Pillow: python -m pip install pillow\n"
            "Он не нужен сайту в рантайме — только этому скрипту.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    from photo_fx import poster

    return Image, poster


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(
        prog="python tools/avatars.py",
        description="Иллюстрации 3:4 для жюри и ведущего: силуэт, неон, частицы",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="корень static/img")
    parser.add_argument(
        "--edition", type=Path, default=None, help="YAML выпуска (по умолчанию 2026)"
    )
    parser.add_argument("--slug", default=None, help="только один человек")
    parser.add_argument("--quality", type=int, default=82, help="качество WebP (по умолчанию 82)")
    parser.add_argument(
        "--check", action="store_true", help="только показать план, ничего не записывать"
    )
    args = parser.parse_args(argv)

    edition = args.edition or latest_edition()
    if not edition.is_file():
        print(f"Нет файла выпуска: {edition}", file=sys.stderr)
        return 1

    slugs = [args.slug] if args.slug else judge_slugs(edition)
    if not slugs:
        print(f"В {edition.name} нет судей: нечего рисовать", file=sys.stderr)
        return 1
    bad = [slug for slug in slugs if not SLUG_RE.fullmatch(slug)]
    if bad:
        print(f"Плохие slug: {', '.join(bad)}", file=sys.stderr)
        return 1

    width, height = PhotoKind.PORTRAIT.size
    out_dir = args.out / "people"
    if args.check:
        for slug in slugs:
            print(f"{slug}: {width}×{height} — план, файлы не тронуты")
        print(f"\n--check: {edition.name}, {len(slugs)} карточек")
        return 0

    Image, poster = _load_pillow()
    out_dir.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        image = poster(slug, (width, height))
        # Пустая копия: `.info` не тянет метаданные в WebP/JPEG.
        clean = Image.new("RGB", image.size)
        clean.paste(image)
        webp_path = out_dir / f"{slug}.webp"
        jpg_path = out_dir / f"{slug}.jpg"
        clean.save(webp_path, "WEBP", quality=args.quality, method=6)
        clean.save(jpg_path, "JPEG", quality=85, optimize=True, progressive=True)
        print(
            f"{slug}: {width}×{height}, "
            f"webp {webp_path.stat().st_size / 1024:.1f} КБ, "
            f"jpg {jpg_path.stat().st_size / 1024:.1f} КБ"
        )

    print(
        "\nКарточки в "
        f"{edition.relative_to(ROOT).as_posix()}:\n"
        "    photo: img/people/<slug>.webp\n"
        f"    photo_author: '{AUTHOR}'\n"
        f"    photo_license: '{LICENSE}'\n"
        "    photo_nature: illustration\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
