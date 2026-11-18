"""Бот-проводник MAX: `Update` → сообщение со ссылкой на сайт (`docs/09` §9.8).

Бот без состояния: ответ зависит от текста `Update`, настроек и свежего контента
сайта. Поэтому здесь нет базы, сессий и счётчиков, а сбой отправки не
превращается в 500 — webhook уже ответил 200, и MAX доставит событие повторно сам.

Второй механизм тот же, что у мини-приложения: путь страницы уезжает в `payload`
кнопки `open_app`, а `max-bridge.js` открывает `<путь>.html`. Поэтому список
разделов обязан совпадать с настоящими страницами сайта — это проверяет тест, а
не обещание в документации.

Ответы не выдумывают данных: цена, блоки дня и адрес берутся из `Edition` и
`Venue`. Если тайминга в «Положении» ещё нет — так и написано, без «примерно».
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import yaml

from ..config import BASE_DIR, Settings
from ..models import Edition, Participant, Performance, Studio, Venue
from . import vk_parser
from .edition import fee_prices

logger = logging.getLogger("zaruba.max_bot")

# Исходящий запрос: официальный домен MAX Bot API. `platform-api.max.ru` ещё
# жив, но документация просит переходить на второй.
MAX_API = "https://platform-api2.max.ru"
# MAX ждёт ответа webhook за 30 секунд, но сообщение уходит уже после ответа:
# здесь таймаут нужен только чтобы зависшая сеть не держала поток воркера.
OUTBOUND_TIMEOUT = 10.0
# Long polling: сервер держит запрос столько, сколько сказано в `timeout`, и
# отвечает сразу, как только событие появилось. 30 с — рекомендация MAX.
LONG_POLL_TIMEOUT = 30.0
# Пауза между пустыми ответами: без неё цикл превращается в «долбёжку» сервера.
POLL_PAUSE = 1.0


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
    "и я пришлю ссылку.\n"
    "Открыть сайт можно по клику на кнопку рядом с полем для сообщений."
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


def _section(slug: str) -> Section:
    return next(item for item in SECTIONS if item.slug == slug)


# «Где?» — вопрос без существительного: основы «площадк» в нём нет, а площадка
# нужна. Проверяем только короткий вопрос целиком, иначе «где купить билеты»
# уехало бы на площадку вместо билетов.
LOCATION_QUESTIONS: tuple[str, ...] = ("где", "куда", "где проходит", "где будет", "где находится")


def is_location_question(text: str) -> bool:
    return text.strip(" ?!.,") in LOCATION_QUESTIONS


def page_url(site: str, slug: str) -> str:
    return f"{site}/{slug}" if slug else site


def buttons(slug: str, settings: Settings) -> list[list[dict[str, Any]]]:
    """Кнопки: мини-приложение на нужной странице и сам сайт.

    `slug` — путь страницы без `.html`: `schedule` или `participants/ivan-petrov`.
    Нет имени бота — нет `open_app`, нет адреса сайта — нет ссылки: пустая кнопка
    хуже её отсутствия, а текст ответа остаётся полезным сам по себе.
    """

    rows: list[list[dict[str, Any]]] = []
    if settings.max_bot_name:
        open_app: dict[str, Any] = {
            "type": "open_app",
            "text": "Открыть в MAX",
            "web_app": settings.max_bot_name,
        }
        if slug:
            # Тот же путь, что понимает `?startapp=` в `max-bridge.js`.
            open_app["payload"] = slug
        rows.append([open_app])
    site = settings.max_bot_site
    if site:
        rows.append([{"type": "link", "text": "Открыть сайт", "url": page_url(site, slug)}])
    return rows


def message_body(text: str, slug: str, settings: Settings) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    rows = buttons(slug, settings)
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


MONTHS_GEN: tuple[str, ...] = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Кавычки в названиях студий мешают поиску: «Печать танца» и Печать танца —
# одна студия, и человек не должен угадывать, какие кавычки в контенте.
_QUOTES = str.maketrans({char: "" for char in "«»\"“”„'"})
# Короче — это уже не название: «Аня» встречается в любом вопросе.
MIN_NAME = 4


def human_date(value: date) -> str:
    """`2026-12-12` → «12 декабря»: после предлогов нужен родительный падеж."""

    return f"{value.day} {MONTHS_GEN[value.month - 1]}"


def human_dates(dates: Sequence[date]) -> str:
    """`12–13 декабря 2026` одним куском, если месяц и год совпадают."""

    if not dates:
        return ""
    if len(dates) == 1:
        return f"{human_date(dates[0])} {dates[0].year}"
    first, last = dates[0], dates[-1]
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{last.day} {MONTHS_GEN[first.month - 1]} {first.year}"
    return ", ".join(f"{human_date(item)} {item.year}" for item in dates)


def tickets_answer(edition: Edition, today: date) -> str:
    """Билеты по дням и взнос участника с действующей ставкой.

    Ставка «до срока» считается по `early_until` и `today`, а не по дате запуска
    бота: цену человек читает сейчас, и она должна совпадать со страницей.
    """

    lines = ["Билеты."]
    for ticket in edition.tickets:
        when = ""
        if ticket.day and ticket.day <= len(edition.dates):
            when = f" · {human_date(edition.dates[ticket.day - 1])}"
        elif ticket.block:
            when = f" · {ticket.block}"
        lines.append(f"• {ticket.title}{when} — {ticket.price} ₽")
    if not edition.tickets:
        lines.append("Цены на билеты ещё не объявлены.")

    prices = [item.price for item in fee_prices(edition, today)]
    if prices:
        early = [price for price in prices if price.early_active]
        if early:
            until = min(price.early_until for price in early)
            amount = min(price.early for price in early)
            lines.append(f"Взнос участника: от {amount} ₽ до {human_date(until)}.")
        else:
            until = min(price.early_until for price in prices)
            amount = min(price.late for price in prices)
            lines.append(
                f"Взнос участника: от {amount} ₽ — ранняя ставка была до {human_date(until)}."
            )
    return "\n".join(lines)


def schedule_answer(edition: Edition) -> str:
    """Блоки по дням с временем; тайминга нет — прямо об этом и говорим."""

    lines = ["Расписание."]
    blocks = 0
    for index, day in enumerate(edition.dates, start=1):
        rows = edition.blocks_for_day(index)
        if not rows:
            continue
        lines.append(human_date(day))
        lines.extend(
            f"• {block.starts_at:%H:%M}–{block.ends_at:%H:%M} · {block.title} · {block.stage}"
            for block in rows
        )
        blocks += len(rows)
    if blocks:
        return "\n".join(lines)
    if edition.nominations:
        return (
            "Расписание.\n"
            "Тайминг блоков ещё не опубликован: «Положение» называет даты "
            f"{human_dates(edition.dates)}, а время появится после жеребьёвки. "
            "Номинации по дням уже есть на странице расписания."
        )
    return "Расписание появится после публикации «Положения» о чемпионате."


def venue_answer(edition: Edition, venue: Venue | None) -> str:
    """Даты и адрес — из `Venue`, а не из текста шаблона."""

    if venue is None:
        return f"Площадка.\nДаты: {human_dates(edition.dates)}. Адрес уточняется."
    return f"Площадка.\n{human_dates(edition.dates)} — {venue.name}, {venue.address_line}."


@dataclass(frozen=True, slots=True)
class Hit:
    """Найденная студия или участник: сводка выступлений и путь карточки."""

    kind: str
    slug: str
    title: str
    path: str
    lines: tuple[str, ...] = ()


def _plain(value: str) -> str:
    """Имя для сравнения: нижний регистр, без кавычек и лишних пробелов."""

    return " ".join(value.lower().replace("ё", "е").translate(_QUOTES).split())


def _matches(query: str, name: str) -> bool:
    """Совпадение по имени: короткие имена не ищем — они есть в любом вопросе."""

    if len(name) < MIN_NAME:
        return False
    return name in query or (len(query) >= MIN_NAME and query in name)


def _show_lines(edition: Edition, performances: Sequence[Performance]) -> tuple[str, ...]:
    """До пяти выступлений: время, номинация и название номера."""

    index = edition.nomination_index
    lines: list[str] = []
    for show in sorted(performances, key=lambda item: item.scheduled_at)[:5]:
        nomination = index.get(show.nomination)
        title = nomination.title if nomination is not None else show.nomination
        moment = f"{show.scheduled_at:%H:%M}, {human_date(show.scheduled_at.date())}"
        lines.append(f"• {moment} · {title} · «{show.title}»")
    return tuple(lines)


def _studio_hit(edition: Edition, studio: Studio) -> Hit:
    members = {item.slug for item in edition.participants if item.studio == studio.slug}
    shows = [show for show in edition.performances if members & set(show.participants)]
    return Hit(
        kind="studio",
        slug=studio.slug,
        title=studio.name,
        path=f"studios/{studio.slug}",
        lines=_show_lines(edition, shows),
    )


def _participant_hit(edition: Edition, participant: Participant) -> Hit:
    shows = [show for show in edition.performances if participant.slug in show.participants]
    return Hit(
        kind="participant",
        slug=participant.slug,
        title=participant.display_name,
        path=f"participants/{participant.slug}",
        lines=_show_lines(edition, shows),
    )


def find_hit(edition: Edition, query: str) -> Hit | None:
    """Студия или участник по названию: длинное имя точнее короткого.

    Студия проверяется раньше участника: на её карточке видно всех, кто в ней
    танцует, — это полезнее, чем одна строка про однофамильца.
    """

    text = _plain(query)
    if len(text) < 3:
        return None
    studios = sorted(edition.studios, key=lambda item: len(_plain(item.name)), reverse=True)
    for studio in studios:
        if _matches(text, _plain(studio.name)):
            return _studio_hit(edition, studio)
    participants = sorted(
        edition.participants,
        key=lambda item: len(_plain(item.display_name)),
        reverse=True,
    )
    for participant in participants:
        if _matches(text, _plain(participant.display_name)):
            return _participant_hit(edition, participant)
    return None


def hit_answer(hit: Hit) -> str:
    head = f"Студия {hit.title}" if hit.kind == "studio" else f"Участник: {hit.title}"
    if not hit.lines:
        return f"{head}.\nВыступлений в расписании пока нет: заявки появятся после жеребьёвки."
    return "\n".join([f"{head}.", *hit.lines])


def section_answer(
    section: Section,
    edition: Edition | None,
    venue: Venue | None,
    today: date,
) -> tuple[str, str]:
    """Текст раздела и путь страницы. Без контента — честная общая фраза."""

    if edition is not None:
        if section.slug == "tickets":
            return tickets_answer(edition, today), "tickets"
        if section.slug == "schedule":
            return schedule_answer(edition), "schedule"
        if section.slug == "venue":
            return venue_answer(edition, venue), "venue"
    text = f"{section.title} — «ЗАРУБА», Тюмень.\nОткройте раздел на сайте или в мини-приложении."
    return text, section.slug


def _outgoing(
    update: Mapping[str, Any],
    message: Mapping[str, Any],
    text: str,
    slug: str,
    settings: Settings,
) -> Outgoing | None:
    chat_id = _chat_id(update, message)
    if chat_id is None:
        logger.warning("MAX: событие без chat_id, отвечать некуда: %s", update.get("update_type"))
        return None
    return Outgoing(chat_id=chat_id, body=message_body(text, slug, settings))


def plan_reply(
    update: Mapping[str, Any],
    settings: Settings,
    *,
    edition: Edition | None = None,
    venue: Venue | None = None,
    today: date | None = None,
) -> Outgoing | None:
    """Что ответить на событие. `None` — отвечать не нужно (или некуда).

    `edition` и `venue` — свежий контент сайта: с ними ответ называет цену, блоки
    и адрес. Без них бот не молчит, а отвечает общей фразой со ссылкой.
    """

    kind = update.get("update_type")
    raw_message = update.get("message")
    message: Mapping[str, Any] = raw_message if isinstance(raw_message, Mapping) else {}
    moment = today or date.today()

    if kind == "bot_started":
        return _outgoing(update, message, GREETING, "", settings)
    if kind == "message_callback":
        return _callback_reply(update, message, settings)
    if kind != "message_created":
        # Остальные события (правки, удаления, остановка бота) ответа не требуют.
        return None

    sender = message.get("sender") or update.get("user") or {}
    if sender.get("is_bot"):
        # Ответ на собственное сообщение — бесконечная переписка бота с ботом.
        return None

    text = normalize(str((message.get("body") or {}).get("text") or ""))
    if not text:
        return _outgoing(update, message, GREETING, "", settings)

    # Команды публикации проверяются до разделов: «опубликовать vk-243» не
    # должно случайно совпасть с основой слова из списка разделов.
    command = _owner_command(update, message, text, settings)
    if command is not None:
        return command

    # Имя студии или участника важнее раздела: «студия Печать танца» — это запрос
    # про конкретную карточку, а не про каталог.
    if edition is not None:
        hit = find_hit(edition, text)
        if hit is not None:
            return _outgoing(update, message, hit_answer(hit), hit.path, settings)

    section = find_section(text)
    if section is None and is_location_question(text):
        section = _section("venue")
    if section is not None:
        answer, slug = section_answer(section, edition, venue, moment)
        return _outgoing(update, message, answer, slug, settings)
    if any(word in text for word in GREETING_WORDS):
        return _outgoing(update, message, GREETING, "", settings)
    return _outgoing(update, message, UNKNOWN, "", settings)


# `opener` — точка подмены в тестах: сеть в pytest не нужна. Через него идут и
# `POST /messages`, и `GET /updates`, поэтому проверяется весь цикл опроса.
class Response(Protocol):
    """Ответ HTTP в том объёме, который нужен боту: статус и тело."""

    status: int

    def read(self) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *exc: object) -> None: ...


Opener = Callable[[urllib.request.Request, float], Response]


def _opener(settings: Settings) -> Opener:
    """Сетевой вызов по умолчанию: TLS-проверка с системными корнями и CA Минцифры."""

    context = _ssl_context(settings)

    def open_(request: urllib.request.Request, timeout: float) -> Response:
        return urllib.request.urlopen(request, timeout=timeout, context=context)

    return open_


def _ssl_context(settings: Settings) -> ssl.SSLContext:
    """Системные корни плюс сертификат Минцифры, если он задан.

    `load_verify_locations` дополняет набор, а не заменяет его: иначе пришлось бы
    копировать в один файл все доверенные CA. Проверку сертификата не отключаем
    ни при каких настройках — иначе токен бота уйдёт «в никуда».
    """

    context = ssl.create_default_context()
    if settings.max_bot_ca_file is not None:
        context.load_verify_locations(cafile=str(settings.max_bot_ca_file))
    return context


def post_message(
    settings: Settings,
    chat_id: int,
    body: Mapping[str, Any],
    *,
    opener: Opener | None = None,
) -> bool:
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
    open_ = opener or _opener(settings)
    try:
        with open_(request, OUTBOUND_TIMEOUT) as response:
            status = int(response.status)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("MAX: сообщение в чат %s не доставлено: %s", chat_id, exc)
        return False
    if status != 200:
        logger.warning("MAX: сообщение в чат %s отклонено, статус %s", chat_id, status)
        return False
    return True


def get_updates(
    settings: Settings,
    marker: str | None = None,
    *,
    timeout: float = LONG_POLL_TIMEOUT,
    opener: Opener | None = None,
) -> dict[str, Any]:
    """`GET /updates` — long polling без вебхука и белого IP (`docs/09` §9.10).

    Сбой сети — пустой ответ, а не исключение: цикл опроса обязан пережить
    ночной обрыв связи и продолжить с того же `marker`. Без `marker` MAX отдаёт
    только последнее событие, поэтому потеря состояния стоит одного повтора.
    """

    params: dict[str, str] = {"timeout": str(int(timeout)), "limit": "100"}
    if marker:
        params["marker"] = marker
    request = urllib.request.Request(
        f"{MAX_API}/updates?{urllib.parse.urlencode(params)}",
        method="GET",
        headers={"Authorization": settings.max_bot_token},
    )
    open_ = opener or _opener(settings)
    try:
        with open_(request, timeout + OUTBOUND_TIMEOUT) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("MAX: опрос событий не удался: %s", exc)
        return {}
    if not isinstance(payload, dict):
        logger.warning("MAX: в ответе на опрос не объект, а %s", type(payload).__name__)
        return {}
    return payload


def reply_to(
    settings: Settings,
    payload: Mapping[str, Any],
    *,
    edition: Edition | None = None,
    venue: Venue | None = None,
    today: date | None = None,
) -> int:
    """Обрабатывает пачку событий из `get_updates`: сколько сообщений ушло.

    Контент передаётся готовым: цикл опроса читает его один раз на пачку, а
    `plan_reply` остаётся чистой функцией, которую можно проверить без сети.
    """

    updates = payload.get("updates")
    if not isinstance(updates, list):
        return 0
    sent = 0
    for update in updates:
        if not isinstance(update, Mapping):
            continue
        plan = plan_reply(update, settings, edition=edition, venue=venue, today=today)
        if plan is None:
            continue
        if post_message(settings, plan.chat_id, plan.body):
            sent += 1
    return sent


# --- Полуавтоматическая публикация постов ВК (`docs/09` §9.14) --------------
#
# Конвейер: пост сообщества → черновик (`services/vk_parser`) → карточка в чат
# владельца → «Опубликовать» → файл контента, `content check`, коммит и пуш.
# Кнопка живёт в мессенджере, поэтому единственная защита от чужого нажатия —
# сравнение chat_id с чатом владельца: всё остальное (секрет вебхука, токен)
# доказывает подлинность платформы, а не право менять сайт.

DRAFT_ID = r"vk-\d{1,20}"
# `опубликовать vk-243` / `отклонить vk-243`: глагол и id, ничего лишнего.
DRAFT_COMMAND_RE = re.compile(rf"^(опубликовать|отклонить)\s+({DRAFT_ID})$")
DRAFT_LIST_WORD = "черновики"
# `payload` кнопки: тот же смысл, но машинный формат (`publish:vk-243`).
DRAFT_CALLBACK_RE = re.compile(rf"^(publish|reject|list)(?::({DRAFT_ID}))?$")

PUBLISHED = "Опубликовано! Сайт обновится через 30–40 секунд."
REJECTED = "Черновик отклонён."


def owner_chats(settings: Settings) -> frozenset[int]:
    """Чаты владельца: `ZARUBA_VK_CHAT_ID` и чат срочных оповещений.

    Список, а не одно поле: на домашнем ПК это один и тот же чат, но в облаке
    оповещения могут уходить в канал, и тогда команды приходят во второй.
    """

    return frozenset(
        chat for chat in (settings.vk_chat_id, settings.max_alert_chat_id) if chat is not None
    )


def draft_action(text: str) -> tuple[str, str] | None:
    """Текст сообщения → действие и id черновика. `None` — это не команда.

    Сравнение строгое: «опубликуй вк 243» или «опубликовать всё» остаются
    обычным текстом, и бот отвечает на них как на разговор, а не меняет сайт.
    """

    lowered = text.strip()
    if lowered == DRAFT_LIST_WORD:
        return ("list", "")
    match = DRAFT_COMMAND_RE.match(lowered)
    if match is None:
        return None
    verb, draft_id = match.groups()
    return ("publish" if verb == "опубликовать" else "reject", draft_id)


def callback_action(update: Mapping[str, Any]) -> tuple[str, str] | None:
    """Нажатие кнопки → действие и id. `None` — чужой или непонятный payload."""

    callback = update.get("callback")
    if not isinstance(callback, Mapping):
        return None
    match = DRAFT_CALLBACK_RE.match(str(callback.get("payload") or ""))
    if match is None:
        return None
    action, draft_id = match.groups()
    if action == "list":
        return ("list", "")
    if draft_id is None:
        return None
    return (action, draft_id)


def drafts_answer(settings: Settings) -> str:
    """Список черновиков, ждущих решения: id, тип и заголовок."""

    drafts = [
        draft
        for draft in vk_parser.load_drafts(Path(settings.vk_drafts_file))
        if draft.status is vk_parser.DraftStatus.PENDING
    ]
    if not drafts:
        return "Черновиков нет: новых постов ВКонтакте не приходило."
    lines = ["Черновики, ждут решения:"]
    lines.extend(f"• {draft.id} — {draft.kind.label}: {draft.title}" for draft in drafts)
    lines.append("Команды: «Опубликовать vk-…», «Отклонить vk-…».")
    return "\n".join(lines)


def reject_draft(settings: Settings, draft_id: str) -> str:
    """Помечает черновик отклонённым: файл контента не трогаем вовсе."""

    path = Path(settings.vk_drafts_file)
    draft = vk_parser.find_draft(vk_parser.load_drafts(path), draft_id)
    if draft is None:
        return f"Черновик {draft_id} не найден."
    if draft.status is not vk_parser.DraftStatus.PENDING:
        return f"Черновик {draft_id} уже {draft.status.label} — менять нечего."
    vk_parser.mark_draft(path, draft_id, vk_parser.DraftStatus.REJECTED)
    return f"{REJECTED} ({draft_id})"


def draft_answer(settings: Settings, action: str, draft_id: str = "") -> str:
    """Действие по команде владельца. Исключения — текстом, а не 500: webhook
    обязан ответить 200, иначе MAX повторит доставку и отпишется от бота."""

    try:
        if action == "publish":
            return publish_draft(settings, draft_id)
        if action == "reject":
            return reject_draft(settings, draft_id)
        return drafts_answer(settings)
    except (vk_parser.DraftError, OSError) as exc:
        logger.warning("MAX: черновики недоступны: %s", exc)
        return f"Черновики недоступны: {exc}"


# Точка подмены в тестах: настоящий git и `content check` в pytest не запускаются.
# Аргументы — списком, без `shell`: строка с кавычками на Windows ломается.
Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]

# `sys.executable` — это и есть `.venv\Scripts\python.exe`, когда бот запущен из
# `.venv` (`docs/09` §9.12): путь не дублируется в коде и не ломается на Linux.
CONTENT_CHECK: tuple[str, ...] = (sys.executable, "-m", "zaruba.content", "check")


class PublishError(Exception):
    """Публикация остановлена: сообщение называет причину, а не код возврата."""


def _run(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Боевой запуск: без shell, вывод читается как UTF-8.

    Кодировка задаётся явно: русские сообщения `zaruba.content check` в консоли
    Windows иначе декодируются как cp866, и в MAX уехала бы абракадабра.
    """

    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _tail(result: subprocess.CompletedProcess[str], limit: int = 300) -> str:
    """Последняя непустая строка вывода: в ней и лежит ошибка."""

    text = (result.stderr or "").strip() or (result.stdout or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:limit] if lines else f"код {result.returncode}"


