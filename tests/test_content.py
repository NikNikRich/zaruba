import os
import runpy
import sys
import time
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from zaruba.config import Settings
from zaruba.content import (
    ContentError,
    load_edition,
    load_editions,
    load_faq,
    load_glossary,
    load_history,
    load_knowledge,
    load_latest,
    load_photos,
    load_site,
    load_venues,
    main,
)
from zaruba.models import (  # noqa: I001 - импорт моделей одним блоком
    AgeGroup,
    DanceStyle,
    EventStatus,
    FaqGroup,
    GlossaryEntry,
    Nomination,
    NominationLevel,
    Participant,
)

CONTENT_DIR = Path(__file__).resolve().parents[1] / "content" / "editions"
GLOSSARY_FILE = Path(__file__).resolve().parents[1] / "content" / "glossary.yaml"
FAQ_FILE = Path(__file__).resolve().parents[1] / "content" / "faq.yaml"
VENUES_FILE = Path(__file__).resolve().parents[1] / "content" / "venues.yaml"
HISTORY_FILE = Path(__file__).resolve().parents[1] / "content" / "history.yaml"
PHOTOS_FILE = Path(__file__).resolve().parents[1] / "content" / "photos.yaml"


def _replaced(tmp_path: Path, source: Path, old: str, new: str, name: str) -> Path:
    """Копия YAML с одной заменой: тест ломает одно поле, а не весь файл."""

    raw = source.read_text(encoding="utf-8")
    assert old in raw, f"в {source.name} нет строки {old!r}"
    path = tmp_path / name
    path.write_text(raw.replace(old, new, 1), encoding="utf-8")
    return path


def _written(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _broken_copy(tmp_path: Path, edition_path: Path, old: str, new: str) -> Path:
    return _replaced(tmp_path, edition_path, old, new, "broken.yaml")


def test_edition_has_required_volume(edition_path: Path) -> None:
    """Этап 0 из §6.22: один выпуск, 8 участников, 8 выступлений, 3 блока.

    Номинаций 29: 21 реальная из судейских листов 2025 и 8 демонстрационных —
    каркас каталога, помеченный `demo`.
    """

    edition = load_edition(edition_path).edition

    assert edition.edition == 2025
    assert edition.days == 2
    assert len(edition.blocks) == 3
    assert len(edition.participants) == 8
    assert len(edition.performances) == 8
    assert len(edition.nominations) == 29
    assert len([item for item in edition.nominations if item.demo]) == 8


def test_real_2025_nominations_come_from_judge_sheets(edition_path: Path) -> None:
    """Сетка 2025 — из папки «Судейские листы Заруба 2025»: 9 + 12 номинаций."""

    edition = load_edition(edition_path).edition
    real = [item for item in edition.nominations if not item.demo]

    assert len(real) == 21
    assert sum(1 for item in real if item.day == 1) == 9
    assert sum(1 for item in real if item.day == 2) == 12
    assert {item.status for item in real} == {EventStatus.DONE}
    assert {item.source_id for item in real} == {"judge-sheets-2025"}
    titles = {item.title for item in real}
    assert "BEST ZARUBA SOLO KIDS 3-9" in titles
    assert "BEST ZARUBA DUET ДО 14" in titles
    assert "BEST ZARUBA HEELSSTRIP" in titles
    # Тайминга в листах нет — выдуманное «12:00» не появляется даже у фактов.
    assert all(item.starts_at is None for item in real)


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


def test_site_is_cached_and_reloads_after_file_change(content_settings: Settings) -> None:
    """Кэш по mtime: повторный запрос не парсит YAML, а правка файла его сбрасывает.

    TTL здесь не подходит: «до минуты показываем вчерашнее расписание» — это
    худший вид ошибки в день события.
    """

    first = load_site(content_settings)
    second = load_site(content_settings)

    assert second[0] is first[0], "контент разобран повторно, хотя файлы не менялись"

    path = content_settings.content_dir / "2026.yaml"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("«ЗАРУБА» 2026", "«ЗАРУБА» 2026 (правка)", 1), encoding="utf-8")
    stamp = time.time() + 5
    os.utime(path, (stamp, stamp))

    third = load_site(content_settings)

    assert third[0].edition.title != first[0].edition.title, "кэш не увидел правку контента"


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


# --- Ошибки загрузки: сообщение называет файл и поле, а не падает трейсбеком.


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContentError) as error:
        load_edition(tmp_path / "нет.yaml")

    assert "файл не найден" in str(error.value)


def test_broken_yaml_is_reported(tmp_path: Path) -> None:
    path = _written(tmp_path, "broken.yaml", "edition: [2025\n")

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "не разобрать YAML" in str(error.value)


def test_yaml_root_must_be_mapping(tmp_path: Path) -> None:
    """Список вместо объекта — частая ошибка, когда потеряли ключ верхнего уровня."""

    path = _written(tmp_path, "list.yaml", "- 2025\n- 2026\n")

    with pytest.raises(ContentError) as error:
        load_edition(path)

    assert "ожидался YAML-объект" in str(error.value)


def test_venue_with_bad_capacity_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, VENUES_FILE, "capacity: 850", 'capacity: "много"', "venues.yaml")

    with pytest.raises(ContentError) as error:
        load_venues(path)

    message = str(error.value)
    assert "данные не прошли проверку" in message
    assert "capacity" in message


def test_duplicate_venue_slug_is_reported(tmp_path: Path) -> None:
    raw = VENUES_FILE.read_text(encoding="utf-8")
    block = raw[raw.index("  - slug:") :]
    path = _written(tmp_path, "venues.yaml", raw + block)

    with pytest.raises(ContentError) as error:
        load_venues(path)

    assert "описана дважды" in str(error.value)


