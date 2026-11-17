"""Рекламные слоты (§6.27): флаг, заглушки, маркировка и валидация офферов.

Проверяется не «красиво ли», а контракт: без валидного оффера с `erid` слот
не рендерится, в prod заглушек нет, а маркировка 38-ФЗ стоит рядом с ссылкой.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zaruba.config import Settings
from zaruba.content import (
    ContentError,
    load_offers,
    load_preview_service_tips,
    main,
)
from zaruba.main import create_app
from zaruba.models import ServiceTip, TipSlot
from zaruba.services.tips import resolve_tip, tip_for_slot

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tip(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "slug": "coffee-2026",
        "slot": "schedule_top",
        "title": "Кофе на площадке",
        "text": "Кофейня в фойе КТЗ: скидка по бейджу участника.",
        "cta_label": "Меню",
        "url": "https://example.ru/menu",
        "advertiser": "ИП Иванов И. И.",
        "erid": "2VtzqwXk9Ab",
    }
    payload.update(overrides)
    return payload


def _write_offers(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        yaml.safe_dump({"tips": rows}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _settings(base: Settings, **overrides: object) -> Settings:
    """Копия настроек контента с точечными правками для одного теста."""

    payload: dict[str, Any] = base.model_dump()
    payload.update(overrides)
    return Settings(**payload)


def _client(base: Settings, **overrides: object) -> TestClient:
    """Клиент на копии контента с точечными правками настроек."""

    return TestClient(create_app(_settings(base, **overrides)))


def test_service_tip_keeps_slot_and_marking() -> None:
    tip = ServiceTip.model_validate(_tip())

    assert tip.slot is TipSlot.SCHEDULE_TOP
    assert tip.priority == 100
    assert tip.erid == "2VtzqwXk9Ab"


@pytest.mark.parametrize("field", ["erid", "advertiser", "url", "slug", "slot"])
def test_service_tip_requires_marking_and_target(field: str) -> None:
    payload = _tip()
    del payload[field]

    with pytest.raises(ValidationError):
        ServiceTip.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slot", "sidebar"),
        ("erid", "short"),
        ("url", "ftp://example.ru/menu"),
        ("priority", 5000),
        ("advertiser", "ИП"),
        ("text", "коротко"),
        ("extra", "лишнее поле"),
    ],
)
def test_service_tip_rejects_unexpected_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ServiceTip.model_validate(_tip(**{field: value}))


def test_tip_for_slot_prefers_priority_and_skips_expired() -> None:
    today = date(2026, 12, 1)
    tips = [
        ServiceTip.model_validate(_tip(slug="late", priority=50)),
        ServiceTip.model_validate(_tip(slug="early", priority=10)),
        ServiceTip.model_validate(
            _tip(slug="expired", priority=1, valid_until=today - timedelta(days=1))
        ),
    ]

    chosen = tip_for_slot(tips, TipSlot.SCHEDULE_TOP, today=today)
    assert chosen is not None
    assert chosen.slug == "early"
    assert tip_for_slot(tips, TipSlot.CHECKLIST_CARD, today=today) is None


def test_tip_for_slot_is_deterministic_on_equal_priority() -> None:
    today = date(2026, 12, 1)
    tips = [
        ServiceTip.model_validate(_tip(slug="b-tip")),
        ServiceTip.model_validate(_tip(slug="a-tip")),
    ]

    chosen = tip_for_slot(tips, TipSlot.SCHEDULE_TOP, today=today)
    assert chosen is not None
    assert chosen.slug == "a-tip"


def test_missing_offers_file_means_no_ads(tmp_path: Path) -> None:
    assert load_offers(tmp_path / "offers.yaml") == []


def test_offers_are_loaded_and_duplicates_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "offers.yaml"
    _write_offers(path, [_tip(), _tip(slug="photo", slot="checklist_card")])

    assert [tip.slug for tip in load_offers(path)] == ["coffee-2026", "photo"]

    _write_offers(path, [_tip(), _tip()])
    with pytest.raises(ContentError, match="повторяются"):
        load_offers(path)


def test_battle_offers_reject_demo_erid(tmp_path: Path) -> None:
    path = tmp_path / "offers.yaml"
    _write_offers(path, [_tip(erid="DEMO-PREVIEW-01")])

    with pytest.raises(ContentError) as error:
        load_offers(path)

    assert "DEMO-" in str(error.value)
    assert "offers.preview.yaml" in str(error.value)


def test_broken_offer_names_file_and_field(tmp_path: Path) -> None:
    path = tmp_path / "offers.yaml"
    _write_offers(path, [_tip(erid="")])

    with pytest.raises(ContentError) as error:
        load_offers(path)

    assert "offers.yaml" in str(error.value)
    assert "erid" in str(error.value)


def test_flag_defaults_follow_the_environment() -> None:
    assert Settings().service_tips_visible is True
    assert Settings().service_tips_placeholders is True

    assert Settings(env="prod").service_tips_visible is False
    assert Settings(env="prod").service_tips_placeholders is False

    assert Settings(service_tips_enabled=False).service_tips_visible is False
    assert Settings(env="prod", service_tips_enabled=True).service_tips_visible is True
    assert Settings(env="prod", service_tips_enabled=True).service_tips_placeholders is False

    # Презентация показывает заполненные примеры и третью заглушку даже в prod:
    # страницы preview закрыты от индексации, а явный выключатель не должен
    # превращать показ в пустые рамки.
    assert Settings(preview_enabled=True).service_tips_visible is True
    assert Settings(preview_enabled=True).service_tips_placeholders is True
    assert Settings(env="prod", preview_enabled=True).service_tips_visible is True
    assert Settings(env="prod", preview_enabled=True).service_tips_placeholders is True
    assert (
        Settings(env="prod", preview_enabled=True, service_tips_enabled=False).service_tips_visible
        is True
    )


def test_placeholders_are_rendered_in_dev(content_settings: Settings) -> None:
    with _client(content_settings) as client:
        schedule = client.get("/schedule").text
        faq = client.get("/faq").text
        results = client.get("/results").text

    assert 'data-slot="schedule_top"' in schedule
    assert "[Место под полезную интеграцию: слот schedule_top]" in schedule
    assert 'data-placeholder="true"' in schedule
    assert "[Место под полезную интеграцию: слот checklist_card]" in faq
    assert "[Место под полезную интеграцию: слот protocols_inline]" in results


def test_disabled_flag_renders_nothing(content_settings: Settings) -> None:
    with _client(content_settings, service_tips_enabled=False) as client:
        for path in ("/schedule", "/faq", "/results"):
            assert "service-tip" not in client.get(path).text, path


def test_prod_never_renders_placeholders(content_settings: Settings) -> None:
    with _client(content_settings, env="prod") as client:
        assert "service-tip" not in client.get("/schedule").text

    with _client(content_settings, env="prod", service_tips_enabled=True) as client:
        assert "service-tip" not in client.get("/schedule").text


def test_real_offer_is_marked_and_stays_in_its_slot(content_settings: Settings) -> None:
    _write_offers(
        content_settings.offers_file,
        [
            _tip(),
            _tip(
                slug="photo-pack",
                slot="checklist_card",
                title="Фотопакет участника",
                text="Все кадры выступления в полном разрешении.",
            ),
            _tip(
                slug="stream",
                slot="protocols_inline",
                title="Трансляция дня",
                text="Прямой эфир со второй сцены выпуска.",
            ),
        ],
    )

    with _client(content_settings, service_tips_enabled=True) as client:
        schedule = client.get("/schedule").text
        faq = client.get("/faq").text
        results = client.get("/results").text

    assert schedule.count('data-slot="schedule_top"') == 1
    assert "Кофе на площадке" in schedule
    assert 'rel="sponsored noopener noreferrer"' in schedule
    assert "ИП Иванов И. И." in schedule
    assert "2VtzqwXk9Ab" in schedule
    assert "[Место под" not in schedule
    assert "Фотопакет участника" not in schedule

    assert 'data-slot="checklist_card"' in faq
    assert "Фотопакет участника" in faq
    assert "Кофе на площадке" not in faq

    assert 'data-slot="protocols_inline"' in results
    assert "Трансляция дня" in results
    assert "Кофе на площадке" not in results


def test_expired_offer_leaves_the_slot_empty_in_prod(content_settings: Settings) -> None:
    yesterday = date.today() - timedelta(days=1)
    _write_offers(content_settings.offers_file, [_tip(valid_until=yesterday)])

    with _client(content_settings, env="prod", service_tips_enabled=True) as client:
        assert "service-tip" not in client.get("/schedule").text


def test_valid_offer_is_rendered_in_prod(content_settings: Settings) -> None:
    _write_offers(content_settings.offers_file, [_tip()])

    with _client(content_settings, env="prod", service_tips_enabled=True) as client:
        html = client.get("/schedule").text

    assert "Кофе на площадке" in html
    assert "2VtzqwXk9Ab" in html
    assert "[Место под" not in html


def test_content_check_reports_broken_offers(
    content_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Битый `offers.yaml` ломает pre-commit, а не первую страницу в проде."""

    _write_offers(content_settings.offers_file, [_tip(erid="")])
    monkeypatch.setattr("zaruba.content.get_settings", lambda: content_settings)

    assert main(["check"]) == 1
    assert "offers.yaml" in capsys.readouterr().err


