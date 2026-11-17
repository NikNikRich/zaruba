"""Медиа и коммерческие предложения: модели, целостность и `content check`.

Модули расширяемые, поэтому тесты фиксируют не только «валидная строка
проходит», но и то, что опечатка в платформе, категории или `erid` ломает
`python -m zaruba.content check`, а не тихо попадает на страницу.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from zaruba.content import ContentError, load_edition, main, merge_overlay
from zaruba.models import (
    CommerceOffer,
    Edition,
    MediaItem,
    MediaKind,
    MediaPlatform,
    OfferCategory,
    OfferStatus,
)


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


def test_media_nomination_must_exist() -> None:
    with pytest.raises(ValidationError, match="media.teaser-2026: неизвестная номинация"):
        _edition(media=[_media(nomination_slug="best-nope")])
