"""Mini App MAX (`docs/09`): флаг, CSP, адаптер и компактная шапка.

Проверки держат границу между обычным сайтом и режимом мессенджера: без флага
разметка и заголовки остаются прежними, с флагом появляется ровно то, что
описано в спеке, а не «что-нибудь ещё».
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app
from zaruba.middleware import (
    CSP,
    CSP_MAX_MINIAPP,
    MAX_BRIDGE_ORIGIN,
    security_headers,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "zaruba" / "static"
BRIDGE_JS = STATIC_DIR / "max-bridge.js"
MAX_MINIAPP_CSS = STATIC_DIR / "max-miniapp.css"
BRIDGE_SRC = f"{MAX_BRIDGE_ORIGIN}/js/max-web-app.js"


@contextmanager
def mini_client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(max_miniapp=True))) as test_client:
        yield test_client


def test_env_flag_enables_miniapp(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ZARUBA_MAX_MINIAPP=true` — тот же флаг, что видит CI и Pages."""

    monkeypatch.setenv("ZARUBA_MAX_MINIAPP", "true")

    assert Settings().max_miniapp is True

    monkeypatch.setenv("ZARUBA_MAX_MINIAPP", "false")

    assert Settings().max_miniapp is False


def test_miniapp_is_off_by_default(client: TestClient) -> None:
    """Обычный сайт не должен знать про MAX: ни скрипта, ни политики, ни шапки."""

    page = client.get("/")

    assert BRIDGE_SRC not in page.text
    assert "max-bridge.js" not in page.text
    assert "max-miniapp.css" not in page.text
    assert 'id="max-welcome-modal"' not in page.text
    assert "data-welcome-open" not in page.text
    assert "site-header--embedded" not in page.text
    assert page.headers["content-security-policy"] == CSP
    assert page.headers["x-frame-options"] == "DENY"


def test_miniapp_connects_bridge_and_adapter() -> None:
    with mini_client() as mini:
        html = mini.get("/").text

    assert f'<script src="{BRIDGE_SRC}"></script>' in html
    assert re.search(r"/static/max-bridge\.js\?v=[0-9a-f]{8}", html), html[:800]
    assert 'class="site-header site-header--embedded"' in html


def test_miniapp_hides_the_duplicate_logo_but_keeps_a_home_link() -> None:
    """Нативный заголовок MAX уже называет бота: логотип — дубль, ссылка нужна."""

    with mini_client() as mini:
        html = mini.get("/").text

    assert re.search(r'<a class="brand" href="/" hidden>', html)
    assert re.search(r'<a href="/" aria-current="page">Главная</a>', html)


def test_miniapp_csp_allows_bridge_and_embedding() -> None:
    with mini_client() as mini:
        headers = mini.get("/").headers

    csp = headers["content-security-policy"]

    assert csp == CSP_MAX_MINIAPP
    assert "script-src 'self' https://st.max.ru" in csp
    assert "frame-ancestors 'self' https://max.ru https://*.max.ru" in csp
    assert "x-frame-options" not in headers, "DENY закрыл бы Mini App во фрейме клиента"
    for forbidden in ("unsafe-inline", "unsafe-eval", "default-src *"):
        assert forbidden not in csp, forbidden


def test_adapter_accepts_nested_card_paths() -> None:
    """Бот ведёт на карточку (`participants/ivan-petrov`), а не только на каталог.

    Смотрим на сам шаблон из `max-bridge.js`, а не на копию в тесте: правило
    «одна страница или вложенный путь» живёт в одном месте. Пустой `start_param`,
    заглавные буквы и `../` остаются за бортом — адаптер не должен уводить на
    произвольный файл сборки.
    """

    source = BRIDGE_JS.read_text(encoding="utf-8")
    line = next(item for item in source.splitlines() if ".test(start" in item)
    found = re.search(r"/\^(.*)\$/\.test", line)
    assert found is not None, line

    pattern = re.compile(found.group(1))

    assert pattern.fullmatch("participants/ivan-petrov")
    assert pattern.fullmatch("studios/pechat-tantsa")
    assert pattern.fullmatch("schedule")
    for bad in ("", "../secret", "Schedule", "a" * 70):
        assert not pattern.fullmatch(bad), bad


def test_miniapp_renders_welcome_dialog_hidden_by_default() -> None:
    """Окно есть в разметке, но с `hidden`: без JS экран не перекрыт.

    Ссылки внутри окна — обычные абсолютные `href="/..."`: их переписывает
    сборка `dist/`, а не шаблон (docs/09 §9.11).
    """

    with mini_client() as mini:
        html = mini.get("/").text

    assert 'id="max-welcome-modal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert re.search(r'id="max-welcome-modal"[^>]*hidden', html), "окно не спрятано без JS"
    assert re.search(r"/static/max-miniapp\.css\?v=[0-9a-f]{8}", html), html[:800]
    for text in (
        "Добро пожаловать на ЗАРУБУ 2026!",
        "Следи за таймингом",
        "Ищи своих",
        "Спрашивай бота",
    ):
        assert text in html, text
    assert re.search(r'href="/schedule" data-welcome-close', html)
    assert re.search(r'href="/studios" data-welcome-close', html)
    assert re.search(r'href="/tickets" data-welcome-close', html)
    assert re.search(r'type="button" data-welcome-close>Понятно, закрыть', html)
    assert re.search(r"data-welcome-open>Гид по приложению", html)


def test_welcome_css_is_separate_dark_and_touch_friendly() -> None:
    """Окно живёт в отдельном файле и не тратит бюджет `style.css`."""

    css = MAX_MINIAPP_CSS.read_text(encoding="utf-8")
    card = re.search(r"\.max-welcome__card\s*\{([^}]*)\}", css)

    assert card is not None, "нет правила .max-welcome__card"
    assert "var(--bg2)" in card.group(1), "карточка не в тёмной палитре сайта"
    assert "var(--lime)" in card.group(1), "нет лаймовой рамки"
    assert "var(--r)" in card.group(1), "радиус не совпадает с остальным сайтом"
    assert ".max-welcome[hidden] { display: none; }" in css, "hidden не гасит окно"
    assert re.search(r"min-height:\s*44px", css), "тач-таргет меньше 44 px"
    assert "@media (max-width: 360px)" in css, "нет адаптива под узкий экран"
    assert MAX_MINIAPP_CSS.stat().st_size < 6000, (
        f"max-miniapp.css {MAX_MINIAPP_CSS.stat().st_size} байт — файл перестал быть лёгким"
    )


def test_adapter_shows_welcome_once_and_respects_deeplinks() -> None:
    """Флаг в `localStorage`, `try/catch` и запасной путь через `start_param`."""

    source = BRIDGE_JS.read_text(encoding="utf-8")

    for token in (
        "zaruba_max_welcome_v1",
        "localStorage.getItem",
        "localStorage.setItem",
        "catch (error)",
        "getElementById('max-welcome-modal')",
        "welcome.hidden = false",
        "welcome.hidden = true",
        "[data-welcome-close]",
        "[data-welcome-open]",
    ):
        assert token in source, token


def test_miniapp_changes_only_csp_and_frame_header() -> None:
    """Отличие режима — ровно политика и снятый `X-Frame-Options`."""

    plain = dict(security_headers())
    mini = dict(security_headers(miniapp=True))

    assert set(plain) - {"X-Frame-Options"} == set(mini)
    assert mini["X-Content-Type-Options"] == plain["X-Content-Type-Options"]
    assert mini["Content-Security-Policy"] == CSP_MAX_MINIAPP


def test_adapter_stays_small_and_has_a_fallback() -> None:
    """Адаптер — меньше 90 строк: навигация, диплинк и окно приветствия.

    Порог поднят с 40 вместе с приветственным окном (§9.11): третьей
    обязанности здесь быть не должно, для неё нужен отдельный повод.
    """

    source = BRIDGE_JS.read_text(encoding="utf-8")

    assert len(source.splitlines()) < 90, f"{len(source.splitlines())} строк"
    for token in (
        "app.ready()",
        "BackButton.show()",
        "BackButton.hide()",
        "history.back()",
        "openLink(",
        "platform",
        "start_param",
        r"index\.html",
    ):
        assert token in source, token
    for forbidden in ("eval(", "document.write", "innerHTML"):
        assert forbidden not in source, forbidden
