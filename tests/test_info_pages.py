from fastapi.testclient import TestClient

from zaruba.models import DanceStyle


def test_judges_page_shows_confirmed_people_only(client: TestClient) -> None:
    """§6.11: имена — только из источника, пустые поля — «уточняется»."""

    html = client.get("/judges").text

    for name in ("Фара", "Александр Бурцев", "Дарья Губанова", "Дмитрий Бончинче", "Гео (GEO)"):
        assert name in html, name
    assert "ведущий" in html
    assert "ведёт сцену · блоки уточняются" in html
    assert "какие блоки судит — уточняется" in html
    assert 'href="https://vk.com/zaruba72champ"' in html
    assert "Ирина" not in html, "жюри 2025 в выпуске 2026 быть не должно"


def test_judges_page_shows_official_criteria(client: TestClient) -> None:
    """§1.6.5: критерии и баллы — из «Положения», а не из общих слов."""

    html = client.get("/judges").text

    assert "Сложность хореографии" in html
    assert "Техничность и синхронность" in html
    assert "Внешний вид" in html
    assert "Мастер-классы 12 декабря" in html
    assert "Официального списка критериев нет" not in html


def test_tickets_page_does_not_sell_anything(client: TestClient) -> None:
    """§6.13: сайт не продаёт билеты; цены — только подтверждённые."""

    html = client.get("/tickets").text

    assert "Мы не продаём билеты и не принимаем деньги" in html
    assert "Продажи на выпуск 2025 закрыты" not in html
    assert "1\u00a0000 ₽" in html
    assert "1\u00a0800 ₽" in html
    assert "1\u00a0500 ₽" in html, "взносы в таблице — с разрядами, как цены билетов"
    assert "1500 ₽" not in html, "разряды нужны и во взносах: «1 500», а не «1500»"
    assert "Официальная форма заявки" in html
    assert "не объявлен" in html, "ценз 2026 на афише не опубликован"


def test_tickets_page_explains_refund_and_discount(client: TestClient) -> None:
    html = client.get("/tickets").text

    assert "Скидка 25 %" in html
    assert "7 декабря 2026" in html
    assert "возврат" in html.lower()


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

    assert 'href="/glossary#style-heels-girly"' in html
    assert 'href="/glossary#style-experimental"' in html
    assert "БЕГ — команда занимается до 3 лет" in html


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
    assert 'href="https://vk.com/zaruba72champ"' in html
    assert 'href="https://forms.gle/W5651Np7epysbeWWA"' in html
