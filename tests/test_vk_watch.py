"""CLI мониторинга ВК (`docs/09` §9.13): лог состояния, дедупликация и отправка в MAX.

Главная граница: один пост уходит один раз, даже если он закреплён и висит в
начале ленты месяцами. Второй прогон на тех же данных не отправляет ничего.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from zaruba import vk_watch
from zaruba.config import Settings
from zaruba.services import max_bot, vk
from zaruba.services.vk import Post, VkError
from zaruba.vk_watch import (
    WatchError,
    check_once,
    load_seen,
    main,
    remember_head,
    save_seen,
    watch_forever,
)

OWNER = -185685166
AT = datetime(2026, 9, 3, 8, 48, 8, tzinfo=UTC)
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def watch_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "vk_token": "vk1.a.test-token",
        "vk_group": "zaruba72champ",
        "vk_posts": 10,
        "vk_chat_id": 777,
        "max_alert_chat_id": None,
        "vk_seen_file": tmp_path / "vk_seen.json",
    }
    return Settings(**{**base, **overrides})


def make_item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "owner_id": OWNER,
        "id": 243,
        "date": 1794748171,
        "text": "Открыта регистрация.",
    }
    return {**base, **overrides}


def feed(*items: dict[str, Any]) -> dict[str, Any]:
    return {"response": {"count": len(items), "items": list(items)}}


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.status = 200
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def opener_for(*payloads: Any) -> vk.Opener:
    """Ответы по одному на запрос: последний повторяется, если запросов больше."""

    queue = list(payloads)

    def open_(request: Request, timeout: float) -> FakeResponse:
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(payload)

    return open_


def collector(results: list[bool] | None = None) -> tuple[list[tuple[int, dict[str, Any]]], Any]:
    calls: list[tuple[int, dict[str, Any]]] = []
    answers = list(results or [])

    def post(settings: Settings, chat_id: int, body: dict[str, Any]) -> bool:
        calls.append((chat_id, body))
        return answers.pop(0) if answers else True

    return calls, post


def test_seen_round_trip_is_atomic_and_sorted(tmp_path: Path) -> None:
    path = tmp_path / "vk_seen.json"

    save_seen(path, {"-2_9": "2026-11-18T10:00:00+00:00", "-1_5": "2026-11-18T09:00:00+00:00"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert list(payload["seen"]) == ["-1_5", "-2_9"]
    assert load_seen(path) == {
        "-2_9": "2026-11-18T10:00:00+00:00",
        "-1_5": "2026-11-18T09:00:00+00:00",
    }
    assert not list(tmp_path.glob(".*.tmp")), "временный файл не должен оставаться"


def test_missing_seen_file_is_an_empty_log(tmp_path: Path) -> None:
    assert load_seen(tmp_path / "нет.json") == {}


def test_broken_seen_file_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / "vk_seen.json"
    path.write_text("не json", encoding="utf-8")

    with pytest.raises(WatchError, match="vk_seen.json"):
        load_seen(path)


def test_seen_file_without_the_field_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vk_seen.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")

    with pytest.raises(WatchError, match="seen"):
        load_seen(path)


def test_seen_file_with_a_json_array_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vk_seen.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(WatchError, match="JSON-объект"):
        load_seen(path)


def test_remember_head_does_not_write_an_empty_log(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path)

    assert remember_head(settings, opener=opener_for(feed()), now=NOW) == 0
    assert not settings.vk_seen_file.exists()


def test_remember_head_writes_every_post_and_sends_nothing(tmp_path: Path) -> None:
    """Первый запуск: запомнить хвост, иначе все старые посты уйдут разом."""

    settings = watch_settings(tmp_path)
    payload = feed(make_item(id=241), make_item(id=242), make_item(id=243))

    count = remember_head(settings, opener=opener_for(payload), now=NOW)

    assert count == 3
    assert sorted(load_seen(settings.vk_seen_file)) == [
        f"{OWNER}_241",
        f"{OWNER}_242",
        f"{OWNER}_243",
    ]


def test_check_sends_only_new_posts_oldest_first(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path)
    save_seen(settings.vk_seen_file, {f"{OWNER}_242": "2026-11-01T00:00:00+00:00"})
    payload = feed(
        make_item(id=243, date=1794748171),
        make_item(id=242, date=1794425382),
        make_item(id=241, date=1794225949),
    )
    calls, poster = collector()

    report = check_once(settings, poster=poster, opener=opener_for(payload), now=NOW)

    assert report.sent == (f"{OWNER}_241", f"{OWNER}_243")
    assert report.failed == ()
    assert [chat_id for chat_id, _ in calls] == [777, 777]
    assert f"{OWNER}_241" in load_seen(settings.vk_seen_file)


def test_second_run_sends_nothing(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path)
    payload = feed(make_item(id=243))
    check_once(settings, poster=collector()[1], opener=opener_for(payload), now=NOW)
    calls, poster = collector()

    report = check_once(settings, poster=poster, opener=opener_for(payload), now=NOW)

    assert report.sent == ()
    assert calls == []


def test_failed_delivery_is_not_logged_and_retried(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path)
    payload = feed(make_item(id=243))

    report = check_once(settings, poster=collector([False])[1], opener=opener_for(payload), now=NOW)

    assert report.failed == (f"{OWNER}_243",)
    assert load_seen(settings.vk_seen_file) == {}

    retried = check_once(settings, poster=collector()[1], opener=opener_for(payload), now=NOW)
    assert retried.sent == (f"{OWNER}_243",)


def test_dry_run_lists_the_queue_without_a_chat_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path, vk_chat_id=None, max_alert_chat_id=None)
    calls, poster = collector()

    report = check_once(
        settings, dry_run=True, poster=poster, opener=opener_for(feed(make_item(id=243))), now=NOW
    )

    assert report.sent == ()
    assert calls == []
    assert "[dry-run]" in capsys.readouterr().out


def test_missing_chat_id_stops_the_run(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path, vk_chat_id=None, max_alert_chat_id=None)

    with pytest.raises(WatchError, match="ZARUBA_VK_CHAT_ID"):
        check_once(settings, poster=collector()[1], opener=opener_for(feed(make_item(id=243))))


def test_the_alert_chat_is_the_fallback(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path, vk_chat_id=None, max_alert_chat_id=555)
    calls, poster = collector()

    check_once(settings, poster=poster, opener=opener_for(feed(make_item(id=243))), now=NOW)

    assert [chat_id for chat_id, _ in calls] == [555]


def test_loop_survives_an_api_error_and_keeps_the_log(tmp_path: Path) -> None:
    """Ошибка VK не должна убивать цикл: следующий опрос дошлёт то же самое."""

    settings = watch_settings(tmp_path)
    pauses: list[float] = []
    _, poster = collector()

    watch_forever(
        settings,
        cycles=2,
        poster=poster,
        opener=opener_for(
            {"error": {"error_code": 6, "error_msg": "Too many requests per second"}},
            feed(make_item(id=243)),
        ),
        sleep=pauses.append,
        now=NOW,
    )

    assert pauses == [60]
    assert f"{OWNER}_243" in load_seen(settings.vk_seen_file)


def test_loop_stops_after_the_given_cycles(tmp_path: Path) -> None:
    settings = watch_settings(tmp_path)
    pauses: list[float] = []

    watch_forever(
        settings,
        interval=15,
        cycles=3,
        poster=collector()[1],
        opener=opener_for(feed()),
        sleep=pauses.append,
        now=NOW,
    )

    assert pauses == [15, 15]


def test_loop_writes_a_start_line_to_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Пустой `vk_watch.log` не отличить от «процесс не поднялся»."""

    settings = watch_settings(tmp_path)

    with caplog.at_level(logging.INFO, logger="zaruba.vk_watch"):
        watch_forever(
            settings,
            cycles=1,
            poster=collector()[1],
            opener=opener_for(feed()),
            sleep=lambda pause: None,
            now=NOW,
        )

    assert "старт" in caplog.text
    assert "zaruba72champ" in caplog.text
    assert "777" in caplog.text


