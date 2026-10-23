"""Человеческие объяснения ошибок запроса.

FastAPI по умолчанию отвечает на неверный query-параметр JSON-ом
`{"detail": [{"msg": "Input should be less than or equal to 3", ...}]}`.
Для сайта это тупик: человек видит машинный текст, а страница «ломается».
Здесь ошибки pydantic переводятся в пары «параметр — что не так», а
`pages/422.html` показывает их обычной страницей с выходом (§6.21).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi.exceptions import RequestValidationError

# Подсказки по типу ошибки — на случай, когда в `ctx` нет чисел и границ.
HINTS: tuple[tuple[str, str], ...] = (
    ("int_parsing", "нужно целое число"),
    ("int_type", "нужно целое число"),
    ("bool_parsing", "нужно true или false"),
    ("bool_type", "нужно true или false"),
    ("missing", "параметр обязателен"),
    ("enum", "такого значения нет"),
    ("literal_error", "такого значения нет"),
    ("string_too_long", "слишком длинное значение"),
    ("string_type", "нужно текстовое значение"),
)

MAX_EXPECTED = 160


def _human_list(raw: str) -> str:
    """`"'3-6', '18+' or 'any'"` → «3-6, 18+ или any»: без машинных кавычек и `or`."""

    parts = [part.strip().strip("'\"") for part in re.split(r",\s*|\s+or\s+", raw)]
    parts = [part for part in parts if part]
    if not parts:
        return raw
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} или {parts[-1]}"


def _message(error: dict[str, Any]) -> str:
    """Одна ошибка → фраза, которую не стыдно показать посетителю."""

    kind = str(error.get("type", ""))
    ctx: dict[str, Any] = error.get("ctx") or {}

    expected = ctx.get("expected")
    if expected:
        text = _human_list(str(expected))
        if len(text) > MAX_EXPECTED:
            text = text[: MAX_EXPECTED - 1] + "…"
        return f"допустимые значения: {text}"
    if "ge" in ctx and "le" in ctx:
        return f"допустимо от {ctx['ge']} до {ctx['le']}"
    if "le" in ctx:
        return f"допустимо не больше {ctx['le']}"
    if "ge" in ctx:
        return f"допустимо не меньше {ctx['ge']}"
    if "lt" in ctx:
        return f"должно быть меньше {ctx['lt']}"
    if "gt" in ctx:
        return f"должно быть больше {ctx['gt']}"
    if "max_length" in ctx:
        return f"не длиннее {ctx['max_length']} символов"
    if "min_length" in ctx:
        return f"не короче {ctx['min_length']} символов"

    for prefix, hint in HINTS:
        if kind == prefix or kind.startswith(f"{prefix}_"):
            return hint
    return "значение не подходит"


def query_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    """Список ошибок без технического шума: только имя параметра и причина."""

    seen: set[tuple[str, str]] = set()
    items: list[dict[str, str]] = []
    for error in exc.errors():
        parts = [str(part) for part in error.get("loc", ()) if part != "query"]
        param = " → ".join(parts) or "запрос"
        message = _message(error)
        if (param, message) in seen:
            continue
        seen.add((param, message))
        items.append({"param": param, "message": message})
    return items
