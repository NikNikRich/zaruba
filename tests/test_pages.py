import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.templating import money

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "zaruba" / "static"

PAGES = (
    "/",
    "/event",
    "/history",
    "/ratings",
    "/about",
    "/rules",
    "/privacy",
    "/takedown",
    "/schedule",
    "/performances",
    "/performances/perf-01-hip-hop-6-11",
    "/participants",
    "/participants/v-m",
    "/studios",
    "/studios/demo-studio-a",
    "/judges",
    "/tickets",
    "/glossary",
    "/faq",
    "/search",
    "/venue",
)


def test_home_shows_key_facts_without_js(client: TestClient) -> None:
    """§6.22 п.5: страница читается при выключенном JS, факты — в HTML."""

    html = client.get("/").text

    assert "Чемпионат по современным танцам" in html
    assert "12–13 декабря 2026" in html
    assert "Золотые ворота Сибири" in html
    assert "ул. Герцена, 87" in html
    assert "БЕСТ ЗАРУБА КИДС" in html
    assert "10:00–11:30" in html, "время мастер-классов — единственный подтверждённый тайминг"
    # Исполняемого JS на инфо-страницах нет; JSON-LD (§6.19) — данные, а не скрипт.
    scripts = re.findall(r"<script[^>]*>", html, flags=re.IGNORECASE)
    assert all('type="application/ld+json"' in tag for tag in scripts), scripts


def test_home_does_not_link_to_missing_pages(client: TestClient) -> None:
    """В шапке и футере только реализованные разделы: ссылок в 404 быть не должно."""

    html = client.get("/").text

    for path in (
        "/schedule",
        "/venue",
        "/event",
        "/performances",
        "/participants",
        "/search",
    ):
        assert f'href="{path}"' in html, path


def test_schedule_renders_nominations_and_master_classes(client: TestClient) -> None:
    """Тайминга блоков нет, зато есть номинации по дням и время мастер-классов."""

    html = client.get("/schedule").text

    assert "БЕСТ ЗАРУБА КИДС" in html
    assert 'id="master-classes"' in html
    assert "10:00–11:30" in html
    assert "тайминг уточняется" in html
    assert 'id="block-day1-solo"' not in html, "выдуманных блоков 2025 в расписании 2026 нет"


def test_schedule_day_filter(client: TestClient) -> None:
    html = client.get("/schedule?day=2").text

    assert "БЕСТ ЗАРУБА КИДС" in html
    assert "БЕСТ ЗАРУБА СОЛО ДЕТИ" not in html
    assert "День 2" in html
    assert 'id="master-classes"' not in html, "мастер-классы идут в первый день"


def test_schedule_rejects_unknown_day(client: TestClient) -> None:
    """Неверный день — страница сайта с объяснением, а не JSON от pydantic."""

    response = client.get("/schedule?day=9")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Проверьте параметры в адресе" in response.text
    assert "день" not in response.text.lower() or "day" in response.text
    assert '"detail"' not in response.text


@pytest.mark.parametrize(
    ("path", "param", "hint"),
    (
        ("/schedule?day=9", "day", "допустимо не больше 3"),
        ("/performances?day=99", "day", "допустимо не больше 3"),
        ("/participants?age=abc", "age", "допустимые значения"),
        ("/participants?age=99%2B", "age", "допустимые значения"),
        ("/performances?page=0", "page", "допустимо не меньше 1"),
        ("/search?q=" + "я" * 81, "q", "не длиннее 80 символов"),
    ),
)
def test_invalid_query_returns_explained_page(
    client: TestClient, path: str, param: str, hint: str
) -> None:
    """§6.21: вместо сырого 422 — объяснение, какой параметр не подошёл и почему."""

    response = client.get(path)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert f"<code>{param}</code>" in response.text
    assert hint in response.text
    assert 'name="robots" content="noindex, follow"' in response.text
    assert 'href="http://testserver' in response.text, "ссылка «без фильтров» ведёт на страницу"
    assert "<script" not in response.text.lower()


def test_venue_page_states_unknowns_honestly(client: TestClient) -> None:
    html = client.get("/venue").text

    assert "Золотые ворота Сибири" in html
    assert "до 850 гостей" in html
    assert "не придумываем" in html
    assert "/schedule" in html


def test_pages_warn_about_past_edition(client: TestClient) -> None:
    """§6.3: архивный выпуск помечен полосой, свежий — нет."""

    for path in (
        "/performances",
        "/participants",
        "/performances/perf-01-hip-hop-6-11",
        "/participants/v-m",
        "/studios/demo-studio-a",
    ):
        assert "Выпуск 2025 завершён" in client.get(path).text, path

    for path in (
        "/",
        "/event",
        "/history",
        "/ratings",
        "/schedule",
        "/judges",
        "/tickets",
        "/glossary",
        "/faq",
        "/search",
        "/venue",
    ):
        assert "завершён" not in client.get(path).text, path


def test_demo_rows_are_marked(client: TestClient) -> None:
    """Демонстрационные данные помечены бейджем «пример», а не выдаются за факты."""

    html = client.get("/performances").text

    assert "пример" in html
    assert "демонстрационные" in html
    assert "Это архив 2025 года" in html


def test_pages_have_no_none_artifacts(client: TestClient) -> None:
    for path in PAGES:
        html = client.get(path).text
        assert "None" not in html, path
        assert "&lt;None&gt;" not in html, path


