"""§6.19: robots.txt, sitemap.xml и JSON-LD — проверяем то, что видит робот."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import load_edition
from zaruba.main import create_app
from zaruba.services.seo import event_json_ld

LD_JSON = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', flags=re.DOTALL | re.IGNORECASE
)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def json_ld(html: str) -> list[dict]:
    blocks = [json.loads(match) for match in LD_JSON.findall(html)]
    assert blocks, "на странице нет ни одного блока JSON-LD"
    return blocks


def by_type(html: str, type_name: str) -> dict:
    found = [block for block in json_ld(html) if block.get("@type") == type_name]
    assert len(found) == 1, f"ожидался один блок {type_name}, найдено {len(found)}"
    return found[0]


def test_robots_txt_points_to_sitemap(client: TestClient) -> None:
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "User-agent: *" in response.text
    assert "Allow: /" in response.text
    assert "Sitemap: http://testserver/sitemap.xml" in response.text
    assert "Disallow: /search" not in response.text, "noindex работает только на обходимой странице"


def test_public_base_url_overrides_proxy_headers() -> None:
    """Canonical, robots и sitemap берутся из настройки, а не из Host запроса.

    `request.base_url` зависит от `uvicorn --proxy-headers`: без флага поисковик
    получал `http://127.0.0.1:8000`, а сайт молча терял индексацию.
    """

    app = create_app(Settings(public_base_url="https://zaruba72.ru/"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as test_client:
        page = test_client.get("/event")
        sitemap = test_client.get("/sitemap.xml")
        robots = test_client.get("/robots.txt")

    assert '<link rel="canonical" href="https://zaruba72.ru/event">' in page.text
    assert "<loc>https://zaruba72.ru/schedule</loc>" in sitemap.text
    assert "Sitemap: https://zaruba72.ru/sitemap.xml" in robots.text


def test_sitemap_is_valid_xml_with_confirmed_pages(client: TestClient) -> None:
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.text)
    locs = {item.text for item in root.findall("sm:url/sm:loc", SITEMAP_NS)}

    for path in (
        "/",
        "/event",
        "/history",
        "/schedule",
        "/performances",
        "/participants",
        "/faq",
        "/glossary",
        "/venue",
        "/about",
        "/rules",
        "/privacy",
        "/takedown",
    ):
        assert f"http://testserver{path}" in locs, path
    assert all("?" not in loc for loc in locs), "фильтры и пагинация в sitemap не попадают"
    assert not any(loc.endswith("/ratings") for loc in locs), (
        "/ratings закрыт флагом и отдаёт noindex — в sitemap его нет"
    )


def test_sitemap_skips_demo_and_private_pages(client: TestClient) -> None:
    """Демо-2025 и карточки несовершеннолетних без согласия закрыты от индексации."""

    root = ET.fromstring(client.get("/sitemap.xml").text)
    locs = {item.text for item in root.findall("sm:url/sm:loc", SITEMAP_NS)}

    assert "http://testserver/participants/v-m" not in locs, "демо-участник не индексируется"
    assert "http://testserver/performances/perf-01-hip-hop-6-11" not in locs
    assert "http://testserver/studios/demo-studio-a" not in locs


def test_home_has_event_json_ld(client: TestClient) -> None:
    event = by_type(client.get("/").text, "Event")

    assert event["@context"] == "https://schema.org"
    assert event["name"].startswith("«ЗАРУБА» 2026")
    assert event["startDate"] == "2026-12-12", "тайминга блоков нет — только дата"
    assert event["endDate"] == "2026-12-13"
    assert event["location"]["name"] == "КТЗ «Золотые ворота Сибири»"
    assert event["location"]["address"]["streetAddress"] == "ул. Герцена, 87"
    assert event["eventAttendanceMode"] == "https://schema.org/OfflineEventAttendanceMode"


def test_event_json_ld_uses_published_timing(edition_path: Path) -> None:
    """Если блоки опубликованы, время берётся из них, а не придумывается.

    Заодно проверка переносимости: `ZoneInfo` без пакета `tzdata` падал
    с `ZoneInfoNotFoundError` прямо на рендере — на Windows и в slim-образах,
    где системной базы часовых поясов нет.
    """

    bundle = load_edition(edition_path)

    event = event_json_ld(bundle, "https://zaruba72.ru")

    assert event["startDate"] == "2025-12-06T12:00:00+05:00"
    assert event["endDate"] == "2025-12-07T14:40:00+05:00"


def test_event_json_ld_offer_links_to_prices_not_to_sales(client: TestClient) -> None:
    event = by_type(client.get("/event").text, "Event")

    offer = event["offers"]
    assert offer["url"] == "http://testserver/tickets", (
        "сайт не продаёт — ведём на свою страницу цен"
    )
    assert offer["priceCurrency"] == "RUB"
    assert offer["price"] == 800, "в offers — минимальная подтверждённая цена билета"


def test_faq_page_has_faq_json_ld(client: TestClient) -> None:
    faq = by_type(client.get("/faq").text, "FAQPage")

    questions = faq["mainEntity"]
    assert len(questions) >= 10
    assert all(item["@type"] == "Question" for item in questions)
    assert all(item["acceptedAnswer"]["text"] for item in questions)


def test_glossary_page_has_defined_terms(client: TestClient) -> None:
    glossary = by_type(client.get("/glossary").text, "DefinedTermSet")

    terms = glossary["hasDefinedTerm"]
    assert len(terms) >= 9
    assert {term["name"] for term in terms} >= {"Hip-Hop", "Breaking"}
    assert all(term["url"].startswith("http://testserver/glossary#") for term in terms)


@pytest.mark.parametrize(
    ("path", "names"),
    (
        ("/participants/v-m", ("Главная", "Участники", "В. М.")),
        ("/performances/perf-01-hip-hop-6-11", ("Главная", "Выступления")),
        ("/studios/demo-studio-a", ("Главная", "Студии")),
        ("/faq", ("Главная", "Вопросы и ответы")),
        ("/history", ("Главная", "История")),
        ("/ratings", ("Главная", "Рейтинги")),
        ("/about", ("Главная", "О сайте")),
        ("/takedown", ("Главная", "Удаление данных")),
        ("/venue", ("Главная", "Площадка")),
    ),
)
def test_nested_pages_have_breadcrumbs(
    client: TestClient, path: str, names: tuple[str, ...]
) -> None:
    crumbs = by_type(client.get(path).text, "BreadcrumbList")
    items = crumbs["itemListElement"]

    assert [item["name"] for item in items[: len(names)]] == list(names)
    assert [item["position"] for item in items] == list(range(1, len(items) + 1))
    assert items[-1]["item"] == f"http://testserver{path}"


def test_home_has_no_breadcrumbs_and_no_person(client: TestClient) -> None:
    """Главная — корень; `Person` не размечаем ни для кого (§6.19)."""

    blocks = json_ld(client.get("/").text)

    assert not any(block.get("@type") == "BreadcrumbList" for block in blocks)
    for path in ("/", "/participants/v-m", "/participants/a-k"):
        assert not any(block.get("@type") == "Person" for block in json_ld(client.get(path).text))


def test_error_pages_have_no_structured_data(client: TestClient) -> None:
    """На 404 и 422 разметки нет: адрес там заведомо не тот, что в canonical."""

    for path in ("/nope", "/schedule?day=9"):
        html = client.get(path).text
        assert "application/ld+json" not in html, path


def test_canonical_only_on_successful_pages(client: TestClient) -> None:
    """§6.19: canonical — только на 2xx: на ошибке он указывает на саму ошибку."""

    assert (
        '<link rel="canonical" href="http://testserver/participants">'
        in client.get("/participants").text
    )
    for path in ("/nope", "/schedule?day=9"):
        assert 'rel="canonical"' not in client.get(path).text, path
    assert 'rel="canonical"' not in client.post("/participants").text, "405"
    # Кнопка «без фильтров» на 422 не зависела от canonical и осталась рабочей.
    assert (
        '<a class="btn" href="/schedule">Открыть эту страницу без фильтров</a>'
        in client.get("/schedule?day=9").text
    )
