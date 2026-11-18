import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = REPO_ROOT / "content" / "editions"
VENUES_FILE = REPO_ROOT / "content" / "venues.yaml"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_2025 = FIXTURES_DIR / "editions" / "2025.yaml"


def _settings_for(content: Path) -> Settings:
    return Settings(
        content_dir=content / "editions",
        venues_file=content / "venues.yaml",
        alerts_file=content / "alerts.yaml",
        scores_file=content / "scores.yaml",
        glossary_file=content / "glossary.yaml",
        faq_file=content / "faq.yaml",
        history_file=content / "history.yaml",
        photos_file=content / "photos.yaml",
        offers_file=content / "offers.yaml",
        offers_preview_file=content / "offers.preview.yaml",
        results_dir=content / "results",
    )


@pytest.fixture(scope="session")
def demo_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Копия `content/` с демо-каркасом 2025 из фикстур.

    Публикуемый `content/editions/2025.yaml` содержит только подтверждённые факты;
    выдуманные студии и выступления нужны тестам каталогов и живут отдельно.
    """

    content = tmp_path_factory.mktemp("demo-content") / "content"
    shutil.copytree(REPO_ROOT / "content", content)
    shutil.copyfile(FIXTURE_2025, content / "editions" / "2025.yaml")
    return _settings_for(content)


@pytest.fixture(scope="session")
def client(demo_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(demo_settings)) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def live_client() -> Iterator[TestClient]:
    """Приложение на реальном `content/`: проверяем состояние без демо-каркаса."""

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def demo_edition_path() -> Path:
    """Демо-каркас каталога живёт в фикстурах, а не в публикуемом контенте."""

    return FIXTURE_2025


@pytest.fixture(scope="session")
def venues():
    from zaruba.content import load_venues

    return load_venues(VENUES_FILE)


@pytest.fixture(scope="session")
def edition_path(demo_settings: Settings) -> Path:
    """Копия 2025 с демо-каркасом: проверки объёма и порчи YAML не касаются контента."""

    return demo_settings.content_dir / "2025.yaml"


@pytest.fixture(scope="session")
def edition_2026() -> Path:
    return CONTENT_DIR / "2026.yaml"


@pytest.fixture
def content_settings(tmp_path: Path) -> Settings:
    """Копия `content/` в tmp: тесты могут править файлы, не задевая репозиторий."""

    content = tmp_path / "content"
    shutil.copytree(REPO_ROOT / "content", content)

    return _settings_for(content)


@pytest.fixture
def broken_settings(content_settings: Settings) -> Settings:
    """Копия `content/` с испорченным выпуском: проверяем аварию, а не эталон.

    `2026.yaml` ломается в одном поле — так тест остаётся валидным, пока
    в контенте меняются даты, участники и тайминг.
    """

    path = content_settings.content_dir / "2026.yaml"
    raw = path.read_text(encoding="utf-8")
    assert "edition: 2026" in raw, "в 2026.yaml изменился год — поправьте фикстуру"
    path.write_text(raw.replace("edition: 2026", 'edition: "не год"', 1), encoding="utf-8")

    return content_settings
