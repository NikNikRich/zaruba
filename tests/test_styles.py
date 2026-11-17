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


def _token(css: str, name: str) -> str:
    """HEX-значение токена: `--bg` не должен совпасть с `--bg2`."""

    match = re.search(re.escape(name) + r":\s*(#[0-9a-fA-F]{6})", css)
    assert match, f"нет токена {name}"
    return match.group(1).lower()


def _luminance(color: str) -> float:
    """Относительная яркость по WCAG 2.x: каналы sRGB, затем линейное значение."""

    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    """Коэффициент контраста WCAG: (L1 + 0.05) / (L2 + 0.05)."""

    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_dim_text_meets_wcag_aa_on_every_surface() -> None:
    """`--dim` — подписи 11 px: на всех трёх фонах нужно ≥ 4.5:1 (WCAG AA)."""

    css = _css()
    dim = _token(css, "--dim")

    for surface in ("--bg", "--bg2", "--bg3"):
        ratio = _contrast(dim, _token(css, surface))
        assert ratio >= 4.5, f"--dim {dim} на {surface}: {ratio:.2f} < 4.5"

    assert _luminance(dim) < _luminance(_token(css, "--ink")), "--dim светлее основного текста"
    assert _luminance(dim) < _luminance(_token(css, "--muted")), (
        "--dim должен оставаться спокойнее --muted, иначе иерархия пропала"
    )


def test_day_tabs_height_token_matches_rule() -> None:
    """`--day-tabs-h` — сумма реальных частей `.day-tabs`, а не отдельное число."""

    css = _css()
    token = re.search(r"--day-tabs-h:\s*(\d+)px", css)
    assert token, "нет токена --day-tabs-h"

    tabs = _rule(css, ".day-tabs")[0]
    padding = re.search(r"padding:\s*(\d+)px\s+[^;]+", tabs)
    border = re.search(r"border-bottom:\s*(\d+)px", tabs)
    link = _rule(css, ".day-tabs a")[0]
    link_height = re.search(r"min-height:\s*(\d+)px", link)
    assert padding and border and link_height, "изменилась запись padding/border/min-height"

    computed = 2 * int(padding.group(1)) + int(border.group(1)) + int(link_height.group(1))
    assert computed == int(token.group(1)), (
        f"высота .day-tabs = {computed}px, а --day-tabs-h = {token.group(1)}px"
    )


def test_wide_tables_scroll_inside_their_wrapper() -> None:
    """5+ колонок в карточки не раскладываются: скролл живёт внутри `.table-wrap`."""

    css = _css()
    wrap = _rule(css, ".table-wrap")[0]
    for prop in (
        "overflow-x: auto",
        "max-width: 100%",
        "scrollbar-width: thin",
        "overscroll-behavior-x: contain",
    ):
        assert prop in wrap, f".table-wrap без `{prop}`"

    wide = _rule(css, ".table--wide")
    assert wide, "нет правила .table--wide"
    assert re.search(r"min-width:\s*\d+px", wide[0]), "широкой таблице нужна min-width"

    # Карточный режим не перехватывает широкие таблицы: пятая ячейка падала
    # в авто-строку сетки, рассчитанной на четыре колонки.
    assert not re.search(r"\.table:not\(\.table--results\)(?!:not\(\.table--wide\))", css)