def _git(args: Sequence[str], cwd: Path, run: Runner) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd)


def _git_ok(
    args: Sequence[str], cwd: Path, run: Runner, what: str
) -> subprocess.CompletedProcess[str]:
    result = _git(args, cwd, run)
    if result.returncode != 0:
        raise PublishError(f"git {what} не прошёл: {_tail(result)}")
    return result


def _relative(target: Path, root: Path) -> str:
    """Путь файла внутри репозитория: `git add` ждёт его, а не абсолютный."""

    try:
        return Path(target).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as exc:
        raise PublishError(f"{target}: файл контента вне репозитория {root}") from exc


def _dirty_paths(output: str) -> list[str]:
    """Строки `git status --porcelain` → пути (переименование — новый путь)."""

    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def _require_clean_tree(root: Path, target: Path, run: Runner) -> None:
    """Публикация только на чистом дереве.

    `git add` одного файла сам по себе безопасен, но коммит и пуш утащили бы в
    `main` чужие правки, которые человек ещё не проверял. Неотслеживаемые файлы
    не мешают: они в коммит не попадут, а `.clinerules` или `logs/` на рабочей
    машине есть всегда.
    """

    allowed = _relative(target, root)
    status = _git_ok(["status", "--porcelain", "--untracked-files=no"], root, run, "status")
    foreign = [path for path in _dirty_paths(status.stdout or "") if path != allowed]
    if foreign:
        raise PublishError("в рабочем дереве есть посторонние изменения: " + ", ".join(foreign[:5]))


