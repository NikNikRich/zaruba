"""Медиа и коммерческие предложения: модели, целостность и `content check`.

Модули расширяемые, поэтому тесты фиксируют не только «валидная строка
проходит», но и то, что опечатка в платформе, категории или `erid` ломает
`python -m zaruba.content check`, а не тихо попадает на страницу.
"""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zaruba.config import Settings
from zaruba.content import ContentError, load_edition, main, merge_overlay
from zaruba.main import create_app
from zaruba.models import (
    CommerceOffer,
    Edition,
    MediaItem,
    MediaKind,
    MediaPlatform,
    OfferCategory,
    OfferStatus,
)
from zaruba.templating import _footer_partners


def _edition(**overrides: object) -> Edition:
    """Минимальный выпуск: всё, чего нет в тесте, берётся по умолчанию."""

    payload: dict[str, object] = {
        "edition": 2026,
        "title": "ЗАРУБА 2026",
        "dates": [date(2026, 12, 12)],
        "venue": "zolotye-vorota",
        "updated_at": datetime(2026, 11, 1, 12, 0),
    }
    payload.update(overrides)
    return Edition.model_validate(payload)


def _media(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "slug": "teaser-2026",
        "title": "Тизер ЗАРУБА 2026",
        "kind": "video",
        "platform": "VK Видео",
        "url": "https://vk.com/video-185685166_456239017",
        "meta": "01:12",
    }
    payload.update(overrides)
    return payload


def _offer(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "slug": "hoodie-2026",
        "title": "Худи ЗАРУБА",
        "category": "merch",
        "price": 4900,
        "status": "available",
        "description": "Лимитированная серия для участников и зрителей выпуска.",
        "cta_label": "Заказать мерч",
        "url": "https://zaruba72.example/shop/hoodie",
    }
    payload.update(overrides)
    return payload


def test_media_item_keeps_platform_and_kind() -> None:
    item = MediaItem.model_validate(_media())

    assert item.kind is MediaKind.VIDEO
    assert item.platform is MediaPlatform.VK_VIDEO
    assert item.kind.badge == "Play"
    assert item.preview_image is None
    assert item.nomination_slug is None


@pytest.mark.parametrize("value", ["video", "photo", "stream"])
def test_every_media_kind_has_badge_and_label(value: str) -> None:
    kind = MediaKind(value)

    assert kind.label
    assert kind.badge


@pytest.mark.parametrize(
    "platform",
    ["VK Видео", "Rutube", "Яндекс Диск", "Облако Mail.ru", "YouTube", "Другое"],
)
def test_media_platforms_are_the_supported_hosts(platform: str) -> None:
    assert MediaPlatform(platform).value == platform


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "reels"),
        ("platform", "TikTok"),
        ("url", "ftp://example.com/video"),
        ("title", "ок"),
        ("preview_image", "../secrets.webp"),
        ("preview_image", "img/people/../../etc.webp"),
        ("preview_image", "https://example.com/poster.webp"),
    ],
)
def test_media_item_rejects_unexpected_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        MediaItem.model_validate(_media(**{field: value}))


def test_media_item_accepts_own_static_preview() -> None:
    item = MediaItem.model_validate(_media(preview_image="img/media/teaser-2026.webp"))

    assert item.preview_image == "img/media/teaser-2026.webp"


def test_offer_partner_has_no_price() -> None:
    offer = CommerceOffer.model_validate(
        _offer(
            slug="partner-photo-school",
            title="Фотостудия «Кадр»",
            category="partner",
            price=None,
            description="Партнёр выпуска: съёмка за кулисами и фотозона для гостей.",
            cta_label="Сайт партнёра",
        )
    )

    assert offer.price is None
    assert offer.category is OfferCategory.PARTNER
    assert offer.status is OfferStatus.AVAILABLE


@pytest.mark.parametrize(
    ("category", "price"),
    [
        ("merch", None),
        ("photo_pack", None),
        ("vip", None),
        ("partner", 1000),
    ],
)
def test_offer_price_follows_category(category: str, price: int | None) -> None:
    with pytest.raises(ValidationError):
        CommerceOffer.model_validate(_offer(category=category, price=price))


