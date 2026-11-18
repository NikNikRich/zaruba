"""Посты официального сообщества ВКонтакте (`docs/09` §9.13).

HTML-страница сообщества отдаётся капчей, `vk.com/rss.php` отвечает 404
(проверено 18.11.2026), поэтому единственный надёжный источник — `wall.get`
с токеном. Пост читается как есть: текст цитируется со ссылкой на
первоисточник, а картинки не копируются — на чужой кадр нужно отдельное
основание (`docs/06` §6.5).

Токен уходит только в заголовке `Authorization: Bearer`: в query string он
попал бы в текст ошибки `urllib`, а оттуда — в журнал.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..config import Settings

# Типы ответа и точки подмены берём у бота: клиент тот же (stdlib `urllib`),
# и тестовая заглушка одна на оба модуля.
from .max_bot import Opener, Response

logger = logging.getLogger("zaruba.vk")

VK_API = "https://api.vk.com/method"
# Таймаут только на случай зависшей сети: VK отвечает за доли секунды.
OUTBOUND_TIMEOUT = 15.0
# Длина текста в уведомлении: мессенджер — не читалка, а ссылка на пост рядом.
TEXT_LIMIT = 900
SOURCE_LABEL = "ВКонтакте, сообщество «ЗАРУБА72»"


class VkError(Exception):
    """Ошибка VK API: сообщение называет код и текст от сервера."""


@dataclass(frozen=True, slots=True)
class Post:
    """Пост в том объёме, который нужен уведомлению."""

    owner_id: int
    id: int
    date: datetime
    text: str = ""
    # Типы вложений без самих файлов: «видео» объясняет пустой текст, а копия
    # чужого кадра — уже другой разговор про права.
    kinds: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Ключ лога: `owner_id_id`, как в адресе поста."""

        return f"{self.owner_id}_{self.id}"

    @property
    def url(self) -> str:
        return f"https://vk.com/wall{self.key}"


def _kinds(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("attachments")
    if not isinstance(raw, list):
        return ()
    seen: list[str] = []
    for attachment in raw:
        if isinstance(attachment, Mapping):
            kind = attachment.get("type")
            if isinstance(kind, str) and kind not in seen:
                seen.append(kind)
    return tuple(seen)


def parse_posts(payload: Mapping[str, Any]) -> list[Post]:
    """`response.items` → посты. Ошибка API — исключение, а не пустая лента.

    Пустой список означает «новых постов нет», поэтому `error` обязан стать
    исключением: иначе истёкший токен выглядел бы как тишина в эфире.
    """

    error = payload.get("error")
    if isinstance(error, Mapping):
        raise VkError(f"VK API вернул ошибку {error.get('error_code')}: {error.get('error_msg')}")
    response = payload.get("response")
    if not isinstance(response, Mapping):
        raise VkError("в ответе VK нет ни response, ни error")
    items = response.get("items")
    if not isinstance(items, list):
        raise VkError("response.items в ответе VK — не список")

    posts: list[Post] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            owner_id = int(item["owner_id"])
            post_id = int(item["id"])
            date = datetime.fromtimestamp(int(item["date"]), UTC)
        except (KeyError, TypeError, ValueError) as exc:
            raise VkError(f"пост без обязательных полей: {exc}") from exc
        posts.append(
            Post(
                owner_id=owner_id,
                id=post_id,
                date=date,
                text=str(item.get("text") or ""),
                kinds=_kinds(item),
            )
        )
    return posts


def wall_url(settings: Settings, count: int) -> str:
    """Адрес `wall.get`: только посты сообщества, без репостов чужой стены."""

    params = {
        "domain": settings.vk_group,
        "count": str(count),
        "filter": "owner",
        "v": settings.vk_api_version,
    }
    return f"{VK_API}/wall.get?{urllib.parse.urlencode(params)}"


def _ssl_context(settings: Settings) -> ssl.SSLContext:
    """Системные корни плюс сертификат из настроек.

    `load_verify_locations` дополняет набор, а не заменяет его, и проверку
    сертификата мы не отключаем ни при каких настройках — иначе токен уйдёт
    «в никуда».
    """

    context = ssl.create_default_context()
    ca = settings.vk_ca_file or settings.max_bot_ca_file
    if ca is not None:
        if not ca.is_file():
            raise FileNotFoundError(f"{ca}: файл сертификата не найден")
        context.load_verify_locations(cafile=str(ca))
    return context


def _opener(settings: Settings) -> Opener:
    context = _ssl_context(settings)

    def open_(request: urllib.request.Request, timeout: float) -> Response:
        return urllib.request.urlopen(request, timeout=timeout, context=context)

    return open_


def fetch_posts(
    settings: Settings, *, count: int | None = None, opener: Opener | None = None
) -> list[Post]:
    """Свежие посты сообщества. Обрыв сети — пустой список, а не исключение.

    Цикл опроса обязан пережить ночной разрыв связи: пустой ответ оставляет лог
    нетронутым, и следующий цикл отправит то же самое. Ошибку самого API
    (`VkError`) глушить нельзя — истёкший токен иначе не заметить.
    """

    if not settings.vk_token:
        raise VkError("ZARUBA_VK_TOKEN не задан — читать стену нечем")
    limit = settings.vk_posts if count is None else count
    request = urllib.request.Request(
        wall_url(settings, limit),
        method="GET",
        headers={"Authorization": f"Bearer {settings.vk_token}"},
    )
    open_ = opener or _opener(settings)
    try:
        with open_(request, OUTBOUND_TIMEOUT) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("VK: лента не прочитана: %s", exc)
        return []
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise VkError(f"ответ VK — не JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise VkError("ответ VK — не объект JSON")
    return parse_posts(payload)


def unseen(posts: Sequence[Post], seen: Mapping[str, str]) -> list[Post]:
    """Посты, которых нет в логе, — от старых к новым.

    Порядок публикации, а не порядок ответа: закреплённый пост висит первым
    месяцами, и «сверху вниз» читалось бы задом наперёд.
    """

    fresh = [post for post in posts if post.key not in seen]
    return sorted(fresh, key=lambda post: (post.date, post.id))


def message_text(post: Post) -> str:
    """Текст уведомления: пост, а если текста нет — честно об этом.

    Пустой `text` у поста с видео — обычное дело в этой ленте (проверено на
    живом `wall.get`), и «примерно» вместо подписи здесь недопустимо.
    """

    body = " ".join(post.text.split())
    if not body:
        body = "Пост без текста"
        if post.kinds:
            body = f"{body} (вложение: {', '.join(post.kinds)})"
    elif len(body) > TEXT_LIMIT:
        body = f"{body[:TEXT_LIMIT].rsplit(' ', 1)[0]}…"
    return f"Новый пост «ЗАРУБА72»\n{body}\n\n{SOURCE_LABEL}: {post.url}"


def notification_body(post: Post, settings: Settings) -> dict[str, Any]:
    """Сообщение с кнопкой-ссылкой на пост.

    `max_bot.message_body` не подходит: он ведёт на slug страницы сайта, а у
    поста адрес внешний. Форма клавиатуры та же, что у бота, — `link`.
    """

    return {
        "text": message_text(post),
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [[{"type": "link", "text": "Открыть пост", "url": post.url}]]
                },
            }
        ],
    }
