from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = REPO_ROOT / "content" / "editions"
VENUES_FILE = REPO_ROOT / "content" / "venues.yaml"


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def venues():
    from zaruba.content import load_venues

    return load_venues(VENUES_FILE)


@pytest.fixture(scope="session")
def edition_path() -> Path:
    return CONTENT_DIR / "2025.yaml"
