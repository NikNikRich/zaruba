"""Таблицы предпросмотра: данные, overlay и страницы в режиме `preview`."""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import ContentError, load_edition, load_site, merge_overlay, overlay_dir
from zaruba.main import create_app
from zaruba.sync_tables import (
    TABLE_SPECS,
    TAG_PREVIEW,
    TAG_REAL,
    TableError,
    _base_rows,
    build_overlay,
    drop_preview,
    main,
    read_table,
    timing_patches,
    write_overlay,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLES = REPO_ROOT / "docs" / "templates"
BASE = REPO_ROOT / "content" / "editions" / "2026.yaml"


def _tables_copy(tmp_path: Path) -> Path:
    target = tmp_path / "tables"
    shutil.copytree(TABLES, target)
    return target


def _rewrite(path: Path, old: str, new: str) -> None:
    raw = path.read_text(encoding="utf-8-sig")
    assert old in raw, f"в {path.name} нет {old!r}"
    path.write_text(raw.replace(old, new, 1), encoding="utf-8-sig")


def test_every_table_has_known_columns_and_tags() -> None:
    for spec in TABLE_SPECS:
        rows = read_table(spec, TABLES)
        assert rows, spec.filename
        assert {row["data_tag"] for row in rows} <= {TAG_REAL, TAG_PREVIEW}, spec.filename


def test_real_rows_are_the_published_grid() -> None:
    _, summary = build_overlay(TABLES, BASE)

    assert summary.real["nominations"] == 15
    assert summary.real["judges"] == 5
    assert set(summary.real) == {spec.section for spec in TABLE_SPECS}


def test_preview_rows_fill_the_catalog() -> None:
    data, summary = build_overlay(TABLES, BASE)

    assert summary.preview["studios"] == 12
    assert summary.preview["participants"] == 89
    assert summary.preview["blocks"] == 4
    assert summary.preview["performances"] == 31
    for section in ("studios", "participants", "performances"):
        assert all(row["demo"] is True for row in data[section]), section


def test_timing_patches_only_touch_time() -> None:
    data, _ = build_overlay(TABLES, BASE)
    patches = [row for row in data["nominations"] if "starts_at" in row]

    assert len(patches) == 15
    assert all(set(row) == {"slug", "starts_at", "ends_at"} for row in patches)


def test_overlay_is_ignored_without_the_flag() -> None:
    plain = load_edition(BASE).edition
    preview = load_edition(BASE, preview=True).edition

    assert not plain.studios and not plain.participants and not plain.performances
    assert not plain.blocks and not plain.nomination_index["best-kids"].has_timing

    assert len(preview.studios) == 12
    assert len(preview.participants) == 89
    assert len(preview.performances) == 31
    assert len(preview.blocks) == 4
    kids = preview.nomination_index["best-kids"]
    assert kids.name == "БЕСТ ЗАРУБА КИДС", "патч не должен менять подтверждённое название"
    assert kids.starts_at is not None and kids.ends_at is not None


def test_written_overlay_loads_and_drops(tmp_path: Path, venues) -> None:
    target = tmp_path / "2026.yaml"
    shutil.copyfile(BASE, target)
    data, _ = build_overlay(TABLES, BASE)
    written = write_overlay(data, overlay_dir(target))

    assert written and all(path.is_file() for path in written)
    edition = load_edition(target, venues, preview=True).edition
    assert len(edition.performances) == 31

    removed = drop_preview(overlay_dir(target))

    assert removed and not overlay_dir(target).exists()
    assert not load_edition(target, venues, preview=True).edition.performances


def test_write_removes_stale_overlay_files(tmp_path: Path) -> None:
    data, _ = build_overlay(TABLES, BASE)
    out_dir = tmp_path / "2026.d"
    out_dir.mkdir()
    stale = out_dir / "stale.yaml"
    stale.write_text("stale: []\n", encoding="utf-8")

    written = write_overlay(data, out_dir)

    assert stale not in written
    assert not stale.exists()
    assert drop_preview(tmp_path / "missing") == []


def test_dry_run_does_not_create_files(tmp_path: Path) -> None:
    data, _ = build_overlay(TABLES, BASE)
    out_dir = tmp_path / "2026.d"

    paths = write_overlay(data, out_dir, dry_run=True)

    assert paths and not out_dir.exists()


def test_real_row_mismatch_is_reported(tmp_path: Path) -> None:
    tables = _tables_copy(tmp_path)
    _rewrite(tables / "nominations.csv", "БЕСТ ЗАРУБА БЕЙБИ", "БЕСТ ЗАРУБА БЕЙБИ 3000")

    with pytest.raises(TableError, match="расходится с YAML"):
        build_overlay(tables, BASE)


def test_unknown_table_column_is_reported(tmp_path: Path) -> None:
    tables = _tables_copy(tmp_path)
    path = tables / "studios.csv"
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    rows[0].append("mystery")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerows(rows)

    with pytest.raises(TableError, match="шапка"):
        read_table(TABLE_SPECS[0], tables)


def test_bad_tag_and_duplicate_slug_are_reported(tmp_path: Path) -> None:
    tables = _tables_copy(tmp_path)
    _rewrite(tables / "studios.csv", "preview", "demo")

    with pytest.raises(TableError, match="data_tag"):
        read_table(TABLE_SPECS[0], tables)

    again = _tables_copy(tmp_path / "again")
    path = again / "studios.csv"
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    path.write_text("\n".join([*lines, lines[1]]) + "\n", encoding="utf-8-sig")

    with pytest.raises(TableError, match="повторяется"):
        read_table(TABLE_SPECS[0], again)


def test_cli_check_write_and_drop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "2026.d"

    assert main(["--check", "--tables-dir", str(TABLES), "--base", str(BASE)]) == 0
    assert "real: 20, preview: 142" in capsys.readouterr().out

    args = ["--tables-dir", str(TABLES), "--base", str(BASE), "--out-dir", str(out_dir)]
    assert main(args) == 0
    assert out_dir.is_dir()

    assert main(["--check", *args]) == 0
    (out_dir / "studios.yaml").write_text("studios: []\n", encoding="utf-8")
    assert main(["--check", *args]) == 1

    assert main(["--drop-preview", "--out-dir", str(out_dir)]) == 0
    assert not out_dir.exists()


def test_cache_separates_preview_from_public(preview_settings: Settings) -> None:
    public_settings = preview_settings.model_copy(update={"preview_enabled": False})
    public = load_site(public_settings)[0].edition
    preview = load_site(preview_settings)[0].edition

    assert public.studios == [] and public.performances == []
    assert len(preview.studios) == 12 and len(preview.performances) == 31


def test_overlay_patch_keeps_the_base_file_untouched(tmp_path: Path) -> None:
    raw: dict[str, object] = {"studios": [{"slug": "a", "name": "Исходное", "city": "Тюмень"}]}
    path = tmp_path / "2026.d" / "studios.yaml"

    merged = merge_overlay(raw, [(path, {"studios": [{"slug": "a", "name": "Новое"}]})])

    assert raw["studios"] == [{"slug": "a", "name": "Исходное", "city": "Тюмень"}]
    assert merged["studios"] == [{"slug": "a", "name": "Новое", "city": "Тюмень"}]


def test_overlay_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "2026.d" / "bad.yaml"

    with pytest.raises(ContentError, match="нельзя добавить"):
        merge_overlay({"studios": []}, [(path, {"venues": []})])
    with pytest.raises(ContentError, match="должен быть списком"):
        merge_overlay({"studios": []}, [(path, {"studios": {}})])
    with pytest.raises(ContentError, match="нужен slug"):
        merge_overlay({"studios": []}, [(path, {"studios": [{"name": "без слага"}]})])
    with pytest.raises(ContentError, match="строка повторяется"):
        merge_overlay(
            {"studios": []},
            [(path, {"studios": [{"slug": "a"}, {"slug": "a"}]})],
        )


def test_cli_reports_a_broken_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tables = _tables_copy(tmp_path)
    _rewrite(tables / "participants.csv", "preview", "demo")

    assert main(["--check", "--tables-dir", str(tables), "--base", str(BASE)]) == 1
    assert "ОШИБКА" in capsys.readouterr().err


def _block(start: str = "10:00", end: str = "12:00", nominations: tuple[str, ...] = ("x",)) -> dict:
    return {
        "slug": "b",
        "starts_at": start,
        "ends_at": end,
        "nominations": list(nominations),
    }


def _show(nomination: str = "x", when: str = "10:30") -> dict:
    return {"slug": "p", "nomination": nomination, "scheduled_at": f"2026-12-12T{when}:00+05:00"}


def test_timing_requires_shows_and_block_window() -> None:
    with pytest.raises(TableError, match="нет выступлений"):
        timing_patches({"nominations": []}, {"blocks": [_block()]})

    outside = {"performances": [_show(when="13:00")], "blocks": [_block()]}
    with pytest.raises(TableError, match="вне блока"):
        timing_patches({"nominations": []}, outside)

    empty = {"performances": [_show(when="10:00")], "blocks": [_block("10:00", "10:00")]}
    with pytest.raises(TableError, match="пустое окно"):
        timing_patches({"nominations": []}, empty)

    with pytest.raises(TableError, match="не время"):
        timing_patches({"nominations": []}, {"blocks": [_block("утро", "12:00")]})
    with pytest.raises(TableError, match="не дата-время"):
        timing_patches(
            {"nominations": []},
            {
                "performances": [{"slug": "p", "nomination": "x", "scheduled_at": "завтра"}],
                "blocks": [_block()],
            },
        )


def test_timing_skips_published_and_repeated_nominations() -> None:
    base = {"nominations": [{"slug": "x", "starts_at": "09:00"}]}
    overlay = {"performances": [_show()], "blocks": [_block(), _block()]}

    assert timing_patches(base, overlay) == []


def test_read_table_rejects_missing_empty_and_header_only(tmp_path: Path) -> None:
    spec = TABLE_SPECS[0]
    path = tmp_path / spec.filename

    with pytest.raises(TableError, match="не найден"):
        read_table(spec, tmp_path)

    path.write_text("", encoding="utf-8")
    with pytest.raises(TableError, match="пуста"):
        read_table(spec, tmp_path)

    path.write_text(";".join(spec.columns) + "\n", encoding="utf-8")
    with pytest.raises(TableError, match="нет ни одной строки"):
        read_table(spec, tmp_path)


def test_read_table_rejects_an_empty_slug(tmp_path: Path) -> None:
    spec = TABLE_SPECS[0]
    path = tmp_path / spec.filename
    row = dict.fromkeys(spec.columns, "")
    row["data_tag"] = TAG_PREVIEW
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=spec.columns, delimiter=";")
        writer.writeheader()
        writer.writerow(row)

    with pytest.raises(TableError, match="пустой slug"):
        read_table(spec, tmp_path)


