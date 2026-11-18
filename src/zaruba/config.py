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
    # Срочные оповещения организаторов (`docs/09` §9.9): файл уровня сайта, а не
    # выпуска — оповещение живёт сутки, а выпуск год.
    alerts_file: Path = BASE_DIR / "content" / "alerts.yaml"
    # Подтверждённые главным судьёй оценки из листа «Оценки судей» (`docs/06`
    # §6.28). Как и оповещения — файл уровня сайта: выпуск указан внутри.
    scores_file: Path = BASE_DIR / "content" / "scores.yaml"

    # Презентационный overlay `content/editions/<год>.d/*.yaml`: выдуманные
    # строки видны только при явном включении (`ZARUBA_PREVIEW_ENABLED=1`) и
    # помечены `demo`. По умолчанию выключен — публичный контент и тесты
    # остаются на подтверждённых фактах.
    preview_enabled: bool = False

    # Режим Mini App в мессенджере MAX (docs/09): подключает MAX Bridge,
    # разрешает встраивание во фрейм клиента MAX и убирает дубль логотипа
    # в шапке. По умолчанию выключен — публичный сайт не меняется.
    max_miniapp: bool = False

    # Бот-проводник MAX (docs/09 §9.8). Пара `token` + `secret` — это и есть
    # включатель: без обоих адрес `/max/webhook` отвечает 404. Токен выдаёт
    # MAX при создании бота и живёт только в окружении, никогда — в коде.
    max_bot_token: str = ""
    # Секрет подписки: MAX присылает его в `X-Max-Bot-Api-Secret`, и другого
    # доказательства, что запрос пришёл от платформы, у нас нет.
    max_bot_secret: str = ""
    # Публичное имя бота (username): из него собирается кнопка `open_app`,
    # открывающая мини-приложение сразу на нужной странице.
    max_bot_name: str = ""
    # Адрес сайта для кнопки-ссылки. Пусто — берётся `public_base_url`.
    max_bot_site_url: str = ""
    # Дополнительный корневой сертификат для исходящих запросов к MAX
    # (Минцифры): без него TLS-проверка `platform-api2.max.ru` падает.
    max_bot_ca_file: Path | None = None
    # Чат или канал рассылки срочных оповещений (`docs/09` §9.9). Пусто —
    # рассылка не запускается, но бот-проводник продолжает работать.
    max_alert_chat_id: int | None = None

    # Мониторинг постов официального сообщества ВКонтакте (`docs/09` §9.13).
    # Токен — единственный включатель: без него `python -m zaruba.vk_watch`
    # пишет «проверка пропущена» и выходит с 0, как рассылка без бота.
    vk_token: str = ""
    # Короткое имя сообщества: `wall.get` принимает `domain=`, поэтому числовой
    # owner_id в настройках не нужен — он приходит в каждом посте.
    vk_group: str = "zaruba72champ"
    vk_api_version: str = "5.199"
    # Период опроса: минута — «мгновенно» для человека и 1 запрос в минуту для
    # VK, то есть далеко от лимита 3 запроса в секунду на токен.
    vk_interval: int = 60
    # Сколько последних постов читать за цикл: чтобы не потерять серию
    # публикаций между двумя опросами.
    vk_posts: int = 10
    # Куда слать: пусто — тот же чат, что у срочных оповещений.
    vk_chat_id: int | None = None
    # Файл состояния: `logs/` в .gitignore, мониторинг живёт на одной машине.
    vk_seen_file: Path = BASE_DIR / "logs" / "vk_seen.json"
    # Дополнительный корневой сертификат для api.vk.com, если системных мало.
    vk_ca_file: Path | None = None

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
    def max_bot_enabled(self) -> bool:
        """Бот включён только парой: токен без секрета — открытый адрес в сети."""

        return bool(self.max_bot_token and self.max_bot_secret)

    @property
    def max_bot_site(self) -> str:
        """Адрес сайта для ссылок бота: явный, иначе публичный; без хвостового `/`."""

        return (self.max_bot_site_url or self.public_base_url).rstrip("/")

    @property
    def vk_chat(self) -> int | None:
        """Адресат уведомлений: свой чат, иначе общий чат оповещений."""

        return self.vk_chat_id if self.vk_chat_id is not None else self.max_alert_chat_id

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
