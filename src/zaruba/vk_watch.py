"""Мониторинг постов сообщества «ЗАРУБА72» с уведомлением в MAX (`docs/09` §9.13).

    python -m zaruba.vk_watch --init            # запомнить хвост, ничего не отправляя
    python -m zaruba.vk_watch --once            # один цикл проверки
    python -m zaruba.vk_watch --loop            # проверять до Ctrl+C

Почему лог `owner_id_id`, а не «последняя дата»: закреплённый пост висит в
начале ленты месяцами со старой датой, и сравнение по дате отправило бы его
повторно после каждого перезапуска. Ключ поста такой ошибки не даёт.

Уведомление ничего не публикует на сайте: текст поста — цитата со ссылкой на
первоисточник. Перенос фактов в `content/` остаётся за человеком (AGENTS.md).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .services import max_bot, vk
from .services.vk import VkError

logger = logging.getLogger("zaruba.vk_watch")

LOG_VERSION = 1


class WatchError(Exception):
    """Ошибка мониторинга: сообщение называет файл или переменную окружения."""


@dataclass(frozen=True)
class Report:
    """Итог цикла: что ушло, что нет и сколько постов отправки не требовали."""

    sent: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: int = 0


def load_seen(path: Path) -> dict[str, str]:
    """`пост → момент отправки`. Нет файла — ничего не отправляли."""

    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchError(f"{path}: файл состояния не читается — {exc}") from exc
    if not isinstance(payload, Mapping):
        raise WatchError(f"{path}: ожидался JSON-объект с полем seen")
    seen = payload.get("seen")
    if not isinstance(seen, Mapping):
        raise WatchError(f"{path}: поле seen должно быть объектом «пост → время»")
    return {str(key): str(value) for key, value in seen.items()}


def save_seen(path: Path, seen: Mapping[str, str]) -> Path:
    """Пишет лог через `.tmp`: обрыв записи не оставит половину JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": LOG_VERSION, "seen": dict(sorted(seen.items()))}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def remember_head(
    settings: Settings,
    *,
    seen_path: Path | None = None,
    opener: vk.Opener | None = None,
    now: datetime | None = None,
) -> int:
    """Запомнить текущий хвост ленты без отправки — первый запуск.

    Без этого первый же цикл разослал бы все последние посты разом: лог пуст,
    а в ленте десяток записей.
    """

    path = seen_path or settings.vk_seen_file
    seen = load_seen(path)
    posts = vk.fetch_posts(settings, opener=opener)
    moment = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    for post in posts:
        seen.setdefault(post.key, moment)
    if posts:
        save_seen(path, seen)
    return len(posts)


def check_once(
    settings: Settings,
    *,
    seen_path: Path | None = None,
    dry_run: bool = False,
    poster: Callable[[Settings, int, Mapping[str, Any]], bool] | None = None,
    opener: vk.Opener | None = None,
    now: datetime | None = None,
) -> Report:
    """Один цикл: прочитать ленту, отправить новое, запомнить отправленное.

    Лог пишется сразу после каждого сообщения, а не в конце: если прогон упадёт
    на третьем посте, первые два не уйдут повторно.
    """

    path = seen_path or settings.vk_seen_file
    seen = load_seen(path)
    posts = vk.fetch_posts(settings, opener=opener)
    queue = vk.unseen(posts, seen)
    untouched = len(posts) - len(queue)
    if not queue:
        return Report(skipped=untouched)
    if dry_run:
        # Пробный прогон не требует chat_id: его запускают до настройки
        # секретов, чтобы увидеть очередь глазами.
        for post in queue:
            head = vk.message_text(post).splitlines()[1]
            print(f"[dry-run] {post.key}: {head[:80]}")
        return Report(skipped=untouched)
    chat_id = settings.vk_chat
    if chat_id is None:
        raise WatchError(
            "ZARUBA_VK_CHAT_ID и ZARUBA_MAX_ALERT_CHAT_ID не заданы — некуда отправлять; "
            "укажите chat_id чата или запустите с --dry-run"
        )

    post_message = poster or max_bot.post_message
    moment = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    done: list[str] = []
    failed: list[str] = []
    for post in queue:
        if not post_message(settings, chat_id, vk.notification_body(post, settings)):
            failed.append(post.key)
            continue
        done.append(post.key)
        seen[post.key] = moment
        save_seen(path, seen)
    return Report(sent=tuple(done), failed=tuple(failed), skipped=untouched)


def watch_forever(
    settings: Settings,
    *,
    interval: int | None = None,
    cycles: int | None = None,
    seen_path: Path | None = None,
    poster: Callable[[Settings, int, Mapping[str, Any]], bool] | None = None,
    opener: vk.Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> None:
    """Цикл проверки: `cycles` ограничивает его для теста, `None` — до Ctrl+C.

    Ошибка API не убивает цикл: лог остаётся на месте, и следующий опрос
    дошлёт то же самое. Иначе разовый сбой VK требовал бы человека.
    """

    pause = settings.vk_interval if interval is None else interval
    # Строка на старте: без неё пустой `vk_watch.log` не отличить от «процесс не
    # поднялся», а именно по нему проверяют, что мониторинг жив (docs/09 §9.13).
    logger.info(
        "мониторинг VK: старт, сообщество %s, период %s с, чат %s",
        settings.vk_group,
        pause,
        settings.vk_chat,
    )
    while cycles is None or cycles > 0:
        try:
            report = check_once(
                settings, seen_path=seen_path, poster=poster, opener=opener, now=now
            )
        except (WatchError, VkError) as exc:
            logger.warning("мониторинг VK: %s", exc)
            report = Report()
        if report.sent:
            logger.info("новых постов отправлено: %s", len(report.sent))
        if cycles is not None:
            cycles -= 1
        if cycles == 0:
            break
        sleep(pause)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.vk_watch",
        description="Посты сообщества «ЗАРУБА72» → уведомление в MAX",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--init", action="store_true", help="запомнить текущий хвост ленты и выйти")
    action.add_argument("--once", action="store_true", help="один цикл проверки")
    action.add_argument("--loop", action="store_true", help="проверять до Ctrl+C")
    parser.add_argument(
        "--interval", type=int, default=None, help="период опроса, с (по умолчанию 60)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="показать новые посты и не отправлять"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.vk_token:
        # Не настроено — нормальное состояние форка: сайт важнее мониторинга,
        # поэтому команда не падает, а честно пишет, что пропущена.
        print("МОНИТОРИНГ VK: ZARUBA_VK_TOKEN не задан — проверка пропущена")
        return 0
    try:
        if args.init:
            count = remember_head(settings)
            print(f"OK  запомнено постов: {count}; лог: {settings.vk_seen_file}")
            return 0
        if args.loop and settings.vk_chat is None and not args.dry_run:
            # Цикл без адресата писал бы одну и ту же ошибку раз в минуту:
            # лучше сказать это сразу и не запускаться.
            raise WatchError(
                "ZARUBA_VK_CHAT_ID и ZARUBA_MAX_ALERT_CHAT_ID не заданы — некуда отправлять"
            )
        if args.once:
            report = check_once(settings, dry_run=args.dry_run)
            print(
                f"OK  проверка: отправлено {len(report.sent)}, не доставлено {len(report.failed)}"
            )
            if report.failed:
                print(
                    f"ОШИБКА: не доставлены {list(report.failed)} — повтор при следующем запуске",
                    file=sys.stderr,
                )
                return 1
            return 0
        watch_forever(settings, interval=args.interval)
    except (WatchError, VkError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("остановлено: Ctrl+C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