def test_real_rows_must_cover_the_yaml_and_parse(tmp_path: Path) -> None:
    tables = _tables_copy(tmp_path)
    lines = (tables / "nominations.csv").read_text(encoding="utf-8-sig").splitlines()
    (tables / "nominations.csv").write_text("\n".join([lines[0], *lines[2:]]), encoding="utf-8")
    with pytest.raises(TableError, match="не совпадают"):
        build_overlay(tables, BASE)

    again = _tables_copy(tmp_path / "again")
    _rewrite(again / "nominations.csv", "team;3-6", "team;99-100")
    with pytest.raises(TableError, match="age_group"):
        build_overlay(again, BASE)


def test_base_file_errors_are_reported(tmp_path: Path) -> None:
    with pytest.raises(TableError, match="не найден"):
        build_overlay(TABLES, tmp_path / "none.yaml")

    broken = tmp_path / "broken.yaml"
    broken.write_text("- не выпуск\n", encoding="utf-8")
    with pytest.raises(TableError, match="ожидался YAML-объект"):
        build_overlay(TABLES, broken)

    with pytest.raises(TableError, match="не список"):
        _base_rows({"nominations": {}}, "nominations", broken)


def test_broken_references_stop_the_overlay(tmp_path: Path) -> None:
    tables = _tables_copy(tmp_path)
    _rewrite(tables / "performances.csv", ";a-k;", ";nobody;")

    with pytest.raises(TableError):
        build_overlay(tables, BASE)