def test_unknown_venue_is_reported(tmp_path: Path, edition_path: Path, venues) -> None:
    path = _replaced(
        tmp_path, edition_path, "venue: zolotye-vorota", "venue: no-such-venue", "broken.yaml"
    )

    with pytest.raises(ContentError) as error:
        load_edition(path, venues)

    assert "нет в content/venues.yaml" in str(error.value)


def test_missing_editions_dir_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContentError) as error:
        load_editions(tmp_path / "нет-папки")

    assert "папка с выпусками не найдена" in str(error.value)


def test_empty_editions_dir_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContentError) as error:
        load_editions(tmp_path)

    assert "нет ни одного файла выпуска" in str(error.value)


def test_duplicate_year_is_reported(content_settings: Settings) -> None:
    """Два файла с одним годом: какой из них «правильный» — не решает загрузчик."""

    editions = content_settings.content_dir
    copy = editions / "2025-copy.yaml"
    copy.write_text((editions / "2025.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ContentError) as error:
        load_editions(editions, content_settings.venues_file)

    assert "описан в двух файлах" in str(error.value)


def test_fingerprint_skips_missing_files(content_settings: Settings, tmp_path: Path) -> None:
    """Отсутствующий файл не роняет сайт: отпечаток кэша просто его пропускает."""

    settings = content_settings.model_copy(update={"photos_file": tmp_path / "нет.yaml"})

    assert [bundle.year for bundle in load_site(settings)] == [2026, 2025]


def test_glossary_with_bad_kind_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, GLOSSARY_FILE, "kind: style", "kind: nonsense", "glossary.yaml")

    with pytest.raises(ContentError) as error:
        load_glossary(path)

    assert "данные не прошли проверку" in str(error.value)


def test_glossary_duplicate_slug_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, GLOSSARY_FILE, "slug: trek", "slug: nominaciya", "glossary.yaml")

    with pytest.raises(ContentError) as error:
        load_glossary(path)

    assert "статьи повторяются" in str(error.value)


def test_faq_with_bad_status_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, FAQ_FILE, "status: confirmed", "status: nonsense", "faq.yaml")

    with pytest.raises(ContentError) as error:
        load_faq(path)

    assert "данные не прошли проверку" in str(error.value)


def test_faq_duplicate_slug_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, FAQ_FILE, "slug: s-rebenkom", "slug: skolko-stoit", "faq.yaml")

    with pytest.raises(ContentError) as error:
        load_faq(path)

    assert "вопросы повторяются" in str(error.value)


def test_faq_unknown_source_is_reported() -> None:
    """Ссылка на источник, которого нет ни в одном выпуске, — опечатка, а не факт."""

    with pytest.raises(ContentError) as error:
        load_faq(FAQ_FILE, set())

    assert "неизвестный source_id" in str(error.value)


def test_history_with_bad_year_is_reported(tmp_path: Path) -> None:
    path = _replaced(tmp_path, HISTORY_FILE, "year: 2019", 'year: "не год"', "history.yaml")

    with pytest.raises(ContentError) as error:
        load_history(path)

    assert "данные не прошли проверку" in str(error.value)


def test_photos_with_bad_year_are_reported(tmp_path: Path) -> None:
    # Первое `kind: gallery` в файле — в комментарии-примере, поэтому ломаем год.
    path = _replaced(tmp_path, PHOTOS_FILE, "year: 2025", 'year: "не год"', "photos.yaml")

    with pytest.raises(ContentError) as error:
        load_photos(path)

    assert "данные не прошли проверку" in str(error.value)


def test_photo_duplicate_slug_is_reported(tmp_path: Path) -> None:
    """Дубль `slug` ломает ссылки и имена файлов — это ошибка, в отличие от дубля года."""

    path = _replaced(tmp_path, PHOTOS_FILE, "slug: 2022-video", "slug: 2024-stage", "photos.yaml")

    with pytest.raises(ContentError) as error:
        load_photos(path)

    assert "описано дважды" in str(error.value)


def test_history_duplicate_year_is_reported(tmp_path: Path) -> None:
    # Дата обязана совпадать с годом записи, поэтому дубль делаем «сдвигом»
    # записи 2023 в 2024: проверка года срабатывает, проверка даты — нет.
    text = HISTORY_FILE.read_text(encoding="utf-8")
    text = text.replace("year: 2023", "year: 2024").replace("2023-12-03", "2024-12-03")
    path = tmp_path / "history.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ContentError) as error:
        load_history(path)

    assert "год описан дважды" in str(error.value)


def test_knowledge_takes_sources_from_all_editions(content_settings: Settings) -> None:
    """FAQ общий для сайта: `source_id` может прийти из любого выпуска, не только из свежего."""

    knowledge = load_knowledge(content_settings)
    source_ids = {
        source_id
        for bundle in load_site(content_settings)
        for source_id in bundle.edition.source_index
    }

    assert knowledge.glossary
    assert {item.source_id for item in knowledge.faq if item.source_id} <= source_ids


def test_latest_edition_is_the_newest(content_settings: Settings) -> None:
    assert load_latest(content_settings).year == 2026


def test_module_entrypoint_exits_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m zaruba.content check`: строка `__main__` тоже должна работать."""

    monkeypatch.setattr(sys, "argv", ["zaruba.content", "check"])
    with warnings.catch_warnings():
        # runpy предупреждает, что модуль уже импортирован: здесь это ожидаемо.
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("zaruba.content", run_name="__main__")

    assert exit_info.value.code == 0
