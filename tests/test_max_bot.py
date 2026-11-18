"""Бот-проводник MAX (`docs/09` §9.8): webhook, секрет, ключевые слова и ссылки.

Проверки держат три границы: адрес бота закрыт без настроек и без секрета;
ответ — это ссылка на реально существующую страницу, а не выдуманный адрес;
webhook всегда отвечает 200, потому что иначе MAX ретраит событие и в итоге
отписывается от бота.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app
from zaruba.services import max_bot

SECRET = "zaruba-test-secret"
TOKEN = "1234567890:test-token"
SITE = "https://zaruba72.ru"
BOT = "zaruba72champ_bot"
WEBHOOK = "/max/webhook"


def bot_settings(**overrides: Any) -> Settings:
    """Настройки включённого бота; поля переопределяются по одному в тестах."""

    base: dict[str, Any] = {
        "max_bot_token": TOKEN,
        "max_bot_secret": SECRET,
        "max_bot_name": BOT,
        "max_bot_site_url": SITE,
    }
    return Settings(**{**base, **overrides})


@contextmanager
def bot_client(settings: Settings, *, base_url: str = "http://testserver") -> Iterator[TestClient]:
    """Клиент бота; `base_url` совпадает с доменом сайта, когда он задан."""

    with TestClient(create_app(settings), base_url=base_url) as test_client:
        yield test_client


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, dict[str, Any]]]:
    """Перехват отправки: тест видит, что именно ушло бы в MAX."""

    calls: list[tuple[int, dict[str, Any]]] = []

    def fake_send(settings: Settings, chat_id: int, body: dict[str, Any]) -> bool:
        calls.append((chat_id, body))
        return True

    monkeypatch.setattr(max_bot, "post_message", fake_send)
    return calls


def started() -> dict[str, Any]:
    """Событие `bot_started`: пользователь впервые открыл диалог."""

    return {
        "update_type": "bot_started",
        "timestamp": 1_760_000_000_000,
        "chat_id": 42,
        "user": {"user_id": 42, "first_name": "Гость", "is_bot": False},
    }


def message(text: str, *, is_bot: bool = False, chat_id: int = 42) -> dict[str, Any]:
    """Событие `message_created` в диалоге с ботом."""

    return {
        "update_type": "message_created",
        "timestamp": 1_760_000_000_000,
        "chat_id": chat_id,
        "user": {"user_id": chat_id, "first_name": "Гость", "is_bot": is_bot},
        "message": {
            "sender": {"user_id": chat_id, "first_name": "Гость", "is_bot": is_bot},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "timestamp": 1_760_000_000_000,
            "body": {"mid": "mid.1", "seq": 1, "text": text},
        },
    }


def post(client: TestClient, update: dict[str, Any], *, secret: str = SECRET) -> Any:
    return client.post(
        WEBHOOK,
        content=json.dumps(update, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Max-Bot-Api-Secret": secret},
    )


def buttons(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Кнопки из вложения `inline_keyboard` в том виде, как их получит MAX."""

    for attachment in body.get("attachments", []):
        if attachment.get("type") == "inline_keyboard":
            return [button for row in attachment["payload"]["buttons"] for button in row]
    return []


def link_url(body: dict[str, Any]) -> str:
    return next(button["url"] for button in buttons(body) if button["type"] == "link")


# --- границы адреса -------------------------------------------------------


def test_webhook_does_not_exist_until_the_bot_is_configured(sent: list[Any]) -> None:
    """Без токена и секрета адрес отвечает 404: настроенного бота нет."""

    with bot_client(Settings(max_bot_token="", max_bot_secret="")) as client:
        response = post(client, started())

    assert response.status_code == 404
    assert response.text == "", "404 не должен рендерить страницу сайта"
    assert sent == []


def test_webhook_requires_the_configured_secret(sent: list[Any]) -> None:
    """`X-Max-Bot-Api-Secret` — единственное доказательство, что запрос от MAX."""

    with bot_client(bot_settings()) as client:
        wrong = post(client, started(), secret="not-the-secret")
        missing = client.post(WEBHOOK, content=b"{}", headers={"Content-Type": "application/json"})
        padded = post(client, started(), secret=f" {SECRET} ")
        prefix = post(client, started(), secret=SECRET[:-1])

    assert [wrong.status_code, missing.status_code, padded.status_code, prefix.status_code] == [
        403,
        403,
        403,
        403,
    ]
    assert sent == []


