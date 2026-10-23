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
from zaruba.models import (  # noqa: I001 - импорт моделей одним блоком
    AgeGroup,
    DanceStyle,
    FaqGroup,
    GlossaryEntry,
    Nomination,
    NominationLevel,
    Participant,
)

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


def test_site_loads_editions_newest_first() -> None:
    bundles = load_editions(CONTENT_DIR)

    assert [bundle.year for bundle in bundles] == [2026, 2025]


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
    assert "проверено выпусков: 2" in output


def test_cli_import_is_idempotent(capsys: pytest.CaptureFixture[str], edition_path: Path) -> None:
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


def test_2026_has_official_volume(edition_2026: Path) -> None:
    """§1.6.1: 15 номинаций, два дня, без выдуманного тайминга."""

    edition = load_edition(edition_2026).edition

    assert edition.edition == 2026
    assert edition.days == 2
    assert len(edition.nominations) == 15
    assert len(edition.judges) == 5
    assert edition.registration_until.isoformat() == "2026-12-07"
    assert not edition.blocks, "тайминг блоков 2026 не опубликован — блоков быть не должно"
    assert not any(item.has_timing for item in edition.nominations)
    assert {item.day for item in edition.nominations} == {1, 2}


def test_2026_levels_and_team_sizes(edition_2026: Path) -> None:
    """БЕГ/ПРО — из «Положения»: у КИДС два зачёта, у мини-групп 3–5 человек."""

    edition = load_edition(edition_2026).edition

    kids = edition.nomination_index["best-kids"]
    assert {level for level in kids.levels} == {NominationLevel.BEG, NominationLevel.PRO}
    assert kids.min_participants == 6
    assert kids.team_size_label == "от 6 человек"

    mini = edition.nomination_index["best-mini-groups"]
    assert (mini.min_participants, mini.max_participants) == (3, 5)
    assert mini.team_size_label == "3–5 человек"

    baby = edition.nomination_index["best-baby"]
    assert baby.age_group is AgeGroup.BABY
    assert not baby.levels, "у БЕЙБИ в «Положении» нет деления на БЕГ/ПРО"


def test_2026_age_groups_cover_edition(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition
    groups = {item.age_group for item in edition.nominations}

    assert AgeGroup.BABY in groups
    assert AgeGroup.ADULTS_16 in groups
    assert AgeGroup.ADULTS in groups
    assert AgeGroup.ANY in groups


def test_2026_fees_and_tickets_are_confirmed(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition

    assert len(edition.fees) == 6
    children_team = next(fee for fee in edition.fees if fee.slug == "children-team")
    assert (children_team.amount_early, children_team.amount_late) == (1500, 1800)
    assert children_team.early_until.isoformat() == "2026-11-14"

    prices = {ticket.title: ticket.price for ticket in edition.tickets}
    assert prices["12 декабря, один день"] == 1000
    assert prices["13 декабря, 2-й блок (взрослые команды)"] == 800
    assert prices["12–13 декабря, оба дня"] == 1800

    assert len(edition.master_classes) == 4
    assert edition.master_class_pass_early == 5000
    assert edition.master_class_pass_late == 6000


def test_2026_criteria_are_from_the_document(edition_2026: Path) -> None:
    edition = load_edition(edition_2026).edition
    scores = [(item.title, item.max_score) for item in edition.criteria]

    assert scores[:4] == [
        ("Сложность хореографии", 10),
        ("Техничность и синхронность", 10),
        ("Креативность", 10),
        ("Зрелищность", 10),
    ]
    assert scores[4] == ("Внешний вид", 5)
    assert edition.rules and all(len(rule) < 300 for rule in edition.rules)


def test_nomination_rejects_half_a_timing() -> None:
    with pytest.raises(ValidationError):
        Nomination(
            slug="half-timing",
            name="ТЕСТ",
            format="team",
            age_group="18+",
            day=1,
            starts_at="12:00",
        )


def test_nomination_rejects_time_without_day() -> None:
    with pytest.raises(ValidationError):
        Nomination(
            slug="time-no-day",
            name="ТЕСТ",
            format="team",
            age_group="18+",
            starts_at="12:00",
            ends_at="13:00",
        )


def test_nomination_rejects_team_size_swap() -> None:
    with pytest.raises(ValidationError):
        Nomination(
            slug="team-size",
            name="ТЕСТ",
            format="small-group",
            age_group="15+",
            min_participants=6,
            max_participants=3,
        )
