"""Инварианты CSS, на которых держится липкая шапка (`docs/06` §6.3, §6.20).

Глазами в CI вёрстку не проверить, поэтому здесь зафиксированы ровно те правила,
которые уже ломались: меню переносилось в несколько строк и растило шапку,
а смещения липких элементов были посчитаны «на глаз» вместо общего токена.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE_CSS = Path(__file__).resolve().parents[1] / "src" / "zaruba" / "static" / "style.css"


def _css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> list[str]:
    """Тела правил с точным селектором: `.nav` не цепляет `.nav a`."""

    pattern = re.compile(r"(?<![\w.-])" + re.escape(selector) + r"\s*\{([^}]*)\}")
    return pattern.findall(css)


def test_header_height_is_a_token() -> None:
    """Высота шапки — токен, и шапка действительно такой высоты."""

    css = _css()

    assert re.search(r"--header-h:\s*\d+px", css), "нет токена --header-h"
    assert "var(--header-h)" in _rule(css, ".header-inner")[0], "шапка не следует токену"
    assert "border-bottom" not in _rule(css, ".site-header")[0], (
        "рамка добавляет 1 px к высоте: --header-h перестал бы совпадать с шапкой"
    )


def test_sticky_offsets_use_header_height() -> None:
    """Липкие табы дней встают ровно под шапкой, без магических чисел."""

    css = _css()

    assert "top: var(--header-h)" in _rule(css, ".day-tabs")[0]
    for literal in ("top: 60px", "top: 104px"):
        assert literal not in css, f"{literal} — копия высоты шапки, разъедется при правке"


def test_nav_scrolls_instead_of_wrapping() -> None:
    """Девять пунктов в строку: перенос растил шапку и накрывал липкие табы (§6.3)."""

    css = _css()
    rules = _rule(css, ".nav")

    assert rules, "правило .nav не найдено"
    assert "flex-wrap: nowrap" in rules[0], "меню должно идти одной строкой"
    assert "overflow-x: auto" in rules[0], "на узких экранах меню прокручивается (§6.3)"
    for body in rules:
        assert "flex-wrap: wrap" not in body, "меню снова переносится и растит шапку"


def _media_block(css: str, query: str) -> str:
    """Тело `@media`-блока с вложенными правилами (счёт фигурных скобок)."""

    start = css.index(f"@media ({query}")
    body_start = css.index("{", start)
    depth = 0
    for index in range(body_start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[body_start + 1 : index]
    raise AssertionError(f"не закрыт @media ({query}")


def test_anchor_offsets_have_no_bare_pixels() -> None:
    """Якоря считаются от `--header-h`: число в пикселях разъедется с шапкой."""

    css = _css()

    for prop in ("scroll-padding-top", "scroll-margin-top"):
        values = re.findall(prop + r":\s*([^;}]+)", css)
        assert values, f"нет ни одного {prop}"
        for value in values:
            assert "var(" in value, f"{prop}: {value} — не привязан к токену"
            assert not re.search(r"\d+px", value), f"{prop}: {value} — захардкоженный пиксель"

    assert "scroll-padding-top" in _rule(css, "html")[0], "отступ якорей должен быть на html"


def test_schedule_blocks_clear_sticky_day_tabs() -> None:
    """На расписании над блоком ещё и липкие табы: их высота — тоже токен."""

    css = _css()

    assert re.search(r"--day-tabs-h:\s*\d+px", css), "нет токена --day-tabs-h"
    rules = _rule(css, ".day .block")
    assert rules, "нет правила для блоков расписания"
    assert "scroll-margin-top: var(--day-tabs-h)" in rules[0]


def test_smooth_scroll_respects_reduced_motion() -> None:
    """Плавный скролл — это движение: при `prefers-reduced-motion` он выключается."""

    css = _css()

    assert "scroll-behavior: smooth" in _rule(css, "html")[0]
    reduced = _media_block(css, "prefers-reduced-motion: reduce")
    assert "scroll-behavior: auto" in reduced, "плавность не отключается для reduced-motion"
