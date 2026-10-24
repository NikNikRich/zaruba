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

    # Кэш публичных страниц (docs/06 §6.20): 60 с, расписание — 15 с.
    cache_ttl_public: int = 60
    cache_ttl_schedule: int = 15

    # Фичефлаги: включаются только вместе с механикой из docs/05,
    # иначе на сайте появятся цифры, которых никто не считал.
    ratings_enabled: bool = False
    fan_voting_enabled: bool = False

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
