"""Фотоархив `/history`: свежие выпуски сверху (`docs/06` §6.5).

Сервис намеренно тонкий: карточка — это сам `Photo`, у него уже есть размеры
пресета, подпись об авторстве и ссылка на источник. Сортировка вынесена сюда,
чтобы страница и тесты считали порядок одинаково.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Photo


def gallery(photos: Sequence[Photo]) -> list[Photo]:
    """Свежие выпуски сверху, внутри года — по заголовку.

    Порядок в YAML не значим: дописывать новый кадр можно в любое место файла,
    и он не «переедет» в середину ленты.
    """

    return sorted(photos, key=lambda photo: (-photo.year, photo.title))


def file_photos(photos: Sequence[Photo]) -> int:
    """Сколько карточек показывают файл, а не ссылку: цифра для подписи.

    Считаем все кадры, включая цитаты: подпись под лентой говорит о формате
    файла, а не о том, чей это снимок. Права у каждой карточки свои — в `credit`.
    """

    return sum(1 for photo in photos if photo.file)
