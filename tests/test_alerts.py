"""Срочные оповещения (`docs/09` §9.9): модель, загрузчик, плашка, лист alerts.

Границы: черновик никогда не попадает на страницу; битый файл называет поле, а
не «что-то пошло не так»; лист `alerts` читается отдельно от overlay выпуска,
потому что плашку видят в проде, а overlay включён только в preview.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zaruba.config import Settings
from zaruba.content import ContentError, load_alerts
from zaruba.main import create_app
from zaruba.models import Alert, AlertStatus
from zaruba.services.alerts import active_alert
from zaruba.sync_tables import (
    ALERTS_FILENAME,
    TableError,
    alerts_from,
    parse_alerts,
    read_alerts,
    write_alerts,
)
from zaruba.sync_tables import (
    main as sync_main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLES = REPO_ROOT / "docs" / "templates"
BASE_2026 = REPO_ROOT / "content" / "editions" / "2026.yaml"

ROW = (
    "shift-kids;send;Перенос детского блока;"
    "Детский блок начинается в 12:00, а не в 10:00.;schedule;true"
)
HEADER = "id;status;title;text;link_slug;urgent\n"


def make_alert(**overrides: Any) -> Alert:
    base: dict[str, Any] = {
        "id": "shift-kids",
        "status": "send",
        "title": "Перенос детского блока",
        "text": "Детский блок начинается в 12:00, а не в 10:00.",
        "link_slug": "schedule",
        "urgent": True,
    }
    return Alert(**{**base, **overrides})


# --- модель ----------------------------------------------------------------


def test_default_state_is_a_draft_without_a_banner() -> None:
    alert = Alert(id="draft-1", title="Черновик", text="Короткое сообщение без даты.")

    assert alert.status is AlertStatus.DRAFT
    assert alert.urgent is False
    assert alert.in_feed is False
    assert alert.is_published is False


def test_alert_needs_a_readable_text_and_a_page_slug() -> None:
    with pytest.raises(ValidationError, match="text"):
        Alert(id="short", title="Мало текста", text="ок")
    with pytest.raises(ValidationError, match="link_slug"):
        make_alert(link_slug="Расписание!")


def test_urgent_alert_is_published_only_with_status_send() -> None:
    assert make_alert().in_feed is True
    assert make_alert(status="draft").in_feed is False
    assert make_alert(urgent=False).in_feed is False


def test_active_alert_takes_the_first_published_urgent_row() -> None:
    """Порядок плашки — порядок строк в таблице, а не отдельный приоритет."""

    alerts = [
        make_alert(id="draft", status="draft"),
        make_alert(id="calm", urgent=False),
        make_alert(id="urgent"),
        make_alert(id="second-urgent"),
    ]

    first = active_alert(alerts)

    assert first is not None and first.id == "urgent"
    assert active_alert([]) is None


# --- загрузка файла --------------------------------------------------------


def test_missing_file_means_no_alerts(tmp_path: Path) -> None:
    settings = Settings(alerts_file=tmp_path / "alerts.yaml")

    assert load_alerts(settings) == []


def test_alerts_are_loaded_from_the_site_file(content_settings: Settings) -> None:
    content_settings.alerts_file.write_text(
        yaml.safe_dump({"alerts": [make_alert().model_dump(mode="json", exclude_none=True)]}),
        encoding="utf-8",
    )

    loaded = load_alerts(content_settings)

    assert [item.id for item in loaded] == ["shift-kids"]
    assert loaded[0].urgent is True


def test_broken_alert_names_the_file_and_the_field(content_settings: Settings) -> None:
    content_settings.alerts_file.write_text(
        "alerts:\n  - id: shift-kids\n    title: Без текста\n", encoding="utf-8"
    )

    with pytest.raises(ContentError, match="alerts.yaml"):
        load_alerts(content_settings)


def test_duplicate_ids_are_reported(content_settings: Settings) -> None:
    row = make_alert().model_dump(mode="json", exclude_none=True)
    content_settings.alerts_file.write_text(
        yaml.safe_dump({"alerts": [row, row]}), encoding="utf-8"
    )

    with pytest.raises(ContentError, match="shift-kids"):
        load_alerts(content_settings)


# --- плашка на страницах ---------------------------------------------------


def test_banner_shows_on_home_and_schedule(content_settings: Settings) -> None:
    content_settings.alerts_file.write_text(
        yaml.safe_dump({"alerts": [make_alert().model_dump(mode="json", exclude_none=True)]}),
        encoding="utf-8",
    )

    with TestClient(create_app(content_settings)) as app_client:
        home = app_client.get("/")
        schedule = app_client.get("/schedule")

    for response in (home, schedule):
        assert response.status_code == 200
        assert "alert-band" in response.text
        assert "Перенос детского блока" in response.text
        assert 'href="/schedule"' in response.text


def test_banner_is_absent_without_alerts(client: TestClient) -> None:
    assert "alert-band" not in client.get("/").text
    assert "alert-band" not in client.get("/schedule").text


def test_draft_alert_does_not_reach_the_page(content_settings: Settings) -> None:
    draft = make_alert(status="draft")
    content_settings.alerts_file.write_text(
        yaml.safe_dump({"alerts": [draft.model_dump(mode="json", exclude_none=True)]}),
        encoding="utf-8",
    )

    with TestClient(create_app(content_settings)) as app_client:
        assert "alert-band" not in app_client.get("/").text


# --- лист alerts в синхронизации ------------------------------------------


def test_parse_alerts_reads_the_sheet() -> None:
    alerts = parse_alerts(HEADER + ROW + "\n", Path("alerts.csv"))

    assert [item.id for item in alerts] == ["shift-kids"]
    assert alerts[0].status is AlertStatus.SEND
    assert alerts[0].urgent is True
    assert alerts[0].link_slug == "schedule"


def test_header_only_sheet_means_no_alerts() -> None:
    assert parse_alerts(HEADER, Path("alerts.csv")) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("id;status;title;urgent\nshift;send;Перенос;true\n", "шапка"),
        (HEADER + ROW + "\n" + ROW + "\n", "повторяется"),
        (HEADER + ";send;Перенос;Текст оповещения;schedule;true\n", "пустой id"),
        (HEADER + "shift;send;Перенос;Текст оповещения;schedule;может быть\n", "urgent"),
        (HEADER + "shift;send;Перенос;ок;schedule;true\n", "text"),
    ],
)
def test_parse_alerts_names_the_row_and_the_column(raw: str, message: str) -> None:
    with pytest.raises(TableError, match=message):
        parse_alerts(raw, Path("alerts.csv"))


def test_read_alerts_without_a_file_is_empty(tmp_path: Path) -> None:
    assert read_alerts(tmp_path) == []


def test_alerts_from_prefers_the_sheet_text(tmp_path: Path) -> None:
    texts = {ALERTS_FILENAME: HEADER + ROW + "\n"}

    assert [item.id for item in alerts_from(texts, tmp_path)] == ["shift-kids"]
    assert alerts_from({}, tmp_path) == []


def test_write_alerts_keeps_the_file_when_nothing_changed(tmp_path: Path) -> None:
    path = tmp_path / "alerts.yaml"
    alerts = [make_alert()]

    write_alerts(alerts, path)
    first = path.read_text(encoding="utf-8")
    write_alerts(alerts, path)

    assert path.read_text(encoding="utf-8") == first
    assert "shift-kids" in first


def test_sync_cli_writes_alerts_next_to_the_site(tmp_path: Path) -> None:
    tables = tmp_path / "templates"
    shutil.copytree(TABLES, tables)
    (tables / ALERTS_FILENAME).write_text(HEADER + ROW + "\n", encoding="utf-8")
    alerts_out = tmp_path / "content" / "alerts.yaml"

    code = sync_main(
        [
            "--tables-dir",
            str(tables),
            "--base",
            str(BASE_2026),
            "--out-dir",
            str(tmp_path / "2026.d"),
            "--alerts-out",
            str(alerts_out),
        ]
    )

    assert code == 0
    assert "shift-kids" in alerts_out.read_text(encoding="utf-8")


def test_sync_cli_check_reports_divergence(tmp_path: Path) -> None:
    tables = tmp_path / "templates"
    shutil.copytree(TABLES, tables)
    (tables / ALERTS_FILENAME).write_text(HEADER + ROW + "\n", encoding="utf-8")
    alerts_out = tmp_path / "alerts.yaml"
    alerts_out.write_text("alerts: []\n", encoding="utf-8")

    code = sync_main(
        [
            "--check",
            "--tables-dir",
            str(tables),
            "--base",
            str(BASE_2026),
            "--out-dir",
            str(tmp_path / "2026.d"),
            "--alerts-out",
            str(alerts_out),
        ]
    )

    assert code == 1
