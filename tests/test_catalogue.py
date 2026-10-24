import re

from fastapi.testclient import TestClient


def test_performances_catalog_lists_edition(client: TestClient) -> None:
    """§6.7: каталог отвечает на «что вообще показывают» — все выступления в HTML."""

    html = client.get("/performances").text

    assert html.count('class="perf-card"') == 8
    assert "Соло · Hip-Hop · 6–11" in html
    assert "Шоу · Experimental · 18+" in html
    assert "пример" in html
    assert 'name="robots"' not in html, "чистый каталог индексируется"


def test_performances_filter_by_day(client: TestClient) -> None:
    """Фильтр в query: ссылкой можно поделиться (§6.6, §6.7)."""

    html = client.get("/performances", params={"day": 2}).text

    assert html.count('class="perf-card"') == 3
    assert 'href="/performances/perf-06-jazz-funk-team-12-17"' in html
    assert "/performances/perf-01-hip-hop-6-11" not in html
    assert 'value="2" selected' in html, "выбранный фильтр остаётся в форме"


def test_performances_filter_combines_style_and_age(client: TestClient) -> None:
    html = client.get("/performances", params={"style": "hip-hop", "age": "18+"}).text

    assert html.count('class="perf-card"') == 1
    assert 'href="/performances/perf-03-hip-hop-18"' in html


def test_performances_filter_by_studio(client: TestClient) -> None:
    html = client.get("/performances", params={"studio": "demo-studio-a"}).text

    assert html.count('class="perf-card"') == 4


def test_filtered_catalog_is_noindex(client: TestClient) -> None:
    """§6.19: фильтры — `noindex, follow`, canonical — на чистый каталог."""

    html = client.get("/performances", params={"day": 1}).text

    assert '<meta name="robots" content="noindex, follow">' in html
    assert '<link rel="canonical" href="http://testserver/performances">' in html


def test_empty_filter_result_offers_a_way_out(client: TestClient) -> None:
    """§6.21: пустое состояние обязано содержать выход, а не «ничего не найдено»."""

    html = client.get("/performances", params={"video": 1}).text

    assert html.count('class="perf-card"') == 0
    assert "Под такие фильтры ничего не нашлось" in html
    assert 'href="/performances"' in html


def test_performances_rejects_unknown_style(client: TestClient) -> None:
    response = client.get("/performances", params={"style": "ballet"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "допустимые значения" in response.text


def test_performance_card_works_without_js(client: TestClient) -> None:
    """§6.22 п.5: карточка выступления читается при выключенном JS."""

    html = client.get("/performances/perf-01-hip-hop-6-11").text

    assert "Соло · Hip-Hop · 6–11 лет — А. К." in html
    assert "Студия A · пример" in html
    assert "Видео появится после события" in html
    assert "Официальных результатов нет" in html
    assert 'href="/schedule#block-day1-solo"' in html
    scripts = re.findall(r"<script[^>]*>", html, flags=re.IGNORECASE)
    assert all('type="application/ld+json"' in tag for tag in scripts), scripts


def test_performance_card_links_participants(client: TestClient) -> None:
    html = client.get("/performances/perf-08-experimental-show-18").text

    for slug in ("g-t", "zh-r", "z-f"):
        assert f'href="/participants/{slug}"' in html


def test_unknown_performance_is_404_page(client: TestClient) -> None:
    response = client.get("/performances/perf-99")

    assert response.status_code == 404
    assert "Страница не найдена" in response.text


def test_participant_page_shows_performances(client: TestClient) -> None:
    html = client.get("/participants/v-m").text

    assert "В. М." in html
    assert "Студия B · пример" in html
    assert 'href="/performances/perf-03-hip-hop-18"' in html
    assert 'href="/performances/perf-07-contemporary-team-18"' in html
    assert html.count('href="/performances/perf-') == 2
    assert "Лучший результат" in html
    assert 'name="robots"' not in html, "совершеннолетний индексируется"


def test_minor_without_consent_is_noindex_and_without_media(client: TestClient) -> None:
    """§6.22 п.6: страница ребёнка без согласия — `noindex`, без фото и клипа."""

    html = client.get("/participants/a-k").text

    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert "Публикуем только инициалы" in html
    assert "<img" not in html
    assert "vk.com/zaruba72champ" in html, "у приватной страницы должен быть выход"


def test_unknown_participant_is_404_page(client: TestClient) -> None:
    assert client.get("/participants/nobody").status_code == 404


def test_event_page_keeps_chronology_honest(client: TestClient) -> None:
    """§6.5: год без подтверждённых данных не выдумываем."""

    html = client.get("/event").text

    assert "12–13 декабря 2026" in html
    assert "2025" in html, "архив 2025 остаётся в хронологии"
    assert "2024" in html, "2024 подтверждён архивом сообщества — теперь он в таблице"
    assert "2019" in html
    assert "2023" in html, "2023 подтверждён программой от 30.11.2023 — теперь он в таблице"
    assert "2020" not in html and "2021" not in html, "пандемийных лет в архиве нет"
    assert "Год без подтверждённых данных" in html
    assert 'href="/history"' in html
    assert "Золотые ворота Сибири" in html
    assert "Хронометраж: команды — до 4:00" in html


def test_unknown_page_renders_site_404(client: TestClient) -> None:
    response = client.get("/no-such-page")

    assert response.status_code == 404
    assert "Страница не найдена" in response.text
    assert 'href="/schedule"' in response.text
