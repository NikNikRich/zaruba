"""Рейтинги и методика: `/ratings` (`docs/06` §6.10, `docs/05` §5.4.6).

Таблиц на сайте нет и быть не может, пока нет источника цифр: официальные
протоколы выпусков не публикуются, а голосование зрителей не открыто.
Поэтому сервис отдаёт то, что можно показать честно, — состав рейтингов,
формулы, ограничения и причины, по которым строк нет. Придумать места
и баллы нельзя: это ровно тот случай, когда пустое состояние честнее графика.

Когда появится механика голосования (§5.4.6), сюда добавятся строки таблиц;
версия методики при этом станет `v2` с новой датой, а не тихо перепишется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import Settings
from ..models import Edition

METHOD_VERSION = "v1"
METHOD_UPDATED = date(2026, 10, 23)

# Константы Эло из §6.10: менять их можно только вместе с версией методики.
ELO_K = 24
ELO_START = 1000
MIN_PERFORMANCES = 3


@dataclass(frozen=True)
class RatingKind:
    """Один рейтинг: что считает, по какой формуле и как часто обновляется."""

    slug: str
    title: str
    measures: str
    formula: str
    updated: str


@dataclass(frozen=True)
class RatingsView:
    """Всё, что нужно странице: статус раздела, методика и причины пустоты."""

    enabled: bool
    kinds: tuple[RatingKind, ...]
    limits: tuple[str, ...]
    blockers: tuple[str, ...]
    version: str
    updated: date


KINDS: tuple[RatingKind, ...] = (
    RatingKind(
        slug="participant",
        title="Фан-рейтинг участника",
        measures="сила по голосам зрителей и победам в дуэлях",
        formula=f"Эло: R + K·(S − E), K = {ELO_K}, старт {ELO_START}, сезон = год",
        updated="раз в сутки",
    ),
    RatingKind(
        slug="studio",
        title="Рейтинг студий",
        measures="глубина состава, а не размер студии",
        formula="медиана топ-3 результатов участников студии",
        updated="после события",
    ),
    RatingKind(
        slug="judges-vs-hall",
        title="«Судьи vs зал»",
        measures="расхождение вкусов зрителей и судей",
        formula="доля категорий, где победитель зала не совпал с победителем судей",
        updated="после события",
    ),
)

LIMITS: tuple[str, ...] = (
    "Возрастные группы не смешиваются: ребёнок 9 лет не встаёт в одну таблицу со взрослым.",
    f"Минимум {MIN_PERFORMANCES} выступления в сезоне — иначе участник в таблицу не попадает.",
    "Рядом с очками — число голосов и ± доверительный интервал: Эло на пяти голосах это шум.",
    "Изменение формулы — новая версия методики с датой, а не тихая правка.",
)


def ratings_view(settings: Settings, edition: Edition) -> RatingsView:
    """Статус раздела и методика; таблиц нет, пока нет источника цифр."""

    blockers: list[str] = []
    if not settings.ratings_enabled:
        blockers.append(
            "Раздел не запущен: рейтинги включаются только вместе с механикой "
            "голосования, иначе на сайте появятся цифры, которых никто не считал."
        )
    if not settings.fan_voting_enabled:
        blockers.append(
            "Голосование зрителей не открыто: счётчика голосов на сайте нет, "
            "а Эло без голосов — это просто число 1000 у каждого."
        )
    blockers.append(
        "Официальные протоколы выпусков не публикуются: места, баллы и «судьи vs зал» "
        "посчитать не из чего — в данных сайта нет ни одного результата."
    )
    if not edition.performances:
        blockers.append(
            "В выпуске нет ни одного выступления: считать нечего, даже если бы "
            "протоколы были опубликованы."
        )

    return RatingsView(
        enabled=settings.ratings_enabled,
        kinds=KINDS,
        limits=LIMITS,
        blockers=tuple(blockers),
        version=METHOD_VERSION,
        updated=METHOD_UPDATED,
    )