def _yaml_header(path: Path) -> str:
    """Шапка-комментарий файла: она объясняет, откуда берутся строки.

    `yaml.safe_dump` комментарии не сохраняет, поэтому блок `#` в начале файла
    читается отдельно и дописывается обратно. Без этого первая же публикация
    стёрла бы объяснение про лист `alerts` из Google Таблицы.
    """

    if not path.is_file():
        return ""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            lines.append(line)
            continue
        break
    header = "\n".join(lines).rstrip()
    return f"{header}\n" if header else ""


def _rows(path: Path, key: str) -> list[dict[str, Any]]:
    """Список строк YAML-файла по ключу (`alerts` или `media`)."""

    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PublishError(f"{path}: не разобрать YAML — {exc}") from exc
    if not isinstance(raw, Mapping):
        raise PublishError(f"{path}: ожидался YAML-объект с полем {key}")
    rows = raw.get(key) or []
    if not isinstance(rows, list):
        raise PublishError(f"{path}: поле {key} должно быть списком")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _write_yaml(path: Path, header: str, payload: Mapping[str, Any]) -> None:
    """Атомарная запись: `.tmp` + `os.replace`, иначе сайт увидит половину файла."""

    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(f"{header}{body}", encoding="utf-8")
    os.replace(tmp, path)


