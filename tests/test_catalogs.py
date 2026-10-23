from fastapi.testclient import TestClient


def test_participants_catalog_lists_everyone(client: TestClient) -> None:
    """§6.8: каталог отвечает на «найди знакомого» — все участники в HTML."""

    html = client.get("/participants").text

    assert html.count('class="person-card"') == 8
    assert "Студия A · пример" in html
    assert "А. К." in html
    assert "пример" in html
    assert 'name="robots"' not in html, "чистый каталог индексируется"
    assert '<link rel="canonical" href="http://testserver/participants">' in html


def test_participants_filter_by_studio(client: TestClient) -> None:
    html = client.get("/participants", params={"studio": "demo-studio-a"}).text

    assert html.count('class="person-card"') == 3
    assert 'value="demo-studio-a" selected' in html


def test_participants_filter_by_age(client: TestClient) -> None:
    html = client.get("/participants", params={"age": "18+"}).text

    assert html.count('class="person-card"') == 5
    assert 'value="18+" selected' in html


def test_participants_search_by_name_and_studio(client: TestClient) -> None:
    by_name = client.get("/participants", params={"q": "В. М."}).text
    by_studio = client.get("/participants", params={"q": "Студия C"}).text

    assert by_name.count('class="person-card"') == 1
    assert 'href="/participants/v-m"' in by_name
    assert by_studio.count('class="person-card"') == 2


def test_participants_empty_filter_offers_a_way_out(client: TestClient) -> None:
    """§6.21: пустое состояние обязано содержать выход."""

    html = client.get("/participants", params={"q": "нет-такого-имени"}).text

    assert html.count('class="person-card"') == 0
    assert "никого не нашлось" in html
    assert 'href="/participants"' in html
    assert 'href="/studios"' in html


def test_filtered_participants_are_noindex(client: TestClient) -> None:
    html = client.get("/participants", params={"studio": "demo-studio-a"}).text

    assert '<meta name="robots" content="noindex, follow">' in html
    assert '<link rel="canonical" href="http://testserver/participants">' in html


def test_participants_rejects_unknown_age(client: TestClient) -> None:
    assert client.get("/participants", params={"age": "40+"}).status_code == 422


def test_studios_catalog_lists_studios(client: TestClient) -> None:
    html = client.get("/studios").text

    assert html.count('class="info-card"') == 3
    assert 'href="/studios/demo-studio-a"' in html
    assert "участников: 3" in html


def test_studios_search_by_style(client: TestClient) -> None:
    html = client.get("/studios", params={"q": "contemporary"}).text

    assert html.count('class="info-card"') == 2
    assert '<meta name="robots" content="noindex, follow">' in html


def test_studio_page_lists_members_and_shows(client: TestClient) -> None:
    html = client.get("/studios/demo-studio-a").text

    assert "Студия A · пример" in html
    assert html.count('class="person-card"') == 3
    assert 'href="/participants/a-k"' in html
    assert 'href="/performances/perf-01-hip-hop-6-11"' in html
    assert "Тренеры появятся" in html
    # Склонение числительных: 1 выступление, 2 выступления.
    assert "1 выступление" in html
    assert "2 выступления" in html


def test_unknown_studio_is_404_page(client: TestClient) -> None:
    response = client.get("/studios/nobody")

    assert response.status_code == 404
    assert "Страница не найдена" in response.text


def test_search_groups_results_by_type(client: TestClient) -> None:
    """§6.16: результаты сгруппированы, у каждой группы — своя ссылка."""

    html = client.get("/search", params={"q": "hip"}).text

    assert "Выступления" in html
    assert "Номинации" in html
    assert "Глоссарий" in html
    assert 'href="/performances/perf-01-hip-hop-6-11"' in html
    assert 'href="/glossary#style-hip-hop"' in html
    assert '<meta name="robots" content="noindex, follow">' in html


def test_search_finds_participant_by_name(client: TestClient) -> None:
    html = client.get("/search", params={"q": "В. М."}).text

    assert 'href="/participants/v-m"' in html
    assert "Студия B · пример" in html


def test_search_without_results_offers_catalogs(client: TestClient) -> None:
    html = client.get("/search", params={"q": "нет-такого"}).text

    assert "ничего не нашлось" in html
    for path in ("/participants", "/performances", "/studios", "/glossary"):
        assert f'href="{path}"' in html, path


def test_search_requires_two_characters(client: TestClient) -> None:
    html = client.get("/search", params={"q": "х"}).text

    assert "Слишком короткий запрос" in html


def test_search_empty_page_shows_hint(client: TestClient) -> None:
    html = client.get("/search").text

    assert "Введите запрос" in html
    assert 'name="robots"' not in html, "пустая страница поиска индексируется"
