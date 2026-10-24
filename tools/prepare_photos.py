"""Приведение фото к одному формату: пресеты из `zaruba.models.PhotoKind`.

Зачем скрипт: размеры кадра заданы кодом (`gallery` — 4:3, 960×720, `portrait` —
3:4, 480×640), и шаблон ждёт ровно их. Фото с телефона, камеры и скриншота
из альбома получаются одинаковыми, если прогнать их здесь, а не «на глазок».

    python tools/prepare_photos.py --check
    python tools/prepare_photos.py
    python tools/prepare_photos.py --preset portrait --slug fara assets/photos/people/IMG_0421.jpg
    python tools/prepare_photos.py --rights citation --purpose info assets/photos/history/press.jpg

Исходники лежат в `assets/photos/` и в git не попадают: в репозиторий идут
только готовые превью в `src/zaruba/static/img/`. Метаданные (EXIF, GPS, ICC)
не переносятся — на публичной странице им не место.

Пресет `portrait` применяется к портретам жюри: в `people/` лежат исходники —
официальные фото со страниц организаций и афиши сообщества. `tools/crop_portraits.py`
режет их по рамкам вокруг головы в `people/portrait/`, а этот скрипт делает из кропов
480×640: афиша целиком в карточку не встаёт — лица в ней почти не видно. Поэтому обход
по умолчанию папку `people/` пропускает, а кропы прогоняют явным путём.

Подсказка YAML печатает и основание публикации: `--rights own` — свой кадр или
разрешение автора, `--rights citation --purpose info` — цитирование по п. 1
ст. 1274 ГК РФ (информационная, научная, учебная или культурная цель).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zaruba.models import PhotoKind  # noqa: E402  (путь к пакету задан выше)

DEFAULT_SOURCE = ROOT / "assets" / "photos"
DEFAULT_OUT = ROOT / "src" / "zaruba" / "static" / "img"
SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Папка-источник решает пресет, если он не задан флагом: `people` — портреты.
FOLDER_PRESETS = {"people": PhotoKind.PORTRAIT, "history": PhotoKind.GALLERY}


@dataclass(frozen=True)
class Report:
    source: Path
    slug: str
    kind: PhotoKind
    size_in: tuple[int, int]
    webp_bytes: int
    warning: str | None

    @property
    def out_dir(self) -> str:
        return "people" if self.kind is PhotoKind.PORTRAIT else "history"

    def yaml_hint(self, rights: str = "own", purpose: str = "info") -> str:
        """Готовая карточка для YAML — вместе с основанием публикации.

        Два законных основания (см. `models.PhotoUse`): своё разрешение или
        цитирование по п. 1 ст. 1274 ГК РФ. Во втором случае подсказка сразу
        требует цель цитирования — без неё загрузка YAML упадёт, и это нарочно.
        """

        head = (
            f"    kind: {self.kind.value}\n"
            f"    file: img/{self.out_dir}/{self.slug}.webp\n"
            "    alt: ''      # что на снимке, для тех, кто не видит\n"
            "    author: ''   # кто снял: имя обязательно в обоих случаях\n"
        )
        if rights == "citation":
            return (
                head
                + "    use: citation\n"
                + f"    purpose: {purpose}  # info | science | education | culture\n"
                + "    # source: тот же блок, что ниже, — он и есть источник заимствования\n"
                + "    # в карточке человека вместо source — photo_source_url на публикацию\n"
            )
        return head + "    license: ''  # «разрешение организаторов», CC BY, служебное фото\n"


def _load_pillow() -> tuple[object, object]:
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - зависит от окружения, не от кода
        print(
            "Нужен Pillow: python -m pip install pillow\n"
            "Он не нужен сайту в рантайме — только этому скрипту.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return Image, ImageOps


def collect(paths: list[Path], skip: set[Path] | None = None) -> list[Path]:
    """Файлы из аргументов; папки — рекурсивно, чтобы не перечислять руками.

    `skip` — папки, которые обход не берёт: в `people/` лежат исходники (фото
    и афиши), а на сайт идут кропы 3:4 из `people/portrait/`.
    """

    ignored = skip or set()
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(
                sorted(
                    p
                    for p in path.rglob("*")
                    if p.suffix.lower() in SUFFIXES
                    and not any(p.is_relative_to(folder) for folder in ignored)
                )
            )
        elif path.suffix.lower() in SUFFIXES:
            found.append(path)
    return found


def slug_for(path: Path) -> str:
    """Имя файла как slug: `IMG_0421` не годится — переименуйте в `fara`."""

    slug = path.stem.strip().lower().replace("_", "-").replace(" ", "-")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"{path.name}: slug «{slug}» не подходит — нужны строчные латинские буквы, "
            "цифры и дефис (переименуйте файл или задайте --slug)"
        )
    return slug


def preset_for(path: Path, forced: PhotoKind | None) -> PhotoKind:
    if forced is not None:
        return forced
    for parent in path.parents:
        if parent.name in FOLDER_PRESETS:
            return FOLDER_PRESETS[parent.name]
    return PhotoKind.GALLERY


def prepare_one(
    path: Path,
    kind: PhotoKind,
    slug: str,
    out_root: Path,
    quality: int,
    check: bool,
    fx: str = "none",
) -> Report:
    Image, ImageOps = _load_pillow()
    width, height = kind.size
    out_dir = out_root / ("people" if kind is PhotoKind.PORTRAIT else "history")

    with Image.open(path) as raw:
        source_size = raw.size
        # orientation из EXIF: без него вертикальный кадр ляжет набок, а EXIF
        # мы вырезаем — исправлять будет уже нечем.
        upright = ImageOps.exif_transpose(raw).convert("RGB")
        fitted = ImageOps.fit(upright, (width, height), method=Image.LANCZOS)
        if fx != "none":
            # Импорт здесь: без Pillow скрипт уже вышел с понятным сообщением.
            from photo_fx import apply_dance_fx

            fitted = apply_dance_fx(fitted, seed=slug)
        # Чистая копия: у resize и exif_transpose в `.info` остаются exif и icc,
        # и WebP сохранил бы их, хотя публиковать их не нужно.
        clean = Image.new("RGB", fitted.size)
        clean.paste(fitted)

        webp_bytes = 0
        if not check:
            out_dir.mkdir(parents=True, exist_ok=True)
            webp_path = out_dir / f"{slug}.webp"
            clean.save(webp_path, "WEBP", quality=quality, method=6)
            webp_bytes = webp_path.stat().st_size

    warning = None
    if min(source_size) < min(width, height):
        warning = f"исходник {source_size[0]}×{source_size[1]} меньше пресета — будет апскейл"
    return Report(
        source=path,
        slug=slug,
        kind=kind,
        size_in=source_size,
        webp_bytes=webp_bytes,
        warning=warning,
    )


def _kb(value: int) -> str:
    return f"{value / 1024:.1f} КБ"


def _force_utf8() -> None:
    """Консоль Windows по умолчанию cp1251: без этого печать «×» роняет скрипт."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(
        prog="python tools/prepare_photos.py",
        description="Ресайз и кроп фото под пресеты сайта (4:3 и 3:4), без EXIF",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=f"файлы или папки (по умолчанию {DEFAULT_SOURCE.relative_to(ROOT)})",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="корень static/img")
    parser.add_argument(
        "--preset",
        choices=[kind.value for kind in PhotoKind],
        default=None,
        help="пресет для всех файлов; по умолчанию решает папка (history/people)",
    )
    parser.add_argument("--slug", default=None, help="slug для одного файла")
    parser.add_argument(
        "--rights",
        choices=["own", "citation"],
        default="own",
        help="основание в подсказке YAML: own — разрешение автора, citation — ст. 1274 ГК РФ",
    )
    parser.add_argument(
        "--purpose",
        choices=["info", "science", "education", "culture"],
        default="info",
        help="цель цитирования для --rights citation",
    )
    parser.add_argument("--quality", type=int, default=82, help="качество WebP (по умолчанию 82)")
    parser.add_argument(
        "--fx",
        choices=["none", "dance"],
        default="none",
        help="спецэффекты поверх кадра: dance — неон, свет, пыль (лицо не меняется)",
    )
    parser.add_argument(
        "--check", action="store_true", help="только показать план, ничего не записывать"
    )
    args = parser.parse_args(argv)

    # `people/` — исходники, а не превью: на сайт идут кропы 3:4 из `people/portrait/`,
    # которые собирает `tools/crop_portraits.py`. Явный путь в аргументах эту защиту
    # снимает — кропы прогоняют именно так.
    untouched = (DEFAULT_SOURCE / "people") if not args.paths else None
    sources = collect(args.paths or [DEFAULT_SOURCE], {untouched} if untouched else None)
    if untouched is not None and untouched.is_dir():
        print("people/: пропущено — там исходники; портреты собирают из people/portrait")
    if not sources:
        if untouched is not None and untouched.is_dir():
            print("Нечего готовить: в people/ исходники, на сайт идут кропы people/portrait")
            return 0
        print(f"Нет файлов: {args.paths or [DEFAULT_SOURCE]}", file=sys.stderr)
        return 1
    if args.slug and len(sources) != 1:
        print("--slug задаётся только для одного файла", file=sys.stderr)
        return 1

    forced = PhotoKind(args.preset) if args.preset else None
    problems = 0
    for path in sources:
        try:
            slug = args.slug or slug_for(path)
        except ValueError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            problems += 1
            continue
        try:
            report = prepare_one(
                path,
                preset_for(path, forced),
                slug,
                args.out,
                args.quality,
                args.check,
                args.fx,
            )
        except (OSError, ValueError) as exc:
            print(f"ОШИБКА: {path.name}: {exc}", file=sys.stderr)
            problems += 1
            continue

        weight = (
            f"webp {_kb(report.webp_bytes)}" if not args.check else f"качество webp {args.quality}"
        )
        print(
            f"{report.slug}: {report.size_in[0]}×{report.size_in[1]} → "
            f"{report.kind.size[0]}×{report.kind.size[1]}, {weight}"
        )
        if report.warning:
            print(f"  ! {report.warning}")
        print(report.yaml_hint(args.rights, args.purpose))

    if args.check:
        print("\n--check: файлы не изменены")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
