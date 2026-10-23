from fastapi.testclient import TestClient

from zaruba.models import DanceStyle


def test_judges_page_shows_confirmed_people_only(client: TestClient) -> None:
    """§6.11: имена — только из источника, пустые поля — «уточняется»."""

    html = client.get("/judges").text

    for name in ("Анастасия Юрасова", "Андрей Морыженков", "Наталья Мафия", "GEO"):
        assert name in html, name
    assert "MATÈ" in html
    assert "ведущий" in html
    assert "ведёт сцену · блоки уточняются" in html
    assert "какие блоки судит — уточняется" in html
    assert 'href="https://tyumen.kassy.ru/"' in html


def test_judges_page_states_criteria_are_unconfirmed(client: TestClient) -> None:
    html = client.get("/judges").text

    assert "Официального списка критериев нет" in html
    assert "«Положение» чемпионата не опубликовано" in html
    assert "не правила «Зарубы»" in html


def test_tickets_page_does_not_sell_anything(client: TestClient) -> None:
    """§6.13: сайт не продаёт билеты и не выдумывает цены."""

    html = client.get("/tickets").text

    assert "Мы не продаём билеты и не принимаем деньги" in html
    assert "Продажи на выпуск 2025 закрыты" in html
    assert "Цены не публикуем" in html
    assert 'href="https://tyumen.kassy.ru/"' in html
    assert "6+" in html
    # Единственное упоминание рубля — объяснение, почему диапазонов на сайте нет.
    assert html.count("₽") == 1


def test_glossary_covers_every_style_and_terms(client: TestClient) -> None:
    """§6.15: девять направлений — по одному якорю на стиль из справочника."""

    html = client.get("/glossary").text

    for style in DanceStyle:
        assert f'id="style-{style.value}"' in html, style.value
    assert html.count('id="term-') == 8
    assert 'id="term-nominaciya"' in html
    assert "Пример:" in html


def test_event_directions_link_to_glossary(client: TestClient) -> None:
    """Направление на /event ведёт в глоссарий, а не в пустоту."""

    html = client.get("/event").text

    assert 'href="/glossary#style-hip-hop"' in html
    assert 'href="/glossary#style-contemporary"' in html


def test_faq_has_twelve_questions_in_four_groups(client: TestClient) -> None:
    html = client.get("/faq").text

    assert html.count("<details") == 12
    for group in ("spectator", "participant", "parent", "site"):
        assert f'id="faq-{group}"' in html, group
    assert "Зрителю" in html
    assert "Участнику" in html
    assert "Родителям" in html
    assert "Сайту" in html


def test_faq_marks_unconfirmed_answers(client: TestClient) -> None:
    """Ответ без источника помечен статусом, а не выдаётся за факт."""

    html = client.get("/faq").text

    assert "не подтверждено" in html
    assert "подтверждено" in html
    assert "Сколько стоит билет?" in html
    assert "vk.com/zaruba72champ" in html


def test_faq_links_source_for_confirmed_answer(client: TestClient) -> None:
    html = client.get("/faq").text

    assert "Мы не принимаем заявки и не берём данные" in html
    assert 'href="https://tyumen.kassy.ru/"' in html