def test_cli_requires_a_flag() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_cli_skips_a_fork_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(vk_watch, "get_settings", lambda: watch_settings(tmp_path, vk_token=""))

    assert main(["--once"]) == 0
    assert "пропущена" in capsys.readouterr().out


def test_cli_init_remembers_the_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path)
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)
    monkeypatch.setattr(vk, "fetch_posts", lambda *a, **k: [Post(OWNER, 243, AT, "Привет")])

    assert main(["--init"]) == 0
    assert "запомнено постов: 1" in capsys.readouterr().out
    assert f"{OWNER}_243" in load_seen(settings.vk_seen_file)


def test_cli_once_sends_and_keeps_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path)
    calls, poster = collector()
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)
    monkeypatch.setattr(vk, "fetch_posts", lambda *a, **k: [Post(OWNER, 243, AT, "Привет")])
    monkeypatch.setattr(max_bot, "post_message", poster)

    assert main(["--once"]) == 0
    assert len(calls) == 1
    assert "отправлено 1" in capsys.readouterr().out


def test_cli_returns_1_when_delivery_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path)
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)
    monkeypatch.setattr(vk, "fetch_posts", lambda *a, **k: [Post(OWNER, 243, AT, "Привет")])
    monkeypatch.setattr(max_bot, "post_message", lambda *a, **k: False)

    assert main(["--once"]) == 1
    assert "не доставлены" in capsys.readouterr().err


def test_cli_reports_an_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path)
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)

    def broken(*args: Any, **kwargs: Any) -> list[Post]:
        raise VkError("VK API вернул ошибку 5: User authorization failed")

    monkeypatch.setattr(vk, "fetch_posts", broken)

    assert main(["--once"]) == 1
    assert "User authorization failed" in capsys.readouterr().err


def test_cli_checks_the_chat_before_a_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Без chat_id цикл писал бы одну ошибку в минуту — проверяем заранее."""

    settings = watch_settings(tmp_path, vk_chat_id=None, max_alert_chat_id=None)
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)

    assert main(["--loop"]) == 1
    assert "ZARUBA_VK_CHAT_ID" in capsys.readouterr().err


def test_cli_loop_starts_the_watcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = watch_settings(tmp_path)
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)
    monkeypatch.setattr(vk_watch, "watch_forever", lambda s, **kwargs: started.append(kwargs))

    assert main(["--loop", "--interval", "15"]) == 0
    assert started == [{"interval": 15}]


def test_cli_stops_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = watch_settings(tmp_path)

    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(vk_watch, "get_settings", lambda: settings)
    monkeypatch.setattr(vk_watch, "watch_forever", interrupted)

    assert main(["--loop"]) == 0
    assert "Ctrl+C" in capsys.readouterr().out