def test_token_is_never_echoed_in_the_response() -> None:
    """Токен живёт в переменной окружения: в ответе и в теле сообщения его нет."""

    with bot_client(bot_settings()) as client:
        response = post(client, started())

    assert response.status_code == 200
    assert TOKEN not in response.text


def test_garbage_body_still_gets_200(sent: list[Any]) -> None:
    """Любой не-200 — повод для ретраев и отписки, поэтому мусор тоже 200."""

    with bot_client(bot_settings()) as client:
        response = client.post(
            WEBHOOK,
            content=b"{not json",
            headers={"Content-Type": "application/json", "X-Max-Bot-Api-Secret": SECRET},
        )

    assert response.status_code == 200
    assert sent == []


# --- ответ --------------------------------------------------------------


def test_start_answers_with_the_site_link(sent: list[tuple[int, dict[str, Any]]]) -> None:
    with bot_client(bot_settings()) as client:
        response = post(client, started())

    assert response.status_code == 200
    chat_id, body = sent[0]
    assert chat_id == 42
    assert "ЗАРУБА" in body["text"]
    open_app = next(button for button in buttons(body) if button["type"] == "open_app")
    assert open_app["web_app"] == BOT
    assert "payload" not in open_app, "у главной нет диплинка"
    assert link_url(body) == SITE


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Когда расписание?", "schedule"),
        ("покажи участников", "participants"),
        ("сколько стоят билеты", "tickets"),
        ("какие результаты", "results"),
        ("как добраться до площадки", "venue"),
        ("кто в жюри", "judges"),
        ("видео и фото с чемпионата", "media"),
        ("удалить мои данные", "takedown"),
        ("глоссарий направлений", "glossary"),
        ("история фестиваля", "history"),
    ],
)
def test_question_opens_the_matching_page(
    sent: list[tuple[int, dict[str, Any]]], text: str, slug: str
) -> None:
    with bot_client(bot_settings()) as client:
        post(client, message(text))

    _, body = sent[0]
    assert link_url(body) == f"{SITE}/{slug}"
    open_app = next(button for button in buttons(body) if button["type"] == "open_app")
    assert open_app["payload"] == slug


def test_command_form_is_understood(sent: list[tuple[int, dict[str, Any]]]) -> None:
    """`/start` и `/start@zaruba_bot` — та же команда, что обычное приветствие."""

    with bot_client(bot_settings()) as client:
        post(client, message("/start"))
        post(client, message("/start@zaruba72champ_bot"))

    assert len(sent) == 2
    assert all("ЗАРУБА" in body["text"] for _, body in sent)


def test_unknown_text_lists_the_sections(sent: list[tuple[int, dict[str, Any]]]) -> None:
    with bot_client(bot_settings()) as client:
        post(client, message("абракадабра"))

    _, body = sent[0]
    assert "расписание" in body["text"]
    assert link_url(body) == SITE


def test_bot_messages_are_ignored(sent: list[Any]) -> None:
    """Ответ на собственное сообщение — бесконечный диалог, поэтому `is_bot` отсекаем."""

    with bot_client(bot_settings()) as client:
        response = post(client, message("расписание", is_bot=True))

    assert response.status_code == 200
    assert sent == []


def test_other_update_types_are_ignored(sent: list[Any]) -> None:
    with bot_client(bot_settings()) as client:
        post(client, {"update_type": "bot_stopped", "timestamp": 1, "chat_id": 42})

    assert sent == []


def test_one_update_produces_one_message(sent: list[tuple[int, dict[str, Any]]]) -> None:
    """Второго сообщения быть не должно: лимит MAX — 2 в секунду на диалог."""

    with bot_client(bot_settings()) as client:
        post(client, message("билеты"))

    assert len(sent) == 1


# --- конфигурация -------------------------------------------------------


def test_site_url_falls_back_to_the_public_address(sent: list[tuple[int, dict[str, Any]]]) -> None:
    """Один адрес на весь сайт: `ZARUBA_PUBLIC_BASE_URL` уже есть, дублировать не нужно."""

    settings = bot_settings(max_bot_site_url="", public_base_url="https://example.test")
    # Домен задан — включён `TrustedHost`: чужой `Host` получает 400, поэтому
    # клиент ходит на настоящий адрес сайта.
    with bot_client(settings, base_url="https://example.test") as client:
        post(client, message("расписание"))

    _, body = sent[0]
    assert link_url(body) == "https://example.test/schedule"


