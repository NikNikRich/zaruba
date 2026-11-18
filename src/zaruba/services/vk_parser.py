"""Пост ВКонтакте → структурированный черновик для сайта (`docs/09` §9.14).

Конвейер: `zaruba.vk_watch` читает новый пост и отдаёт его сюда. Модуль решает,
что из поста выйдет на сайте — срочное оповещение (`Alert`) или карточка
видео/фотоотчёта (`MediaItem`), — и складывает заготовку в `logs/vk_drafts.json`.
Публикует только человек: кнопка в MAX передаёт боту команду (`max_bot`).

Почему черновик, а не автоматическая запись в `content/*.yaml`. Пост пишут для
соцсети: там эмодзи, хештеги и ссылки-обёртки. Превращать это в боевой контент
без человека — значит выдумывать факты за организатора (AGENTS.md). Поэтому
модуль готовит заготовку, называет источник и оставляет решение владельцу.

Все преобразования текста — обычные функции без сети и без файлов, поэтому
проверяются тестом без заглушек.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import MediaKind, MediaPlatform

if TYPE_CHECKING:
    # Только для типов: `services.vk` импортирует `services.max_bot`, а тот —
    # этот модуль. Импорт во время выполнения замкнул бы круг.
    from .vk import Post

# Версия формата `logs/vk_drafts.json`: новый файл читается старым кодом или
# честно отвергается, а не разбирается «наполовину».
DRAFT_VERSION = 1

# Границы полей совпадают с моделями `Alert` и `MediaItem`: черновик, который
# заведомо не пройдёт `content check`, не должен попадать в карточку владельцу.
TITLE_LIMIT = 80
TEXT_LIMIT = 280
MEDIA_TITLE_LIMIT = 120

PICTURE_NOTE = "В посте есть картинка — проверь детали глазами перед публикацией"

# Основа слова, а не целое: «перенос», «перенесли», «переносится» — одно событие.
ALERT_WORDS: tuple[str, ...] = (
    "внимани",
    "важно",
    "срочно",
    "перенос",
    "перенес",
    "задерж",
    "отмен",
    "старт",
    "начал",
    "закрыт",
    "открыт",
    "регистрац",
    "измени",
)

# Ссылка на видео, альбом или диск — то, из чего получается карточка `/media`.
MEDIA_LINK_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:vk\.com|vkvideo\.ru|rutube\.ru|disk\.yandex\.ru"
    r"|cloud\.mail\.ru|youtube\.com|youtu\.be)/\S+",
    re.IGNORECASE,
)

# Хостинг по адресу: подпись карточки берётся из ссылки, а не из текста поста.
PLATFORMS: tuple[tuple[str, MediaPlatform], ...] = (
    ("rutube.ru", MediaPlatform.RUTUBE),
    ("disk.yandex.ru", MediaPlatform.YANDEX_DISK),
    ("vkvideo.ru", MediaPlatform.VK_VIDEO),
    ("vk.com", MediaPlatform.VK_VIDEO),
    ("cloud.mail.ru", MediaPlatform.MAIL_CLOUD),
    ("youtube.com", MediaPlatform.YOUTUBE),
    ("youtu.be", MediaPlatform.YOUTUBE),
)

# Типы вложений VK, которые означают «пост про видео» (проверено на `wall.get`).
VIDEO_KINDS = frozenset({"video", "short_video"})
# Всё, что владелец должен посмотреть глазами: фото-афиша, скан, PDF-документ.
PICTURE_KINDS = frozenset({"photo", "graffiti", "doc", "market_album"})

PHOTO_WORDS: tuple[str, ...] = ("фото", "отчет", "альбом", "снимк")
STREAM_WORDS: tuple[str, ...] = ("эфир", "трансляц", "прямой включ")

# Разделы сайта, на которые умеет ссылаться плашка (`link_slug` в `Alert`).
# Порядок важен: «регистрация» — это билеты, а не расписание, и побеждает
# первое совпадение сверху.
SLUG_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tickets", ("билет", "вход", "касс", "цена", "стоимост", "взнос", "регистрац")),
    ("venue", ("площадк", "адрес", "как добрат", "фойе", "золотые ворота")),
    ("schedule", ("расписан", "блок", "перенос", "тайминг", "во сколько", "старт")),
    ("results", ("результат", "итог", "протокол", "победител", "места", "оценк")),
    ("media", ("фото", "видео", "отчет", "эфир", "трансляц", "альбом")),
)

# Подписи разделов для карточки в MAX. Ключи — настоящие slug страниц сайта
# (`max_bot.SECTIONS`), совпадение проверяет тест: разойтись они не должны.
SECTION_TITLES: dict[str, str] = {
    "schedule": "Расписание",
    "tickets": "Билеты",
    "venue": "Площадка",
    "results": "Результаты",
    "media": "Фото и видео",
}

SOURCE_PREFIX = "Источник: "

_HASHTAG_RE = re.compile(r"(?<!\S)#[^\s#]+")
_URL_RE = re.compile(r"https?://\S+")


class DraftError(Exception):
    """Черновик не прочитан или не записан: сообщение называет файл и причину."""


class DraftKind(StrEnum):
    """Во что пост превращается на сайте: плашка или карточка медиа."""

    ALERT = "alert"
    MEDIA = "media"

    @property
    def label(self) -> str:
        return "Оповещение на сайт" if self is DraftKind.ALERT else "Фото/Видео"


class DraftStatus(StrEnum):
    """`pending` — ждёт владельца, дальше только два исхода."""

    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return {
            "pending": "ждёт решения",
            "published": "опубликован",
            "rejected": "отклонён",
        }[self.value]


@dataclass(frozen=True, slots=True)
class Draft:
    """Заготовка контента из поста ВК: ровно то, что увидит владелец в карточке."""

    id: str
    kind: DraftKind
    title: str
    text: str
    post_key: str
    post_url: str
    created_at: str
    status: DraftStatus = DraftStatus.PENDING
    link_slug: str | None = None
    urgent: bool = True
    has_picture: bool = False
    # Поля карточки медиа: у оповещения их нет, и `None` — честное «не медиа»,
    # а не пустая строка.
    media_kind: str | None = None
    platform: str | None = None
    media_url: str | None = None


def clean_text(raw: str) -> str:
    """Текст поста без хештегов, ссылок и лишних пробелов.

    Ссылки вырезаются: у `Alert` нет поля источника, а адрес поста модуль
    хранит отдельно (`post_url`) и добавляет в текст сам — ссылка внутри тела
    съела бы лимит 280 символов и разорвала фразу.
    """

    body = _URL_RE.sub(" ", _HASHTAG_RE.sub(" ", raw))
    return " ".join(body.split())


def _clip(text: str, limit: int) -> str:
    """Обрезка по границе слова: «перенос бло…» выглядит как ошибка вёрстки."""

    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{cut.rstrip(' ,.;:—-–')}…"


def extract_title(raw: str) -> str:
    """Первая осмысленная строка поста: до 80 символов, без ссылок и хештегов.

    Строка из одних хештегов или из двух букв — не заголовок: пропускаем её и
    берём следующую, иначе плашка начнётся с «#заруба».
    """

    for line in raw.splitlines():
        candidate = " ".join(_URL_RE.sub(" ", _HASHTAG_RE.sub(" ", line)).split())
        candidate = candidate.strip(" .,:;—-–·|")
        if len(candidate) < 3:
            continue
        return _clip(candidate, TITLE_LIMIT)
    return ""


def media_links(raw: str) -> list[str]:
    """Ссылки на видео/альбомы/диски в порядке появления, без повторов."""

    seen: list[str] = []
    for match in MEDIA_LINK_RE.finditer(raw):
        link = match.group(0).rstrip(".,;:!?)»\"'")
        if link not in seen:
            seen.append(link)
    return seen


def platform_for(url: str) -> MediaPlatform:
    lowered = url.lower()
    for host, platform in PLATFORMS:
        if host in lowered:
            return platform
    return MediaPlatform.OTHER


def guess_slug(raw: str) -> str | None:
    """Раздел сайта по ключевым словам поста; `None` — плашка без ссылки."""

    lowered = raw.lower()
    for slug, words in SLUG_WORDS:
        if any(word in lowered for word in words):
            return slug
    return None


def classify(post: Post) -> DraftKind | None:
    """Тип черновика или `None`, если пост к публикации не готов.

    Медиа важнее слов: пост «смотрите, как прошёл блок» с видео — это отчёт, а
    не оповещение, даже если в тексте есть слово «блок». Пост без медиа и без
    слов-сигналов (поздравление, благодарность) остаётся обычным уведомлением:
    придумывать для него раздел и текст плашки нельзя.
    """

    if media_links(post.text) or VIDEO_KINDS & set(post.kinds):
        return DraftKind.MEDIA
    lowered = post.text.lower()
    if any(word in lowered for word in ALERT_WORDS):
        return DraftKind.ALERT
    return None


def has_picture(post: Post) -> bool:
    """Есть ли в посте картинка или документ — то, что видно только глазами."""

    return bool(PICTURE_KINDS & set(post.kinds)) or "vk.com/photo" in post.text.lower()


def _media_kind(post: Post, links: Sequence[str]) -> MediaKind:
    lowered = post.text.lower()
    if any(word in lowered for word in STREAM_WORDS):
        return MediaKind.STREAM
    if any(word in lowered for word in PHOTO_WORDS):
        return MediaKind.PHOTO
    if any("album" in link or "photo" in link for link in links):
        return MediaKind.PHOTO
    return MediaKind.VIDEO


def _body(post: Post) -> str:
    """Тело сообщения: текст поста, а без текста — честная пометка о вложении."""

    body = clean_text(post.text)
    if body:
        return body
    kinds = ", ".join(post.kinds) if post.kinds else "нет"
    return f"Пост без текста (вложение: {kinds})"


def _alert_text(post: Post) -> str:
    """Текст плашки вместе со ссылкой на первоисточник.

    `Alert` не умеет хранить URL, а факт без источника публиковать нельзя
    (AGENTS.md), поэтому адрес поста — часть текста. Место под него вычитается
    из лимита заранее: обрезка после склейки отрезала бы источник.
    """

    source = f"{SOURCE_PREFIX}{post.url}"
    room = TEXT_LIMIT - len(source) - 1
    body = _clip(_body(post), room)
    return f"{body} {source}" if body else source


def _media_title(post: Post, links: Sequence[str]) -> str:
    title = extract_title(post.text)
    if title:
        return _clip(title, MEDIA_TITLE_LIMIT)
    # Пост без текста (обычное дело для видео в этой ленте) — заголовок из даты:
    # придумывать подпись вместо автора нельзя, а карточке нужно имя.
    return f"Материал «ЗАРУБА72» от {post.date:%d.%m.%Y}"


def build_draft(post: Post) -> Draft | None:
    """Пост → черновик. `None` — пост не подходит ни под оповещение, ни под медиа."""

    kind = classify(post)
    if kind is None:
        return None
    links = media_links(post.text)
    common: dict[str, Any] = {
        "id": f"vk-{post.id}",
        "kind": kind,
        "post_key": post.key,
        "post_url": post.url,
        "created_at": post.date.isoformat(timespec="seconds"),
        "has_picture": has_picture(post),
    }
    if kind is DraftKind.MEDIA:
        url = links[0] if links else post.url
        platform = platform_for(url)
        if platform is MediaPlatform.OTHER and VIDEO_KINDS & set(post.kinds):
            platform = MediaPlatform.VK_VIDEO
        return Draft(
            **common,
            title=_media_title(post, links),
            text=_clip(_body(post), TEXT_LIMIT),
            link_slug="media",
            urgent=False,
            media_kind=_media_kind(post, links).value,
            platform=platform.value,
            media_url=url,
        )
    return Draft(
        **common,
        title=extract_title(post.text) or _media_title(post, links),
        text=_alert_text(post),
        link_slug=guess_slug(post.text),
        urgent=True,
    )


def section_label(slug: str | None) -> str:
    """`schedule` → «/schedule — Расписание»: владельцу видно, куда ведёт плашка."""

    if not slug:
        return "не определён — плашка будет без ссылки"
    title = SECTION_TITLES.get(slug)
    return f"/{slug} — {title}" if title else f"/{slug}"


def card_body(draft: Draft) -> dict[str, Any]:
    """Сообщение владельцу: что именно уйдёт на сайт и две кнопки решения.

    Кнопки — `callback`: нажатие приходит событием `message_callback`, и бот
    разбирает `payload` (`publish:vk-243`), а не текст. Команды продублированы
    словами в тексте: если клавиатура не отрисуется, владелец наберёт их руками,
    и ничего не потеряется.
    """

    lines = [
        f"Черновик из ВКонтакте: {draft.kind.label}",
        f"Заголовок: {draft.title}",
        f"Текст: {draft.text}",
        f"Раздел: {section_label(draft.link_slug)}",
        f"Оригинал: {draft.post_url}",
    ]
    if draft.has_picture:
        lines.append(f"! {PICTURE_NOTE}")
    lines.append(f"Команды: «Опубликовать {draft.id}» / «Отклонить {draft.id}»")
    buttons = [
        [
            {
                "type": "callback",
                "text": f"Опубликовать {draft.id}",
                "payload": f"publish:{draft.id}",
            }
        ],
        [{"type": "callback", "text": f"Отклонить {draft.id}", "payload": f"reject:{draft.id}"}],
        [{"type": "link", "text": "Открыть пост", "url": draft.post_url}],
    ]
    return {
        "text": "\n".join(lines),
        "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}],
    }


def _row(draft: Draft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "kind": draft.kind.value,
        "status": draft.status.value,
        "title": draft.title,
        "text": draft.text,
        "post_key": draft.post_key,
        "post_url": draft.post_url,
        "created_at": draft.created_at,
        "link_slug": draft.link_slug,
        "urgent": draft.urgent,
        "has_picture": draft.has_picture,
        "media_kind": draft.media_kind,
        "platform": draft.platform,
        "media_url": draft.media_url,
    }


def _from_row(row: Mapping[str, Any], path: Path) -> Draft:
    draft_id = row.get("id")
    if not isinstance(draft_id, str) or not draft_id:
        raise DraftError(f"{path}: у черновика нет id")
    try:
        kind = DraftKind(str(row.get("kind")))
        status = DraftStatus(str(row.get("status") or DraftStatus.PENDING.value))
    except ValueError as exc:
        raise DraftError(f"{path}: черновик {draft_id}: {exc}") from exc
    return Draft(
        id=draft_id,
        kind=kind,
        status=status,
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        post_key=str(row.get("post_key") or ""),
        post_url=str(row.get("post_url") or ""),
        created_at=str(row.get("created_at") or ""),
        link_slug=str(row["link_slug"]) if row.get("link_slug") else None,
        urgent=bool(row.get("urgent", False)),
        has_picture=bool(row.get("has_picture", False)),
        media_kind=str(row["media_kind"]) if row.get("media_kind") else None,
        platform=str(row["platform"]) if row.get("platform") else None,
        media_url=str(row["media_url"]) if row.get("media_url") else None,
    )


def load_drafts(path: Path) -> list[Draft]:
    """Черновики из `logs/vk_drafts.json`; нет файла — пустой список.

    Битый файл — исключение, а не «пусто»: иначе бот молча потерял бы
    неопубликованный пост и сказал владельцу «черновик не найден».
    """

    path = Path(path)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(f"{path}: файл черновиков не читается — {exc}") from exc
    if not isinstance(payload, Mapping):
        raise DraftError(f"{path}: ожидался JSON-объект с полем drafts")
    rows = payload.get("drafts")
    if not isinstance(rows, list):
        raise DraftError(f"{path}: поле drafts должно быть списком")
    return [_from_row(row, path) for row in rows if isinstance(row, Mapping)]


def save_drafts(path: Path, drafts: Sequence[Draft]) -> Path:
    """Пишет файл через `.tmp` и `os.replace`: обрыв не оставит половину JSON."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": DRAFT_VERSION, "drafts": [_row(draft) for draft in drafts]}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def find_draft(drafts: Sequence[Draft], draft_id: str) -> Draft | None:
    return next((draft for draft in drafts if draft.id == draft_id), None)


def upsert_draft(path: Path, draft: Draft) -> Path:
    """Добавляет черновик или заменяет прежний с тем же id.

    Один пост — одна строка: повторный цикл мониторинга не должен плодить
    дубликаты, а ручная правка файла не должна терять историю решений.
    """

    drafts = [item for item in load_drafts(path) if item.id != draft.id]
    drafts.append(draft)
    return save_drafts(path, drafts)


def mark_draft(path: Path, draft_id: str, status: DraftStatus) -> Draft:
    """Меняет статус черновика: `pending` → `published` или `rejected`."""

    drafts = load_drafts(path)
    for index, draft in enumerate(drafts):
        if draft.id != draft_id:
            continue
        updated = replace(draft, status=status)
        drafts[index] = updated
        save_drafts(path, drafts)
        return updated
    raise DraftError(f"черновика {draft_id} нет в {path}")