@pytest.mark.parametrize("erid", ["2VtzqwXk9Ab", "abcdef12"])
def test_offer_accepts_marking_token(erid: str) -> None:
    assert CommerceOffer.model_validate(_offer(erid=erid)).erid == erid


@pytest.mark.parametrize("erid", ["", "ер к", "abc", "x" * 65])
def test_offer_rejects_broken_erid(erid: str) -> None:
    with pytest.raises(ValidationError):
        CommerceOffer.model_validate(_offer(erid=erid))


def test_offer_status_labels_are_human() -> None:
    assert CommerceOffer.model_validate(_offer(status="sold_out")).status.label == "распродано"
    assert CommerceOffer.model_validate(_offer(status="preorder")).status.label == "предзаказ"


def test_edition_reads_media_and_commerce() -> None:
    edition = _edition(media=[_media()], commerce=[_offer()])

    assert [item.slug for item in edition.media] == ["teaser-2026"]
    assert [item.slug for item in edition.commerce] == ["hoodie-2026"]


def test_edition_rejects_duplicate_media_slug() -> None:
    with pytest.raises(ValidationError, match="media: повторяются slug"):
        _edition(media=[_media(), _media()])


def test_edition_rejects_duplicate_commerce_slug() -> None:
    with pytest.raises(ValidationError, match="commerce: повторяются slug"):
        _edition(commerce=[_offer(), _offer()])


def _edition_yaml(media_row: str) -> str:
    return f"""
edition: 2026
title: ЗАРУБА 2026
dates: ["2026-12-12"]
venue: zolotye-vorota
updated_at: "2026-11-01T12:00:00+05:00"
media:
  - {media_row}
"""


def test_content_check_names_file_and_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "2026.yaml"
    path.write_text(
        _edition_yaml("slug: teaser-2026\n    title: Тизер ЗАРУБА 2026\n    kind: reels")
        + "    platform: VK Видео\n    url: https://vk.com/video-1\n",
        encoding="utf-8",
    )

    assert main(["check", str(path)]) == 1
    captured = capsys.readouterr()
    assert "2026.yaml" in captured.err
    assert "kind" in captured.err