def _restore(path: Path, original: bytes | None) -> None:
    """Возвращает файл к состоянию до публикации; `None` — файла не было."""

    if original is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(original)
    os.replace(tmp, path)


def edition_media_file(settings: Settings) -> Path:
    """Файл выпуска для карточки медиа: `content/editions/<год>.yaml`.

    Overlay `*.d/media.yaml` не подходит: он читается только при
    `ZARUBA_PREVIEW_ENABLED` (`docs/06` §6.25) и хранит демонстрационные строки,
    поэтому опубликованная карточка не появилась бы на сайте вовсе. Год берётся
    из имени самого свежего `content/editions/*.yaml` — без загрузки выпуска,
    чтобы публикация не зависела от валидности остального контента.
    """

    years = [
        int(path.stem) for path in Path(settings.content_dir).glob("*.yaml") if path.stem.isdigit()
    ]
    year = max(years) if years else date.today().year
    return Path(settings.content_dir) / f"{year}.yaml"


def _alert_row(draft: vk_parser.Draft) -> dict[str, Any]:
    """Строка `Alert` для `alerts.yaml`: опубликована и срочная — значит плашка."""

    row: dict[str, Any] = {
        "id": draft.id,
        "status": "send",
        "title": draft.title,
        "text": draft.text,
    }
    if draft.link_slug:
        row["link_slug"] = draft.link_slug
    row["urgent"] = True
    return row