def test_invalid_age_lists_human_labels(client: TestClient) -> None:
    """Допустимые значения читаются по-русски: без `&#39;` и без `or` из pydantic."""

    response = client.get("/participants?age=abc")

    assert "допустимые значения: 3-6, 6-11" in response.text
    assert "или any" in response.text
    assert "&#39;" not in response.text
    assert " or " not in response.text


def test_money_filter_groups_thousands() -> None:
    """Цены читаются по разрядам и не рвутся между тысячей и остатком."""

    assert money(1000) == "1\u00a0000"
    assert money(800) == "800"
    assert money(None) == ""


def test_prices_use_thousands_separator(client: TestClient) -> None:
    html = client.get("/tickets").text

    assert "1\u00a0000 ₽" in html, "билет на один блок"
    assert "1\u00a0500 ₽" in html, "взнос за команду до срока"
    assert "2\u00a0300 ₽" in html, "взнос за детское соло до срока"


def test_every_page_has_canonical(client: TestClient) -> None:
    """§6.19: canonical на всех страницах, без query-хвостов."""

    for path in PAGES:
        html = client.get(path).text
        assert f'<link rel="canonical" href="http://testserver{path}">' in html, path


def test_internal_links_of_new_pages_resolve(client: TestClient) -> None:
    """Ни одна ссылка с новых страниц не ведёт в 404 (§6.21)."""

    pages = (
        "/participants",
        "/studios",
        "/studios/demo-studio-a",
        "/judges",
        "/tickets",
        "/glossary",
        "/faq",
        "/search?q=hip",
        "/participants/v-m",
        "/performances/perf-01-hip-hop-6-11",
    )
    checked: set[str] = set()
    broken: list[str] = []
    for page in pages:
        html = client.get(page).text
        for href in re.findall(r'href="(/[^"]*)"', html):
            path = href.split("#", 1)[0]
            if not path or path in checked:
                continue
            checked.add(path)
            if client.get(path).status_code != 200:
                broken.append(f"{page} → {path}")

    assert not broken, broken


def test_wide_tables_are_wrapped_for_horizontal_scroll(client: TestClient) -> None:
    """Таблица с 5+ колонками прокручивается внутри `.table-wrap`.

    Без обёртки страница распирается по горизонтали, а карточная сетка на
    четыре колонки оставляет пятую ячейку без места.
    """

    pages = ("/schedule", "/results", "/studios/demo-studio-a")
    checked = 0
    for page in pages:
        html = client.get(page).text
        wrapped = re.findall(r'<div class="table-wrap">.*?</div>', html, flags=re.DOTALL)
        for table in re.findall(r"<table.*?</table>", html, flags=re.DOTALL):
            if table.count('<th scope="col">') < 5:
                continue
            checked += 1
            assert any(table in block for block in wrapped), (
                f"{page}: широкая таблица без .table-wrap"
            )
            assert "table--results" in table or "table--wide" in table, (
                f"{page}: у широкой таблицы нет класса table--wide"
            )
    assert checked >= 4, "таблицы с 5+ колонками не найдены — проверка выродилась"


def test_brand_assets_are_served(client: TestClient) -> None:
    """§6.19: иконка и OG-обложка — часть сайта, а не файлы «где-то в репозитории»."""

    for path, content_type in (
        ("/static/favicon.svg", "image/svg+xml"),
        ("/static/og-cover.png", "image/png"),
    ):
        response = client.get(path)

        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(content_type), path


def test_og_cover_is_1200x630_png() -> None:
    """Соцсети кадрируют карточку под 1.905:1: другой размер — обрезанный заголовок."""

    raw = (STATIC_DIR / "og-cover.png").read_bytes()

    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), "og-cover.png — не PNG"
    width, height = struct.unpack(">II", raw[16:24])
    assert (width, height) == (1200, 630)


def test_favicon_uses_the_lime_accent_from_the_stylesheet() -> None:
    """HEX иконки берётся из `--lime`, а не «на глаз»: иначе фавикон живёт своей жизнью."""

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    lime = re.search(r"--lime:\s*(#[0-9a-f]{6})", css)
    assert lime is not None, "в style.css пропал токен --lime"
    svg = (STATIC_DIR / "favicon.svg").read_text(encoding="utf-8")

    assert 'rx="6"' in svg
    assert lime.group(1) in svg.lower()
    assert "#0b0b0f" in svg.lower()
    # `--` внутри XML-комментария — синтаксическая ошибка, и браузер покажет
    # пустой квадрат вместо иконки. Разбор XML ловит это раньше пользователя.
    assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"


def test_home_has_favicon_and_social_meta(client: TestClient) -> None:
    """§6.19: иконка, цвет адресной строки и карточка для VK, Telegram и X."""

    html = client.get("/").text

    assert (
        '<link rel="icon" href="http://testserver/static/favicon.svg" type="image/svg+xml">' in html
    )
    assert '<meta name="theme-color" content="#0b0b0f">' in html
    for tag in (
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="ЗАРУБА">',
        '<meta property="og:title" '
        'content="ЗАРУБА 2026 — Всероссийский танцевальный чемпионат | Тюмень">',
        '<meta property="og:description" content="12–13 декабря, Тюмень '
        "(КТЗ «Золотые ворота Сибири»). Соло, дуэты, команды и шоу-кейсы. "
        'Расписание, судейство и результаты. Зарубимся?">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
    ):
        assert tag in html, tag
    # Абсолютный адрес: VK и X не разрешают относительный путь к картинке.
    assert '<meta property="og:image" content="http://testserver/static/og-cover.png">' in html
