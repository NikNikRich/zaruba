"""Рассылка срочных оповещений в MAX (`docs/09` §9.9).

    python -m zaruba.max_notify --send-alerts

Строки листа `alerts` со статусом `send` уходят в чат или канал
`ZARUBA_MAX_ALERT_CHAT_ID` с кнопкой запуска мини-приложения. Повторной отправки
нет: `id` каждой ушедшей строки лежит в `content/editions/<год>.d/sent_alerts.json`,
а сам лог коммитится workflow — иначе новый запуск CI начал бы с чистого листа.

Почему лог, а не «поле в таблице»: `sent_at` пришлось бы писать обратно в Google
Таблицу, то есть давать CI право записи в таблицу владельца. Лог в репозитории
дешевле и виден в истории.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .content import ContentError, load_alerts, load_latest, overlay_dir
from .models import Alert
from .services import max_bot

SENT_LOG = "sent_alerts.json"
LOG_VERSION = 1


class NotifyError(Exception):
    """Ошибка рассылки: сообщение называет файл или переменную окружения."""


@dataclass(frozen=True)
class Report:
    """Итог прогона: что ушло, что нет и сколько строк отправки не требовали."""

    sent: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: int = 0


def sent_log_path(settings: Settings, year: int) -> Path:
    """`content/editions/2026.d/sent_alerts.json` — рядом с overlay выпуска."""

    return overlay_dir(Path(settings.content_dir) / f"{year}.yaml") / SENT_LOG


def load_log(path: Path) -> dict[str, str]:
    """`id → момент отправки`. Нет файла — ничего не отправляли."""

    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotifyError(f"{path}: лог отправленных не читается — {exc}") from exc
    if not isinstance(payload, Mapping):
        raise NotifyError(f"{path}: ожидался JSON-объект с полем sent")
    sent = payload.get("sent")
    if not isinstance(sent, Mapping):
        raise NotifyError(f"{path}: поле sent должно быть объектом «id → время»")
    return {str(key): str(value) for key, value in sent.items()}


def save_log(path: Path, sent: Mapping[str, str]) -> Path:
    """Пишет лог через `.tmp`: обрыв записи не оставит половину JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": LOG_VERSION, "sent": dict(sorted(sent.items()))}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def pending(alerts: Sequence[Alert], sent: Mapping[str, str]) -> list[Alert]:
    """Опубликованные строки, которых нет в логе: черновик не уходит никогда."""

    return [item for item in alerts if item.is_published and item.id not in sent]


def alert_body(alert: Alert, settings: Settings) -> dict[str, Any]:
    """Сообщение для чата: заголовок, текст и кнопка на страницу или мини-приложение."""

    return max_bot.message_body(f"{alert.title}\n{alert.text}", alert.link_slug or "", settings)


def send_alerts(
    settings: Settings,
    alerts: Sequence[Alert],
    log_path: Path,
    *,
    dry_run: bool = False,
    poster: Callable[[Settings, int, Mapping[str, Any]], bool] | None = None,
    now: datetime | None = None,
) -> Report:
    """Отправляет то, чего нет в логе, и пишет лог сразу после каждого успеха.

    Запись после каждого сообщения, а не в конце: если прогон упадёт на третьем
    оповещении, первые два всё равно не уйдут повторно.
    """

    sent = load_log(log_path)
    queue = pending(alerts, sent)
    untouched = len(alerts) - len(queue)
    if not queue:
        return Report(skipped=untouched)
    if dry_run:
        # Пробный прогон не требует chat_id: его запускают до настройки секретов,
        # чтобы увидеть очередь глазами.
        for alert in queue:
            head = alert_body(alert, settings)["text"].splitlines()[0]
            print(f"[dry-run] {alert.id}: {head}")
        return Report(skipped=untouched)
    chat_id = settings.max_alert_chat_id
    if chat_id is None:
        raise NotifyError(
            "ZARUBA_MAX_ALERT_CHAT_ID не задан — некуда отправлять оповещения; "
            "укажите chat_id канала или запустите с --dry-run"
        )

    post = poster or max_bot.post_message
    moment = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    done: list[str] = []
    failed: list[str] = []
    for alert in queue:
        if not post(settings, chat_id, alert_body(alert, settings)):
            failed.append(alert.id)
            continue
        done.append(alert.id)
        sent[alert.id] = moment
        save_log(log_path, sent)
    return Report(sent=tuple(done), failed=tuple(failed), skipped=untouched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zaruba.max_notify",
        description="Срочные оповещения из content/alerts.yaml → MAX",
    )
    parser.add_argument(
        "--send-alerts",
        action="store_true",
        help="отправить все строки со статусом send, которых нет в логе",
    )
    parser.add_argument("--dry-run", action="store_true", help="показать очередь и не отправлять")
    args = parser.parse_args(argv)
    if not args.send_alerts:
        parser.error("нужен --send-alerts: без флага команда ничего не делает")

    settings = get_settings()
    if not settings.max_bot_token:
        # Бот не настроен — нормальное состояние форка: сборка страниц важнее
        # рассылки, поэтому шаг CI не падает, а честно пишет, что пропущен.
        print("ОПОВЕЩЕНИЯ: ZARUBA_MAX_BOT_TOKEN не задан — рассылка пропущена")
        return 0
    try:
        alerts = load_alerts(settings)
        year = load_latest(settings).year
        log_path = sent_log_path(settings, year)
        report = send_alerts(settings, alerts, log_path, dry_run=args.dry_run)
    except (NotifyError, ContentError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(f"OK  оповещения: отправлено {len(report.sent)}, не доставлено {len(report.failed)}")
    if report.sent:
        print(f"OK  лог: {log_path}")
    if report.failed:
        print(
            f"ОШИБКА: не доставлены {list(report.failed)} — повтор при следующем запуске",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