def _media_row(draft: vk_parser.Draft) -> dict[str, Any]:
    """Строка `MediaItem` для списка `media` выпуска."""

    return {
        "slug": draft.id,
        "title": draft.title,
        "kind": draft.media_kind or "video",
        "platform": draft.platform or "Другое",
        "url": draft.media_url or draft.post_url,
    }


def _apply_draft(target: Path, draft: vk_parser.Draft) -> None:
    """Записывает черновик в файл контента по его типу.

    Оповещение встаёт первым и снимает `urgent` с прежних: на сайте горит одна
    плашка, и это должна быть актуальная (`services/alerts.active_alert` берёт
    первую опубликованную срочную строку). Старые строки не удаляются — они
    остаются опубликованными, просто перестают быть плашкой.
    """

    header = _yaml_header(target)
    if draft.kind is vk_parser.DraftKind.ALERT:
        rows = _rows(target, "alerts")
        for row in rows:
            if row.get("urgent") and row.get("status", "draft") == "send":
                row["urgent"] = False
        rows.insert(0, _alert_row(draft))
        _write_yaml(target, header, {"alerts": rows})
        return
    rows = _rows(target, "media")
    rows.append(_media_row(draft))
    _write_yaml(target, header, {"media": rows})


def _commit(target: Path, root: Path, draft: vk_parser.Draft, run: Runner) -> None:
    """Коммит одного файла и пуш в `main`.

    Сбой на любом шаге оставляет дерево таким, каким оно было: `git add` без
    коммита снимается `git reset`, коммит откатывается `reset --hard`. Иначе
    следующее `git pull --ff-only` из супервизора разъехалось бы с origin, и
    публикация перестала бы работать до ручного вмешательства.
    """

    relative = _relative(target, root)
    _git_ok(["add", relative], root, run, "add")
    committed = _git(["commit", "-m", f"chore(bot): публикация из VK {draft.id}"], root, run)
    if committed.returncode != 0:
        _git(["reset", "--quiet"], root, run)
        raise PublishError(f"git commit не прошёл: {_tail(committed)}")
    pushed = _git(["push", "origin", "main"], root, run)
    if pushed.returncode != 0:
        _git(["reset", "--hard", "HEAD~1"], root, run)
        raise PublishError(f"git push не удался: {_tail(pushed)}")


