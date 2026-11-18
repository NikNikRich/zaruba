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
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import load_latest
from zaruba.main import create_app
from zaruba.models import (
    AgeGroup,
    DanceFormat,
    Edition,
    Fee,
    FeeAudience,
    Nomination,
    Participant,
    Performance,
    ScheduleBlock,
    Studio,
    Ticket,
)
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


# --- ответы из живого контента (§9.8, шаг 1) ------------------------------
#
# Сеть и YAML здесь не нужны: ответ — чистая функция от `Edition`, `Venue` и
# даты. Проверяем именно данные: цену с учётом `early_until`, блоки с временем,
# адрес площадки и карточку студии или участника.


def sample_edition() -> Edition:
    """Небольшой выпуск: два дня, один блок, одна студия, один участник."""

    return Edition(
        edition=2026,
        title="«ЗАРУБА» 2026",
        dates=[date(2026, 12, 12), date(2026, 12, 13)],
        venue="zolotye-vorota",
        status="confirmed",
        updated_at=datetime(2026, 11, 17, 18, 0, tzinfo=UTC),
        nominations=[
            Nomination(
                slug="best-baby",
                name="БЕСТ ЗАРУБА БЕЙБИ",
                format=DanceFormat.TEAM,
                age_group=AgeGroup.ADULTS_15,
                day=1,
            )
        ],
        blocks=[
            ScheduleBlock(
                slug="kids",
                day=1,
                title="Детский блок",
                starts_at=time(10, 0),
                ends_at=time(14, 0),
                nominations=["best-baby"],
            )
        ],
        studios=[Studio(slug="pechat-tantsa", name="«Печать танца»")],
        participants=[
            Participant(
                slug="ivan-petrov",
                display_name="Иван Петров",
                age_group=AgeGroup.ADULTS,
                studio="pechat-tantsa",
            )
        ],
        performances=[
            Performance(
                slug="wind-solo",
                title="Соло «Ветер»",
                nomination="best-baby",
                participants=["ivan-petrov"],
                scheduled_at=datetime(2026, 12, 12, 11, 30),
                order_no=1,
            )
        ],
        tickets=[Ticket(slug="day1", title="12 декабря, один день", price=1000, day=1)],
        fees=[
            Fee(
                slug="adults-solo",
                audience=FeeAudience.ADULTS,
                format=DanceFormat.SOLO,
                amount_early=1500,
                amount_late=1800,
                early_until=date(2026, 11, 14),
            )
        ],
    )


def reply(text: str, **kwargs: Any) -> dict[str, Any]:
    plan = max_bot.plan_reply(message(text), bot_settings(), **kwargs)
    assert plan is not None
    return plan.body


def test_tickets_answer_names_the_price_and_the_early_rate() -> None:
    body = reply("сколько стоят билеты", edition=sample_edition(), today=date(2026, 11, 10))

    assert "1000 ₽" in body["text"]
    assert "12 декабря" in body["text"]
    assert "от 1500 ₽ до 14 ноября" in body["text"]
    assert link_url(body) == f"{SITE}/tickets"


def test_tickets_answer_switches_to_the_late_rate() -> None:
    """После `early_until` бот называет позднюю ставку, а не прошлую."""

    body = reply("билеты", edition=sample_edition(), today=date(2026, 11, 18))

    assert "от 1800 ₽" in body["text"]
    assert "ранняя ставка была до 14 ноября" in body["text"]


def test_schedule_answer_lists_blocks_with_time() -> None:
    body = reply("расписание", edition=sample_edition())

    assert "• 10:00–14:00 · Детский блок" in body["text"]
    assert "12 декабря" in body["text"]


def test_schedule_without_timing_says_it_is_not_published() -> None:
    """У 2026 тайминга нет: бот честно говорит об этом и не выдумывает часы."""

    live = load_latest(bot_settings()).edition
    assert not live.blocks, "в 2026 появился тайминг — обновите ожидание теста"

    body = reply("во сколько блоки", edition=live)

    assert "не опубликован" in body["text"]
    assert link_url(body) == f"{SITE}/schedule"


def test_venue_answer_gives_dates_and_address(venues: Any) -> None:
    body = reply(
        "где проходит",
        edition=sample_edition(),
        venue=venues["zolotye-vorota"],
    )

    assert "12–13 декабря 2026" in body["text"]
    assert "Золотые ворота Сибири" in body["text"]
    assert "ул. Герцена, 87" in body["text"]
    assert link_url(body) == f"{SITE}/venue"


def test_venue_answer_without_venue_does_not_invent_an_address() -> None:
    assert "Адрес уточняется" in reply("площадка", edition=sample_edition())["text"]