@pytest.fixture(scope="module")
def preview_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    content = tmp_path_factory.mktemp("preview") / "content"
    shutil.copytree(REPO_ROOT / "content", content)
    data, _ = build_overlay(TABLES, BASE)
    write_overlay(data, overlay_dir(content / "editions" / "2026.yaml"))
    return Settings(
        content_dir=content / "editions",
        venues_file=content / "venues.yaml",
        glossary_file=content / "glossary.yaml",
        faq_file=content / "faq.yaml",
        history_file=content / "history.yaml",
        photos_file=content / "photos.yaml",
        results_dir=content / "results",
        preview_enabled=True,
    )


@pytest.fixture(scope="module")
def preview_client(preview_settings: Settings) -> TestClient:
    with TestClient(create_app(preview_settings)) as client:
        yield client


def test_preview_pages_are_filled(preview_client: TestClient) -> None:
    pages = (
        "/",
        "/event",
        "/schedule",
        "/judges",
        "/participants",
        "/participants/lisa",
        "/performances",
        "/performances/perf-day2-exp-01",
        "/studios",
        "/studios/pechat-tantsa",
        "/results",
    )

    for path in pages:
        response = preview_client.get(path)
        assert response.status_code == 200, path
        assert "None" not in response.text, path

    home = preview_client.get("/").text
    assert "предпросмотр" in home
    assert 'content="noindex, nofollow"' in home
    filtered = preview_client.get("/participants?page=2").text
    assert 'content="noindex, nofollow"' in filtered

    schedule = preview_client.get("/schedule").text
    assert 'id="block-day1-solo-duet"' in schedule
    assert 'id="block-day2-adults-teams"' in schedule
    assert "Демонстрационный тайминг" in schedule
    assert "пример" in schedule
    assert "тайминг уточняется" not in schedule

    assert "Белый шум" in preview_client.get("/performances?page=2").text
    assert "ЮБИЛЕЙНАЯ ЗАРУБА 2025" in preview_client.get("/results").text


