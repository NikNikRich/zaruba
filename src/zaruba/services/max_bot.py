"""Бот-проводник MAX: `Update` → сообщение со ссылкой на сайт (`docs/09` §9.8).

Бот без состояния: ответ зависит только от текста `Update` и настроек. Поэтому
здесь нет базы, сессий и счётчиков, а сбой отправки не превращается в 500 —
webhook уже ответил 200, и MAX доставит событие повторно сам.

Второй механизм тот же, что у мини-приложения: `slug` страницы уезжает в
`payload` кнопки `open_app`, а `max-bridge.js` открывает `<slug>.html`.
Поэтому список разделов обязан совпадать с настоящими страницами сайта — это
проверяет тест, а не обещание в документации.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from ..config import Settings

logger = logging.getLogger("zaruba.max_bot")

# Исходящий запрос: официальный домен MAX Bot API. `platform-api.max.ru` ещё
# жив, но документация просит переходить на второй.
MAX_API = "https://platform-api2.max.ru"
# MAX ждёт ответа webhook за 30 секунд, но сообщение уходит уже после ответа:
# здесь таймаут нужен только чтобы зависшая сеть не держала поток воркера.
OUTBOUND_TIMEOUT = 10.0

# Команды и приветствия: на них бот отвечает меню, а не «не нашёл раздел».
GREETING_WORDS: tuple[str, ...] = (
    "start",
    "старт",
    "привет",
    "здравств",
    "меню",
    "начат",
    "помощ",
    "help",
    "hello",
)

GREETING = (
    "Здравствуйте! Это бот фан-сайта «ЗАРУБА» (Тюмень).\n"
    "Напишите раздел — «расписание», «билеты», «участники», «результаты» — "
    "и я пришлю ссылку."
)

UNKNOWN = (
    "Такого раздела я не знаю. На сайте есть расписание, участники, билеты, "
    "результаты, площадка, жюри, глоссарий, история.\n"
    "Напишите одно слово из списка — пришлю ссылку."
)


@dataclass(frozen=True, slots=True)
class Section:
    """Раздел сайта: `slug` — адрес страницы и параметр запуска мини-приложения."""

    slug: str
    title: str
    words: tuple[str, ...] = ()


HOME = Section("", "Главная")

# Основа слова, а не целое: «расписание», «расписанию», «расписании» — один
# раздел, и заставлять человека угадывать падеж незачем.
SECTIONS: tuple[Section, ...] = (
    Section("event", "О событии", ("событ", "номинац", "возраст", "формат", "уровн")),
    Section("schedule", "Расписание", ("расписан", "тайминг", "блоки", "во сколько")),
    Section("performances", "Выступления", ("выступлен",)),
    Section("participants", "Участники", ("участник", "кто танцует")),
    Section("studios", "Студии", ("студи", "школ")),
    Section("judges", "Жюри", ("жюри", "судь")),
    Section("results", "Результаты", ("результат", "итог", "победител")),
    Section("tickets", "Билеты", ("билет", "вход", "зрител")),
    Section("venue", "Площадка", ("площадк", "как добрат", "адрес")),
    Section("faq", "Вопросы и ответы", ("вопрос", "faq", "частые")),
    Section("media", "Фото и видео", ("фото", "видео", "медиа", "клип")),
    Section("privacy", "Персональные данные", ("персональн", "приват", "конфиденциальн")),
    Section("about", "О сайте", ("о сайте", "кто сделал", "проект")),
    Section("rules", "Правила", ("правил", "положени", "регламент")),
    Section("takedown", "Удаление данных", ("удал", "правк", "жалоб")),
    Section("glossary", "Глоссарий", ("глоссар", "термин", "направлен", "стил")),
    Section("history", "История", ("истори", "архив", "прошл")),
    Section("ratings", "Рейтинги", ("рейтинг", "балл")),
    Section("search", "Поиск", ("поиск", "найти")),
)

# Длинное совпадение точнее короткого, поэтому основы проверяются по убыванию
# длины: «о сайте» — это «О сайте», а не «сайт» вообще, а «как добрат» — это
# площадка, даже если в тексте есть слово «адрес».
_WORDS: tuple[tuple[str, Section], ...] = tuple(
    sorted(
        ((word, section) for section in SECTIONS for word in section.words),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


class Outgoing(NamedTuple):
    """Готовое сообщение: кому и что отправить."""

    chat_id: int
    body: dict[str, Any]


def normalize(raw: str) -> str:
    """Текст сообщения в виде для поиска: нижний регистр, `ё`→`е`, без команды.

    `/start@zaruba72champ_bot` → `start`: упоминание бота в команде MAX
    добавляет сам, и без срезки оно превратило бы команду в «не нашёл».
    """

    text = " ".join(raw.replace("ё", "е").replace("Ё", "Е").lower().split())
    if text.startswith("/"):
        head, _, tail = text[1:].partition(" ")
        text = f"{head.split('@', 1)[0]} {tail}".strip()
    return text


def find_section(text: str) -> Section | None:
    """Раздел по тексту: побеждает самая длинная подошедшая основа."""

    for word, section in _WORDS:
        if word in text:
            return section
    return None


def page_url(site: str, section: Section) -> str:
    return f"{site}/{section.slug}" if section.slug else site


def buttons(section: Section, settings: Settings) -> list[list[dict[str, Any]]]:
    """Кнопки: мини-приложение на нужной странице и сам сайт.

    Нет имени бота — нет `open_app`, нет адреса сайта — нет ссылки: пустая
    кнопка хуже её отсутствия, а текст ответа остаётся полезным сам по себе.
    """

    rows: list[list[dict[str, Any]]] = []
    if settings.max_bot_name:
        open_app: dict[str, Any] = {
            "type": "open_app",
            "text": "Открыть в MAX",
            "web_app": settings.max_bot_name,
        }
        if section.slug:
            # Тот же `slug`, что понимает `?startapp=` в `max-bridge.js`.
            open_app["payload"] = section.slug
        rows.append([open_app])
    site = settings.max_bot_site
    if site:
        rows.append([{"type": "link", "text": "Открыть сайт", "url": page_url(site, section)}])
    return rows


def message_body(text: str, section: Section, settings: Settings) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    rows = buttons(section, settings)
    if rows:
        body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": rows}}]
    return body


def _chat_id(update: Mapping[str, Any], message: Mapping[str, Any]) -> int | None:
    """Куда отвечать: `chat_id` события, затем получатель, затем отправитель."""

    candidates = (
        update.get("chat_id"),
        (message.get("recipient") or {}).get("chat_id"),
        (message.get("sender") or {}).get("user_id"),
        (update.get("user") or {}).get("user_id"),
    )
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _outgoing(
    update: Mapping[str, Any],
    message: Mapping[str, Any],
    text: str,
    section: Section,
    settings: Settings,
) -> Outgoing | None:
    chat_id = _chat_id(update, message)
    if chat_id is None:
        logger.warning("MAX: событие без chat_id, отвечать некуда: %s", update.get("update_type"))
        return None
    return Outgoing(chat_id=chat_id, body=message_body(text, section, settings))


def plan_reply(update: Mapping[str, Any], settings: Settings) -> Outgoing | None:
    """Что ответить на событие. `None` — отвечать не нужно (или некуда)."""

    kind = update.get("update_type")
    raw_message = update.get("message")
    message: Mapping[str, Any] = raw_message if isinstance(raw_message, Mapping) else {}

    if kind == "bot_started":
        return _outgoing(update, message, GREETING, HOME, settings)
    if kind != "message_created":
        # Остальные события (правки, удаления, остановка бота) ответа не требуют.
        return None

    sender = message.get("sender") or update.get("user") or {}
    if sender.get("is_bot"):
        # Ответ на собственное сообщение — бесконечная переписка бота с ботом.
        return None

    text = normalize(str((message.get("body") or {}).get("text") or ""))
    section = find_section(text)
    if section is not None:
        answer = (
            f"{section.title} — «ЗАРУБА», Тюмень.\nОткройте раздел на сайте или в мини-приложении."
        )
        return _outgoing(update, message, answer, section, settings)
    if not text or any(word in text for word in GREETING_WORDS):
        return _outgoing(update, message, GREETING, HOME, settings)
    return _outgoing(update, message, UNKNOWN, HOME, settings)


def post_message(settings: Settings, chat_id: int, body: Mapping[str, Any]) -> bool:
    """Отправить сообщение в чат MAX: `False` — не доставлено, исключение не летит.

    Токен уходит только в заголовке `Authorization`. `urllib` не печатает
    заголовки запроса в тексте ошибки, поэтому журнал токен не раскрывает.
    """

    request = urllib.request.Request(
        f"{MAX_API}/messages?chat_id={chat_id}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": settings.max_bot_token,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    context = ssl.create_default_context()
    if settings.max_bot_ca_file is not None:
        # Дополняет системные корни, а не заменяет их: иначе пришлось бы
        # копировать в файл весь набор доверенных CA вместе с Минцифры.
        context.load_verify_locations(cafile=str(settings.max_bot_ca_file))
    try:
        with urllib.request.urlopen(request, timeout=OUTBOUND_TIMEOUT, context=context) as response:
            status = int(response.status)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("MAX: сообщение в чат %s не доставлено: %s", chat_id, exc)
        return False
    if status != 200:
        logger.warning("MAX: сообщение в чат %s отклонено, статус %s", chat_id, status)
        return False
    return True
