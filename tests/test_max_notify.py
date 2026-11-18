"""Рассылка срочных оповещений в MAX (`docs/09` §9.9): лог отправленных и CLI.

Главная граница: одна строка уходит один раз. Лог лежит в репозитории, потому
что runner GitHub Actions одноразовый — файл в /tmp исчез бы вместе с ним, и
следующий запуск разослал бы то же оповещение снова.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from zaruba import max_notify
from zaruba.config import Settings
from zaruba.max_notify import (
    NotifyError,
    alert_body,
    load_log,
    main,
    pending,
    save_log,
    send_alerts,
    sent_log_path,
)
from zaruba.models import Alert
from zaruba.services import max_bot

NOW = datetime(2026, 12, 11, 9, 0, tzinfo=UTC)


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


def bot_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "max_bot_token": "1234567890:test-token",
        "max_bot_name": "zaruba72champ_bot",
        "max_bot_site_url": "https://zaruba72.ru",
        "max_alert_chat_id": 777,
    }
    return Settings(**{**base, **overrides})


def collector() -> tuple[list[tuple[int, dict[str, Any]]], Any]:
    """Перехват отправки: тест видит адресата и тело сообщения."""

    calls: list[tuple[int, dict[str, Any]]] = []

    def post(settings: Settings, chat_id: int, body: dict[str, Any]) -> bool:
        calls.append((chat_id, body))
        return True

    return calls, post


# --- очередь и лог ---------------------------------------------------------


def test_pending_skips_drafts_and_already_sent() -> None:
    alerts = [
        make_alert(id="sent-1"),
        make_alert(id="draft-1", status="draft"),
        make_alert(id="fresh"),
    ]

    queue = pending(alerts, {"sent-1": "2026-12-10T09:00:00+00:00"})

    assert [item.id for item in queue] == ["fresh"]


def test_log_lives_next_to_the_overlay(tmp_path: Path) -> None:
    settings = bot_settings(content_dir=tmp_path / "editions")

    path = sent_log_path(settings, 2026)

    assert path == tmp_path / "editions" / "2026.d" / "sent_alerts.json"


def test_log_round_trip_is_atomic_and_sorted(tmp_path: Path) -> None:
    path = tmp_path / "2026.d" / "sent_alerts.json"

    save_log(path, {"b": "2026-12-11T09:00:00+00:00", "a": "2026-12-10T09:00:00+00:00"})

    assert list(load_log(path)) == ["a", "b"]
    assert not list(path.parent.glob("*.tmp"))


def test_broken_log_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "sent_alerts.json"
    path.write_text("{не json", encoding="utf-8")

    with pytest.raises(NotifyError, match="sent_alerts.json"):
        load_log(path)

    path.write_text(json.dumps(["shift-kids"]), encoding="utf-8")
    with pytest.raises(NotifyError, match="JSON-объект"):
        load_log(path)

    path.write_text(json.dumps({"sent": "нет"}), encoding="utf-8")
    with pytest.raises(NotifyError, match="sent"):
        load_log(path)


# --- отправка --------------------------------------------------------------


def test_message_carries_the_title_and_the_mini_app_button() -> None:
    body = alert_body(make_alert(), bot_settings())

    assert body["text"].startswith("Перенос детского блока")
    buttons = [
        button
        for attachment in body["attachments"]
        for row in attachment["payload"]["buttons"]
        for button in row
    ]
    open_app = next(button for button in buttons if button["type"] == "open_app")
    link = next(button for button in buttons if button["type"] == "link")
    assert open_app["payload"] == "schedule"
    assert link["url"] == "https://zaruba72.ru/schedule"


def test_send_posts_and_writes_the_log(tmp_path: Path) -> None:
    calls, post = collector()
    log = tmp_path / "2026.d" / "sent_alerts.json"

    report = send_alerts(bot_settings(), [make_alert()], log, poster=post, now=NOW)

    assert report.sent == ("shift-kids",)
    assert calls[0][0] == 777
    assert "Детский блок" in calls[0][1]["text"]
    assert load_log(log) == {"shift-kids": "2026-12-11T09:00:00+00:00"}


def test_second_run_sends_nothing(tmp_path: Path) -> None:
    calls, post = collector()
    log = tmp_path / "sent_alerts.json"
    settings = bot_settings()

    first = send_alerts(settings, [make_alert()], log, poster=post, now=NOW)
    second = send_alerts(settings, [make_alert()], log, poster=post, now=NOW)

    assert first.sent == ("shift-kids",)
    assert second.sent == ()
    assert second.skipped == 1
    assert len(calls) == 1


def test_failed_delivery_is_not_logged(tmp_path: Path) -> None:
    """Не доставлено — значит, строка повторится: лог пополняется только успехом."""

    log = tmp_path / "sent_alerts.json"

    report = send_alerts(bot_settings(), [make_alert()], log, poster=lambda *args: False, now=NOW)

    assert report.failed == ("shift-kids",)
    assert load_log(log) == {}


def test_draft_never_goes_out(tmp_path: Path) -> None:
    calls, post = collector()

    report = send_alerts(
        bot_settings(), [make_alert(status="draft")], tmp_path / "log.json", poster=post, now=NOW
    )

    assert report.sent == ()
    assert report.skipped == 1
    assert calls == []


def test_dry_run_lists_the_queue_without_chat_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "sent_alerts.json"

    report = send_alerts(
        bot_settings(max_alert_chat_id=None), [make_alert()], log, dry_run=True, now=NOW
    )

    assert report.sent == ()
    assert "Перенос детского блока" in capsys.readouterr().out
    assert not log.exists()


def test_missing_chat_id_stops_the_run(tmp_path: Path) -> None:
    with pytest.raises(NotifyError, match="ZARUBA_MAX_ALERT_CHAT_ID"):
        send_alerts(
            bot_settings(max_alert_chat_id=None),
            [make_alert()],
            tmp_path / "log.json",
            poster=lambda *args: True,
            now=NOW,
        )


# --- CLI -------------------------------------------------------------------


def test_cli_requires_the_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2


def test_cli_skips_a_fork_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(max_notify, "get_settings", lambda: bot_settings(max_bot_token=""))

    assert main(["--send-alerts"]) == 0
    assert "пропущена" in capsys.readouterr().out


def test_cli_sends_the_content_alert_and_keeps_the_log(
    content_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content_settings.alerts_file.write_text(
        "alerts:\n"
        "  - id: shift-kids\n"
        "    status: send\n"
        "    title: Перенос детского блока\n"
        "    text: Детский блок начинается в 12:00, а не в 10:00.\n"
        "    link_slug: schedule\n"
        "    urgent: true\n",
        encoding="utf-8",
    )
    settings = content_settings.model_copy(
        update={"max_bot_token": "token", "max_bot_name": "bot", "max_alert_chat_id": 777}
    )
    calls: list[tuple[int, dict[str, Any]]] = []

    def fake_post(settings: Settings, chat_id: int, body: dict[str, Any]) -> bool:
        calls.append((chat_id, body))
        return True

    monkeypatch.setattr(max_notify, "get_settings", lambda: settings)
    monkeypatch.setattr(max_bot, "post_message", fake_post)

    assert main(["--send-alerts"]) == 0
    log = sent_log_path(settings, 2026)
    assert list(load_log(log)) == ["shift-kids"]
    assert "отправлено 1" in capsys.readouterr().out
    assert len(calls) == 1

    # Второй запуск того же workflow ничего не отправляет.
    assert main(["--send-alerts"]) == 0
    assert len(calls) == 1


def test_cli_returns_1_when_delivery_failed(
    content_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_settings.alerts_file.write_text(
        "alerts:\n"
        "  - id: shift-kids\n"
        "    status: send\n"
        "    title: Перенос детского блока\n"
        "    text: Детский блок начинается в 12:00, а не в 10:00.\n"
        "    link_slug: schedule\n"
        "    urgent: true\n",
        encoding="utf-8",
    )
    settings = content_settings.model_copy(
        update={"max_bot_token": "token", "max_alert_chat_id": 777}
    )
    monkeypatch.setattr(max_notify, "get_settings", lambda: settings)
    monkeypatch.setattr(max_bot, "post_message", lambda *args: False)

    assert main(["--send-alerts"]) == 1
    assert load_log(sent_log_path(settings, 2026)) == {}


def test_cli_reports_a_broken_alerts_file(
    content_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_settings.alerts_file.write_text("alerts:\n  - id: broken\n", encoding="utf-8")
    monkeypatch.setattr(
        max_notify,
        "get_settings",
        lambda: content_settings.model_copy(update={"max_bot_token": "token"}),
    )

    assert main(["--send-alerts"]) == 1


def test_part_of_urls_are_relative_to_the_site() -> None:
    """Кнопка ведёт на тот же домен: чужой адрес в оповещении — фишинг."""

    body = alert_body(make_alert(link_slug=None), bot_settings())

    link = next(
        button
        for attachment in body["attachments"]
        for row in attachment["payload"]["buttons"]
        for button in row
        if button["type"] == "link"
    )
    assert urlsplit(link["url"]).netloc == "zaruba72.ru"