class _PreviewDate(date):
    """Фиксированная дата показа: демо-офферы живут до 2026-12-20."""

    @classmethod
    def today(cls) -> date:
        return date(2026, 12, 1)


def test_preview_offers_are_demo_and_do_not_leak_into_battle_file() -> None:
    demo = load_offers(REPO_ROOT / "content" / "offers.preview.yaml", allow_demo=True)
    battle = load_offers(REPO_ROOT / "content" / "offers.yaml")

    assert {tip.slot for tip in demo} == {TipSlot.SCHEDULE_TOP, TipSlot.CHECKLIST_CARD}
    assert {tip.erid for tip in demo} == {"DEMO-PREVIEW-01", "DEMO-PREVIEW-02"}
    assert all(tip.url.host.endswith(".example") for tip in demo)
    assert all(tip.valid_until == date(2026, 12, 20) for tip in demo)
    assert all(not tip.erid.startswith("DEMO-") for tip in battle)


def test_preview_offer_expires_like_a_real_one(content_settings: Settings) -> None:
    preview = load_offers(content_settings.offers_preview_file, allow_demo=True)

    assert tip_for_slot(preview, TipSlot.SCHEDULE_TOP, today=date(2026, 12, 20)) is not None
    assert tip_for_slot(preview, TipSlot.SCHEDULE_TOP, today=date(2026, 12, 21)) is None