def publish_draft(
    settings: Settings,
    draft_id: str,
    *,
    repo_root: Path | None = None,
    run: Runner | None = None,
) -> str:
    """Черновик → боевой контент → `main`. Возвращает ответ владельцу в MAX.

    Порядок шагов — это и есть защита: сначала чистота дерева, потом файл, потом
    `content check` и только после него коммит. Любая неудача откатывает файл, а
    черновик остаётся `pending`: повторяемая попытка после правки данных, а не
    «наполовину опубликовано».
    """

    drafts_path = Path(settings.vk_drafts_file)
    draft = vk_parser.find_draft(vk_parser.load_drafts(drafts_path), draft_id)
    if draft is None:
        return f"Черновик {draft_id} не найден: возможно, он уже обработан."
    if draft.status is not vk_parser.DraftStatus.PENDING:
        return f"Черновик {draft_id} уже {draft.status.label} — повторно не публикую."

    root = Path(repo_root or BASE_DIR)
    run_ = run or _run
    target = (
        Path(settings.alerts_file)
        if draft.kind is vk_parser.DraftKind.ALERT
        else edition_media_file(settings)
    )
    original = target.read_bytes() if target.is_file() else None
    try:
        _require_clean_tree(root, target, run_)
        _apply_draft(target, draft)
        check = run_(CONTENT_CHECK, root)
        if check.returncode != 0:
            _restore(target, original)
            return f"Проверка контента не прошла, файл откатил: {_tail(check)}"
        _commit(target, root, draft, run_)
    except (PublishError, OSError) as exc:
        _restore(target, original)
        logger.warning("MAX: публикация %s остановлена: %s", draft_id, exc)
        return f"Не опубликовано ({draft_id}): {exc}"
    vk_parser.mark_draft(drafts_path, draft_id, vk_parser.DraftStatus.PUBLISHED)
    logger.info("MAX: черновик %s опубликован", draft_id)
    return PUBLISHED