@pytest.mark.parametrize("encoding", ("utf-8", "utf-8-sig", "cp1251"))
def test_read_table_accepts_real_world_encodings(tmp_path: Path, encoding: str) -> None:
    """UTF-8 без BOM, UTF-8 с BOM и Windows-1251 — три реальных выгрузки.

    Русский Excel сохраняет CSV в cp1251, «Блокнот» и Google Sheets — в UTF-8;
    падать с `UnicodeDecodeError` на нормальной таблице контент-менеджера нельзя.
    """

    tables = _tables_copy(tmp_path)
    path = tables / "studios.csv"
    text = path.read_text(encoding="utf-8-sig")
    path.write_bytes(text.encode(encoding))

    rows = read_table(TABLE_SPECS[0], tables)

    assert len(rows) == 12
    assert rows[0]["slug"] == "pechat-tantsa"
    assert rows[0]["name"] == "«Печать танца»"


def test_utf16_table_is_rejected_with_a_clear_error(tmp_path: Path) -> None:
    """UTF-16 не поддерживаем, но говорим об этом, а не показываем стектрейс."""

    tables = _tables_copy(tmp_path)
    path = tables / "studios.csv"
    text = path.read_text(encoding="utf-8-sig")
    path.write_bytes(text.encode("utf-16"))

    with pytest.raises(TableError, match="UTF-16"):
        read_table(TABLE_SPECS[0], tables)


def test_headers_and_cells_are_trimmed_and_blank_lines_skipped(tmp_path: Path) -> None:
    """«Грязная» таблица: пробелы вокруг шапки и клеток, пустые строки."""

    spec = TABLE_SPECS[0]
    path = tmp_path / spec.filename
    header = " ; ".join(f"  {name}  " for name in spec.columns)
    path.write_text(
        "\n".join(
            [
                header,
                "",
                "   ",
                "  studio-x  ;  Студия X  ;  Тюмень  ;  ;  ;  preview  ",
                "",
            ]
        ),
        encoding="utf-8",
    )

    rows = read_table(spec, tmp_path)

    assert rows == [
        {
            "slug": "studio-x",
            "name": "Студия X",
            "city": "Тюмень",
            "vk_url": "",
            "demo": "",
            "data_tag": "preview",
        }
    ]


def test_duplicate_slug_names_the_sheet_and_keeps_the_overlay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Дубль ключа — ошибка валидации, а не повод перезаписать готовый overlay."""

    tables = _tables_copy(tmp_path)
    base = tmp_path / "2026.yaml"
    shutil.copyfile(BASE, base)
    out_dir = overlay_dir(base)
    data, _ = build_overlay(tables, base)
    written = write_overlay(data, out_dir)
    before = {path.name: path.read_bytes() for path in written}
    assert before

    path = tables / "studios.csv"
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    path.write_text("\n".join([*lines, lines[1]]) + "\n", encoding="utf-8")

    assert main(["--tables-dir", str(tables), "--base", str(base)]) == 1
    stderr = capsys.readouterr().err

    assert "studios.csv" in stderr, "ошибка не называет лист"
    assert "slug «pechat-tantsa» повторяется" in stderr, "ошибка не называет ключ"
    assert re.search(r"впервые в строке \d+", stderr), "нет номера первой строки"
    assert {path.name: path.read_bytes() for path in written} == before, (
        "невалидная таблица перезаписала существующий overlay"
    )
