from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app
from zaruba.models import DanceStyle


def _client_with(settings: Settings, old: str, new: str) -> TestClient:
    """Клиент на копии контента с одной заменой: проверяем вычисление, а не эталон."""

    path = settings.content_dir / "2026.yaml"
    raw = path.read_text(encoding="utf-8")
    assert old in raw, f"в 2026.yaml нет строки {old!r}"
    path.write_text(raw.replace(old, new, 1), encoding="utf-8")
    return TestClient(create_app(settings))


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
    # Ценз 2026 объявлен: метка «6+» на афише кассы (сверено 18.11.2026,
    # в «Положении» её нет) — страница обязана показать её, а не «не объявлен».
    assert "возрастной ценз" in html
    assert "6+" in html


def test_tickets_page_explains_refund_and_discount(client: TestClient) -> None:
    html = client.get("/tickets").text

    assert "Скидка 25 %" in html
    assert "7 декабря 2026" in html
    assert "возврат" in html.lower()


def test_tickets_registration_link_comes_from_content(content_settings: Settings) -> None:
    """Ссылка на форму заявки — из `source_index`, а не строка в шаблоне (§6.13)."""

    with _client_with(
        content_settings,
        "https://forms.gle/W5651Np7epysbeWWA",
        "https://example.test/registration-2026",
    ) as client:
        html = client.get("/tickets").text

    assert 'href="https://example.test/registration-2026"' in html
    assert "https://forms.gle/W5651Np7epysbeWWA" not in html


def test_tickets_contacts_come_from_content(content_settings: Settings) -> None:
    """Телефон на `/tickets` — из YAML: шаблон не хранит номер сам."""

    with _client_with(
        content_settings, 'value: "+7 912 995-28-77"', 'value: "+7 912 995-28-70"'
    ) as client:
        html = client.get("/tickets").text

    assert "+7 912 995-28-70" in html
    assert "+7 912 995-28-77" not in html


def test_tickets_marks_expired_early_fee(content_settings: Settings) -> None:
    """Истёкший ранний тариф помечен, а не выдан за действующую цену."""

    with _client_with(
        content_settings, 'early_until: "2026-11-14"', 'early_until: "2026-01-01"'
    ) as client:
        html = client.get("/tickets").text

    assert "Ранний тариф" in html
    assert "1 января 2026" in html
    assert "истёк" in html
    assert "Базовый" in html


def test_judges_master_class_tariff_comes_from_content(content_settings: Settings) -> None:
    """Срок и действующая цена мастер-классов берутся из `early_until`."""

    with _client_with(
        content_settings, 'early_until: "2026-11-12"', 'early_until: "2026-01-01"'
    ) as client:
        html = client.get("/judges").text

    assert "1 января 2026" in html
    assert "истёк" in html
    assert "2\u00a0500 ₽" in html, "после срока действует базовая цена"


def test_judges_master_class_date_comes_from_edition_dates(
    content_settings: Settings,
) -> None:
    """Заголовок мастер-классов считается из `edition.dates`, а не зашит в HTML."""

    with _client_with(
        content_settings,
        'dates: ["2026-12-12", "2026-12-13"]',
        'dates: ["2026-12-19", "2026-12-20"]',
    ) as client:
        html = client.get("/judges").text

    assert "Мастер-классы 19 декабря" in html
    assert "Мастер-классы 12 декабря" not in html


def test_schedule_master_class_slot_comes_from_content(content_settings: Settings) -> None:
    """«10:00–16:00» — границы классов из YAML, а не константа шаблона."""

    path = content_settings.content_dir / "2026.yaml"
    raw = path.read_text(encoding="utf-8")
    assert 'starts_at: "10:00"' in raw
    assert 'ends_at: "16:00"' in raw
    path.write_text(
        raw.replace('starts_at: "10:00"', 'starts_at: "09:00"', 1).replace(
            'ends_at: "16:00"', 'ends_at: "17:00"', 1
        ),
        encoding="utf-8",
    )

    with TestClient(create_app(content_settings)) as client:
        html = client.get("/schedule").text

    assert "09:00–17:00" in html
    assert "10:00–16:00" not in html


def test_schedule_shows_the_current_master_class_price(content_settings: Settings) -> None:
    """После срока расписание показывает базовую цену, а не устаревшую раннюю."""

    with _client_with(
        content_settings, 'early_until: "2026-11-12"', 'early_until: "2026-01-01"'
    ) as client:
        html = client.get("/schedule").text

    assert "2\u00a0500 ₽" in html


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