def _owner_command(
    update: Mapping[str, Any],
    message: Mapping[str, Any],
    text: str,
    settings: Settings,
) -> Outgoing | None:
    """Ответ на команду публикации — только из чата владельца.

    Чужому человеку на те же слова отвечаем как на обычный текст (`None` →
    дальше по разбору сообщения): «опубликовать vk-1» не должно быть способом
    поменять сайт, а ответ «нет прав» подсказал бы, что такие команды есть.
    """

    action = draft_action(text)
    if action is None:
        return None
    chat_id = _chat_id(update, message)
    if chat_id is None or chat_id not in owner_chats(settings):
        logger.warning("MAX: команда публикации из чужого чата %s — игнорирую", chat_id)
        return None
    answer = draft_answer(settings, *action)
    return Outgoing(chat_id=chat_id, body=message_body(answer, "", settings))


def _callback_reply(
    update: Mapping[str, Any], message: Mapping[str, Any], settings: Settings
) -> Outgoing | None:
    """Нажатие inline-кнопки: тот же разбор и та же проверка chat_id."""

    chat_id = _chat_id(update, message)
    if chat_id is None:
        return None
    action = callback_action(update)
    if action is None or chat_id not in owner_chats(settings):
        logger.warning("MAX: кнопка черновика из чужого чата %s — игнорирую", chat_id)
        return Outgoing(chat_id=chat_id, body=message_body(UNKNOWN, "", settings))
    answer = draft_answer(settings, *action)
    return Outgoing(chat_id=chat_id, body=message_body(answer, "", settings))
