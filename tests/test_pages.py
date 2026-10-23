from fastapi.testclient import TestClient

NAV_WITHOUT_IMPLEMENTATION = ("/performances", "/participants", "/ratings")


def test_home_shows_key_facts_without_js(client: TestClient) -> None:
    """§6.22 п.5: страница читается при выключенном JS, факты — в HTML."""

    html = client.get("/").text

    assert "Чемпионат по современным танцам" in html
    assert "6–7 декабря 2025" in html
    assert "Золотые ворота Сибири" in html
    assert "ул. Герцена, 87" in html
    assert "<script" not in html.lower()


def test_home_does_not_link_to_missing_pages(client: TestClient) -> None:
    """В шапке только реализованные разделы: ссылок в 404 быть не должно."""

    html = client.get("/").text

    assert 'href="/schedule"' in html
    assert 'href="/venue"' in html
    for path in NAV_WITHOUT_IMPLEMENTATION:
        assert f'href="{path}"' not in html


def test_schedule_renders_blocks_with_anchors(client: TestClient) -> None:
    """§6.22 п.3: якорь `#block-...` открывает нужный блок."""

    html = client.get("/schedule").text

    assert 'id="block-day1-solo"' in html
    assert 'id="block-day2-teams"' in html
    assert 'id="block-day2-show"' in html
    assert "12:00–15:00" in html


def test_schedule_day_filter(client: TestClient) -> None:
    html = client.get("/schedule?day=2").text

    assert 'id="block-day2-teams"' in html
    assert 'id="block-day1-solo"' not in html
    assert "День 2" in html


def test_schedule_rejects_unknown_day(client: TestClient) -> None:
    assert client.get("/schedule?day=9").status_code == 422


def test_venue_page_states_unknowns_honestly(client: TestClient) -> None:
    html = client.get("/venue").text

    assert "Золотые ворота Сибири" in html
    assert "до 850 гостей" in html
    assert "не придумываем" in html
    assert "/schedule" in html


def test_pages_warn_about_past_edition(client: TestClient) -> None:
    """§6.3: если ключевой факт не актуален — сверху честная полоса, а не тишина."""

    for path in ("/", "/schedule", "/venue"):
        assert "Выпуск 2025 завершён" in client.get(path).text, path


def test_demo_rows_are_marked(client: TestClient) -> None:
    """Демонстрационные данные помечены бейджем «пример», а не выдаются за факты."""

    html = client.get("/").text

    assert "пример" in html
    assert "демонстрационные" in html


def test_pages_have_no_none_artifacts(client: TestClient) -> None:
    for path in ("/", "/schedule", "/venue"):
        html = client.get(path).text
        assert "None" not in html, path
        assert "&lt;None&gt;" not in html, path