def test_tip_for_slot_uses_fallback_only_without_a_real_offer() -> None:
    today = date(2026, 12, 1)
    expired = ServiceTip.model_validate(_tip(valid_until=today - timedelta(days=1)))
    demo = ServiceTip.model_validate(_tip(slug="demo", erid="DEMO-PREVIEW-01"))
    real = ServiceTip.model_validate(_tip())

    assert tip_for_slot([expired], TipSlot.SCHEDULE_TOP, today=today, fallback=[demo]) == demo
    assert tip_for_slot([real], TipSlot.SCHEDULE_TOP, today=today, fallback=[demo]) == real
    assert tip_for_slot([], TipSlot.CHECKLIST_CARD, today=today, fallback=[demo]) is None


def test_resolve_tip_ignores_preview_until_the_flag_is_on(content_settings: Settings) -> None:
    off = _settings(content_settings, preview_enabled=False, service_tips_enabled=True)
    on = _settings(content_settings, preview_enabled=True)

    assert resolve_tip(off, TipSlot.SCHEDULE_TOP, today=date(2026, 12, 1)) is None
    chosen = resolve_tip(on, TipSlot.SCHEDULE_TOP, today=date(2026, 12, 1))
    assert chosen is not None
    assert chosen.erid == "DEMO-PREVIEW-01"


def test_missing_preview_file_means_no_demo(tmp_path: Path) -> None:
    settings = Settings(offers_preview_file=tmp_path / "offers.preview.yaml")

    assert load_preview_service_tips(settings) == []


def test_preview_demos_never_render_without_the_flag(content_settings: Settings) -> None:
    with _client(content_settings, preview_enabled=False) as client:
        schedule = client.get("/schedule").text
        faq = client.get("/faq").text

    assert "Демо-интеграция" not in schedule
    assert "Демо-интеграция" not in faq
    assert "[Место под полезную интеграцию: слот schedule_top]" in schedule
    assert "[Место под полезную интеграцию: слот checklist_card]" in faq

    with _client(
        content_settings, env="prod", preview_enabled=False, service_tips_enabled=True
    ) as client:
        html = client.get("/schedule").text

    assert "Демо-интеграция" not in html
    assert "service-tip" not in html


@pytest.mark.parametrize("env", ["local", "prod"])
def test_preview_mode_renders_two_demo_offers(
    content_settings: Settings, monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setattr("zaruba.services.tips.date", _PreviewDate)

    with _client(content_settings, env=env, preview_enabled=True) as client:
        schedule = client.get("/schedule").text
        faq = client.get("/faq").text
        results = client.get("/results").text

    assert 'data-slot="schedule_top"' in schedule
    assert "Трансфер и парковка у КТЗ" in schedule
    assert "Демо-интеграция • Партнерский сервис" in schedule
    assert "DEMO-PREVIEW-01" in schedule
    assert 'rel="sponsored noopener noreferrer"' in schedule
    assert "[Место под" not in schedule

    assert 'data-slot="checklist_card"' in faq
    assert "Горячие обеды и кофе" in faq
    assert "Демо-интеграция • Партнерское кафе" in faq
    assert "DEMO-PREVIEW-02" in faq

    # Третий слот остаётся заглушкой: на показе видно оба состояния.
    assert 'data-slot="protocols_inline"' in results
    assert "Демо-интеграция" not in results
    assert "[Место под полезную интеграцию: слот protocols_inline]" in results


def test_real_offer_wins_over_preview_demo(
    content_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("zaruba.services.tips.date", _PreviewDate)
    _write_offers(content_settings.offers_file, [_tip()])

    with _client(content_settings, preview_enabled=True) as client:
        schedule = client.get("/schedule").text
        faq = client.get("/faq").text

    assert "Кофе на площадке" in schedule
    assert "Демо-интеграция" not in schedule
    assert "Горячие обеды и кофе" in faq


def test_content_check_accepts_demo_offers(
    content_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("zaruba.content.get_settings", lambda: content_settings)

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "рекламные слоты: 0 офферов" in out
    assert "демо-офферы: 2 (только preview)" in out