def test_load_edition_reports_unknown_nomination(tmp_path: Path) -> None:
    path = tmp_path / "2026.yaml"
    path.write_text(
        _edition_yaml(
            "slug: teaser-2026\n"
            "    title: Тизер ЗАРУБА 2026\n"
            "    kind: video\n"
            "    platform: VK Видео\n"
            "    url: https://vk.com/video-1\n"
            "    nomination_slug: best-nope"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContentError, match="неизвестная номинация"):
        load_edition(path)


def test_overlay_can_add_media_and_commerce() -> None:
    base: dict[str, object] = {
        "edition": 2026,
        "title": "ЗАРУБА 2026",
        "dates": ["2026-12-12"],
        "venue": "zolotye-vorota",
        "updated_at": "2026-11-01T12:00:00+05:00",
    }
    overlay = {"media": [_media()], "commerce": [_offer()]}

    merged = merge_overlay(base, [(Path("2026.d/preview.yaml"), overlay)])
    edition = Edition.model_validate(merged)

    assert edition.media[0].slug == "teaser-2026"
    assert edition.commerce[0].category is OfferCategory.MERCH


REPO_ROOT = Path(__file__).resolve().parents[1]


def _preview_settings(tmp_path: Path) -> Settings:
    """Копия `content/` с предпросмотром: медиа и коммерция лежат в overlay."""

    content = tmp_path / "content"
    shutil.copytree(REPO_ROOT / "content", content)
    return Settings(
        content_dir=content / "editions",
        venues_file=content / "venues.yaml",
        glossary_file=content / "glossary.yaml",
        faq_file=content / "faq.yaml",
        history_file=content / "history.yaml",
        photos_file=content / "photos.yaml",
        results_dir=content / "results",
        preview_enabled=True,
    )


def _html(tmp_path: Path, path: str) -> str:
    return TestClient(create_app(_preview_settings(tmp_path))).get(path).text


def test_home_renders_media_cards_and_offers(tmp_path: Path) -> None:
    html = _html(tmp_path, "/")

    assert html.count('class="media-card"') == 3
    assert "VK Видео" in html and "Яндекс Диск" in html
    assert "media-card__badge" in html and ">Play<" in html
    assert html.count('class="offer-card"') == 2
    assert "4\u00a0900 ₽" in html
    assert "Мерч и фотопакеты" in html
    assert "Партнёры и спонсоры" in html


def test_media_page_groups_items_and_sits_in_navigation(tmp_path: Path) -> None:
    client = TestClient(create_app(_preview_settings(tmp_path)))
    media = client.get("/media").text

    assert "Видео · 1" in media
    assert "Фото · 1" in media
    assert "Трансляция · 1" in media
    assert "<iframe" not in media.lower()
    assert 'href="/media"' in client.get("/").text


def test_media_cards_link_out_without_frames(tmp_path: Path) -> None:
    media = _html(tmp_path, "/media")
    links = re.findall(r'<a class="media-card"[^>]*>', media)

    assert len(links) == 3
    assert all('rel="noopener noreferrer"' in link for link in links)


def test_commercial_links_are_sponsored_and_erid_is_optional(tmp_path: Path) -> None:
    html = _html(tmp_path, "/tickets")
    cards = re.findall(r'<article class="offer-card".*?</article>', html, flags=re.S)

    assert len(cards) == 2
    hoodie = next(card for card in cards if "hoodie-2026" in card)
    photo_pack = next(card for card in cards if "photo-pack-2026" in card)
    assert "Реклама" not in hoodie, "плашка рекламы без erid"
    assert 'Реклама • erid: <span class="mono">2VtzqwXk9Ab</span>' in photo_pack

    links = re.findall(r'<a[^>]+href="https://zaruba72\.example[^>]*>', html)
    assert links, "коммерческие ссылки не отрендерились"
    assert all('rel="sponsored noopener noreferrer"' in link for link in links), links


def test_results_attach_media_to_nomination(tmp_path: Path) -> None:
    """Материал с `nomination_slug` встаёт под таблицей своей номинации."""

    settings = _preview_settings(tmp_path)
    results = yaml.safe_load((settings.results_dir / "2025.yaml").read_text(encoding="utf-8"))
    nomination = results["nominations"][0]["nomination"]
    edition_path = settings.content_dir / "2025.yaml"
    edition_path.write_text(
        edition_path.read_text(encoding="utf-8")
        + f"""
media:
  - slug: photo-2025-final
    title: Фотоотчёт номинации — Яндекс Диск
    kind: photo
    platform: Яндекс Диск
    url: https://disk.yandex.ru/d/zaruba-2025-final
    nomination_slug: {nomination}
""",
        encoding="utf-8",
    )

    html = TestClient(create_app(settings)).get("/results").text

    assert "Фотоотчёт номинации — Яндекс Диск" in html
    assert html.count('class="media-card"') == 1


def test_without_media_and_commerce_no_empty_sections(client: TestClient) -> None:
    """Списки пусты — блоков нет вовсе, а не «0 материалов» и пустая сетка."""

    home = client.get("/").text
    assert "media-card" not in home
    assert "offer-card" not in home
    assert "Партнёры и спонсоры" not in home
    assert "offer-grid" not in client.get("/tickets").text

    media = client.get("/media").text
    assert "Почему пусто" in media
    assert "media-grid" not in media


def test_footer_partners_are_skipped_on_error_pages_and_broken_content(
    broken_settings: Settings,
) -> None:
    """Футер не грузит контент на странице ошибки и не падает от битого YAML."""

    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=broken_settings))
    )

    assert _footer_partners(request, 503) == []
    assert _footer_partners(request, 200) == []


def test_media_nomination_must_exist() -> None:
    with pytest.raises(ValidationError, match="media.teaser-2026: неизвестная номинация"):
        _edition(media=[_media(nomination_slug="best-nope")])
