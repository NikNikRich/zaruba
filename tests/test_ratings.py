"""§6.10: `/ratings` — методика вместо выдуманных таблиц; раздел живёт за флагом."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import load_site
from zaruba.main import create_app
from zaruba.services.ratings import (
    ELO_K,
    ELO_START,
    METHOD_UPDATED,
    METHOD_VERSION,
    MIN_PERFORMANCES,
    ratings_view,
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@contextmanager
def ratings_client(*, enabled: bool) -> Iterator[TestClient]:
    """Клиент с явным флагом: `.env` не должен влиять на проверку обоих состояний."""

    with TestClient(create_app(Settings(ratings_enabled=enabled))) as test_client:
        yield test_client


def sitemap_locs(client: TestClient) -> set[str]:
    root = ET.fromstring(client.get("/sitemap.xml").text)
    return {item.text for item in root.findall("sm:url/sm:loc", SITEMAP_NS)}


def test_ratings_page_explains_the_method(client: TestClient) -> None:
    """Методика — не «скоро всё будет», а конкретные формулы и версия."""

    html = client.get("/ratings").text

    assert "Рейтинг — игра для зрителя, а не отбор" in html
    assert 'id="method"' in html
    assert f"Методика {METHOD_VERSION}" in html
    assert "23 октября 2026" in html, "версия методики подписана датой"
    assert f"K = {ELO_K}" in html
    assert f"старт {ELO_START}" in html
    assert f"Минимум {MIN_PERFORMANCES} выступления" in html
    assert "медиана топ-3" in html, "рейтинг студий — медиана, а не среднее"
    assert "победитель зала не совпал с победителем судей" in html


def test_ratings_page_has_no_invented_places_or_scores(client: TestClient) -> None:
    """Таблицы рейтинга не рисуются: единственная таблица — сама методика."""

    html = client.get("/ratings").text

    assert html.count("<table") == 1
    assert "Место" not in html
    assert not re.search(r"<td[^>]*>\s*1\s*</td>", html), "первого места из воздуха не берём"


def test_ratings_page_names_the_reasons_for_empty_state(client: TestClient) -> None:
    html = client.get("/ratings").text

    assert "Рейтинги не запущены" in html
    assert "протоколы" in html
    assert "Голосование зрителей не открыто" in html
    assert "число голосов" in html, "рядом с очками всегда число голосов и ±"
    assert 'href="/history"' in html
    assert 'href="https://vk.com/zaruba72champ" rel="noopener noreferrer nofollow"' in html


def test_ratings_is_noindex_and_out_of_sitemap_while_disabled() -> None:
    """Пока раздел не запущен, роботу там нечего делать: noindex и нет в sitemap."""

    with ratings_client(enabled=False) as client:
        html = client.get("/ratings").text

        assert '<meta name="robots" content="noindex, follow">' in html
        assert "http://testserver/ratings" not in sitemap_locs(client)


def test_ratings_becomes_indexable_together_with_the_flag() -> None:
    with ratings_client(enabled=True) as client:
        html = client.get("/ratings").text

        assert 'name="robots"' not in html
        assert "Раздел запущен" in html
        assert "http://testserver/ratings" in sitemap_locs(client)


def test_ratings_nav_entry_appears_and_links_resolve(client: TestClient) -> None:
    """§6.21: пункт в шапке ведёт на существующую страницу, а не в 404."""

    html = client.get("/ratings").text

    assert 'href="/ratings"' in client.get("/").text
    for href in re.findall(r'href="(/[^"]*)"', html):
        path = href.split("#", 1)[0]
        assert client.get(path).status_code == 200, f"/ratings → {path}"


def test_ratings_view_reports_missing_source() -> None:
    """Сервис отдаёт причины, а не пустой список: страница обязана объяснить пустоту."""

    settings = Settings(ratings_enabled=False, fan_voting_enabled=False)
    edition = load_site(settings)[0].edition
    view = ratings_view(settings, edition)

    assert view.enabled is False
    assert len(view.kinds) == 3
    assert {kind.slug for kind in view.kinds} == {"participant", "studio", "judges-vs-hall"}
    assert any("протоколы" in reason for reason in view.blockers)
    assert any("Голосование" in reason for reason in view.blockers)
    assert any("Раздел не запущен" in reason for reason in view.blockers)
    assert any(str(MIN_PERFORMANCES) in limit for limit in view.limits)
    assert view.version == METHOD_VERSION
    assert view.updated == METHOD_UPDATED


def test_ratings_view_drops_blockers_that_no_longer_apply() -> None:
    """Включённые флаги убирают свои причины; протоколы остаются блокером всегда."""

    settings = Settings(ratings_enabled=True, fan_voting_enabled=True)
    edition = load_site(settings)[0].edition
    view = ratings_view(settings, edition)
    reasons = " ".join(view.blockers)

    assert view.enabled is True
    assert "Раздел не запущен" not in reasons
    assert "Голосование" not in reasons
    assert "протоколы" in reasons
