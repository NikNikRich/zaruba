"""Таблицы предпросмотра → overlay-слой выпуска.

Вход — `docs/templates/*.csv` (UTF-8 BOM, разделитель `;`): одна строка — одна
сущность модели, колонка `data_tag` отделяет подтверждённые факты (`real`) от
презентационных (`preview`). Выход — `content/editions/<год>.d/*.yaml`, который
загрузчик подмешивает только при `ZARUBA_PREVIEW_ENABLED=1`.

`real`-строки не перезаписывают опубликованный YAML: скрипт сверяет их с ним и
падает при расхождении. Подтверждённый факт меняется отдельной правкой базового
файла, а не импортом таблицы. `preview`-строки живут в overlay и удаляются
командой `--drop-preview`.

    .venv\\Scripts\\python.exe -m zaruba.sync_tables --check
    .venv\\Scripts\\python.exe -m zaruba.sync_tables
    .venv\\Scripts\\python.exe -m zaruba.sync_tables --drop-preview
    .venv\\Scripts\\python.exe -m zaruba.sync_tables --from-sheets

`--from-sheets` берёт листы не из `docs/templates`, а из Google Таблиц: адреса
экспорта собираются из `ZARUBA_SHEET_ID` и `ZARUBA_SHEETS_MAP`. Выгрузка
атомарна — пока не скачались и не прошли проверку все листы, локальные CSV и
overlay остаются прежними.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ValidationError

from .config import BASE_DIR
from .content import ContentError, merge_overlay, overlay_dir
from .models import (
    Alert,
    AlertFeed,
    CommerceOffer,
    Edition,
    Judge,
    JudgeScore,
    MediaItem,
    Nomination,
    Participant,
    Performance,
    ScheduleBlock,
    ScoreFeed,
    Studio,
)

TAG_REAL = "real"
TAG_PREVIEW = "preview"
TAGS = frozenset({TAG_REAL, TAG_PREVIEW})

# Время выпуска: дата в `content/scores.yaml` не должна зависеть от часового
# пояса машины, где запустили синхронизацию.
EVENT_TZ = "Asia/Yekaterinburg"
# --- Русские заголовки мастер-таблицы --------------------------------------
#
# Мастер-таблицу `docs/templates/ZARUBA_2026_MASTER.xlsx` заполняют люди, далёкие
# от CSV: в шапке русские слова, во второй строке — подсказка «что писать»,
# ниже данные. Конвейер принимает обе шапки — старую системную и русскую.
# Соответствие живёт здесь, а не в двух местах: генератор книги и разбор
# листов берут один и тот же словарь, и разъехаться они не могут.
COLUMN_TITLES: dict[str, dict[str, str]] = {
    "studios": {
        "slug": "Код студии",
        "name": "Название студии",
        "city": "Город",
        "vk_url": "Ссылка ВКонтакте",
        "demo": "Это пример? (да/нет)",
        "data_tag": "Тип данных",
    },
    "participants": {
        "slug": "Код участника",
        "display_name": "Имя участника (или инициалы)",
        "age_group": "Возрастная группа",
        "studio": "Код студии",
        "city": "Город",
        "consent_status": "Согласие на публикацию",
        "consent_at": "Дата согласия",
        "demo": "Это пример? (да/нет)",
        "data_tag": "Тип данных",
    },
    "blocks": {
        "slug": "Код блока",
        "day": "День",
        "title": "Название блока",
        "starts_at": "Начало",
        "ends_at": "Окончание",
        "stage": "Сцена",
        "nominations": "Номинации (коды через |)",
        "note": "Примечание",
        "data_tag": "Тип данных",
    },
    "nominations": {
        "slug": "Код номинации",
        "name": "Название номинации",
        "style": "Стиль",
        "format": "Формат",
        "age_group": "Возрастная группа",
        "levels": "Уровни (beg|pro)",
        "min_participants": "Минимум участников",
        "max_participants": "Максимум участников",
        "day": "День",
        "starts_at": "Начало",
        "ends_at": "Окончание",
        "stage": "Сцена",
        "status": "Статус",
        "note": "Примечание",
        "source_id": "Код источника",
        "demo": "Это пример? (да/нет)",
        "data_tag": "Тип данных",
    },
    "judges": {
        "slug": "Код судьи",
        "name": "Имя судьи",
        "role": "Роль",
        "city": "Город",
        "regalia": "Регалии",
        "styles": "Стили (через |)",
        "blocks": "Блоки (коды через |)",
        "source_id": "Код источника",
        "photo": "Файл портрета",
        "photo_author": "Автор фото",
        "photo_license": "Лицензия",
        "photo_use": "Основание публикации",
        "photo_purpose": "Цель цитирования",
        "photo_source_url": "Ссылка на источник фото",
        "photo_nature": "Тип изображения",
        "demo": "Это пример? (да/нет)",
        "data_tag": "Тип данных",
    },
    "performances": {
        "slug": "Код выступления",
        "title": "Название номера",
        "nomination": "Код номинации",
        "participants": "Участники (коды через |)",
        "scheduled_at": "Дата и время",
        "order_no": "Номер по порядку",
        "stage": "Сцена",
        "status": "Статус",
        "video_url": "Ссылка на видео",
        "source_id": "Код источника",
        "demo": "Это пример? (да/нет)",
        "data_tag": "Тип данных",
    },
    "scores": {
        "performance": "Код выступления",
        "nomination": "Номинация",
        "studio": "Студия / Команда",
        "participant": "Участник или номер",
        "score": "Баллы / Оценка",
        "place": "Место",
        "published": "Опубликовать на сайте?",
    },
    "alerts": {
        "id": "Код оповещения",
        "status": "Отправить оповещение?",
        "title": "Заголовок",
        "text": "Текст",
        "link_slug": "Страница на сайте",
        "urgent": "Срочное? (да/нет)",
    },
    "media": {
        "slug": "Код материала",
        "title": "Название",
        "kind": "Тип",
        "platform": "Площадка",
        "url": "Ссылка",
        "preview_image": "Файл превью",
        "meta": "Подпись (время, число фото)",
        "nomination_slug": "Код номинации",
        "data_tag": "Тип данных",
    },
    "commerce": {
        "slug": "Код предложения",
        "title": "Название",
        "category": "Категория",
        "price": "Цена, ₽",
        "status": "Наличие",
        "description": "Описание",
        "cta_label": "Текст кнопки",
        "url": "Ссылка",
        "erid": "Токен маркировки (erid)",
        "data_tag": "Тип данных",
    },
}

# Обратный словарь: русский заголовок → системная колонка.
HEADER_ALIASES: dict[str, dict[str, str]] = {
    section: {title: column for column, title in columns.items()}
    for section, columns in COLUMN_TITLES.items()
}

# Подсказки второй строки. Каждая начинается со слова-маркера («Например»,
# «Формат», «Выберите»…): по ним разбор понимает, что вторая строка — не данные.
COLUMN_HINTS: dict[str, dict[str, str]] = {
    "studios": {
        "slug": "Например: pechat-tantsa — короткий код латиницей, без пробелов",
        "name": "Что писать: как в заявке, например «Печать танца»",
        "city": "Например: Тюмень",
        "vk_url": "Например: https://vk.com/club123 — пусто, если группы нет",
        "demo": "Выберите: да — пример для показа, нет — настоящая студия",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "participants": {
        "slug": "Например: lisa — короткий код латиницей, без пробелов",
        "display_name": "Например: Л. В. — до 18 лет только инициалы или сценическое имя",
        "age_group": "Выберите группу: 3-6, 7-11, 12-14, 15-17, 18+",
        "studio": "Например: pechat-tantsa — код из листа «Студии»",
        "city": "Например: Тюмень",
        "consent_status": "Выберите: не запрошено, запрошено, получено, отказ",
        "consent_at": "Формат: 12.11.2026 — только если согласие получено",
        "demo": "Выберите: да — пример для показа, нет — настоящий участник",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "blocks": {
        "slug": "Например: day1-solo-duet — короткий код латиницей",
        "day": "Выберите: 1, 2 или 3",
        "title": "Что писать: как в расписании, например «Соло и дуэты»",
        "starts_at": "Формат ЧЧ:ММ, например 14:30",
        "ends_at": "Формат ЧЧ:ММ, например 16:00",
        "stage": "Например: Сцена",
        "nominations": "Например: best-kids|best-juniors — коды через |",
        "note": "Например: вход за 30 минут — пусто, если добавить нечего",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "nominations": {
        "slug": "Например: best-kids — короткий код латиницей",
        "name": "Например: БЕСТ ЗАРУБА КИДС — как в «Положении»",
        "style": "Например: hip-hop — пусто, если стиль не указан",
        "format": "Выберите: соло, дуэт, мини-группа, команда, шоу",
        "age_group": "Выберите группу: 3-6, 7-11, 12-14, 15-17, 18+",
        "levels": "Например: beg|pro — уровни через |, пусто — без деления",
        "min_participants": "Например: 6 — целое число или пусто",
        "max_participants": "Например: 20 — целое число или пусто",
        "day": "Выберите: 1, 2 или 3",
        "starts_at": "Формат ЧЧ:ММ, например 10:00",
        "ends_at": "Формат ЧЧ:ММ, например 11:30",
        "stage": "Например: Сцена",
        "status": "Выберите: по расписанию, перенесено, отменено",
        "note": "Например: регистрация за час — пусто, если добавить нечего",
        "source_id": "Например: polozhenie-2026 — код источника выпуска",
        "demo": "Выберите: да — пример для показа, нет — настоящая номинация",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "judges": {
        "slug": "Например: fara — короткий код латиницей",
        "name": "Например: Александр Бурцев",
        "role": "Выберите: судья или ведущий",
        "city": "Например: Москва",
        "regalia": "Что писать: звания и проекты одной строкой, например «финалист ТНТ»",
        "styles": "Например: hip-hop|breaking — стили через |",
        "blocks": "Например: day1-solo-duet — коды блоков через |",
        "source_id": "Например: vk-fara — код источника выпуска",
        "photo": "Например: img/people/fara.webp — путь от static/",
        "photo_author": "Например: сообщество «ЗАРУБА72»",
        "photo_license": "Например: разрешение автора от 01.11.2026",
        "photo_use": "Выберите: own — свой кадр или разрешение, citation — цитата",
        "photo_purpose": "Выберите: info, science, education, culture — только для цитаты",
        "photo_source_url": "Например: https://vk.com/wall-185685166_223",
        "photo_nature": "Выберите: фото или иллюстрация",
        "demo": "Выберите: да — пример для показа, нет — настоящий судья",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "performances": {
        "slug": "Например: perf-day1-solo-01 — короткий код латиницей",
        "title": "Например: Тихий свет — как объявляет ведущий",
        "nomination": "Например: best-solo-kids — код из листа «Номинации»",
        "participants": "Например: a-k|b-s — коды участников через |",
        "scheduled_at": "Формат: 2026-12-12T10:00:00+05:00",
        "order_no": "Например: 1 — целое число, порядок в блоке",
        "stage": "Например: Сцена",
        "status": "Выберите: по расписанию, перенесено, отменено",
        "video_url": "Например: https://vk.com/video-1_2 — пусто, если видео нет",
        "source_id": "Например: polozhenie-2026 — код источника выпуска",
        "demo": "Выберите: да — пример для показа, нет — настоящее выступление",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "scores": {
        "performance": "Например: perf-day1-solo-01 — код из листа «Выступления»",
        "nomination": "Например: best-solo-kids — код из листа «Номинации»",
        "studio": "Например: «Печать танца» — как в заявке",
        "participant": "Например: А. К. — до 18 лет только инициалы",
        "score": "Например: 31,5 — балл из протокола",
        "place": "Например: 1 — пусто, если место не присвоено",
        "published": "Выберите: ДА — показывать на сайте, НЕТ — строка никуда не уйдёт",
    },
    "alerts": {
        "id": "Например: shift-kids — короткий код латиницей",
        "status": "Выберите: Опубликовать — уйдёт в MAX, Черновик — останется в таблице",
        "title": "Например: Перенос детского блока — до 80 символов",
        "text": "Например: Детский блок начинается в 12:00, а не в 10:00. — до 280 символов",
        "link_slug": "Например: schedule — страница сайта без слэшей",
        "urgent": "Выберите: да — плашка на главной, нет — только сообщение в MAX",
    },
    "media": {
        "slug": "Например: teaser-2026 — короткий код латиницей",
        "title": "Например: Тизер ЗАРУБА 2026 — VK Видео",
        "kind": "Выберите: видео, фото, трансляция",
        "platform": "Выберите: VK Видео, Rutube, Яндекс Диск, Облако Mail.ru, YouTube",
        "url": "Например: https://vk.com/video-185685166_456239017",
        "preview_image": "Например: img/media/teaser-2026.webp — или оставьте пустым",
        "meta": "Например: 01:12 — длительность или число фото",
        "nomination_slug": "Например: best-kids — пусто, если материал общий",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
    "commerce": {
        "slug": "Например: hoodie-2026 — короткий код латиницей",
        "title": "Например: Худи «ЗАРУБА» — лимитированная серия",
        "category": "Выберите: мерч, фотопакет, VIP, партнёр",
        "price": "Например: 4900 — целые рубли, у партнёра пусто",
        "status": "Выберите: в наличии, предзаказ, распродано",
        "description": "Что писать: 1–2 предложения, без обещаний и скидок",
        "cta_label": "Например: Заказать мерч — текст кнопки",
        "url": "Например: https://zaruba72.example/shop/hoodie",
        "erid": "Например: 2VtzqwXk9Ab — только настоящий токен ОРД, иначе пусто",
        "data_tag": "Выберите: подтверждено — факт из источника, пример — демо-строка",
    },
}

# Выпадающие списки листа: русская подпись → системное значение. Один словарь
# на две задачи: генератор книги берёт из него список, разбор переводит значение.
# Ключ `("*", колонка)` действует во всех листах.
SELECTS: dict[tuple[str, str], dict[str, str]] = {
    ("*", "data_tag"): {"подтверждено": TAG_REAL, "пример": TAG_PREVIEW},
    ("*", "demo"): {"да": "true", "нет": "false"},
    ("*", "urgent"): {"да": "true", "нет": "false"},
    ("scores", "published"): {"ДА": "true", "НЕТ": "false"},
    ("alerts", "status"): {"Опубликовать": "send", "Черновик": "draft"},
    ("nominations", "format"): {
        "соло": "solo",
        "дуэт": "duet",
        "мини-группа": "small-group",
        "команда": "team",
        "шоу": "show",
    },
    ("nominations", "status"): {
        "анонс": "announced",
        "по расписанию": "scheduled",
        "перенесено": "moved",
        "отменено": "cancelled",
    },
    ("performances", "status"): {
        "анонс": "announced",
        "по расписанию": "scheduled",
        "перенесено": "moved",
        "отменено": "cancelled",
    },
    ("participants", "consent_status"): {
        "не запрошено": "not_requested",
        "запрошено": "requested",
        "получено": "granted",
        "отказ": "declined",
    },
    ("judges", "role"): {"судья": "judge", "ведущий": "mc"},
    ("judges", "photo_use"): {"own": "own", "citation": "citation"},
    ("judges", "photo_purpose"): {
        "info": "info",
        "science": "science",
        "education": "education",
        "culture": "culture",
    },
    ("judges", "photo_nature"): {"фото": "photo", "иллюстрация": "illustration"},
    ("media", "kind"): {"видео": "video", "фото": "photo", "трансляция": "stream"},
    ("commerce", "category"): {
        "мерч": "merch",
        "фотопакет": "photo_pack",
        "VIP": "vip",
        "партнёр": "partner",
    },
    ("commerce", "status"): {
        "в наличии": "available",
        "предзаказ": "preorder",
        "распродано": "sold_out",
    },
}

# Маркеры строки-подсказки: вторая строка листа объясняет, что писать.
HINT_PREFIXES = (
    "например",
    "формат",
    "выберите",
    "что писать",
    "оставьте",
    "напишите",
    "подсказка",
    "для примера",
)


def column_for(section: str, title: str, known: Sequence[str]) -> str | None:
    """Русский заголовок → системная колонка; `None` — колонка неизвестна."""

    title = title.strip()
    if title in known:
        return title
    return HEADER_ALIASES.get(section, {}).get(title)


def value_alias(section: str, column: str, value: str) -> str:
    """Переводит русское значение из выпадающего списка в системное."""

    alias = SELECTS.get((section, column)) or SELECTS.get(("*", column))
    if not alias:
        return value
    for label, system in alias.items():
        if value.strip().lower() == label.lower():
            return system
    return value


def is_hint_row(row: Mapping[str, str]) -> bool:
    """Вторая строка листа — подсказка, а не данные.

    Подсказки начинаются с маркеров («Например», «Формат», «Выберите»…).
    Половина клеток с маркером — достаточно: одна такая клетка встречается
    и в настоящей строке («Например» в примечании), и терять её нельзя.
    """

    values = [value.strip().lower() for value in row.values() if value and value.strip()]
    if not values:
        return False
    hits = sum(1 for value in values if value.startswith(HINT_PREFIXES))
    return hits >= 2 and hits * 2 >= len(values)


class TableError(Exception):
    """Ошибка таблицы: сообщение называет файл, строку и колонку."""


# UTF-16 (LE/BE) начинается с BOM: такой файл мы не разбираем, но и молча
# превращать его в «кракозябры» через cp1251 нельзя — ошибка должна быть
# понятной. Остальные два BOM (`utf-8-sig`) снимает сам кодек.
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _decode_table(raw: bytes, label: str) -> str:
    """Декодирует CSV в UTF-8 (с BOM или без) либо Windows-1251.

    Русский Excel сохраняет таблицы в cp1251, «Блокнот» и Google Sheets — в
    UTF-8. Порядок важен: cp1251 декодирует почти любые байты, поэтому её
    пробуем последней, иначе UTF-8 с кириллицей молча стал бы мусором.
    """

    if raw.startswith(UTF16_BOMS):
        raise TableError(
            f"{label}: файл сохранён в UTF-16 — пересохраните таблицу в UTF-8 или Windows-1251"
        )
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TableError(
        f"{label}: не удалось определить кодировку — нужен UTF-8 (с BOM или без) или Windows-1251"
    )


def _read_table_text(path: Path) -> str:
    return _decode_table(path.read_bytes(), str(path))


def _text(row: dict[str, str], key: str) -> str | None:
    value = row.get(key, "").strip()
    return value or None


def _number(row: dict[str, str], key: str) -> int | None:
    value = _text(row, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise TableError(f"колонка {key}: «{value}» — не целое число") from exc


def _items(row: dict[str, str], key: str) -> list[str]:
    value = _text(row, key)
    if value is None:
        return []
    return [part.strip() for part in value.replace(",", "|").split("|") if part.strip()]


def _present(**values: object) -> dict[str, Any]:
    """Только заданные поля: пустая клетка оставляет значение модели по умолчанию."""

    return {key: value for key, value in values.items() if value is not None and value != []}


def _studio(row: dict[str, str], preview: bool) -> Studio:
    return Studio(
        **_present(
            slug=row["slug"],
            name=row["name"],
            city=_text(row, "city"),
            vk_url=_text(row, "vk_url"),
            demo=True if preview else None,
        )
    )


def _participant(row: dict[str, str], preview: bool) -> Participant:
    return Participant(
        **_present(
            slug=row["slug"],
            display_name=row["display_name"],
            age_group=row["age_group"],
            studio=row["studio"],
            city=_text(row, "city"),
            consent_status=_text(row, "consent_status"),
            consent_at=_text(row, "consent_at"),
            demo=True if preview else None,
        )
    )


def _block(row: dict[str, str], preview: bool) -> ScheduleBlock:
    # У блока нет поля demo: презентационность видна по номинациям и баннеру.
    del preview
    return ScheduleBlock(
        **_present(
            slug=row["slug"],
            day=_number(row, "day"),
            title=row["title"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            stage=_text(row, "stage"),
            nominations=_items(row, "nominations"),
            note=_text(row, "note"),
        )
    )


def _performance(row: dict[str, str], preview: bool) -> Performance:
    return Performance(
        **_present(
            slug=row["slug"],
            title=row["title"],
            nomination=row["nomination"],
            participants=_items(row, "participants"),
            scheduled_at=row["scheduled_at"],
            order_no=_number(row, "order_no"),
            stage=_text(row, "stage"),
            status=_text(row, "status"),
            video_url=_text(row, "video_url"),
            source_id=_text(row, "source_id"),
            demo=True if preview else None,
        )
    )


def _nomination(row: dict[str, str], preview: bool) -> Nomination:
    return Nomination(
        **_present(
            slug=row["slug"],
            name=_text(row, "name"),
            style=_text(row, "style"),
            format=row["format"],
            age_group=row["age_group"],
            levels=_items(row, "levels"),
            min_participants=_number(row, "min_participants"),
            max_participants=_number(row, "max_participants"),
            day=_number(row, "day"),
            starts_at=_text(row, "starts_at"),
            ends_at=_text(row, "ends_at"),
            stage=_text(row, "stage"),
            status=_text(row, "status"),
            note=_text(row, "note"),
            source_id=_text(row, "source_id"),
            demo=True if preview else None,
        )
    )


def _judge(row: dict[str, str], preview: bool) -> Judge:
    photo = _text(row, "photo")
    return Judge(
        **_present(
            slug=row["slug"],
            name=row["name"],
            role=_text(row, "role"),
            city=_text(row, "city"),
            regalia=_text(row, "regalia"),
            styles=_items(row, "styles"),
            blocks=_items(row, "blocks"),
            source_id=_text(row, "source_id"),
            photo=photo,
            photo_author=_text(row, "photo_author") if photo else None,
            photo_license=_text(row, "photo_license") if photo else None,
            photo_use=_text(row, "photo_use") if photo else None,
            photo_purpose=_text(row, "photo_purpose") if photo else None,
            photo_source_url=_text(row, "photo_source_url") if photo else None,
            photo_nature=_text(row, "photo_nature") if photo else None,
            demo=True if preview else None,
        )
    )


def _media(row: dict[str, str], preview: bool) -> MediaItem:
    # У медиа нет поля `demo`: карточка видна только с включённым предпросмотром,
    # и общий баннер уже объясняет, что строки демонстрационные.
    del preview
    return MediaItem(
        **_present(
            slug=row["slug"],
            title=row["title"],
            kind=row["kind"],
            platform=row["platform"],
            url=row["url"],
            preview_image=_text(row, "preview_image"),
            meta=_text(row, "meta"),
            nomination_slug=_text(row, "nomination_slug"),
        )
    )


def _commerce(row: dict[str, str], preview: bool) -> CommerceOffer:
    # `status` и `price` необязательны в таблице: пустая клетка — значение
    # модели по умолчанию, а не «пустая строка вместо цены».
    del preview
    return CommerceOffer(
        **_present(
            slug=row["slug"],
            title=row["title"],
            category=row["category"],
            price=_number(row, "price"),
            status=_text(row, "status"),
            description=row["description"],
            cta_label=row["cta_label"],
            url=row["url"],
            erid=_text(row, "erid"),
        )
    )


@dataclass(frozen=True)
class TableSpec:
    section: str
    filename: str
    columns: tuple[str, ...]
    model: type[BaseModel]
    build: Callable[[dict[str, str], bool], BaseModel]


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "studios",
        "studios.csv",
        ("slug", "name", "city", "vk_url", "demo", "data_tag"),
        Studio,
        _studio,
    ),
    TableSpec(
        "participants",
        "participants.csv",
        (
            "slug",
            "display_name",
            "age_group",
            "studio",
            "city",
            "consent_status",
            "consent_at",
            "demo",
            "data_tag",
        ),
        Participant,
        _participant,
    ),
    TableSpec(
        "blocks",
        "blocks.csv",
        (
            "slug",
            "day",
            "title",
            "starts_at",
            "ends_at",
            "stage",
            "nominations",
            "note",
            "data_tag",
        ),
        ScheduleBlock,
        _block,
    ),
)


TABLE_SPECS += (
    TableSpec(
        "performances",
        "performances.csv",
        (
            "slug",
            "title",
            "nomination",
            "participants",
            "scheduled_at",
            "order_no",
            "stage",
            "status",
            "video_url",
            "source_id",
            "demo",
            "data_tag",
        ),
        Performance,
        _performance,
    ),
    TableSpec(
        "nominations",
        "nominations.csv",
        (
            "slug",
            "name",
            "style",
            "format",
            "age_group",
            "levels",
            "min_participants",
            "max_participants",
            "day",
            "starts_at",
            "ends_at",
            "stage",
            "status",
            "note",
            "source_id",
            "demo",
            "data_tag",
        ),
        Nomination,
        _nomination,
    ),
    TableSpec(
        "judges",
        "judges.csv",
        (
            "slug",
            "name",
            "role",
            "city",
            "regalia",
            "styles",
            "blocks",
            "source_id",
            "photo",
            "photo_author",
            "photo_license",
            "photo_use",
            "photo_purpose",
            "photo_source_url",
            "photo_nature",
            "demo",
            "data_tag",
        ),
        Judge,
        _judge,
    ),
    TableSpec(
        "media",
        "media.csv",
        (
            "slug",
            "title",
            "kind",
            "platform",
            "url",
            "preview_image",
            "meta",
            "nomination_slug",
            "data_tag",
        ),
        MediaItem,
        _media,
    ),
    TableSpec(
        "commerce",
        "commerce.csv",
        (
            "slug",
            "title",
            "category",
            "price",
            "status",
            "description",
            "cta_label",
            "url",
            "erid",
            "data_tag",
        ),
        CommerceOffer,
        _commerce,
    ),
)

# --- Лист `alerts`: срочные оповещения -------------------------------------
#
# Отдельный лист, а не ещё одна таблица выпуска: оповещение живёт сутки, ему не
# нужны `data_tag` и сверка с опубликованным YAML — наоборот, таблица и есть
# источник истины. Поэтому у него свой разбор и свой файл `content/alerts.yaml`,
# а не overlay `content/editions/<год>.d`: плашку видят в проде, а overlay
# подмешивается только при `ZARUBA_PREVIEW_ENABLED=1`.

ALERT_COLUMNS = ("id", "status", "title", "text", "link_slug", "urgent")
ALERTS_SECTION = "alerts"
ALERTS_FILENAME = "alerts.csv"
ALERTS_PATH = BASE_DIR / "content" / "alerts.yaml"


def parse_alerts(raw: str, path: Path) -> list[Alert]:
    """Разбирает лист `alerts`; пустая таблица — «оповещений нет», а не ошибка."""

    try:
        delimiter = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(raw.splitlines(), delimiter=delimiter)
    fields = tuple((name or "").strip() for name in (reader.fieldnames or ()))
    if not fields:
        raise TableError(f"{path}: таблица пуста — нужна шапка {list(ALERT_COLUMNS)}")
    mapping = _header_map(ALERTS_SECTION, fields, ALERT_COLUMNS, path)

    alerts: list[Alert] = []
    seen: dict[str, int] = {}
    first_data_row = True
    for raw_row in reader:
        row = {
            mapping[(key or "").strip()]: (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(row.values()):
            continue
        if first_data_row:
            first_data_row = False
            if is_hint_row(row):
                continue
        line = reader.line_num
        row = {column: value_alias(ALERTS_SECTION, column, value) for column, value in row.items()}
        alert_id = row.get("id", "")
        if not alert_id:
            raise TableError(f"{path}:{line}: пустой id — по нему считается повтор отправки")
        if alert_id in seen:
            raise TableError(
                f"{path}:{line}: id «{alert_id}» повторяется — впервые в строке {seen[alert_id]}"
            )
        seen[alert_id] = line
        try:
            alerts.append(
                Alert.model_validate(
                    {
                        "id": alert_id,
                        "status": row.get("status") or "draft",
                        "title": row.get("title", ""),
                        "text": row.get("text", ""),
                        "link_slug": row.get("link_slug") or None,
                        "urgent": row.get("urgent") or False,
                    }
                )
            )
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '<строка>'}: {error['msg']}"
                for error in exc.errors()
            )
            raise TableError(f"{path}:{line}: {details}") from exc
    return alerts


def read_alerts(tables_dir: Path) -> list[Alert]:
    """Локальный `docs/templates/alerts.csv`; файла нет — оповещений нет."""

    path = tables_dir / ALERTS_FILENAME
    if not path.is_file():
        return []
    return parse_alerts(_read_table_text(path), path)


def alerts_from(texts: Mapping[str, str], tables_dir: Path) -> list[Alert]:
    """Оповещения из выгрузки Google Таблиц, а при её отсутствии — из CSV."""

    if ALERTS_FILENAME in texts:
        return parse_alerts(texts[ALERTS_FILENAME], Path(f"<лист {ALERTS_FILENAME}>"))
    return read_alerts(tables_dir)


def write_alerts(alerts: Sequence[Alert], path: Path = ALERTS_PATH) -> Path:
    """Пишет `content/alerts.yaml` через `.tmp` и подмену: половины файла не бывает.

    Совпадающий файл не перезаписывается: иначе каждая выгрузка давала бы коммит
    в `sync-sheets` и стирала комментарии-подсказки в файле.
    """

    path = Path(path)
    payload = [item.model_dump(mode="json", exclude_none=True) for item in alerts]
    if path.is_file():
        try:
            loaded = AlertFeed.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            )
        except (yaml.YAMLError, ValidationError):
            loaded = None
        if loaded is not None:
            current = [item.model_dump(mode="json", exclude_none=True) for item in loaded.alerts]
            if current == payload:
                return path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({"alerts": payload}, allow_unicode=True, sort_keys=False)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _check_alerts(alerts: Sequence[Alert], path: Path) -> list[str]:
    """Сверка таблицы с `content/alerts.yaml`: расхождение — повод перезаписать."""

    if not path.is_file():
        return [] if not alerts else [f"{path}: файла нет, а в таблице {len(alerts)} строк"]
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return [f"{path}: ожидался YAML-объект с полем alerts"]
    try:
        current = AlertFeed.model_validate(loaded).alerts
    except ValidationError as exc:
        return [f"{path}: {_validation_message(path, exc)}"]

    problems: list[str] = []
    left = {item.id: item.model_dump() for item in alerts}
    right = {item.id: item.model_dump() for item in current}
    for slug in sorted(set(left) - set(right)):
        problems.append(f"alerts.{slug}: есть в таблице, нет в {path.name}")
    for slug in sorted(set(right) - set(left)):
        problems.append(f"alerts.{slug}: есть в {path.name}, нет в таблице")
    for slug in sorted(set(left) & set(right)):
        different = sorted(key for key in left[slug] if left[slug][key] != right[slug][key])
        if different:
            problems.append(f"alerts.{slug}: расходятся поля {different}")
    return problems


# --- Лист `scores`: подтверждённые оценки судей (`docs/06` §6.28) -----------
#
# Отдельный лист и отдельный файл, как у оповещений: оценка, подтверждённая
# главным судьёй, должна быть видна в проде, а не только в предпросмотре.
# Железное правило: строка без `ДА` в колонке «Опубликовать на сайте?» не
# разбирается вовсе — ни в файл, ни на страницу, ни в мини-приложение.

SCORE_COLUMNS = (
    "performance",
    "nomination",
    "studio",
    "participant",
    "score",
    "place",
    "published",
)
SCORES_SECTION = "scores"
# Системный ключ `results` принимается как синоним: так лист называли в первых
# версиях карты, и ломать уже созданный секрет из-за переименования нельзя.
SCORES_ALIAS = "results"
SCORES_FILENAME = "scores.csv"
SCORES_PATH = BASE_DIR / "content" / "scores.yaml"

SCORE_YES = frozenset({"да", "yes", "true", "1", "д", "y"})
SCORE_NO = frozenset({"", "нет", "no", "false", "0", "н", "n"})


def _score_number(value: str, where: str) -> float:
    """Балл: русская запятая принимается наравне с точкой («31,5»)."""

    try:
        return float(value.strip().replace(",", "."))
    except ValueError as exc:
        raise TableError(f"{where}: «{value}» — не число") from exc


def _score_place(value: str, where: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if not text.isdigit():
        raise TableError(f"{where}: место «{value}» — нужно 1, 2, 3 или пустая клетка")
    return int(text)


def _publication(raw: str, where: str) -> bool:
    text = raw.strip().lower()
    if text in SCORE_YES:
        return True
    if text in SCORE_NO:
        return False
    raise TableError(f"{where}: «{raw}» в колонке «Опубликовать на сайте?» — нужно ДА или НЕТ")


def _now() -> datetime:
    return datetime.now(ZoneInfo(EVENT_TZ))


def parse_scores(raw: str, path: Path, *, edition: int, now: datetime | None = None) -> ScoreFeed:
    """Лист «Оценки судей» → только подтверждённые строки.

    Строки с `НЕТ` и пустой клеткой отбрасываются до проверки модели: это не
    ошибка в черновике, а рабочая строка, которой рано на сайт. Опечатка
    в подтверждённой строке, наоборот, называется файлом, строкой и полем.
    """

    try:
        delimiter = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(raw.splitlines(), delimiter=delimiter)
    fields = tuple((name or "").strip() for name in (reader.fieldnames or ()))
    if not fields:
        raise TableError(f"{path}: таблица пуста — нужна шапка {list(SCORE_COLUMNS)}")
    mapping = _header_map(SCORES_SECTION, fields, SCORE_COLUMNS, path)

    rows: list[JudgeScore] = []
    first_data_row = True
    for raw_row in reader:
        row = {
            mapping[(key or "").strip()]: (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(row.values()):
            continue
        if first_data_row:
            first_data_row = False
            if is_hint_row(row):
                continue
        row = {column: value_alias(SCORES_SECTION, column, value) for column, value in row.items()}
        line = reader.line_num
        where = f"{path}:{line}"
        if not _publication(row.get("published", ""), where):
            # Ни при каких условиях: ни файл, ни страница, ни мини-приложение.
            continue
        try:
            rows.append(
                JudgeScore(
                    performance=row.get("performance") or None,
                    nomination=row.get("nomination", ""),
                    studio=row.get("studio") or None,
                    participant=row.get("participant", ""),
                    score=_score_number(row.get("score", ""), where),
                    place=_score_place(row.get("place", ""), where),
                    published=True,
                )
            )
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '<строка>'}: {error['msg']}"
                for error in exc.errors()
            )
            raise TableError(f"{where}: {details}") from exc
    return ScoreFeed(edition=edition, updated_at=now or _now(), rows=rows)


def read_scores(tables_dir: Path, *, edition: int, now: datetime | None = None) -> ScoreFeed:
    """Локальный `docs/templates/scores.csv`; файла нет — подтверждённых строк нет."""

    path = Path(tables_dir) / SCORES_FILENAME
    if not path.is_file():
        return ScoreFeed(edition=edition, updated_at=now or _now(), rows=[])
    return parse_scores(_read_table_text(path), path, edition=edition, now=now)


def scores_from(
    texts: Mapping[str, str],
    tables_dir: Path,
    *,
    edition: int,
    now: datetime | None = None,
) -> ScoreFeed:
    """Оценки из выгрузки Google Таблиц, а при её отсутствии — из CSV."""

    if SCORES_FILENAME in texts:
        return parse_scores(
            texts[SCORES_FILENAME],
            Path(f"<лист {SCORES_FILENAME}>"),
            edition=edition,
            now=now,
        )
    return read_scores(tables_dir, edition=edition, now=now)


def write_scores(feed: ScoreFeed, path: Path = SCORES_PATH) -> Path:
    """Пишет `content/scores.yaml` через `.tmp` и подмену.

    В файл попадают только подтверждённые строки: `published: false` в
    `content/scores.yaml` невозможен физически, а не «по договорённости».
    Совпадающий файл не перезаписывается: иначе каждая выгрузка давала бы
    коммит в `sync-sheets` и стирала комментарии-подсказки. Файл не создаётся,
    пока нет ни одной подтверждённой строки: пустой файл обещал бы раздел,
    которого на странице нет.
    """

    path = Path(path)
    payload = [row.model_dump(mode="json") for row in feed.published]
    if path.is_file():
        try:
            loaded = ScoreFeed.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            )
        except (yaml.YAMLError, ValidationError):
            loaded = None
        if loaded is not None:
            current = [row.model_dump(mode="json") for row in loaded.published]
            if loaded.edition == feed.edition and current == payload:
                return path
    elif not payload:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        {
            "edition": feed.edition,
            "updated_at": feed.updated_at.isoformat(),
            "rows": payload,
        },
        allow_unicode=True,
        sort_keys=False,
    )
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _check_scores(feed: ScoreFeed, path: Path) -> list[str]:
    """Сверка таблицы с `content/scores.yaml`; `updated_at` не сравнивается.

    Сравниваются только подтверждённые строки — ровно то, что пишет
    `write_scores`: черновик в файле не считается расхождением, он просто
    не публикуется.
    """

    path = Path(path)
    payload = [row.model_dump(mode="json") for row in feed.published]
    if not path.is_file():
        if not payload:
            return []
        return [f"{path}: файла нет, а в таблице {len(payload)} подтверждённых строк"]
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return [f"{path}: ожидался YAML-объект с полями edition и rows"]
    try:
        current = ScoreFeed.model_validate(loaded)
    except ValidationError as exc:
        return [_validation_message(path, exc)]

    problems: list[str] = []
    if current.edition != feed.edition:
        problems.append(f"{path}: выпуск {current.edition}, а в таблице {feed.edition}")
    if [row.model_dump(mode="json") for row in current.published] != payload:
        problems.append(f"оценки судей расходятся с {path.name}")
    return problems


# --- Google Таблицы: CSV по публичной ссылке -------------------------------
#
# Таблицу заполняет владелец в браузере, а не контент-менеджер в git. При доступе
# «Все, у кого есть ссылка» у каждого листа есть постоянный адрес экспорта CSV —
# без ключа API и без библиотеки Google. Разбор CSV у нас свой (`read_table`),
# поэтому дополнительных зависимостей нет: качаем стандартной библиотекой, а
# `pip`-лок остаётся таким, каким его собрал `uv`.

DEFAULT_SHEETS_TIMEOUT = 10.0
SHEETS_MAP_ENV = "ZARUBA_SHEETS_MAP"
SHEETS_ID_ENV = "ZARUBA_SHEET_ID"
SHEETS_BASE_URL = "https://docs.google.com/spreadsheets/d"
SHEETS_USER_AGENT = "zaruba-sync-tables/1.0"

# `opener` — точка подмены в тестах: сеть в pytest не нужна.
Opener = Callable[[urllib.request.Request, float], IO[bytes]]


def sheet_export_url(sheet_id: str, gid: str) -> str:
    """Адрес экспорта листа: `.../d/<ID>/export?format=csv&gid=<GID>`."""

    return f"{SHEETS_BASE_URL}/{sheet_id}/export?format=csv&gid={gid}"


def _sheet_map(environ: Mapping[str, str]) -> dict[str, str]:
    """Разбирает `ZARUBA_SHEETS_MAP`: раздел модели → gid или полный URL."""

    raw = environ.get(SHEETS_MAP_ENV, "").strip()
    sheet_id = environ.get(SHEETS_ID_ENV, "").strip()
    if not raw:
        if sheet_id:
            raise TableError(
                f"{SHEETS_ID_ENV} задан, а {SHEETS_MAP_ENV} нет — неизвестно, какой gid "
                'у какого листа; пример: {"blocks": "0", "studios": "123456"}'
            )
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TableError(
            f"{SHEETS_MAP_ENV}: не JSON ({exc.msg}, позиция {exc.pos}) — ожидался объект "
            'вида {"blocks": "0", "studios": "123456"}'
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise TableError(f'{SHEETS_MAP_ENV}: нужен непустой JSON-объект "лист": "gid"')

    mapping: dict[str, str] = {}
    for section, value in parsed.items():
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not value.strip():
            raise TableError(
                f"{SHEETS_MAP_ENV}: у листа «{section}» пустое значение — нужен gid или URL"
            )
        mapping[str(section)] = value.strip()
    return mapping


def sheet_sources(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Имя файла таблицы → адрес CSV.

    Пустой словарь — переменные не заданы, работаем с локальными
    `docs/templates/*.csv`. Неполная карта — ошибка, а не тихая замена
    половины листов: иначе overlay соберётся из старых и новых данных сразу.
    """

    environ = os.environ if environ is None else environ
    mapping = _sheet_map(environ)
    if not mapping:
        return {}

    known = {spec.section for spec in TABLE_SPECS} | {
        ALERTS_SECTION,
        SCORES_SECTION,
        SCORES_ALIAS,
    }
    unknown = sorted(set(mapping) - known)
    if unknown:
        raise TableError(f"{SHEETS_MAP_ENV}: неизвестные листы {unknown}; есть {sorted(known)}")
    missing = [spec.section for spec in TABLE_SPECS if spec.section not in mapping]
    if missing:
        raise TableError(
            f"{SHEETS_MAP_ENV}: не заданы листы {missing} — выгрузка была бы неполной, "
            "а частичная замена таблиц запрещена"
        )

    sheet_id = environ.get(SHEETS_ID_ENV, "").strip()

    def url_for(section: str, value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value
        if not sheet_id:
            raise TableError(
                f"{SHEETS_MAP_ENV}: у листа «{section}» указан gid «{value}», но "
                f"{SHEETS_ID_ENV} не задан — нужен id таблицы или полный URL листа"
            )
        return sheet_export_url(sheet_id, value)

    sources: dict[str, str] = {
        spec.filename: url_for(spec.section, mapping[spec.section]) for spec in TABLE_SPECS
    }
    # `alerts` не обязателен: оповещений может не быть, и старые карты листов
    # без него должны продолжать работать без правки секрета.
    if ALERTS_SECTION in mapping:
        sources[ALERTS_FILENAME] = url_for(ALERTS_SECTION, mapping[ALERTS_SECTION])
    if SCORES_SECTION in mapping and SCORES_ALIAS in mapping:
        raise TableError(
            f"{SHEETS_MAP_ENV}: лист оценок указан дважды — «{SCORES_SECTION}» и "
            f"«{SCORES_ALIAS}»; оставьте один ключ"
        )
    score_key = SCORES_SECTION if SCORES_SECTION in mapping else SCORES_ALIAS
    if score_key in mapping:
        sources[SCORES_FILENAME] = url_for(SCORES_SECTION, mapping[score_key])
    return sources


def _opener(request: urllib.request.Request, timeout: float) -> IO[bytes]:
    """Сетевой вызов по умолчанию; тесты подменяют модульный атрибут."""

    return urllib.request.urlopen(request, timeout=timeout)


# Google отдаёт страницу входа вместо CSV, когда ссылка закрыта. Признак
# дешёвый, но однозначный: CSV никогда не начинается с тега документа.
HTML_MARKERS = ("<!doctype html", "<html")


def _looks_like_html(text: str) -> bool:
    return text.lstrip("\ufeff \t\r\n").lower().startswith(HTML_MARKERS)


def _http_hint(code: int) -> str:
    if code in {401, 403}:
        return "таблица закрыта: включите «Доступ по ссылке» → «Читатель» и проверьте gid"
    if code in {400, 404}:
        return "лист не найден: проверьте SHEET_ID и gid из адреса листа"
    return "повторите запрос позже или проверьте адрес листа"


def fetch_sheet_text(
    url: str,
    *,
    timeout: float = DEFAULT_SHEETS_TIMEOUT,
    opener: Opener | None = None,
) -> str:
    """Скачивает один лист. Любая ошибка называет адрес и причину, а не код."""

    open_ = opener or _opener
    request = urllib.request.Request(url, headers={"User-Agent": SHEETS_USER_AGENT})
    try:
        with open_(request, timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise TableError(f"{url}: HTTP {exc.code} — {_http_hint(exc.code)}") from exc
    except TimeoutError as exc:
        raise TableError(f"{url}: нет ответа за {timeout:g} с — лист не скачался") from exc
    except urllib.error.URLError as exc:
        # URLError(TimeoutError) — таймаут, обёрнутый в «нет соединения».
        if isinstance(exc.reason, TimeoutError):
            raise TableError(f"{url}: нет ответа за {timeout:g} с — лист не скачался") from exc
        raise TableError(f"{url}: сетевая ошибка — {exc.reason}") from exc

    if not raw.strip():
        raise TableError(f"{url}: пустой ответ вместо CSV — лист пуст или закрыт")
    text = _decode_table(raw, url)
    if _looks_like_html(text):
        raise TableError(
            f"{url}: вместо CSV пришёл HTML — таблица закрыта. Откройте «Доступ по ссылке» "
            "(«Читатель») и проверьте, что адрес ведёт на export?format=csv"
        )
    return text


def fetch_sheets(
    sources: Mapping[str, str],
    *,
    timeout: float = DEFAULT_SHEETS_TIMEOUT,
    opener: Opener | None = None,
) -> dict[str, str]:
    """Все листы или ничего: первый сбой прерывает выгрузку.

    Ошибка дополняется именем листа: «лист judges.csv: …: нет ответа за 10 с» —
    по такому сообщению видно, что именно чинить, не открывая таблицу.
    """

    texts: dict[str, str] = {}
    for filename, url in sorted(sources.items()):
        try:
            texts[filename] = fetch_sheet_text(url, timeout=timeout, opener=opener)
        except TableError as exc:
            raise TableError(f"лист {filename}: {exc}") from exc
    return texts


def write_tables(texts: Mapping[str, str], tables_dir: Path) -> list[Path]:
    """Заменяет локальные CSV скачанными: сначала `.tmp`, потом подмена файла.

    Вызывается только после проверки всей выгрузки, поэтому «половина
    обновилась» из-за ошибки данных невозможна; `.tmp` + `os.replace` закрывают
    вторую половину случая — обрыв записи на середине.
    """

    tables_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    for filename, text in sorted(texts.items()):
        target = tables_dir / filename
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(text.encode("utf-8"))
        staged.append((tmp, target))
    for tmp, target in staged:
        os.replace(tmp, target)
    return sorted(target for _, target in staged)


@dataclass(frozen=True)
class Summary:
    real: dict[str, int]
    preview: dict[str, int]

    def line(self) -> str:
        real = sum(self.real.values())
        preview = sum(self.preview.values())
        parts = [f"{section}: {count}" for section, count in self.preview.items() if count]
        detail = f" ({', '.join(parts)})" if parts else ""
        return f"real: {real}, preview: {preview}{detail}"


def _validation_message(path: Path, exc: ValidationError) -> str:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<корень>"
        details.append(f"{location}: {error['msg']}")
    return f"{path}: " + "; ".join(details)


def read_table(spec: TableSpec, tables_dir: Path) -> list[dict[str, str]]:
    """Строки таблицы с проверкой шапки, тега и повторов slug."""

    path = tables_dir / spec.filename
    if not path.is_file():
        raise TableError(f"{path}: файл таблицы не найден")
    return _parse_table(spec, _read_table_text(path), path)


def _header_map(
    section: str, fields: Sequence[str], known: Sequence[str], path: Path
) -> dict[str, str]:
    """Заголовок в файле → системная колонка; проверяет полноту шапки.

    Принимаются обе шапки: системная (`slug`, `name`, …) и русская из
    мастер-таблицы (`Код студии`, `Название студии`, …). Смешивать их можно:
    таблица, где часть заголовков осталась английской, не должна падать
    из-за косметики.
    """

    mapping: dict[str, str] = {}
    for name in fields:
        column = column_for(section, name, known)
        mapping[name] = name if column is None else column

    unknown = [name for name, column in mapping.items() if column not in known]
    missing = [column for column in known if column not in mapping.values()]
    if missing or unknown:
        raise TableError(f"{path}: шапка не совпадает с моделью (нет {missing}, лишние {unknown})")

    translated = list(mapping.values())
    duplicates = sorted({column for column in translated if translated.count(column) > 1})
    if duplicates:
        raise TableError(f"{path}: две колонки указывают на одно поле {duplicates} — оставьте одну")
    return mapping


def _parse_table(spec: TableSpec, raw: str, path: Path) -> list[dict[str, str]]:
    """Разбор прочитанного CSV: один код и для файла, и для скачанного листа."""

    if not raw.strip():
        raise TableError(f"{path}: таблица пуста")

    try:
        delimiter = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(raw.splitlines(), delimiter=delimiter)
    fields = tuple((name or "").strip() for name in (reader.fieldnames or ()))
    mapping = _header_map(spec.section, fields, spec.columns, path)

    rows: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    first_data_row = True
    for raw_row in reader:
        row = {
            mapping[(key or "").strip()]: (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(row.values()):
            continue
        if first_data_row:
            first_data_row = False
            # Вторая строка мастер-таблицы — подсказка «что писать», а не данные.
            if is_hint_row(row):
                continue
        line = reader.line_num
        row = {column: value_alias(spec.section, column, value) for column, value in row.items()}
        tag = row.get("data_tag", "")
        if tag not in TAGS:
            raise TableError(
                f"{path}:{line}: data_tag «{tag}» — нужно «подтверждено» или «пример» "
                "(в старой таблице: real или preview)"
            )
        slug = row.get("slug", "")
        if not slug:
            raise TableError(f"{path}:{line}: пустой slug")
        if slug in seen:
            raise TableError(
                f"{path}:{line}: slug «{slug}» повторяется — впервые в строке {seen[slug]}"
            )
        seen[slug] = line
        rows.append(row)
    if not rows:
        raise TableError(f"{path}: нет ни одной строки")
    return rows


def _base_rows(base: dict[str, object], section: str, path: Path) -> list[dict[str, object]]:
    value = base.get(section, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise TableError(f"{path}: раздел «{section}» в выпуске — не список")
    return [item for item in value if isinstance(item, dict)]


def _check_real_rows(
    spec: TableSpec,
    rows: list[dict[str, str]],
    base_rows: list[dict[str, object]],
    table_path: Path,
    base_path: Path,
) -> None:
    """`real`-строки — зеркало опубликованного YAML, а не источник записи."""

    truth = {str(row.get("slug")): row for row in base_rows if isinstance(row.get("slug"), str)}
    real_rows = [row for row in rows if row["data_tag"] == TAG_REAL]
    real_slugs = {row["slug"] for row in real_rows}
    if real_slugs != set(truth):
        missing = sorted(set(truth) - real_slugs)
        extra = sorted(real_slugs - set(truth))
        raise TableError(
            f"{table_path}: real-строки не совпадают с {base_path.name}: "
            f"нет в таблице {missing}, нет в YAML {extra}"
        )

    for row in real_rows:
        slug = row["slug"]
        try:
            from_table = spec.build(row, False)
            from_base = spec.model.model_validate(truth[slug])
        except ValidationError as exc:
            raise TableError(_validation_message(table_path, exc)) from exc
        left = from_table.model_dump()
        right = from_base.model_dump()
        different = sorted(key for key in left if left[key] != right[key])
        if different:
            raise TableError(
                f"{table_path}: {spec.section}.{slug}: таблица расходится с YAML "
                f"по полям {different} — правьте опубликованный файл, а не таблицу"
            )


def _preview_rows(spec: TableSpec, rows: list[dict[str, str]], path: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for row in rows:
        if row["data_tag"] != TAG_PREVIEW:
            continue
        try:
            model = spec.build(row, True)
        except ValidationError as exc:
            raise TableError(_validation_message(path, exc)) from exc
        payload = model.model_dump(mode="json", exclude_none=True)
        if "demo" in type(model).model_fields:
            payload["demo"] = True
        prepared.append(payload)
    return prepared


def _at(value: object, where: str) -> time:
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise TableError(f"{where}: «{value}» — не время ЧЧ:ММ") from exc


def _moment(value: object, where: str) -> time:
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError as exc:
        raise TableError(f"{where}: «{value}» — не дата-время ISO 8601") from exc


def timing_patches(
    base: dict[str, object],
    overlay: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Время номинации из её выступлений: «Положение» тайминга не даёт.

    Патч не трогает название, возраст и источник: в базе остаётся подтверждённый
    факт, а в overlay — только вычисленные границы для страницы расписания.
    """

    published = {
        str(row.get("slug"))
        for row in _base_rows(base, "nominations", Path("<base>"))
        if row.get("starts_at") is not None
    }
    shows: dict[str, list[dict[str, Any]]] = {}
    for row in overlay.get("performances", []):
        shows.setdefault(str(row["nomination"]), []).append(row)

    patches: list[dict[str, Any]] = []
    patched: set[str] = set()
    for block in overlay.get("blocks", []):
        block_start = _at(block["starts_at"], f"blocks.{block['slug']}.starts_at")
        block_end = _at(block["ends_at"], f"blocks.{block['slug']}.ends_at")
        for slug in block.get("nominations", []):
            slug = str(slug)
            if slug in published or slug in patched:
                continue
            rows = shows.get(slug, [])
            if not rows:
                raise TableError(
                    f"blocks.{block['slug']}: у номинации «{slug}» нет выступлений — "
                    "время в расписании вывести не из чего"
                )
            moments = []
            for row in rows:
                moment = _moment(row["scheduled_at"], f"performances.{row['slug']}.scheduled_at")
                if moment < block_start or moment > block_end:
                    raise TableError(
                        f"performances.{row['slug']}: {moment:%H:%M} вне блока "
                        f"{block_start:%H:%M}–{block_end:%H:%M}"
                    )
                moments.append(moment)
            first = max(min(moments), block_start)
            finish = min(
                datetime.combine(date.min, max(moments)) + timedelta(minutes=10),
                datetime.combine(date.min, block_end),
            ).time()
            if finish <= first:
                raise TableError(f"blocks.{block['slug']}: у номинации «{slug}» пустое окно")
            patches.append(
                {
                    "slug": slug,
                    "starts_at": first.strftime("%H:%M"),
                    "ends_at": finish.strftime("%H:%M"),
                }
            )
            patched.add(slug)
    return patches


def build_overlay(
    tables_dir: Path,
    base_path: Path,
    *,
    texts: Mapping[str, str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], Summary]:
    """Читает таблицы, сверяет real-строки и собирает overlay в память.

    `texts` (имя файла → CSV) — уже скачанные листы. Проверяем их до записи на
    диск: иначе сбой на середине оставил бы каталог наполовину обновлённым.
    """

    if not base_path.is_file():
        raise TableError(f"{base_path}: файл выпуска не найден")
    loaded = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TableError(f"{base_path}: ожидался YAML-объект выпуска")
    base: dict[str, object] = loaded

    overlay: dict[str, list[dict[str, Any]]] = {}
    real: dict[str, int] = {}
    preview: dict[str, int] = {}
    for spec in TABLE_SPECS:
        if texts is None:
            path = tables_dir / spec.filename
            rows = read_table(spec, tables_dir)
        else:
            raw = texts.get(spec.filename)
            if raw is None:
                raise TableError(f"{spec.filename}: лист не скачан — выгрузка неполная")
            # Метка вместо пути: сообщение скажет, что дело в листе, а не в файле.
            path = Path(f"<лист {spec.section}>")
            rows = _parse_table(spec, raw, path)
        _check_real_rows(spec, rows, _base_rows(base, spec.section, base_path), path, base_path)
        real[spec.section] = sum(1 for row in rows if row["data_tag"] == TAG_REAL)
        prepared = _preview_rows(spec, rows, path)
        preview[spec.section] = len(prepared)
        if prepared:
            overlay[spec.section] = prepared

    patches = timing_patches(base, overlay)
    if patches:
        overlay.setdefault("nominations", []).extend(patches)

    merged = merge_overlay(base, [(base_path, overlay)])
    try:
        Edition.model_validate(merged)
    except ValidationError as exc:
        raise TableError(_validation_message(base_path, exc)) from exc
    return overlay, Summary(real=real, preview=preview)


def _overlay_yaml(section: str, rows: list[dict[str, Any]]) -> str:
    return yaml.safe_dump({section: rows}, allow_unicode=True, sort_keys=False, width=100)


def _check_overlay(overlay: dict[str, list[dict[str, Any]]], out_dir: Path) -> list[str]:
    """Сверяет файлы overlay с тем, что дали таблицы: `--check` не должен врать."""

    expected = {
        out_dir / f"{section}.yaml": _overlay_yaml(section, rows)
        for section, rows in overlay.items()
        if rows
    }
    problems: list[str] = []
    for path, text in expected.items():
        if not path.is_file():
            problems.append(f"нет файла {path.name}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"устарел {path.name}")
    problems.extend(
        f"лишний файл {path.name}"
        for path in sorted(out_dir.glob("*.yaml"))
        if path not in expected
    )
    return problems


def write_overlay(
    overlay: dict[str, list[dict[str, Any]]], out_dir: Path, *, dry_run: bool = False
) -> list[Path]:
    """Пишет только непустые разделы и убирает устаревшие файлы overlay."""

    if dry_run:
        return [out_dir / f"{section}.yaml" for section, rows in overlay.items() if rows]
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for section, rows in overlay.items():
        if not rows:
            continue
        path = out_dir / f"{section}.yaml"
        path.write_text(_overlay_yaml(section, rows), encoding="utf-8")
        written.append(path)
    for stale in sorted(out_dir.glob("*.yaml")):
        if stale not in written:
            stale.unlink()
    return sorted(written)


def drop_preview(out_dir: Path) -> list[Path]:
    """Удаляет overlay: презентационные строки исчезают без правки базового YAML."""

    if not out_dir.is_dir():
        return []
    removed = sorted(out_dir.glob("*.yaml"))
    for path in removed:
        path.unlink()
    with contextlib.suppress(OSError):
        out_dir.rmdir()
    return removed


def _edition_number(base_path: Path) -> int:
    """Год выпуска из базового YAML: лист оценок год не содержит."""

    try:
        loaded = yaml.safe_load(Path(base_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TableError(f"{base_path}: не прочитать выпуск — {exc}") from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get("edition"), int):
        raise TableError(f"{base_path}: в выпуске нет целого поля edition")
    return int(loaded["edition"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.sync_tables",
        description="CSV-таблицы предпросмотра → content/editions/<год>.d",
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=BASE_DIR / "docs" / "templates",
        help="папка с CSV (по умолчанию docs/templates)",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=BASE_DIR / "content" / "editions" / "2026.yaml",
        help="опубликованный выпуск, с которым сверяются real-строки",
    )
    parser.add_argument("--out-dir", type=Path, help="папка overlay (по умолчанию <год>.d)")
    parser.add_argument(
        "--alerts-out",
        type=Path,
        default=ALERTS_PATH,
        help="файл срочных оповещений (по умолчанию content/alerts.yaml)",
    )
    parser.add_argument(
        "--scores-out",
        type=Path,
        default=SCORES_PATH,
        help="файл подтверждённых оценок (по умолчанию content/scores.yaml)",
    )
    parser.add_argument("--check", action="store_true", help="только проверить, не писать")
    parser.add_argument("--drop-preview", action="store_true", help="удалить overlay и выйти")
    parser.add_argument(
        "--from-sheets",
        action="store_true",
        help="скачать листы из Google Таблиц (ZARUBA_SHEET_ID + ZARUBA_SHEETS_MAP)",
    )
    parser.add_argument(
        "--sheets-timeout",
        type=float,
        default=DEFAULT_SHEETS_TIMEOUT,
        help=f"таймаут запроса к таблице, с (по умолчанию {DEFAULT_SHEETS_TIMEOUT:g})",
    )
    args = parser.parse_args(argv)
    out_dir = args.out_dir or overlay_dir(args.base)

    try:
        if args.drop_preview:
            removed = drop_preview(out_dir)
            print(f"OK: удалено файлов overlay: {len(removed)}")
            return 0
        texts: dict[str, str] | None = None
        if args.from_sheets:
            sources = sheet_sources()
            if not sources:
                raise TableError(
                    f"{SHEETS_MAP_ENV} не задан — --from-sheets работать не может; "
                    "укажите таблицу или запустите без флага на локальных CSV"
                )
            texts = fetch_sheets(sources, timeout=args.sheets_timeout)
        overlay, summary = build_overlay(args.tables_dir, args.base, texts=texts)
        # Оповещения читаются до записи файлов — по той же причине, что и
        # overlay: битая строка не должна оставить половину каталога обновлённой.
        alerts = alerts_from(texts or {}, args.tables_dir)
        # Оценки читаются там же: строка без `ДА` отбрасывается ещё до записи,
        # поэтому неподтверждённый протокол физически не попадает в контент.
        scores = scores_from(texts or {}, args.tables_dir, edition=_edition_number(args.base))
        if args.check:
            problems = _check_overlay(overlay, out_dir)
            problems += _check_alerts(alerts, args.alerts_out)
            problems += _check_scores(scores, args.scores_out)
            if problems:
                print(
                    f"ОШИБКА: данные расходятся с файлами: {', '.join(problems)}",
                    file=sys.stderr,
                )
                return 1
            print(f"OK  таблицы: {summary.line()}")
            print(f"OK  overlay совпадает: {out_dir}")
            print(f"OK  оповещения совпадают: {args.alerts_out} ({len(alerts)})")
            print(f"OK  оценки судей совпадают: {args.scores_out} ({len(scores.rows)})")
            return 0
        # Запись только после проверки всех листов: до этой строки любая ошибка
        # оставляет и CSV, и overlay прежними.
        written_tables = write_tables(texts, args.tables_dir) if texts else []
        written = write_overlay(overlay, out_dir)
        write_alerts(alerts, args.alerts_out)
        write_scores(scores, args.scores_out)
        print(f"OK  таблицы: {summary.line()}")
        if texts:
            print(f"OK  листов из Google Таблиц: {len(written_tables)} → {args.tables_dir}")
        print(f"OK  записано файлов overlay: {len(written)} → {out_dir}")
        print(f"OK  оповещения: {len(alerts)} → {args.alerts_out}")
        print(f"OK  оценки судей: подтверждено {len(scores.rows)} → {args.scores_out}")
        return 0
    except (TableError, ContentError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