def test_without_name_and_site_the_answer_is_text_only(
    sent: list[tuple[int, dict[str, Any]]],
) -> None:
    """Пустые настройки — не падение: бот отвечает текстом и не придумывает кнопки."""

    settings = bot_settings(max_bot_name="", max_bot_site_url="", public_base_url="")
    with bot_client(settings) as client:
        post(client, message("билеты"))

    _, body = sent[0]
    assert "Билеты" in body["text"]
    assert "attachments" not in body


def test_head_request_does_not_break_the_webhook() -> None:
    """`HEAD` включён глобально, но POST-адрес не должен на нём падать."""

    with bot_client(bot_settings()) as client:
        assert client.head(WEBHOOK).status_code == 405


# --- совпадение слагов и страниц ----------------------------------------


def test_every_section_slug_is_a_real_page(client: TestClient) -> None:
    """Ссылка бота ведёт на существующую страницу: выдуманный `slug` — 404 у человека."""

    for section in max_bot.SECTIONS:
        response = client.get(f"/{section.slug}")
        assert response.status_code == 200, section.slug


# --- отправка -----------------------------------------------------------


def test_send_failure_is_logged_not_raised() -> None:
    """Сеть отвалилась — webhook уже ответил 200, повтор доставит MAX сам."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("нет сети")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("urllib.request.urlopen", boom)
        assert max_bot.post_message(bot_settings(), 42, {"text": "привет"}) is False


def test_message_goes_to_the_chat_from_the_update() -> None:
    """Диалог и групповой чат приходят одним полем `chat_id` — его и используем."""

    body = {"text": "привет"}
    with pytest.MonkeyPatch.context() as patch:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float = 0, context: Any = None) -> Any:
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = request.data

            class Response:
                status = 200

                def __enter__(self) -> Response:
                    return self

                def __exit__(self, *exc: Any) -> None:
                    return None

            return Response()

        patch.setattr("urllib.request.urlopen", fake_urlopen)
        assert max_bot.post_message(bot_settings(), 555, body) is True

    assert captured["url"] == f"{max_bot.MAX_API}/messages?chat_id=555"
    assert captured["auth"] == TOKEN
    assert json.loads(captured["body"]) == body


def test_chat_is_taken_from_the_recipient_when_the_event_has_none(
    sent: list[tuple[int, dict[str, Any]]],
) -> None:
    """`chat_id` приходит в разных местах: событие, получатель, отправитель."""

    update = message("билеты")
    update.pop("chat_id")
    with bot_client(bot_settings()) as client:
        post(client, update)

    assert sent[0][0] == 42


def test_event_without_any_chat_is_answered_with_200(sent: list[Any]) -> None:
    """Отвечать некуда — это не ошибка доставки: MAX не должен ретраить вечно."""

    with bot_client(bot_settings()) as client:
        response = post(client, {"update_type": "bot_started", "timestamp": 1})

    assert response.status_code == 200
    assert sent == []


def test_non_200_from_max_is_logged_as_failure() -> None:
    """429 и прочие отказы не ретраим внутри запроса: повод для повтора даёт MAX."""

    class Response:
        status = 429

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
        assert max_bot.post_message(bot_settings(), 42, {"text": "привет"}) is False


def test_extra_ca_file_is_added_to_the_trust_store(tmp_path: Any) -> None:
    """Сертификат Минцифры дополняет системные корни, а не отменяет проверку TLS."""

    loaded: list[str] = []

    class Context:
        def load_verify_locations(self, cafile: str) -> None:
            loaded.append(cafile)

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    ca_file = tmp_path / "russian-trust.crt"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("ssl.create_default_context", lambda: Context())
        patch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
        settings = bot_settings(max_bot_ca_file=ca_file)
        assert max_bot.post_message(settings, 42, {"text": "привет"}) is True

    assert loaded == [str(ca_file)]


def test_json_that_is_not_an_update_is_ignored(sent: list[Any]) -> None:
    """JSON-массив вместо объекта — тоже 200: событие не наше, повтора не нужно."""

    with bot_client(bot_settings()) as client:
        response = client.post(
            WEBHOOK,
            content=b"[]",
            headers={"Content-Type": "application/json", "X-Max-Bot-Api-Secret": SECRET},
        )

    assert response.status_code == 200
    assert sent == []
