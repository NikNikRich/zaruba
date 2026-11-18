"""Сборка `dist/` для MAX Mini App на GitHub Pages (`docs/09`).

Сборка — единственное место, где проверяется результат целиком: страницы,
относительные ссылки, `.nojekyll` и `robots.txt`. Полный обход выполняется
один раз на модуль, дальше тесты читают готовую папку.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    """`scripts/` — не пакет: путь добавляется до импорта сборщика."""

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))

    import build_max_dist

    return build_max_dist


@pytest.fixture(scope="module")
def dist(builder: ModuleType, demo_settings, tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("max-dist") / "dist"
    settings = demo_settings.model_copy(update={"max_miniapp": True})
    builder.build(target, settings)
    return target


def test_build_writes_pages_static_and_pages_markers(dist: Path) -> None:
    assert (dist / "index.html").is_file()
    assert (dist / ".nojekyll").is_file()
    assert (dist / ".nojekyll").read_text(encoding="utf-8") == ""
    assert "Disallow: /" in (dist / "robots.txt").read_text(encoding="utf-8")
    assert (dist / "static" / "style.css").is_file()
    assert (dist / "static" / "max-bridge.js").is_file()
    assert len(list(dist.glob("*.html"))) >= 15


def test_build_keeps_links_relative(dist: Path) -> None:
    """С GitHub Pages `/static/...` ушёл бы в корень домена, а не в репозиторий."""

    pages = sorted(dist.rglob("*.html"))
    assert pages, "в сборке нет ни одной страницы"

    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert not re.search(r'(?:href|src|action|poster)="/', html), page
        assert not re.search(r'(?:href|src|action|poster)="http://testserver', html), page
        assert '<link rel="canonical"' not in html, page


def test_build_has_nested_cards(dist: Path) -> None:
    """Ссылки из подпапки (`../static/...`) — тот случай, который ломается первым."""

    nested = [page for page in dist.rglob("*.html") if page.parent != dist]

    assert nested, "в сборке нет ни одной карточки каталога"


def test_build_includes_max_bridge(dist: Path) -> None:
    html = (dist / "index.html").read_text(encoding="utf-8")

    assert "https://st.max.ru/js/max-web-app.js" in html
    assert re.search(r"static/max-bridge\.js\?v=[0-9a-f]{8}", html)
    assert "site-header--embedded" in html


def test_clean_output_removes_stale_files(builder: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "dist"
    target.mkdir()
    (target / "stale.html").write_text("прошлый выпуск", encoding="utf-8")

    builder.clean_output(target)

    assert target.is_dir()
    assert not list(target.iterdir())


def test_refuses_to_clean_sources(builder: ModuleType) -> None:
    for path in (REPO_ROOT, REPO_ROOT / "src", REPO_ROOT / "content"):
        with pytest.raises(builder.PreviewBuildError):
            builder.check_target(path)
