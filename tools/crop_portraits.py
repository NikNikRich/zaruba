"""Портреты жюри: кроп 3:4 по рамке вокруг головы.

Карточки `/judges` показывают лицо крупно, поэтому на сайте стоят не афиши
целиком, а кропы 480×640: с 160-пиксельной карточки афишу не прочитать, а лицо
в ней занимает меньше четверти кадра.

Источники в `assets/photos/people/` двух видов:

* официальные фото со страниц организаций — `burtsev.jpg` (педагог MDC NRG,
  nrg-style.ru) и `mc-geo.jpg` (хореограф PROТАНЦЫ CAMP, protancycamp.com):
  там человек снят крупно, рамка только убирает лишний фон;
* афиши сообщества «ЗАРУБА72» — `fara.jpg`, `gubanova.jpg`, `bonchinche.jpg`
  (копии лежат в `people/posters/`): фото человека в них — узкая вертикальная
  полоса, поэтому рамки выставлены по головам, с запасом над макушкой
  и под подбородком, чтобы не срезать лицо.

Координаты — в пикселях исходника; ширина и высота рамки относятся как 3:4 —
как у пресета `portrait` (480×640). Кропы пишутся в `people/portrait/<slug>.jpg`,
откуда их забирает `tools/prepare_photos.py`:

    python tools/crop_portraits.py --check
    python tools/crop_portraits.py
    python tools/prepare_photos.py assets/photos/people/portrait
    python tools/crop_portraits.py --slug mc-geo

Исходники в git не попадают: коммитятся только готовые WebP/JPEG из
`src/zaruba/static/img/people/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "photos" / "people"
# Кропы — в подпапку: `prepare_photos.py` по папке иначе взял бы и исходники
# (фото и афиши) и сделал центральный кроп вместо рамки по лицу.
OUTPUT = SOURCE / "portrait"
ASPECT = (3, 4)  # ширина:высота пресета `portrait`

# Рамки: x1, y1, x2, y2 в пикселях исходника. Макушка — с запасом сверху,
# подбородок — с запасом снизу: обрезанная голова на карточке заметнее, чем
# полоса фона по краям кадра. Голова по возможности по центру рамки.
FRAMES: dict[str, tuple[int, int, int, int]] = {
    # Афиши 1024×1280 (fara — 1080×1350), фото в левой полосе.
    "fara": (40, 110, 370, 550),  # 330×440
    "gubanova": (70, 110, 370, 510),  # 300×400
    "bonchinche": (40, 45, 340, 445),  # 300×400
    # Официальные фото со страниц организаций.
    "burtsev": (1150, 1010, 2050, 2210),  # 900×1200, рост 3898×5847
    "mc-geo": (200, 25, 1550, 1825),  # 1350×1800, квадрат 2000×2000
}


def _load_pillow() -> object:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - зависит от окружения, не от кода
        print(
            "Нужен Pillow: python -m pip install pillow\n"
            "Он не нужен сайту в рантайме — только этому скрипту.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return Image


def _force_utf8() -> None:
    """Консоль Windows по умолчанию cp1251: без этого печать «×» роняет скрипт."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def frame_problem(box: tuple[int, int, int, int], size: tuple[int, int]) -> str | None:
    """Проблема с рамкой для исходника `size` — или None, если рамка годная."""

    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0:
        return "пустая рамка"
    if width * ASPECT[1] != height * ASPECT[0]:
        return f"рамка {width}×{height} не {ASPECT[0]}:{ASPECT[1]}"
    if x1 < 0 or y1 < 0 or x2 > size[0] or y2 > size[1]:
        return f"рамка выходит за исходник {size[0]}×{size[1]}"
    return None


def crop_one(
    slug: str,
    box: tuple[int, int, int, int],
    source_dir: Path,
    out_dir: Path,
    quality: int,
    check: bool,
) -> tuple[tuple[int, int], Path]:
    Image = _load_pillow()
    source = source_dir / f"{slug}.jpg"
    if not source.exists():
        raise FileNotFoundError(f"нет исходника {source.relative_to(ROOT)}")
    target = out_dir / f"{slug}.jpg"

    with Image.open(source) as raw:
        problem = frame_problem(box, raw.size)
        if problem:
            raise ValueError(problem)
        size = (box[2] - box[0], box[3] - box[1])
        if not check:
            out_dir.mkdir(parents=True, exist_ok=True)
            # convert("RGB") до кропа: PNG с прозрачностью иначе даст чёрные поля.
            raw.convert("RGB").crop(box).save(
                target, "JPEG", quality=quality, optimize=True, progressive=True
            )
        return (raw.size, size), target


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(
        prog="python tools/crop_portraits.py",
        description="Кроп портретов 3:4 по рамкам вокруг головы (фото и афиши)",
    )
    parser.add_argument(
        "slugs",
        nargs="*",
        choices=sorted(FRAMES),
        help="кроп только этих людей (по умолчанию все рамки)",
    )
    parser.add_argument("--slug", action="append", default=None, help="то же, флагом")
    parser.add_argument(
        "--source", type=Path, default=SOURCE, help="папка с исходниками (фото и афиши)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT,
        help="куда писать портреты (по умолчанию people/portrait)",
    )
    parser.add_argument("--quality", type=int, default=95, help="качество JPEG (по умолчанию 95)")
    parser.add_argument(
        "--check", action="store_true", help="только проверить рамки, ничего не записывать"
    )
    args = parser.parse_args(argv)

    slugs = args.slug or args.slugs or sorted(FRAMES)
    unknown = [slug for slug in slugs if slug not in FRAMES]
    if unknown:
        print(f"Нет рамки для: {', '.join(unknown)}", file=sys.stderr)
        return 1

    problems = 0
    for slug in slugs:
        try:
            (source_size, size), target = crop_one(
                slug, FRAMES[slug], args.source, args.out, args.quality, args.check
            )
        except (OSError, ValueError) as exc:
            print(f"ОШИБКА: {slug}: {exc}", file=sys.stderr)
            problems += 1
            continue
        tail = "рамка годная" if args.check else str(target.relative_to(ROOT))
        print(f"{slug}: {size[0]}×{size[1]} из {source_size[0]}×{source_size[1]} → {tail}")

    if args.check:
        print("\n--check: файлы не изменены")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
