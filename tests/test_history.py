"""§6.5: `/history` собирает архив и загруженные выпуски, ничего не выдумывая."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zaruba.content import ContentError, load_history, load_site
from zaruba.services.history import history_rows

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = REPO_ROOT / "content" / "history.yaml"


def test_history_page_shows_every_confirmed_year(client: TestClient) -> None:
    html = client.get("/history").text

    for year in ("2026", "2025", "2024", "2022", "2019"):
        assert f">{year}<" in html or f">{year}\n" in html or year in html, year
    assert "ZARUBA 72 DANCE FEST" in html
    assert "8 декабря 2024" in html
    assert "не подтверждены" in html, "у 2019 и 2022 дат нет — так и пишем"
    assert "текущий" in html
    assert "https://vk.com/zaruba72champ" in html
    assert 'rel="noopener noreferrer nofollow"' in html, "чужие ссылки — nofollow (§6.9)"


def test_history_page_lists_missing_years_explicitly(client: TestClient) -> None:
    """Пробелы названы словами: 2020, 2021 и 2023 не подтверждены."""

    html = client.get("/history").text

    assert "2020, 2021, 2023" in html
    assert "победители" in html.lower()
    assert "официальные протоколы" in html


def test_history_rows_merge_archive_and_editions() -> None:
    bundles = load_site()
    archive = load_history(HISTORY_FILE)
    rows = history_rows(bundles, archive)

    assert [row.year for row in rows] == sorted({row.year for row in rows}, reverse=True)
    assert rows[0].year == bundles[0].year
    assert rows[0].live is True
    assert rows[0].link == "/event"
    archived = {row.year for row in rows} - {bundle.year for bundle in bundles}
    assert archived == {2019, 2022, 2024}
    for row in rows:
        if row.year in archived:
            assert row.link is None, "архивным годам некуда вести — ссылок в никуда нет"
            assert row.source is not None


def test_archive_does_not_duplicate_loaded_edition() -> None:
    """Если год появился в content/editions, архивная запись не показывается дважды."""

    bundles = load_site()
    archive = load_history(HISTORY_FILE)
    duplicate = archive[0].model_copy(update={"year": bundles[0].year})
    rows = history_rows(bundles, [*archive, duplicate])

    assert [row.year for row in rows].count(bundles[0].year) == 1


def test_history_yaml_rejects_duplicate_year(tmp_path: Path) -> None:
    payload = yaml.safe_load(HISTORY_FILE.read_text(encoding="utf-8"))
    payload["entries"].append(dict(payload["entries"][0]))
    path = tmp_path / "history.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentError, match="год описан дважды"):
        load_history(path)


def test_history_entry_rejects_date_of_another_year() -> None:
    from zaruba.models import HistoryEntry

    with pytest.raises(ValidationError, match="год"):
        HistoryEntry.model_validate(
            {
                "year": 2022,
                "title": "«ЗАРУБА 72 2022»",
                "dates": ["2024-12-08"],
                "note": "Дата из другого выпуска — так нельзя.",
                "source": {
                    "id": "vk-archive",
                    "title": "Архив сообщества",
                    "url": "https://vk.com/zaruba72champ",
                    "retrieved_at": "2026-10-23",
                },
            }
        )
