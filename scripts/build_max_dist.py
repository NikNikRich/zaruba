"""Статика для MAX Mini App на GitHub Pages (`docs/09`).

Сборка та же, что у презентационной копии: приложение обходится через
`TestClient`, страницы сохраняются в HTML, внутренние ссылки переписываются на
относительные, `src/zaruba/static/` копируется рядом. Отличий ровно три:

* приложение поднимается с `max_miniapp=True` (MAX Bridge, CSP, компактная шапка);
* в корень кладётся `.nojekyll` — без него GitHub Pages прогоняет файлы через
  Jekyll, а он ломает относительные адреса;
* рядом лежит `robots.txt` с `Disallow: /`: копия на `github.io` — дубль сайта,
  а `canonical` при переписывании ссылок срезается (в статике он вёл бы на
  `testserver`).

Запуск из корня репозитория:

    python scripts/build_max_dist.py              # папка dist/
    python scripts/build_max_dist.py --dist D:\\tmp\\dist

Окружение: `ZARUBA_PREVIEW_ENABLED=1` включает демо-данные, `ZARUBA_ENV=prod`
убирает заглушки рекламных слотов. `ZARUBA_MAX_MINIAPP` сборщик не читает:
он существует только для Mini App и включает режим сам.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from build_static_preview import (
    CARD_PREFIXES,
    BuildStats,
    PreviewBuildError,
    copy_static,
    crawl,
    seed_paths,
    validate,
    write_pages,
)

if TYPE_CHECKING:
    from zaruba.config import Settings


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
DEFAULT_DIST_DIR = REPO_ROOT / "dist"

# Папки, которые нельзя удалять «на всякий случай»: их потеря — не мусор, а
# исходники. `dist` по умолчанию в `.gitignore`, поэтому чистим его смело.
PROTECTED_DIRS = (
    SRC_DIR,
    REPO_ROOT / "content",
    REPO_ROOT / "docs",
    REPO_ROOT / "tests",
    REPO_ROOT / "scripts",
    REPO_ROOT / "deploy",
)

ROBOTS_TEXT = "User-agent: *\nDisallow: /\n"


def check_target(dist: Path) -> Path:
    """Каталог сборки; корень репозитория, `src` и системный корень — отказ."""

    target = dist.resolve()
    if target == REPO_ROOT or target in REPO_ROOT.parents or target in PROTECTED_DIRS:
        raise PreviewBuildError(f"{target} — не папка сборки: удалять её нельзя")
    return target


def clean_output(target: Path) -> None:
    """Полная пересборка: старые файлы в `dist` — мусор от прошлого выпуска."""

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)


def build(dist: Path | None = None, settings: Settings | None = None) -> BuildStats:
    """Собирает сайт в `dist`; `settings` нужен тестам, чтобы взять фикстуры."""

    from fastapi.testclient import TestClient

    if settings is None:
        from zaruba.config import Settings as SettingsClass

        settings = SettingsClass(max_miniapp=True)

    from zaruba.main import create_app

    target = check_target(dist or DEFAULT_DIST_DIR)
    with TestClient(create_app(settings)) as client:
        pages = crawl(client, seed_paths(settings))

    clean_output(target)

    stats = BuildStats(pages=len(pages))
    stats.cards = sum(1 for url in pages if urlsplit(url).path.startswith(CARD_PREFIXES))
    write_pages(target, pages, stats)
    stats.static_files = copy_static(target)
    # `.nojekyll` — пустой файл-маркер: содержимое у него не читается.
    (target / ".nojekyll").write_text("", encoding="utf-8")
    (target / "robots.txt").write_text(ROBOTS_TEXT, encoding="utf-8")
    validate(target, stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать статику для MAX Mini App (GitHub Pages)")
    parser.add_argument(
        "--dist",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help=f"куда положить сборку (по умолчанию {DEFAULT_DIST_DIR})",
    )
    args = parser.parse_args(argv)

    try:
        stats = build(args.dist)
    except PreviewBuildError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(f"OK  страниц: {stats.pages} (из них карточек: {stats.cards})")
    print(f"OK  статика: {stats.static_files} файл(ов)")
    print(f"OK  внутренних ссылок переписано: {stats.rewritten_links}")
    print(f"OK  папка: {args.dist.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
