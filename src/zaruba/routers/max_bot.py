"""Webhook бота MAX (`docs/09` §9.8): принять `Update` и ответить ссылкой.

Роутер только проводит запрос: проверил секрет, разобрал JSON, отдал решение
сервису. Логики «что ответить» здесь нет — она в `services/max_bot.py`, и её
можно проверить без клиента.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, Response

from ..config import Settings
from ..content import ContentError, load_latest
from ..services import max_bot

logger = logging.getLogger("zaruba.max_bot")

router = APIRouter()

# Заголовок, которым MAX доказывает, что запрос пришёл от платформы: значение
# задаётся при создании подписки (`POST /subscriptions`, поле `secret`).
MAX_SECRET_HEADER = "X-Max-Bot-Api-Secret"


@router.post("/max/webhook", include_in_schema=False)
async def max_webhook(request: Request, background: BackgroundTasks) -> Response:
    """Событие MAX → сообщение со ссылкой на раздел сайта.

    Ответ всегда 200, когда запрос подлинный: MAX считает ошибкой любой другой
    код, повторяет доставку 10 раз и через 8 часов молчания отписывается.
    Поэтому мусор в теле — тоже 200, а сбой отправки виден только в журнале.
    """

    settings = request.app.state.settings
    if not settings.max_bot_enabled:
        # Ни токена, ни секрета — значит, бота нет: адрес для внешнего мира
        # не существует, и страницу сайта на 404 тоже не показываем.
        return Response(status_code=404)

    given = request.headers.get(MAX_SECRET_HEADER, "")
    # Сравниваем байты: `compare_digest` на строках падает на не-ASCII, а чужой
    # заголовок с кириллицей — обычная попытка подобрать секрет.
    if not secrets.compare_digest(given.encode("utf-8"), settings.max_bot_secret.encode("utf-8")):
        logger.warning("MAX: webhook с чужим %s", MAX_SECRET_HEADER)
        return Response(status_code=403)

    raw = await request.body()
    try:
        update: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("MAX: тело webhook не разобрано: %s", exc)
        return JSONResponse({"ok": True})
    if not isinstance(update, dict):
        return JSONResponse({"ok": True})

    plan = max_bot.plan_reply(update, settings, **_content(settings))
    if plan is not None:
        # После ответа: MAX должен получить 200 быстрее, чем сеть ответит.
        background.add_task(max_bot.post_message, settings, plan.chat_id, plan.body)
    return JSONResponse({"ok": True})


def _content(settings: Settings) -> dict[str, Any]:
    """Свежий выпуск и площадка для ответа; битый контент — ответ без данных.

    Webhook обязан ответить 200 даже при сломанном YAML: иначе MAX повторит
    доставку десять раз и отпишется. Поэтому ошибка контента — журнал и общая
    фраза, а не 500.
    """

    try:
        bundle = load_latest(settings)
    except ContentError as exc:
        logger.warning("MAX: контент не прочитан, ответ без данных: %s", exc)
        return {}
    return {"edition": bundle.edition, "venue": bundle.venue}