def test_studio_name_leads_to_the_card_and_the_shows() -> None:
    body = reply("студия Печать танца", edition=sample_edition())

    assert "Студия «Печать танца»" in body["text"]
    assert "11:30, 12 декабря" in body["text"]
    assert "БЕСТ ЗАРУБА БЕЙБИ" in body["text"]
    open_app = next(button for button in buttons(body) if button["type"] == "open_app")
    assert open_app["payload"] == "studios/pechat-tantsa"
    assert link_url(body) == f"{SITE}/studios/pechat-tantsa"


def test_participant_name_leads_to_the_card() -> None:
    body = reply("Иван Петров", edition=sample_edition())

    assert "Иван Петров" in body["text"]
    assert "Соло «Ветер»" in body["text"]
    assert link_url(body) == f"{SITE}/participants/ivan-petrov"


def test_short_query_is_not_a_name_search() -> None:
    """«Ив» и «Аня» — часть любого вопроса, поэтому короче четырёх букв не ищем."""

    body = reply("ив", edition=sample_edition())

    assert link_url(body) == SITE


def test_studio_without_shows_says_so() -> None:
    edition = sample_edition().model_copy(update={"performances": []})
    body = reply("печать танца", edition=edition)

    assert "Выступлений в расписании пока нет" in body["text"]


def test_webhook_answers_with_the_live_price(sent: list[tuple[int, dict[str, Any]]]) -> None:
    """Тот же ответ, что и в чистом тесте, но через настоящий webhook."""

    with bot_client(bot_settings()) as client:
        post(client, message("сколько стоят билеты"))

    _, body = sent[0]
    price = load_latest(bot_settings()).edition.tickets[0].price
    assert f"{price} ₽" in body["text"]
    assert link_url(body) == f"{SITE}/tickets"


def test_human_dates_join_one_month_and_split_different_ones() -> None:
    assert max_bot.human_dates([]) == ""
    assert max_bot.human_dates([date(2026, 12, 12)]) == "12 декабря 2026"
    assert max_bot.human_dates([date(2026, 12, 12), date(2026, 12, 13)]) == "12–13 декабря 2026"
    assert (
        max_bot.human_dates([date(2026, 12, 31), date(2027, 1, 2)])
        == "31 декабря 2026, 2 января 2027"
    )


def test_tickets_answer_handles_a_block_ticket_and_an_empty_price_list() -> None:
    edition = sample_edition()
    with_block = edition.model_copy(
        update={
            "tickets": [
                Ticket(slug="block", title="Финальный блок", price=800, block="Вечерний блок")
            ]
        }
    )
    assert "· Вечерний блок — 800 ₽" in max_bot.tickets_answer(with_block, date(2026, 11, 10))

    without_tickets = edition.model_copy(update={"tickets": [], "fees": []})
    assert "ещё не объявлены" in max_bot.tickets_answer(without_tickets, date(2026, 11, 10))


def test_empty_message_is_answered_with_the_greeting() -> None:
    plan = max_bot.plan_reply(message(""), bot_settings())

    assert plan is not None
    assert "ЗАРУБА" in plan.body["text"]


def test_greeting_points_to_the_button_next_to_the_input(
    sent: list[tuple[int, dict[str, Any]]],
) -> None:
    """Постоянный вход на сайт — кнопка у поля ввода: приветствие говорит о ней.

    Кнопки в сообщении живут только до следующей переписки, а кнопка рядом с
    полем для сообщений остаётся на месте — человек ищет сайт там.
    """

    with bot_client(bot_settings()) as client:
        post(client, message("/start"))

    _, body = sent[0]
    assert "рядом с полем для сообщений" in body["text"]


def test_bare_location_question_opens_the_venue() -> None:
    """«Куда?» — вопрос без существительного: раздел всё равно площадка."""

    body = reply("куда?", edition=sample_edition())

    assert link_url(body) == f"{SITE}/venue"


def test_schedule_and_names_without_data_do_not_invent_anything() -> None:
    """Пустой выпуск и слишком короткое имя — честные пустые состояния."""

    empty = sample_edition().model_copy(
        update={"blocks": [], "nominations": [], "studios": [Studio(slug="yug", name="Юг")]}
    )

    assert "после публикации «Положения»" in max_bot.schedule_answer(empty)
    assert max_bot.find_hit(empty, "где юг") is None


def test_broken_content_still_gets_an_answer(
    broken_settings: Settings, sent: list[tuple[int, dict[str, Any]]]
) -> None:
    """Битый YAML — 200 и общая фраза: 500 заставил бы MAX отписаться от бота."""

    settings = broken_settings.model_copy(
        update={
            "max_bot_token": TOKEN,
            "max_bot_secret": SECRET,
            "max_bot_name": BOT,
            "max_bot_site_url": SITE,
        }
    )
    with bot_client(settings) as client:
        response = post(client, message("билеты"))

    assert response.status_code == 200
    _, body = sent[0]
    assert "Билеты" in body["text"]
    assert link_url(body) == f"{SITE}/tickets"
