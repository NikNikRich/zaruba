"""§6.2 п.18 и §5.8: служебные страницы — дисклеймер, регламент, данные, удаление."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import load_latest

LEGAL_PAGES = ("/about", "/rules", "/privacy", "/takedown")
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "zaruba" / "templates"


def test_legal_pages_render_and_are_indexable(client: TestClient) -> None:
    """Служебные страницы — не «черновики»: 200, заголовок и никакого noindex."""

    for path in LEGAL_PAGES:
        response = client.get(path)
        html = response.text

        assert response.status_code == 200, path
        assert "<h1>" in html, path
        assert 'name="robots"' not in html, path
        assert f'<link rel="canonical" href="http://testserver{path}">' in html, path


def test_footer_links_to_every_legal_page_on_any_page(client: TestClient) -> None:
    """§6.3: четыре ссылки в футере каждой страницы, а не только на главной."""

    for page in ("/", "/faq", "/schedule"):
        html = client.get(page).text

        assert 'aria-label="О сайте"' in html, page
        for path in LEGAL_PAGES:
            assert f'href="{path}"' in html, f"{page} → {path}"


def test_disclaimer_is_on_every_page_and_does_not_pretend_to_be_official(
    client: TestClient,
) -> None:
    html = client.get("/about").text

    assert "Неофициальный фанатский проект" in html
    assert "Не связан с организаторами" in html
    assert "не продаёт билеты" in html


def test_about_explains_what_the_site_does_not_do(client: TestClient) -> None:
    html = client.get("/about").text

    assert "зрители, а не организаторы" in html
    assert "результаты" in html and "/ratings" in html
    assert "личных профили" in html or "ссылок на личные профили" in html
    assert "фото детей" in html.lower()
    assert "комментарии" in html


def test_rules_quote_the_regulation_verbatim(client: TestClient) -> None:
    """Регламент пересказан без потерь: каждое правило «Положения» есть в HTML."""

    edition = load_latest(Settings()).edition
    html = client.get("/rules").text

    assert edition.rules, "у выпуска должен быть регламент"
    for rule in edition.rules:
        assert rule in html, rule
    assert "Правила участия" in html
    assert "Правила сайта" in html, "наши правила — отдельным списком, а не в регламенте"


def test_rules_explain_children_and_moderation(client: TestClient) -> None:
    html = client.get("/rules").text

    assert "только инициалы" in html
    assert "письменное согласие" in html
    assert "комментариев нет" in html.lower()
    assert "/takedown" in html


def test_privacy_names_data_and_absence_of_forms(client: TestClient) -> None:
    html = client.get("/privacy").text

    assert "152-ФЗ" in html
    assert "дат рождения" in html.lower()
    assert "счётчиков аналитики" in html
    assert "48 часов" in html
    assert "Полей для персональных данных на сайте нет" in html


def test_templates_have_no_hardcoded_phone_links() -> None:
    """Телефон в разметке — контакт без источника. Только из данных выпуска."""

    offenders = [
        f"{path.relative_to(TEMPLATES_DIR)}:{number}"
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "tel:" in line
    ]

    assert not offenders, f"захардкоженный tel: в шаблонах — {offenders}"


def test_privacy_separates_official_contacts_from_personal_data(client: TestClient) -> None:
    """`/privacy` не спорит сама с собой: контакты оргкомитета — не личные данные."""

    edition = load_latest(Settings()).edition
    html = client.get("/privacy").text

    assert "ни телефонов, ни e-mail" not in html, "отрицание противоречит контактам оргкомитета"
    assert "персональных контактов" in html or "личных контактов" in html
    assert "оргкомитета" in html
    assert edition.contacts, "в выпуске нет официальных контактов"
    for contact in edition.contacts:
        assert contact.value in html, contact.value
        assert edition.source_index[contact.source_id].url in html, contact.source_id


def test_takedown_promises_48_hours_and_a_human_channel(client: TestClient) -> None:
    html = client.get("/takedown").text

    assert "48 часов" in html
    assert "в течение суток" in html
    assert "https://vk.com/zaruba72champ" in html
    assert 'rel="noopener noreferrer nofollow"' in html
    assert "/privacy" in html
    assert "родитель" in html, "заявку за ребёнка подаёт законный представитель"


def test_no_forms_on_legal_pages(client: TestClient) -> None:
    """Форм нет осознанно: заявку пришлось бы хранить и читать — обещать нечего."""

    for path in LEGAL_PAGES:
        assert "<form" not in client.get(path).text, path


def test_takedown_pages_links_resolve(client: TestClient) -> None:
    """§6.21: ни одна ссылка со служебных страниц не ведёт в 404."""

    checked: set[str] = set()
    broken: list[str] = []
    for page in LEGAL_PAGES:
        html = client.get(page).text
        for href in re.findall(r'href="(/[^"]*)"', html):
            path = href.split("#", 1)[0]
            if not path or path in checked:
                continue
            checked.add(path)
            if client.get(path).status_code != 200:
                broken.append(f"{page} → {path}")

    assert not broken, broken
