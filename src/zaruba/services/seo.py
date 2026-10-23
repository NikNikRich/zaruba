"""SEO-слой: JSON-LD, `sitemap.xml` и `robots.txt` (`docs/06` §6.19).

Правила, которые здесь зашиты:

* в sitemap попадают только подтверждённые и разрешённые страницы — демо-записи
  и карточки несовершеннолетних без согласия остаются за бортом;
* `Event` не выдумывает время: если тайминга блоков нет, `startDate` — дата,
  а не «12:00» из примера в документации;
* `Person` не генерируется ни для кого: согласие на микроразметку мы не собираем.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from ..models import Edition, EditionBundle, FaqItem, GlossaryEntry, Participant
from .edition import catalogue_bundle

SITE_NAME = "ЗАРУБА · Тюмень"
SITE_URL_VK = "https://vk.com/zaruba72champ"

# Страницы первого уровня: путь, приоритет, частота изменений.
STATIC_PAGES: tuple[tuple[str, str, str], ...] = (
    ("/", "1.0", "weekly"),
    ("/event", "0.8", "monthly"),
    ("/history", "0.7", "yearly"),
    ("/schedule", "0.9", "weekly"),
    ("/performances", "0.9", "daily"),
    ("/participants", "0.9", "weekly"),
    ("/studios", "0.8", "monthly"),
    ("/judges", "0.7", "monthly"),
    ("/tickets", "0.8", "weekly"),
    ("/glossary", "0.6", "monthly"),
    ("/faq", "0.7", "monthly"),
    ("/venue", "0.7", "monthly"),
)


def absolute(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def json_ld_script(data: dict[str, Any]) -> str:
    """JSON одной строкой и без `</script>` внутри — иначе тег закроется раньше."""

    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def breadcrumbs_json_ld(
    base_url: str, path: str, title: str | None, links: Iterable[dict[str, str]]
) -> dict[str, Any] | None:
    """«Главная → раздел → страница». Для главной крошек нет."""

    path = path or "/"
    if path == "/":
        return None

    labels = {link["url"]: link["label"] for link in links}
    parts = [part for part in path.split("/") if part]
    items: list[tuple[str, str]] = [("Главная", "/")]
    if len(parts) > 1:
        section = f"/{parts[0]}"
        if section in labels:
            items.append((labels[section], section))
    items.append((title or labels.get(path) or parts[-1], path))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, item_path in items:
        if item_path in seen:
            continue
        seen.add(item_path)
        unique.append((name, item_path))

    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": position,
                "name": name,
                "item": absolute(base_url, item_path),
            }
            for position, (name, item_path) in enumerate(unique, start=1)
        ],
    }


def _event_period(edition: Edition) -> tuple[str, str]:
    """Даты выпуска; время — только если организаторы его опубликовали."""

    first_day = [block for block in edition.blocks if block.day == 1]
    last_day = [block for block in edition.blocks if block.day == len(edition.dates)]
    if first_day and last_day:
        zone = ZoneInfo(edition.timezone)
        starts: time = min(block.starts_at for block in first_day)
        ends: time = max(block.ends_at for block in last_day)
        start = datetime.combine(edition.dates[0], starts, tzinfo=zone)
        end = datetime.combine(edition.dates[-1], ends, tzinfo=zone)
        return start.isoformat(), end.isoformat()
    return edition.dates[0].isoformat(), edition.dates[-1].isoformat()


def event_json_ld(
    bundle: EditionBundle, base_url: str, today: date | None = None
) -> dict[str, Any]:
    """`Event` для выпуска: даты, площадка и ссылка на билеты (§6.19)."""

    edition = bundle.edition
    venue = bundle.venue
    today = today or date.today()
    start_date, end_date = _event_period(edition)

    location: dict[str, Any] = {
        "@type": "Place",
        "name": venue.name,
        "address": {
            "@type": "PostalAddress",
            "addressLocality": venue.city,
            "streetAddress": venue.address,
            "addressCountry": "RU",
        },
    }
    if venue.map_url:
        location["hasMap"] = venue.map_url

    description = f"{edition.title} — чемпионат по современным танцам, {venue.city}."
    if edition.note:
        description = f"{description} {edition.note}"

    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": f"{edition.title} — чемпионат по современным танцам",
        "description": description,
        "url": absolute(base_url, "/event"),
        "startDate": start_date,
        "endDate": end_date,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": location,
        "inLanguage": "ru-RU",
    }

    if edition.organizers:
        data["organizer"] = [{"@type": "Organization", "name": name} for name in edition.organizers]

    if edition.tickets:
        # Сайт не продаёт билеты: offer ведёт на страницу с ценами и официальной ссылкой.
        prices = [ticket.price for ticket in edition.tickets]
        data["offers"] = {
            "@type": "Offer",
            "url": absolute(base_url, "/tickets"),
            "priceCurrency": "RUB",
            "price": min(prices),
            "availability": (
                "https://schema.org/SoldOut"
                if edition.dates[-1] < today
                else "https://schema.org/InStock"
            ),
            "seller": {
                "@type": "Organization",
                "name": "Организатор «ЗАРУБА»",
                "url": SITE_URL_VK,
            },
        }

    return data


def faq_json_ld(base_url: str, items: Sequence[FaqItem]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "url": absolute(base_url, "/faq"),
        "mainEntity": [
            {
                "@type": "Question",
                "name": item.question,
                "acceptedAnswer": {"@type": "Answer", "text": item.answer},
            }
            for item in items
        ],
    }


def glossary_json_ld(base_url: str, entries: Sequence[GlossaryEntry]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "DefinedTermSet",
        "name": f"Глоссарий {SITE_NAME}",
        "url": absolute(base_url, "/glossary"),
        "hasDefinedTerm": [
            {
                "@type": "DefinedTerm",
                "name": entry.term,
                "description": entry.what,
                "url": absolute(base_url, f"/glossary#{entry.anchor}"),
                "inDefinedTermSet": absolute(base_url, "/glossary"),
            }
            for entry in entries
        ],
    }


def _public_participants(edition: Edition) -> set[str]:
    return {
        participant.slug
        for participant in edition.participants
        if participant.is_public_person and not participant.demo
    }


def _performance_is_public(edition: Edition, public: set[str], slugs: list[str]) -> bool:
    participants: dict[str, Participant] = edition.participant_index
    return all(slug in public and not participants[slug].demo for slug in slugs)


def sitemap_entries(bundles: Sequence[EditionBundle]) -> list[dict[str, str]]:
    """Только подтверждённые и разрешённые страницы: демо и `noindex` — мимо."""

    if not bundles:
        return []

    edition = bundles[0].edition
    catalog = catalogue_bundle(bundles).edition
    lastmod = edition.updated_at.date().isoformat()

    entries = [
        {"loc": path, "lastmod": lastmod, "changefreq": changefreq, "priority": priority}
        for path, priority, changefreq in STATIC_PAGES
    ]

    public = _public_participants(catalog)
    for participant in catalog.participants:
        if participant.slug in public:
            entries.append(
                {
                    "loc": f"/participants/{participant.slug}",
                    "lastmod": lastmod,
                    "changefreq": "monthly",
                    "priority": "0.6",
                }
            )
    for performance in catalog.performances:
        if performance.demo:
            continue
        if not _performance_is_public(catalog, public, performance.participants):
            continue
        entries.append(
            {
                "loc": f"/performances/{performance.slug}",
                "lastmod": lastmod,
                "changefreq": "monthly",
                "priority": "0.6",
            }
        )
    for studio in catalog.studios:
        if studio.demo:
            continue
        entries.append(
            {
                "loc": f"/studios/{studio.slug}",
                "lastmod": lastmod,
                "changefreq": "monthly",
                "priority": "0.5",
            }
        )

    # Повтор пути недопустим: склеиваем по первому вхождению.
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in entries:
        if entry["loc"] in seen:
            continue
        seen.add(entry["loc"])
        unique.append(entry)
    return unique


def render_sitemap(entries: Sequence[dict[str, str]], base_url: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for entry in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(absolute(base_url, entry['loc']))}</loc>")
        lines.append(f"    <lastmod>{entry['lastmod']}</lastmod>")
        lines.append(f"    <changefreq>{entry['changefreq']}</changefreq>")
        lines.append(f"    <priority>{entry['priority']}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def render_robots(base_url: str) -> str:
    """Фильтры закрыты meta `noindex`, а не `Disallow`: иначе робот не увидит noindex."""

    return "\n".join(
        (
            "# Неофициальный фан-сайт чемпионата «ЗАРУБА».",
            "# Фильтры и пагинация закрыты meta noindex — в robots.txt их нет специально.",
            "User-agent: *",
            "Allow: /",
            "Disallow: /healthz",
            "Disallow: /docs",
            "Disallow: /openapi.json",
            "",
            f"Sitemap: {absolute(base_url, '/sitemap.xml')}",
            "",
        )
    )
