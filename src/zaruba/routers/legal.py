"""Служебные страницы: `/about`, `/rules`, `/privacy`, `/takedown` (`docs/06` §6.2 п.18).

Это не «юридические страницы для галочки». §5.8 обещает удаление данных
за 48 часов, а §6.3 — ссылки на эти четыре страницы в футере. Пока обещание
не подкреплено страницей, обещания не существует.

Тексты намеренно короткие: зритель должен понять, что мы делаем, за один экран.
Ни одной формы здесь нет — заявку нужно хранить и читать, а модератора у проекта
пока нет; поэтому запросы принимает сообщество.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..content import load_latest
from ..templating import render

router = APIRouter()

# Дата правки служебных текстов: меняется вместе с текстом, а не «сегодня».
LEGAL_UPDATED = date(2026, 10, 23)
CONTACT_URL = "https://vk.com/zaruba72champ"


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request) -> HTMLResponse:
    bundle = load_latest(request.app.state.settings)

    return render(
        request,
        "pages/legal/about.html",
        bundle=bundle,
        edition=bundle.edition,
        venue=bundle.venue,
        contact=CONTACT_URL,
        page_title="О сайте",
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request) -> HTMLResponse:
    """Регламент выпуска — из «Положения»; правила сайта — наши, отдельным списком."""

    bundle = load_latest(request.app.state.settings)

    return render(
        request,
        "pages/legal/rules.html",
        bundle=bundle,
        edition=bundle.edition,
        contact=CONTACT_URL,
        page_title="Правила",
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "pages/legal/privacy.html",
        updated=LEGAL_UPDATED,
        contact=CONTACT_URL,
        page_title="Данные и приватность",
    )


@router.get("/takedown", response_class=HTMLResponse)
def takedown_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "pages/legal/takedown.html",
        updated=LEGAL_UPDATED,
        contact=CONTACT_URL,
        page_title="Удаление данных",
    )
