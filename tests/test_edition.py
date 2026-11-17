"""Цены выпуска: ранний тариф действует до `early_until`, после — базовый (§6.13)."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from zaruba.content import ContentError, load_edition
from zaruba.models import MasterClass
from zaruba.services.edition import (
    fee_prices,
    master_class_date,
    master_class_pass,
    master_class_prices,
    master_class_slot,
    registration_state,
)

# Граница тарифа мастер-классов 2026 — 12 ноября. «До» включает сам день:
# в этот день организатор ещё принимает раннюю цену.
BEFORE = date(2026, 11, 11)
DEADLINE = date(2026, 11, 12)
AFTER = date(2026, 11, 13)
# У взносов свой срок — 14 ноября; после него действует базовая ставка.
FEES_AFTER = date(2026, 11, 15)


def test_master_class_early_price_before_deadline(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    prices = master_class_prices(edition, BEFORE)

    assert len(prices) == 4
    assert {item.price.current for item in prices} == {2000}
    assert all(item.price.early_active for item in prices)
    assert not any(item.price.expired for item in prices)


def test_master_class_early_price_is_valid_on_the_deadline(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    prices = master_class_prices(edition, DEADLINE)

    assert {item.price.current for item in prices} == {2000}
    assert all(item.price.early_active for item in prices)


def test_master_class_base_price_after_deadline(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    prices = master_class_prices(edition, AFTER)

    assert {item.price.current for item in prices} == {2500}
    assert {item.price.early for item in prices} == {2000}
    assert all(item.price.expired for item in prices)


def test_fee_base_price_after_deadline(edition_2026: Path) -> None:
    """Взносы переключаются по своему сроку, а не по сроку мастер-классов."""

    edition = load_edition(edition_2026).edition

    fees = fee_prices(edition, FEES_AFTER)
    children_team = next(item for item in fees if item.fee.slug == "children-team")

    assert children_team.price.current == 1800
    assert children_team.price.early == 1500
    assert children_team.price.early_until == date(2026, 11, 14)


def test_master_class_pass_switches_after_deadline(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    before = master_class_pass(edition, BEFORE)
    after = master_class_pass(edition, AFTER)

    assert before is not None and before.current == 5000
    assert after is not None and after.current == 6000


def test_master_class_slot_and_date_come_from_content(edition_2026: Path) -> None:
    """«10:00–16:00» и «12 декабря» вычисляются, а не живут в шаблоне."""

    edition = load_edition(edition_2026).edition

    assert master_class_slot(edition) == (time(10, 0), time(16, 0))
    assert master_class_date(edition) == date(2026, 12, 12)


def test_registration_state_uses_the_deadline(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    assert registration_state(edition, date(2026, 12, 7)) == "open"
    assert registration_state(edition, date(2026, 12, 8)) == "closed"


def test_master_class_early_deadline_is_required() -> None:
    """Без срока цена «до» повисает: модель не должна принимать такой класс."""

    with pytest.raises(ValidationError):
        MasterClass(
            slug="mc-test",
            day=1,
            starts_at="10:00",
            ends_at="11:30",
            teacher="Тестовый педагог",
            price_early=2000,
            price_late=2500,
        )


def test_master_class_deadline_after_edition_start_is_rejected(
    tmp_path: Path, edition_2026: Path
) -> None:
    path = tmp_path / "2026.yaml"
    raw = edition_2026.read_text(encoding="utf-8")
    assert 'early_until: "2026-11-12"' in raw, "в 2026.yaml изменился срок раннего тарифа"
    path.write_text(
        raw.replace('early_until: "2026-11-12"', 'early_until: "2026-12-20"', 1),
        encoding="utf-8",
    )

    with pytest.raises(ContentError, match="ранний срок"):
        load_edition(path)
