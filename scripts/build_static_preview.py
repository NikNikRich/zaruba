"""Статическая копия сайта для показа руководству (`ZARUBA_PREVIEW_ENABLED=1`).

Скрипт одноразовый: обходит приложение через `TestClient`, сохраняет каждую
страницу в HTML, копирует `src/zaruba/static/` и переписывает внутренние ссылки
на относительные. Готовый архив открывается двойным щелчком по `index.html`
без Python, без сервера и без интернета.

Запуск из корня репозитория:

    python scripts/build_static_preview.py                  # папка и ZIP
    python scripts/build_static_preview.py --single-file    # один HTML

Результат: папка `.preview-build/` и `zaruba_2026_preview.zip` либо один
`zaruba_2026_preview.html` — он открывается двойным щелчком, без Python,
без сервера и без интернета.

Чего здесь нет намеренно: сервера и JS. Поиск и фильтры — динамика FastAPI,
поэтому в статической копии форма открывает страницу каталога без фильтра;
это ограничение названо в `README.txt` внутри архива, а не спрятано.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import posixpath
import re
import shutil
import sys
import zipfile
from collections import deque
from dataclasses import dataclass, field
from html import escape as html_escape
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from zaruba.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
DEFAULT_BUILD_DIR = REPO_ROOT / ".preview-build"
DEFAULT_ARCHIVE = REPO_ROOT / "zaruba_2026_preview.zip"
DEFAULT_SINGLE_FILE = REPO_ROOT / "zaruba_2026_preview.html"

# Скрипт лежит вне пакета: добавляем `src`, чтобы сборка шла и без
# `pip install -e .` — руководителю не нужно ничего устанавливать, а нам
# не нужен ещё один повод «забыл активировать venv».
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# `url_for('static', ...)` в этой версии Starlette отдаёт абсолютный адрес
# (`http://testserver/static/style.css`). Без этого правила такие ссылки
# остались бы нерабочими в архиве, а CSS и фавикон — недоступными.
INTERNAL_HOSTS = {"testserver", "localhost", "127.0.0.1", "0.0.0.0"}

# Страницы из §6.2–6.15 и служебный блок. Карточки сюда не входят: они
# добавляются по данным выпуска, потому что каталог разбит по 24 записи
# и часть карточек на первую страницу не попадает.
SEED_PAGES: tuple[str, ...] = (
    "/",
    "/event",
    "/schedule",
    "/performances",
    "/participants",
    "/studios",
    "/judges",
    "/results",
    "/tickets",
    "/venue",
    "/faq",
    "/media",
    "/privacy",
    "/about",
    "/rules",
    "/takedown",
    "/glossary",
    "/history",
    "/ratings",
    "/search",
)

# Служебные адреса, которые не являются HTML-страницами сайта.
SKIP_PATHS = frozenset({"/robots.txt", "/sitemap.xml", "/healthz"})
ASSET_SUFFIXES = (".css", ".js", ".png", ".svg", ".webp", ".jpg", ".ico", ".xml", ".txt")

README_TEXT = """\
ЗАРУБА 2026 — презентационная версия сайта
=========================================

Это статическая копия фан-сайта чемпионата в режиме предпросмотра:
демонстрационные студии, участники, выступления и тайминг помечены «пример».

Как открыть
-----------
1. Распакуйте архив в любую папку (на компьютере или телефоне).
2. Откройте файл index.html двойным щелчком — сайт откроется в браузере.
3. Интернет не нужен: стили, фото и все страницы лежат в этом же архиве.

Что не работает без сервера
---------------------------
- Поиск и фильтры: это динамика FastAPI. Форма открывает страницу каталога
  без фильтра, зато все карточки участников, студий и выступлений доступны
  обычными ссылками.
- Внешние ссылки (ВКонтакте, «Кассы Ру», Яндекс.Диск) открываются в браузере
  и требуют интернета.
- Календарь и рекламные офферы — демонстрационные, а не публичная оферта.

