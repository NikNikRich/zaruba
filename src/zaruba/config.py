"""Настройки приложения: pydantic-settings, префикс переменных `ZARUBA_`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень репозитория: src/zaruba/config.py -> src/zaruba -> src -> корень
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZARUBA_", env_file=".env", extra="ignore")

    env: str = "local"

    # Публичный адрес сайта: canonical, robots.txt и sitemap не должны зависеть
    # от того, передал ли прокси X-Forwarded-Proto. Пусто — берём из запроса.
    public_base_url: str = ""

    content_dir: Path = BASE_DIR / "content" / "editions"
    venues_file: Path = BASE_DIR / "content" / "venues.yaml"
    glossary_file: Path = BASE_DIR / "content" / "glossary.yaml"
    faq_file: Path = BASE_DIR / "content" / "faq.yaml"
    history_file: Path = BASE_DIR / "content" / "history.yaml"
    # Фотоархив: карточки со ссылкой в первоисточник и (если есть) своим файлом.
    photos_file: Path = BASE_DIR / "content" / "photos.yaml"
    # Рекламные офферы нативных слотов (§6.27). Файл уровня сайта, а не выпуска:
    # overlay `editions/*.d` в prod выключен, и реклама из него исчезла бы.
    offers_file: Path = BASE_DIR / "content" / "offers.yaml"
    # Демонстрационные офферы для показа (docs/07 §7.8). Отдельный файл, чтобы
    # боевой `offers.yaml` оставался `tips: []`, а придуманный `erid` не попадал
    # в публичный контент.
    offers_preview_file: Path = BASE_DIR / "content" / "offers.preview.yaml"
    # Разобранные судейские листы по выпускам: `content/results/2025.yaml`.
    # Пустая папка — законное состояние: страница объясняет, почему таблиц нет.
    results_dir: Path = BASE_DIR / "content" / "results"

    # Презентационный overlay `content/editions/<год>.d/*.yaml`: выдуманные
    # строки видны только при явном включении (`ZARUBA_PREVIEW_ENABLED=1`) и
    # помечены `demo`. По умолчанию выключен — публичный контент и тесты
    # остаются на подтверждённых фактах.
    preview_enabled: bool = False

    # Кэш публичных страниц (docs/06 §6.20): 60 с, расписание — 15 с.
    cache_ttl_public: int = 60
    cache_ttl_schedule: int = 15

    # Фичефлаги: включаются только вместе с механикой из docs/05,
    # иначе на сайте появятся цифры, которых никто не считал.
    ratings_enabled: bool = False
    fan_voting_enabled: bool = False

    # Рекламные слоты (§6.27). Поле трёхзначное: `None` — «решает окружение»,
    # True/False — явное указание. В dev по умолчанию включено, чтобы дизайнер
    # видел заглушки; в prod — выключено: рамка «место под интеграцию» на
    # публичном сайте выглядит как недоделка, а не как реклама. Preview
    # включает слоты поверх этого флага: на показе нужны примеры.
    service_tips_enabled: bool | None = None

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def service_tips_visible(self) -> bool:
        """Показывать ли рекламные слоты (офферы или заглушки).

        Preview включает слоты независимо от прод-флага: на показе нужны
        заполненные примеры, а страницы preview и так закрыты от индексации.
        """

        return self.preview_enabled or (
            self.service_tips_enabled if self.service_tips_enabled is not None else not self.is_prod
        )

    @property
    def service_tips_placeholders(self) -> bool:
        """Заглушки — только в dev и в preview: в prod пустая рамка не публикуется."""

        return self.preview_enabled or (self.service_tips_visible and not self.is_prod)


@lru_cache
def get_settings() -> Settings:
    return Settings()
