"""`/robots.txt` и `/sitemap.xml` (`docs/06` §6.19).

Оба файла собираются из контента при каждом запросе: выпуск меняется раз в год,
а кэш на минуту дешевле, чем риск отдать sitemap со снятой страницей.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from ..content import load_site
from ..services.seo import render_robots, render_sitemap, sitemap_entries

router = APIRouter()

CACHE_SECONDS = 3600


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots_txt(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        render_robots(_base_url(request)),
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )


@router.get("/sitemap.xml", response_class=Response, include_in_schema=False)
def sitemap_xml(request: Request) -> Response:
    entries = sitemap_entries(load_site(request.app.state.settings))
    return Response(
        render_sitemap(entries, _base_url(request)),
        media_type="application/xml",
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )
