"""CLI бота MAX: long polling без вебхука и белого IP (`docs/09` §9.10).

    .venv\\Scripts\\python.exe -m zaruba.max_bot --polling

Бот нужен в день чемпионата, а VPS с белым IP стоит денег, которых у фан-сайта
нет. Поэтому основной режим — опрос `GET /updates`: его можно запустить с любого
ПК, и MAX сам отдаёт события. Вебхук `/max/webhook` остаётся рабочим: переезд на
сервер не требует правок, только новой подписки.

Состояние — `marker` в памяти процесса. Без него MAX отдаёт только последнее
событие, то есть после перезапуска человек может получить один повторный ответ.
Хранилище на диске ради этого не заводим: файл на ноутбуке переживёт не дольше
процесса, а база — это уже другой проект.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import date

from .config import Settings, get_settings
from .content import ContentError, load_latest
from .models import Edition, Venue
from .services import max_bot as bot

logger = logging.getLogger("zaruba.max_bot")


def context(settings: Settings) -> tuple[Edition | None, Venue | None]:
    """Свежий выпуск для ответов; битый контент — ответы без данных, а не падение."""

    try:
        bundle = load_latest(settings)
    except ContentError as exc:
        logger.warning("контент не прочитан, ответы пойдут без данных: %s", exc)
        return None, None
    return bundle.edition, bundle.venue


def poll_once(
    settings: Settings,
    marker: str | None = None,
    *,
    timeout: float = bot.LONG_POLL_TIMEOUT,
    opener: bot.Opener | None = None,
    today: date | None = None,
) -> str | None:
    """Один запрос событий: отвечает на них и возвращает новый `marker`."""

    payload = bot.get_updates(settings, marker, timeout=timeout, opener=opener)
    fresh = payload.get("marker")
    if isinstance(fresh, (int, str)) and str(fresh):
        marker = str(fresh)
    edition, venue = context(settings)
    sent = bot.reply_to(settings, payload, edition=edition, venue=venue, today=today)
    if sent:
        logger.info("ответов отправлено: %s, marker %s", sent, marker)
    return marker


def poll_forever(
    settings: Settings,
    marker: str | None = None,
    *,
    timeout: float = bot.LONG_POLL_TIMEOUT,
    pause: float = bot.POLL_PAUSE,
    cycles: int | None = None,
    opener: bot.Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
    today: date | None = None,
) -> str | None:
    """Цикл опроса: `cycles` ограничивает его для теста, `None` — до Ctrl+C."""

    while cycles is None or cycles > 0:
        marker = poll_once(settings, marker, timeout=timeout, opener=opener, today=today)
        if cycles is not None:
            cycles -= 1
        if cycles == 0:
            break
        sleep(pause)
    return marker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.max_bot",
        description="Бот-проводник MAX: long polling вместо вебхука",
    )
    parser.add_argument("--polling", action="store_true", help="опрашивать события до Ctrl+C")
    parser.add_argument(
        "--once",
        action="store_true",
        help="один запрос событий и выход: проверка токена и сертификата",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=bot.LONG_POLL_TIMEOUT,
        help=f"сколько сервер держит запрос, с (по умолчанию {bot.LONG_POLL_TIMEOUT:g})",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=bot.POLL_PAUSE,
        help=f"пауза между запросами, с (по умолчанию {bot.POLL_PAUSE:g})",
    )
    args = parser.parse_args(argv)
    if not (args.polling or args.once):
        parser.error("нужен --polling или --once: без флага команда работает вхолостую")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.max_bot_token:
        print("ОШИБКА: ZARUBA_MAX_BOT_TOKEN не задан — опрос невозможен", file=sys.stderr)
        return 1
    if settings.max_bot_ca_file is not None and not settings.max_bot_ca_file.is_file():
        # Проверяем до сети: иначе каждая итерация падала бы на TLS с общим текстом.
        print(f"ОШИБКА: {settings.max_bot_ca_file}: файл сертификата не найден", file=sys.stderr)
        return 1

    try:
        if args.once:
            marker = poll_once(settings, timeout=args.timeout)
            print(f"OK  опрос выполнен, marker: {marker}")
            return 0
        poll_forever(settings, timeout=args.timeout, pause=args.pause)
    except KeyboardInterrupt:
        print("остановлено: Ctrl+C")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