Архив собран скриптом scripts/build_static_preview.py из того же приложения,
что работает на сервере: данные и вёрстка не переписывались руками.
"""


class PreviewBuildError(RuntimeError):
    """Сборка не удалась: в архиве не должно быть битых страниц и ссылок."""


@dataclass
class BuildStats:
    """Что получилось: цифры для отчёта и проверки результата."""

    pages: int = 0
    cards: int = 0
    static_files: int = 0
    rewritten_links: int = 0
    missing: list[str] = field(default_factory=list)
    absolute: list[str] = field(default_factory=list)


def _page_number(query: str) -> int | None:
    """Номер страницы каталога, если в query только `page=N`.

    Пагинация — единственный параметр, который имеет смысл в статике: страница
    «2 из 4» становится отдельным файлом. Фильтры и поиск так не сохранить,
    поэтому для них ссылка ведёт на каталог целиком.
    """

    params = parse_qs(query, keep_blank_values=True)
    if set(params) != {"page"}:
        return None
    values = params["page"]
    if len(values) != 1 or not values[0].isdigit():
        return None
    number = int(values[0])
    return number if number > 1 else None


def page_file(path: str, query: str = "") -> str:
    """Имя файла для адреса: `/` → `index.html`, `/a/b` → `a/b.html`."""

    slug = path.strip("/") or "index"
    page = _page_number(query)
    if page is not None:
        return f"{slug}-p{page}.html"
    return f"{slug}.html"


def page_route(path: str, query: str = "") -> str:
    """Адрес страницы без якоря: путь плюс `page=N`, если это пагинация."""

    page = _page_number(query)
    return f"{path}?page={page}" if page is not None else path


def crawl_key(href: str) -> str | None:
    """Адрес страницы, которую нужно обойти, или None для внешних ссылок.

    Возвращается только путь (плюс `page=N`): якорь и фильтры не создают новых
    страниц, а `#block-...` остаётся якорем внутри уже сохранённого файла.
    """

    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return None
    path = parts.path
    if path.startswith("/static/") or path in SKIP_PATHS:
        return None
    if path.endswith(ASSET_SUFFIXES):
        return None
    return page_route(path, parts.query)


def relative_url(href: str, current_file: str) -> str | None:
    """Относительная ссылка для `file://` или None, если адрес внешний.

    `/static/x.css` превращается в путь до скопированного файла, страница
    `/schedule` — в `schedule.html`, а из карточки `performances/foo.html`
    те же ссылки получают `../`. Абсолютные адреса тестового сервера
    (`http://testserver/...`) считаются внутренними: иначе CSS не загрузится.
    """

    parts = urlsplit(href)
    if parts.scheme and parts.scheme not in {"http", "https"}:
        return None
    if parts.netloc and parts.netloc.split(":")[0] not in INTERNAL_HOSTS:
        return None
    path = parts.path
    if not path.startswith("/"):
        return None
    if path.startswith("/static/"):
        target = posixpath.join("static", path[len("/static/") :])
    else:
        if path in SKIP_PATHS or path.endswith(ASSET_SUFFIXES):
            return None
        target = page_file(path, parts.query)

    base = posixpath.dirname(current_file) or "."
    relative = posixpath.relpath(target, base)
    # `?v=<hash>` у CSS — единственный query, который стоит сохранять: это
    # версия файла, а не фильтр, и без неё ссылка тоже работает.
    if target.startswith("static/") and parts.query:
        relative = f"{relative}?{parts.query}"
    if parts.fragment:
        relative = f"{relative}#{parts.fragment}"
    return relative


_URL_ATTR = re.compile(
    r"(?P<attr>\b(?:href|src|action|poster|content)\s*=\s*)"
    r"(?P<quote>[\"'])(?P<url>[^\"']*)(?P=quote)",
    re.IGNORECASE,
)
_CANONICAL = re.compile(r"[ \t]*<link rel=\"canonical\"[^>]*>\s*", re.IGNORECASE)


def rewrite_links(html: str, current_file: str) -> tuple[str, int]:
    """Переписывает внутренние ссылки на относительные; возвращает счётчик."""

    # canonical указывает на тестовый сервер: в архиве он не адрес публикации,
    # а подсказка поисковику, которого здесь нет.
    html = _CANONICAL.sub("", html)
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        href = match.group("url")
        relative = relative_url(href, current_file)
        if relative is None or relative == href:
            return match.group(0)
        count += 1
        quote = match.group("quote")
        return f"{match.group('attr')}{quote}{relative}{quote}"

    return _URL_ATTR.sub(replace, html), count


class LinkParser(HTMLParser):
    """Собирает адреса из готовой страницы для проверки после сборки."""

    ATTRS = frozenset({"href", "src", "action", "poster"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in self.ATTRS and value:
                self.links.append(value)


CARD_PREFIXES = ("/participants/", "/studios/", "/performances/")


def seed_paths(settings: Settings) -> list[str]:
    """Все страницы обхода: разделы сайта плюс карточки из данных выпуска.

    Карточки берутся из контента, а не только из ссылок каталога: каталог
    разбит по 24 записи, и на первой странице видно меньше половины участников.
    """

    from zaruba.content import load_site

    paths = list(SEED_PAGES)
    for bundle in load_site(settings):
        edition = bundle.edition
        paths.extend(f"/participants/{item.slug}" for item in edition.participants)
        paths.extend(f"/studios/{item.slug}" for item in edition.studios)
        paths.extend(f"/performances/{item.slug}" for item in edition.performances)
    return list(dict.fromkeys(paths))


def _open_client() -> tuple[TestClient, Settings]:
    """Приложение в режиме показа: overlay, `noindex` и демо-офферы включены."""

    from fastapi.testclient import TestClient

    from zaruba.config import Settings
    from zaruba.main import create_app

    settings = Settings(preview_enabled=True)
    return TestClient(create_app(settings)), settings


def crawl(client: TestClient, seeds: list[str]) -> dict[str, str]:
    """Обходит сайт по внутренним ссылкам и возвращает `адрес → HTML`."""

    queue: deque[str] = deque(seeds)
    seen: set[str] = set()
    pages: dict[str, str] = {}
    failures: dict[str, int] = {}
    while queue:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        response = client.get(url)
        if response.status_code != 200:
            failures[url] = response.status_code
            continue
        if "text/html" not in response.headers.get("content-type", ""):
            continue

        pages[url] = response.text
        parser = LinkParser()
        parser.feed(response.text)
        for href in parser.links:
            key = crawl_key(href)
            if key is not None and key not in seen:
                queue.append(key)

    if failures:
        details = "\n".join(f"  {url}: {status}" for url, status in failures.items())
        raise PreviewBuildError(f"страницы отвечают не 200:\n{details}")
    return pages


def write_pages(build_dir: Path, pages: dict[str, str], stats: BuildStats) -> None:
    for url, html in pages.items():
        parts = urlsplit(url)
        name = page_file(parts.path, parts.query)
        rewritten, count = rewrite_links(html, name)
        stats.rewritten_links += count
        target = build_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewritten, encoding="utf-8")


def copy_static(build_dir: Path) -> int:
    """Копирует `src/zaruba/static/` целиком: CSS, фавикон, обложка, портреты."""

    from zaruba.templating import PACKAGE_DIR

    target = build_dir / "static"
    shutil.copytree(PACKAGE_DIR / "static", target)
    return sum(1 for path in target.rglob("*") if path.is_file())


def validate(build_dir: Path, stats: BuildStats) -> None:
    """Проверяет, что каждая ссылка ведёт на существующий файл внутри архива."""

    for file in sorted(build_dir.rglob("*.html")):
        current = file.relative_to(build_dir).as_posix()
        parser = LinkParser()
        parser.feed(file.read_text(encoding="utf-8"))
        for href in parser.links:
            if href.startswith(("#", "//")):
                continue
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                continue
            if href.startswith("/"):
                stats.absolute.append(f"{current}: {href}")
                continue
            if not parts.path:
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(current), unquote(parts.path))
            )
            if not (build_dir / target).is_file():
                stats.missing.append(f"{current}: {href}")

    broken = [*stats.absolute, *stats.missing]
    if broken:
        lines = "\n".join(f"  {line}" for line in broken[:15])
        raise PreviewBuildError(f"в архиве остались нерабочие ссылки ({len(broken)}):\n{lines}")


def make_archive(build_dir: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for file in sorted(build_dir.rglob("*")):
            if file.is_file():
                bundle.write(file, file.relative_to(build_dir).as_posix())


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"


_TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(?P<body>.*)</body>", re.IGNORECASE | re.DOTALL)
_DESCRIPTION_RE = re.compile(r'<meta name="description" content="(?P<text>[^"]*)"', re.IGNORECASE)
_ID_ATTR_RE = re.compile(r'(?P<before>\bid=")(?P<value>[^"]*)(?P<after>")')
_SINGLE_ATTR_RE = re.compile(
    r"(?P<attr>\b(?:href|src|action|poster)\s*=\s*)(?P<quote>[\"'])(?P<url>[^\"']*)(?P=quote)",
    re.IGNORECASE,
)


def _data_uri(asset: str, cache: dict[str, str]) -> str:
    """Файл статики внутри HTML: `data:`-URI, чтобы файл был один."""

    from zaruba.templating import PACKAGE_DIR

    uri = cache.get(asset)
    if uri is not None:
        return uri
    path = PACKAGE_DIR / "static" / asset
    if not path.is_file():
        raise PreviewBuildError(f"в static/ нет файла «{asset}» — ссылка в шаблоне битая")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    uri = f"data:{mime};base64,{payload}"
    cache[asset] = uri
    return uri


def _single_target(
    href: str, index: int, index_by_url: dict[str, int], assets: dict[str, str]
) -> str | None:
    """Якорь внутри единого файла для ссылки или None, если адрес внешний.

    Навигация идёт по id секций (`#page-7`) и id элементов с префиксом страницы
    (`#p30-block-2`), поэтому работает и с JS, и без него: браузер сам
    прокручивает к цели, если скрипт отключён.
    """

    parts = urlsplit(href)
    if parts.scheme and parts.scheme not in {"http", "https"}:
        return None
    if parts.netloc and parts.netloc.split(":")[0] not in INTERNAL_HOSTS:
        return None

    path = parts.path
    if path.startswith("/static/"):
        return _data_uri(path[len("/static/") :], assets)
    if not path:
        return f"#p{index}-{parts.fragment}" if parts.fragment else None
    if not path.startswith("/"):
        return None

    target = index_by_url.get(page_route(path, parts.query))
    if target is None:
        # Фильтры (`?studio=...`) отдельных страниц не получают: ведём в каталог.
        target = index_by_url.get(path)
    if target is None:
        return None
    if parts.fragment:
        return f"#p{target}-{parts.fragment}"
    return f"#page-{target}"


def _section(
    html: str, index: int, index_by_url: dict[str, int], assets: dict[str, str]
) -> tuple[str, str, int]:
    """Тело страницы как секция единого файла: id с префиксом, ссылки на якоря."""

    body_match = _BODY_RE.search(html)
    if body_match is None:
        raise PreviewBuildError(f"страница {index}: не найден <body>")

    # Префикс страницы обязателен: `main`, `day-1` и `method` есть на нескольких
    # страницах, а id в одном документе должен быть единственным.
    body = _ID_ATTR_RE.sub(
        lambda match: (
            f"{match.group('before')}p{index}-{match.group('value')}{match.group('after')}"
        ),
        body_match.group("body"),
    )
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        href = match.group("url")
        target = _single_target(href, index, index_by_url, assets)
        if target is None or target == href:
            return match.group(0)
        count += 1
        quote = match.group("quote")
        return f"{match.group('attr')}{quote}{target}{quote}"

    body = _SINGLE_ATTR_RE.sub(replace, body)
    title_match = _TITLE_RE.search(html)
    title = html_unescape(title_match.group("title")) if title_match else ""
    return body, title.strip(), count


_SINGLE_CSS = ".preview-page[hidden] { display: none; }"

_SCRIPT = """<script>
(function () {
  'use strict';
  var pages = Array.prototype.slice.call(document.querySelectorAll('.preview-page'));
  if (!pages.length) { return; }
  var byId = {};
  pages.forEach(function (page) { byId[page.id] = page; });

  function pageFor(hash) {
    var id = hash.replace(/^#/, '');
    if (!id) { return pages[0]; }
    if (byId[id]) { return byId[id]; }
    var element = document.getElementById(id);
    while (element && !element.classList.contains('preview-page')) {
      element = element.parentElement;
    }
    return element || null;
  }

  function show(scroll) {
    var target = pageFor(location.hash) || pages[0];
    pages.forEach(function (page) { page.hidden = page !== target; });
    document.title = target.getAttribute('data-title') || document.title;
    Array.prototype.forEach.call(
      document.querySelectorAll('.site-header a[href^="#"], .site-footer a[href^="#"]'),
      function (link) {
        if (link.getAttribute('href') === '#' + target.id) {
          link.setAttribute('aria-current', 'page');
        } else {
          link.removeAttribute('aria-current');
        }
      }
    );
    var anchor = location.hash ?
      document.getElementById(location.hash.replace(/^#/, '')) : null;
    if (anchor && anchor !== target) {
      anchor.scrollIntoView();
    } else if (scroll) {
      window.scrollTo(0, 0);
    }
  }

  window.addEventListener('hashchange', function () { show(true); });
  Array.prototype.forEach.call(document.querySelectorAll('form[action^="#"]'), function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      if (form.getAttribute('action')) { location.hash = form.getAttribute('action'); }
    });
  });
  show(true);
}());
</script>"""


def render_single(pages: dict[str, str], stats: BuildStats) -> str:
    """Собирает один HTML: секции страниц, встроенные стили и картинки."""

    from zaruba.templating import PACKAGE_DIR

    index_by_url = {url: number for number, url in enumerate(pages)}
    assets: dict[str, str] = {}
    sections: list[str] = []
    for index, html in enumerate(pages.values()):
        body, title, count = _section(html, index, index_by_url, assets)
        stats.rewritten_links += count
        hidden = "" if index == 0 else " hidden"
        sections.append(
            f'<section class="preview-page" id="page-{index}"'
            f' data-title="{html_escape(title, quote=True)}"{hidden}>\n{body}\n</section>'
        )

    home = pages.get("/") or next(iter(pages.values()))
    title_match = _TITLE_RE.search(home)
    title = (
        html_unescape(title_match.group("title")).strip()
        if title_match
        else "ЗАРУБА 2026 — предпросмотр"
    )
    description_match = _DESCRIPTION_RE.search(home)
    description = html_unescape(description_match.group("text")) if description_match else ""

    # Печатные правила подключаются с `media="print"`: внутри единого файла их
    # нужно обернуть в `@media print`, иначе белый фон и скрытая навигация
    # применятся и на экране.
    style = (PACKAGE_DIR / "static" / "style.css").read_text(encoding="utf-8")
    print_style = (PACKAGE_DIR / "static" / "print.css").read_text(encoding="utf-8")
    head = "\n".join(
        [
            "<!doctype html>",
            '<html lang="ru">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html_escape(title)}</title>",
            f'<meta name="description" content="{html_escape(description, quote=True)}">',
            f'<link rel="icon" href="{_data_uri("favicon.svg", assets)}" type="image/svg+xml">',
            '<meta name="theme-color" content="#0b0b0f">',
            "<style>",
            style,
            "@media print {",
            print_style,
            "}",
            _SINGLE_CSS,
            "</style>",
            "<noscript><style>.preview-page[hidden] { display: block; }</style></noscript>",
            "</head>",
            "<body>",
        ]
    )
    return head + "\n" + "\n".join(sections) + "\n" + _SCRIPT + "\n</body>\n</html>\n"


def validate_single(markup: str, pages: int, stats: BuildStats) -> None:
    """Проверяет якоря и отсутствие абсолютных адресов в едином файле."""

    ids = set(re.findall(r'\bid="([^"]+)"', markup))
    for number in range(pages):
        if f"page-{number}" not in ids:
            stats.missing.append(f"нет секции #page-{number}")
    for href in re.findall(r'(?:href|action)="#([^"]+)"', markup):
        if href not in ids:
            stats.missing.append(f"нет цели #{href}")
    stats.absolute.extend(
        f"абсолютная ссылка {match}"
        for match in re.findall(r'(?:href|src|action)="(/[^"]*|https?://testserver[^"]*)"', markup)
    )
    broken = [*stats.absolute, *stats.missing]
    if broken:
        lines = "\n".join(f"  {line}" for line in broken[:15])
        raise PreviewBuildError(
            f"в едином файле остались нерабочие ссылки ({len(broken)}):\n{lines}"
        )


def build_single(target: Path) -> BuildStats:
    """Один HTML вместо папки и ZIP: удобно отправить в мессенджер."""

    client, settings = _open_client()
    with client:
        pages = crawl(client, seed_paths(settings))

    stats = BuildStats(pages=len(pages))
    stats.cards = sum(1 for url in pages if urlsplit(url).path.startswith(CARD_PREFIXES))
    markup = render_single(pages, stats)
    validate_single(markup, len(pages), stats)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markup, encoding="utf-8")
    return stats


def build(build_dir: Path, archive: Path) -> BuildStats:
    client, settings = _open_client()
    with client:
        pages = crawl(client, seed_paths(settings))

    if build_dir.exists():
        shutil.rmtree(build_dir)

    stats = BuildStats(pages=len(pages))
    stats.cards = sum(1 for url in pages if urlsplit(url).path.startswith(CARD_PREFIXES))
    write_pages(build_dir, pages, stats)
    stats.static_files = copy_static(build_dir)
    (build_dir / "README.txt").write_text(README_TEXT, encoding="utf-8")
    validate(build_dir, stats)
    make_archive(build_dir, archive)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать статическую копию сайта для показа")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ARCHIVE,
        help=f"куда положить ZIP (по умолчанию {DEFAULT_ARCHIVE.name})",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help=f"папка сборки (по умолчанию {DEFAULT_BUILD_DIR.name})",
    )
    parser.add_argument(
        "--single-file",
        action="store_true",
        help="собрать один HTML-файл вместо папки и ZIP",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=DEFAULT_SINGLE_FILE,
        help=f"куда положить единый HTML (по умолчанию {DEFAULT_SINGLE_FILE.name})",
    )
    args = parser.parse_args(argv)

    if args.single_file:
        try:
            stats = build_single(args.html)
        except PreviewBuildError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 1
        print(f"OK  страниц: {stats.pages} (из них карточек: {stats.cards})")
        print(f"OK  внутренних ссылок переписано: {stats.rewritten_links}")
        print(f"OK  файл: {args.html} ({human_size(args.html.stat().st_size)})")
        return 0

    try:
        stats = build(args.build_dir, args.output)
    except PreviewBuildError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(f"OK  страниц: {stats.pages} (из них карточек: {stats.cards})")
    print(f"OK  статика: {stats.static_files} файл(ов)")
    print(f"OK  внутренних ссылок переписано: {stats.rewritten_links}")
    print(f"OK  папка сборки: {args.build_dir}")
    print(f"OK  архив: {args.output} ({human_size(args.output.stat().st_size)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
