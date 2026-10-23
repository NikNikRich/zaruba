from pathlib import Path

import pytest
from pydantic import ValidationError

from zaruba.content import (
    ContentError,
    load_edition,
    load_editions,
    load_faq,
    load_glossary,
    main,
)
from zaruba.models import DanceStyle, FaqGroup, GlossaryEntry, Participant

CONTENT_DIR = Path(__file__).resolve().parents[1] / "content" / "editions"
GLOSSARY_FILE = Path(__file__).resolve().parents[1] / "content" / "glossary.yaml"
FAQ_FILE = Path(__file__).resolve().parents[1] / "content" / "faq.yaml"


def _broken_copy(tmp_path: Path, edition_path: Path, old: str, new: str) -> Path:
    raw = edition_path.read_text(encoding="utf-8")
    assert old in raw, f"в эталонном файле нет строки {old!r}"
    path = tmp_path / "broken.yaml"
    path.write_text(raw.replace(old, new, 1), encoding="utf-8")
    return path


def test_edition_has_required_volume(edition_path: Path) -> None:
    """Этап 0 из §6.22: один выпуск, 8 участников, 8 выступлений, 3 блока."""

    edition = load_edition(edition_path).edition

    assert edition.edition == 2025
    assert edition.days == 2
    assert len(edition.blocks) == 3
    assert len(edition.participants) == 8
    assert len(edition.performances) == 8
    assert len(edition.nominations) == 8


def test_venue_is_resolved_from_catalog(edition_path: Path) -> None:
    bundle = load_edition(edition_path)

    assert bundle.venue.slug == "zolotye-vorota"
    assert bundle.venue.address_line == "Тюмень, ул. Герцена, 87"
    assert bundle.venue.capacity == 850


def test_minors_without_consent_are_not_public(edition_path: Path) -> None:
    """§6.22 п.6: у ребёнка без согласия нет фото и полного имени."""

    edition = load_edition(edition_path).edition
    minors = [item for item in edition.participants if item.is_minor]

    assert minors, "в наборе должны быть несовершеннолетние — иначе правило не проверяется"
    for participant in minors:
        assert not participant.is_public_person
        assert participant.display_name.endswith("."), participant.display_name


def test_repeated_load_is_stable(edition_path: Path) -> None:
    """Импорт идемпотентен по slug: повторная загрузка даёт тот же результат."""

    assert load_edition(edition_path) == load_edition(edition_path)


def test_site_loads_exactly_one_edition() -> None:
    bundles = load_editions(CONTENT_DIR)

    assert [bundle.year for bundle in bundles] == [2025]


def test_duplicate_slug_is_reported(tmp_path: Path, edition_path: Path) -> None:
    path = _broken_copy(
        tmp_path,
        edition_path,
        "slug: perf-02-hip-hop-12-17",
        "slug: perf-01-hip-hop-6-11",
    )

    with pytest.raises(ContentError) as error:
        load_edition(path)

    message = str(error.value)
    assert "повторяются slug" in message
    assert "broken.yaml" in message


def test_unknown_field_is_rejected(tmp_path: Path, edition_path: Path) -> None:
    """Опечатка в YAML не должна молча теряться."""

    raw = edition_path.read_text(encoding="utf-8") + "\nunknown_field: 1\n"
    path = tmp_path / "broken.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "unknown_field" in str(error.value)


def test_unknown_nomination_is_reported(tmp_path: Path, edition_path: Path) -> None:
    path = _broken_copy(
        tmp_path,
        edition_path,
        "nomination: solo-heels-18",
        "nomination: solo-heels-99",
    )

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "неизвестная номинация «solo-heels-99»" in str(error.value)


def test_reversed_dates_are_reported(tmp_path: Path, edition_path: Path) -> None:
    path = _broken_copy(
        tmp_path,
        edition_path,
        'dates: ["2025-12-06", "2025-12-07"]',
        'dates: ["2025-12-07", "2025-12-06"]',
    )

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "по возрастанию" in str(error.value)


