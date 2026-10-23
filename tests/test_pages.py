import re

import pytest
from fastapi.testclient import TestClient

from zaruba.templating import money

NAV_WITHOUT_IMPLEMENTATION = ("/ratings",)

PAGES = (
    "/",
    "/event",
    "/history",
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
    for path in NAV_WITHOUT_IMPLEMENTATION:
        assert f'href="{path}"' not in html


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
