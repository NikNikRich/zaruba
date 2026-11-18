"""Long polling бота MAX (`docs/09` §9.10): опрос без вебхука на моках HTTP.

Границы: цикл переживает обрыв сети и мусор в ответе; `marker` двигается только
вперёд; ответ считается по свежему контенту; без токена команда не стартует.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from zaruba import max_bot as cli
from zaruba.config import Settings
from zaruba.content import ContentError
from zaruba.models import Edition
from zaruba.services import max_bot as bot

TOKEN = "1234567890:test-token"


def bot_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"max_bot_token": TOKEN, "max_bot_name": "zaruba72champ_bot"}
    return Settings(**{**base, **overrides})


def opener_for(payload: Any) -> bot.Opener:
    """Ответ сервера: словарь (JSON), байты или исключение."""

    def opener(request: Any, timeout: float) -> io.BytesIO:
        if isinstance(payload, Exception):
            raise payload
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return io.BytesIO(body)

    return opener


def event(text: str, chat_id: int = 42) -> dict[str, Any]:
    return {
        "update_type": "message_created",
        "chat_id": chat_id,
        "user": {"user_id": chat_id, "first_name": "Гость", "is_bot": False},
        "message": {
            "sender": {"user_id": chat_id, "first_name": "Гость", "is_bot": False},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": "mid.1", "seq": 1, "text": text},
        },
    }


# --- GET /updates ----------------------------------------------------------


def test_updates_request_carries_marker_and_token() -> None:
    seen: dict[str, Any] = {}

    def opener(request: Any, timeout: float) -> io.BytesIO:
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return io.BytesIO(b'{"updates": [], "marker": 43}')

    payload = bot.get_updates(bot_settings(), "42", timeout=5.0, opener=opener)

    assert payload["marker"] == 43
    assert "marker=42" in seen["url"]
    assert "timeout=5" in seen["url"]
    assert seen["url"].startswith(f"{bot.MAX_API}/updates?")
    assert seen["auth"] == TOKEN
    assert seen["timeout"] == pytest.approx(15.0)


def test_network_failure_is_an_empty_answer() -> None:
    """Ночной обрыв связи не должен ронять цикл: следующий запрос продолжит опрос."""

    payload = bot.get_updates(bot_settings(), opener=opener_for(urllib.error.URLError("нет сети")))

    assert payload == {}


def test_garbage_instead_of_json_is_an_empty_answer() -> None:
    assert bot.get_updates(bot_settings(), opener=opener_for(b"<html>")) == {}
    assert bot.get_updates(bot_settings(), opener=opener_for([1, 2, 3])) == {}


# --- poll_once / poll_forever ---------------------------------------------


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, dict[str, Any]]]:
    calls: list[tuple[int, dict[str, Any]]] = []

    def fake_send(settings: Settings, chat_id: int, body: dict[str, Any]) -> bool:
        calls.append((chat_id, body))
        return True

    monkeypatch.setattr(bot, "post_message", fake_send)
    return calls


def test_poll_once_answers_and_moves_the_marker(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "context", lambda settings: (None, None))
    payload = {"updates": [event("билеты")], "marker": 43}

    marker = cli.poll_once(bot_settings(), "42", opener=opener_for(payload))

    assert marker == "43"
    assert len(sent) == 1
    assert sent[0][0] == 42
    assert "Билеты" in sent[0][1]["text"]


def test_poll_once_keeps_the_old_marker_without_a_new_one(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "context", lambda settings: (None, None))

    marker = cli.poll_once(bot_settings(), "42", opener=opener_for({"updates": []}))

    assert marker == "42"
    assert sent == []


def test_poll_once_uses_the_live_content(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ в опросе — тот же, что в вебхуке: цена и кнопка из свежего выпуска."""

    edition = cli.load_latest(bot_settings()).edition
    monkeypatch.setattr(cli, "context", lambda settings: (edition, None))
    payload = {"updates": [event("билеты")], "marker": 44}

    cli.poll_once(bot_settings(), None, opener=opener_for(payload))

    assert str(edition.tickets[0].price) in sent[0][1]["text"]


def test_poll_forever_stops_after_the_given_cycles(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "context", lambda settings: (None, None))
    payload = {"updates": [event("расписание")], "marker": 45}
    pauses: list[float] = []

    marker = cli.poll_forever(
        bot_settings(),
        opener=opener_for(payload),
        cycles=3,
        pause=2.0,
        sleep=pauses.append,
    )

    assert marker == "45"
    assert len(sent) == 3
    # Спим между запросами, а не после последнего.
    assert pauses == [2.0, 2.0]


def test_context_without_content_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(settings: Settings) -> Edition:
        raise ContentError("2026.yaml: сломан")

    monkeypatch.setattr(cli, "load_latest", boom)

    assert cli.context(bot_settings()) == (None, None)


def test_context_reads_the_live_edition() -> None:
    edition, venue = cli.context(bot_settings())

    assert edition is not None and edition.edition == 2026
    assert venue is not None and venue.slug == "zolotye-vorota"


def test_reply_to_ignores_garbage_in_the_batch(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "context", lambda settings: (None, None))

    assert bot.reply_to(bot_settings(), {"updates": "нет"}) == 0
    payload = {"updates": ["строка", {"update_type": "bot_stopped", "chat_id": 42}]}
    assert bot.reply_to(bot_settings(), payload) == 0
    assert sent == []


def test_poll_forever_one_cycle_does_not_sleep(
    sent: list[tuple[int, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "context", lambda settings: (None, None))
    pauses: list[float] = []

    cli.poll_forever(
        bot_settings(),
        opener=opener_for({"updates": []}),
        cycles=1,
        sleep=pauses.append,
    )

    assert pauses == []


def test_cli_stops_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl+C — нормальный конец работы бота в день чемпионата, а не авария."""

    def stop(settings: Settings, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "get_settings", lambda: bot_settings())
    monkeypatch.setattr(cli, "poll_forever", stop)

    assert cli.main(["--polling"]) == 0
    assert "Ctrl+C" in capsys.readouterr().out


# --- CLI -------------------------------------------------------------------


def test_cli_requires_a_mode() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])

    assert excinfo.value.code == 2


def test_cli_without_token_explains_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: bot_settings(max_bot_token=""))

    assert cli.main(["--polling"]) == 1
    assert "ZARUBA_MAX_BOT_TOKEN" in capsys.readouterr().err


def test_cli_reports_a_missing_ca_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = bot_settings(max_bot_ca_file=tmp_path / "russian_trusted.crt")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["--polling"]) == 1
    assert "сертификата" in capsys.readouterr().err


def test_cli_once_calls_a_single_poll(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[float] = []

    def fake_poll(settings: Settings, marker: str | None = None, **kwargs: Any) -> str:
        seen.append(kwargs["timeout"])
        return "marker-9"

    monkeypatch.setattr(cli, "get_settings", lambda: bot_settings())
    monkeypatch.setattr(cli, "poll_once", fake_poll)

    assert cli.main(["--once", "--timeout", "7"]) == 0
    assert seen == [7.0]
    assert "marker-9" in capsys.readouterr().out