def test_minor_name_must_be_initials() -> None:
    with pytest.raises(ValidationError):
        Participant(
            slug="ivan",
            display_name="Иван Петров",
            age_group="6-11",
            studio="demo-studio-a",
        )


def test_minor_needs_consent_date_when_granted() -> None:
    with pytest.raises(ValidationError):
        Participant(
            slug="ivan",
            display_name="И. П.",
            age_group="6-11",
            studio="demo-studio-a",
            consent_status="granted",
        )


def test_adult_can_have_full_name() -> None:
    participant = Participant(
        slug="anna",
        display_name="Анна Петрова",
        age_group="18+",
        studio="demo-studio-a",
    )

    assert participant.is_public_person


def test_cli_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    output = capsys.readouterr().out

    assert "2025" in output
    assert "8 участников" in output
    assert "проверено выпусков: 1" in output


def test_cli_import_is_idempotent(
    capsys: pytest.CaptureFixture[str], edition_path: Path
) -> None:
    assert main(["import", str(edition_path)]) == 0
    first = capsys.readouterr().out
    assert main(["import", str(edition_path)]) == 0
    second = capsys.readouterr().out

    assert first == second


def test_cli_returns_error_code_for_broken_file(
    tmp_path: Path, edition_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _broken_copy(
        tmp_path,
        edition_path,
        'dates: ["2025-12-06", "2025-12-07"]',
        'dates: ["2025-12-07", "2025-12-06"]',
    )

    assert main(["check", str(path)]) == 1
    assert "ОШИБКА" in capsys.readouterr().err


def test_edition_has_confirmed_judges(edition_path: Path) -> None:
    """Жюри 2025 — подтверждённый факт (docs/01): три судьи и ведущий."""

    edition = load_edition(edition_path).edition

    assert len(edition.judges) == 4
    assert {judge.role.value for judge in edition.judges} == {"judge", "mc"}
    assert all(judge.source_id == "kassy-event" for judge in edition.judges)


def test_judge_with_unknown_block_is_reported(tmp_path: Path, edition_path: Path) -> None:
    path = _broken_copy(
        tmp_path,
        edition_path,
        "regalia: MATÈ",
        "regalia: MATÈ\n    blocks: [no-such-block]",
    )

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "нет блоков" in str(error.value)


def test_glossary_covers_all_styles_and_terms() -> None:
    entries = load_glossary(GLOSSARY_FILE)
    styles = [entry for entry in entries if entry.kind.value == "style"]
    terms = [entry for entry in entries if entry.kind.value == "term"]

    assert {entry.style for entry in styles} == set(DanceStyle)
    assert len(terms) == 8
    assert all(entry.signs for entry in entries)


def test_glossary_style_requires_dictionary_value() -> None:
    with pytest.raises(ValidationError):
        GlossaryEntry(
            slug="test-style",
            term="Тестовое направление",
            kind="style",
            what="Достаточно длинное описание, чтобы пройти проверку длины.",
            signs=["первый признак"],
        )


def test_glossary_term_must_not_have_style() -> None:
    with pytest.raises(ValidationError):
        GlossaryEntry(
            slug="test-term",
            term="Тестовый термин",
            kind="term",
            what="Достаточно длинное описание, чтобы пройти проверку длины.",
            signs=["первый признак"],
            style="hip-hop",
        )


def test_faq_has_twelve_items_in_four_groups() -> None:
    """§6.15: 12 вопросов, четыре группы, у каждого ответа есть статус."""

    items = load_faq(FAQ_FILE)

    assert len(items) == 12
    assert {item.group for item in items} == set(FaqGroup)
    assert all(item.status.label for item in items)


def test_glossary_file_with_duplicate_style_is_reported(tmp_path: Path) -> None:
    raw = GLOSSARY_FILE.read_text(encoding="utf-8")
    broken = raw.replace("style: dancehall", "style: breaking", 1)
    path = tmp_path / "broken-glossary.yaml"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(ContentError) as error:
        load_glossary(path)

    assert "направление описано дважды" in str(error.value)

