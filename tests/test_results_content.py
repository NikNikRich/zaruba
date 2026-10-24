"""§6.24: загрузка результатов — ошибка называет файл и поле, а не молчит."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zaruba.config import Settings
from zaruba.content import ContentError, load_results, load_results_for, result_years
from zaruba.models import ResultNomination, ResultRow, Results

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_FILE = REPO_ROOT / "content" / "results" / "2025.yaml"
EDITION_FILE = REPO_ROOT / "content" / "editions" / "2025.yaml"


def _payload() -> dict:
    return yaml.safe_load(RESULTS_FILE.read_text(encoding="utf-8"))


def _written(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _edition_path(settings: Settings) -> Path:
    return settings.content_dir / "2025.yaml"


def test_results_file_matches_edition_and_lists_every_nomination() -> None:
    settings = Settings()
    results = load_results_for(settings, 2025)

    assert results is not None
    assert results.edition == 2025
    assert len(results.nominations) == 21
    assert results.rows == 207, "строки — участники, а не 713 пар «судья × участник»"
    assert result_years(settings) == [2025]


def test_year_without_results_is_not_an_error() -> None:
    assert load_results_for(Settings(), 2024) is None


def test_unknown_nomination_is_rejected(tmp_path: Path, content_settings: Settings) -> None:
    payload = _payload()
    payload["nominations"][0]["nomination"] = "solo-nowhere"
    path = _written(tmp_path, payload)

    from zaruba.content import load_edition

    bundle = load_edition(_edition_path(content_settings))

    with pytest.raises(ContentError, match="нет в выпуске 2025"):
        load_results(path, bundle)


def test_number_must_match_edition_order(tmp_path: Path, content_settings: Settings) -> None:
    """Номер листа и место номинации в выпуске не могут разъехаться."""

    from zaruba.content import load_edition

    payload = _payload()
    # Оставляем одну номинацию, но ставим ей чужое имя: номер 1 в выпуске
    # принадлежит SOLO KIDS 3-9, а не JUNIORS 10-14.
    payload["nominations"] = [
        {**payload["nominations"][1], "number": 1},
    ]
    path = _written(tmp_path, payload)
    bundle = load_edition(_edition_path(content_settings))

    with pytest.raises(ContentError, match="в выпуске на этом месте"):
        load_results(path, bundle)


def test_child_full_name_is_rejected(tmp_path: Path, content_settings: Settings) -> None:
    """Детская номинация — только инициалы: полное имя не должно доехать до страницы."""

    from zaruba.content import load_edition

    payload = _payload()
    payload["nominations"][0]["rows"][0]["participant"] = "Абабкова Елизавета"
    path = _written(tmp_path, payload)
    bundle = load_edition(_edition_path(content_settings))

    with pytest.raises(ContentError, match="только инициалы"):
        load_results(path, bundle)


def test_adult_full_name_is_allowed(tmp_path: Path, content_settings: Settings) -> None:
    """Обратная граница: у взрослых имя остаётся узнаваемым — проверка не «валит всё»."""

    from zaruba.content import load_edition

    payload = _payload()
    adults = next(
        item for item in payload["nominations"] if item["nomination"] == "solo-adults-18-pro"
    )
    adults["rows"][0]["participant"] = "Татарко Александра"
    path = _written(tmp_path, payload)
    bundle = load_edition(_edition_path(content_settings))

    assert load_results(path, bundle).rows == 207


def test_place_without_full_row_is_rejected() -> None:
    with pytest.raises(Exception, match="неполной строке"):
        ResultRow.model_validate(
            {
                "participant": "Тест",
                "scores": {"mor": 30.0, "mate": 30.0},
                "unreadable": ["mafia"],
                "total": 60.0,
                "place": 1,
            }
        )


def test_total_must_equal_judge_sum() -> None:
    with pytest.raises(Exception, match="total не равен"):
        ResultRow.model_validate(
            {
                "participant": "Тест",
                "scores": {"mor": 30.0, "mate": 30.0},
                "total": 61.0,
                "place": 1,
            }
        )


def test_flagged_judge_must_have_score() -> None:
    with pytest.raises(Exception, match="flagged"):
        ResultRow.model_validate(
            {
                "participant": "Тест",
                "scores": {"mor": 30.0},
                "unreadable": ["mate"],
                "total": 30.0,
                "flagged": ["mate"],
            }
        )


def test_missing_sheet_forbids_places() -> None:
    with pytest.raises(Exception, match="нет листа судьи"):
        ResultNomination.model_validate(
            {
                "nomination": "solo-kids-3-9",
                "day": 1,
                "number": 1,
                "judges": ["mor", "mate"],
                "rows": [
                    {
                        "participant": "Анна",
                        "scores": {"mor": 30.0, "mate": 30.0},
                        "total": 60.0,
                        "place": 1,
                    },
                    {"participant": "Борис", "scores": {"mor": 20.0, "mate": None}, "total": 20.0},
                ],
            }
        )


def test_duplicate_place_is_rejected() -> None:
    with pytest.raises(Exception, match="место присвоено дважды"):
        ResultNomination.model_validate(
            {
                "nomination": "solo-kids-3-9",
                "day": 1,
                "number": 1,
                "judges": ["mor"],
                "rows": [
                    {"participant": "Анна", "scores": {"mor": 30.0}, "total": 30.0, "place": 1},
                    {"participant": "Борис", "scores": {"mor": 20.0}, "total": 20.0, "place": 1},
                ],
            }
        )


def test_results_reject_unknown_judge() -> None:
    with pytest.raises(Exception, match="неизвестные коды судей"):
        Results.model_validate(
            {
                "edition": 2025,
                "updated_at": "2026-10-24T12:00:00+05:00",
                "source_id": "judge-sheets-2025",
                "judges": [{"code": "mor", "name": "Андрей Морыженков"}],
                "nominations": [
                    {
                        "nomination": "solo-kids-3-9",
                        "day": 1,
                        "number": 1,
                        "judges": ["ghost"],
                        "rows": [{"participant": "Анна", "scores": {"ghost": 30.0}, "total": 30.0}],
                    }
                ],
            }
        )
