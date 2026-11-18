"""Мастер-таблица организаторов: `docs/templates/ZARUBA_2026_MASTER.xlsx`.

Один файл вместо девяти CSV: на вкладке «КАК ЗАПОЛНЯТЬ» — правила простыми
словами, на рабочих вкладках — русская шапка, вторая строка-подсказка «что
писать» и выпадающие списки вместо набора вручную. Данные берутся из
`docs/templates/*.csv`, поэтому книга не расходится с тем, что читает
`python -m zaruba.sync_tables`.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe scripts/export_master_xlsx.py
    .venv\\Scripts\\python.exe scripts/export_master_xlsx.py --out D:\\tmp\\master.xlsx

Содержимое книги — функция от CSV: листы, шапки, подсказки и строки собираются
из `docs/templates/*.csv`, поэтому руками книгу не правят. Тест сверяет значения
ячеек опубликованного файла с новой сборкой; байты не сверяются — zip-контейнер
хранит время записи и меняется от запуска к запуску.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402
from openpyxl.worksheet.worksheet import Worksheet  # noqa: E402

from zaruba.sync_tables import (  # noqa: E402
    ALERT_COLUMNS,
    ALERTS_FILENAME,
    COLUMN_HINTS,
    COLUMN_TITLES,
    SCORE_COLUMNS,
    SCORES_FILENAME,
    SELECTS,
    TABLE_SPECS,
    TableSpec,
    parse_alerts,
    read_table,
)

DEFAULT_TABLES_DIR = REPO_ROOT / "docs" / "templates"
DEFAULT_OUT = DEFAULT_TABLES_DIR / "ZARUBA_2026_MASTER.xlsx"

# Порядок вкладок — порядок работы организатора: сначала «что когда», потом
# участники, потом оценки и объявления, потом материалы и мерч.
WORK_SHEETS: tuple[tuple[str, str], ...] = (
    ("Расписание", "blocks"),
    ("Номинации", "nominations"),
    ("Судьи", "judges"),
    ("Студии", "studios"),
    ("Участники", "participants"),
    ("Выступления", "performances"),
    ("Оценки судей", "scores"),
    ("Оповещения", "alerts"),
    ("Фото и видео", "media"),
    ("Мерч и партнеры", "commerce"),
)

SHEET_ABOUT = {
    "blocks": "когда и что идёт на сцене",
    "nominations": "номинации выпуска и их правила",
    "judges": "жюри: имена, регалии, портреты",
    "studios": "студии и команды, которые приехали",
    "participants": "участники: имена, возраст, студия",
    "performances": "кто, с чем и когда выходит",
    "scores": "оценки судей и их публикация",
    "alerts": "срочные сообщения в чат MAX",
    "media": "видео, фото и трансляции",
    "commerce": "мерч, фотопакеты, партнёры",
}

SPECS: dict[str, TableSpec] = {spec.section: spec for spec in TABLE_SPECS}

# Палитра: графитовая шапка с лаймовым акцентом и мятная строка-подсказка.
HEADER_FILL = PatternFill("solid", fgColor="1B1B24")
HINT_FILL = PatternFill("solid", fgColor="F4F7E6")
SUBTITLE_FILL = PatternFill("solid", fgColor="EFEFEF")
LIME = "C6F73A"
HEADER_FONT = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
LIME_FONT = Font(name="Calibri", size=12, bold=True, color=LIME)
HINT_FONT = Font(name="Calibri", size=10, italic=True, color="4A4A55")
CELL_FONT = Font(name="Calibri", size=11, color="1B1B24")
HEADER_ALIGNMENT = Alignment(horizontal="left", vertical="center", wrap_text=True)
HINT_ALIGNMENT = Alignment(horizontal="left", vertical="top", wrap_text=True)
CELL_ALIGNMENT = Alignment(horizontal="left", vertical="top", wrap_text=True)
HEADER_BORDER = Border(bottom=Side(style="medium", color=LIME))

COLUMN_WIDTHS: dict[str, int] = {
    "slug": 22,
    "name": 30,
    "title": 34,
    "text": 46,
    "note": 30,
    "regalia": 40,
    "description": 46,
    "url": 38,
    "vk_url": 32,
    "video_url": 34,
    "photo_source_url": 34,
    "photo": 26,
    "photo_license": 28,
    "photo_author": 26,
    "starts_at": 10,
    "ends_at": 10,
    "day": 8,
    "score": 12,
    "place": 8,
    "price": 12,
    "order_no": 10,
    "nominations": 34,
    "participants": 30,
    "display_name": 28,
    "participant": 26,
}
MIN_WIDTH = 10
MAX_WIDTH = 52


def _read_text(path: Path) -> str:
    """CSV из репозитория: UTF-8 (с BOM или без), запасной вариант — cp1251."""

    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"{path}: не удалось прочитать кодировку")


def _csv_rows(text: str) -> list[dict[str, str]]:
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [
        {(key or "").strip(): (value or "").strip() for key, value in row.items() if key}
        for row in reader
        if any((value or "").strip() for value in row.values())
    ]


def _display(section: str, column: str, value: str) -> str:
    """Системное значение → русская подпись из выпадающего списка."""

    if not value:
        return ""
    text = value.strip().lower()
    if column == "published":
        # В CSV подтверждение пишут словами «да»/«нет», в книге — «ДА»/«НЕТ».
        if text in {"да", "yes", "true", "1"}:
            return "ДА"
        if text in {"нет", "no", "false", "0"}:
            return "НЕТ"
    choices = SELECTS.get((section, column)) or SELECTS.get(("*", column))
    if not choices:
        return value
    for label, system in choices.items():
        if text == system.lower():
            return label
    return value


def _columns_for(section: str) -> tuple[str, ...]:
    if section == "scores":
        return SCORE_COLUMNS
    if section == "alerts":
        return ALERT_COLUMNS
    return SPECS[section].columns


def _rows_for(section: str, tables_dir: Path) -> list[dict[str, str]]:
    """Строки листа из тех же CSV, которые читает `sync_tables`."""

    if section == "alerts":
        path = tables_dir / ALERTS_FILENAME
        if not path.is_file():
            return []
        return [
            {
                "id": item.id,
                "status": item.status.value,
                "title": item.title,
                "text": item.text,
                "link_slug": item.link_slug or "",
                "urgent": str(item.urgent).lower(),
            }
            for item in parse_alerts(_read_text(path), path)
        ]
    if section == "scores":
        path = tables_dir / SCORES_FILENAME
        if not path.is_file():
            return []
        return _csv_rows(_read_text(path))
    return read_table(SPECS[section], tables_dir)


INSTRUCTION_BLOCKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "1. Золотые правила",
        "F7E9A8",
        (
            "Не удаляйте первые две строки на каждой вкладке: строка 1 — шапка, "
            "строка 2 — подсказка «что писать».",
            "Не переименовывайте вкладки: по их названиям сайт находит данные.",
            "Одна строка — одна запись. Пустая клетка значит «данных нет», а не ноль.",
            "Колонка «Тип данных»: подтверждено — факт из источника, "
            "пример — демонстрационная строка.",
            "Не придумывайте данные: нет факта — оставьте клетку пустой.",
        ),
    ),
    (
        "2. Как обновлять расписание и участников",
        "DFF3C8",
        (
            "Вкладка «Расписание»: день, начало и конец в формате ЧЧ:ММ, например 14:30.",
            "Вкладка «Номинации»: код, название, формат, возрастная группа, день.",
            "Порядок заполнения: «Студии» → «Участники» → «Выступления». "
            "Код студии и код участника связывают строки между вкладками.",
            "Коды (латиницей, без пробелов) не меняйте после публикации: "
            "по ним связаны расписание, результаты и мини-приложение.",
            "Сайт обновляется сам: раз в 30 минут он забирает таблицу и публикует страницы.",
        ),
    ),
    (
        "3. Как вносить оценки судей",
        "DCE9FF",
        (
            "Вкладка «Оценки судей»: код выступления, номинация, студия, "
            "участник, балл и место (1, 2, 3 или пусто).",
            "Колонка «Опубликовать на сайте?» — выпадающий список. ДА — строка "
            "уходит на сайт и в мини-приложение.",
            "НЕТ или пустая клетка — строка остаётся в таблице и не публикуется "
            "ни при каких условиях: ни на сайте, ни в мини-приложении.",
            "Публикуйте только после того, как главный судья или оргкомитет "
            "подтвердил протокол. Черновик можно держать в таблице сколько нужно.",
            "Участники до 18 лет — только инициалы или сценическое имя: «А. К.», «Лисичка».",
        ),
    ),
    (
        "4. Как отправлять срочные оповещения в MAX",
        "FFE3D3",
        (
            "Вкладка «Оповещения»: код, статус, заголовок (до 80 символов), "
            "текст (до 280), страница сайта и срочность.",
            "«Отправить оповещение?» — выпадающий список: Опубликовать — уйдёт "
            "в чат MAX, Черновик — останется в таблице.",
            "Срочное «да» — короткая плашка на главной и в расписании; "
            "«нет» — сообщение уйдёт только в чат.",
            "Страница на сайте — одно слово без слэшей, например schedule или results.",
        ),
    ),
)


def _instructions_sheet(workbook: Workbook) -> Worksheet:
    """Первая вкладка: правила крупным шрифтом, цветными блоками."""

    sheet = workbook.active
    sheet.title = "КАК ЗАПОЛНЯТЬ"
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = 5
    for column in ("B", "C", "D"):
        sheet.column_dimensions[column].width = 44

    sheet.merge_cells("A1:D1")
    title = sheet["A1"]
    title.value = "ЗАРУБА 2026 — мастер-таблица организаторов"
    title.font = Font(name="Calibri", size=20, bold=True, color="1B1B24")
    title.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 34

    sheet.merge_cells("A2:D2")
    subtitle = sheet["A2"]
    subtitle.value = (
        "Заполняйте русские клетки: шапка и подсказка уже готовы, а в колонках "
        "со списками достаточно выбрать значение. Сайт и мини-приложение читают "
        "эту таблицу сами — вручную ничего переносить не нужно."
    )
    subtitle.font = Font(name="Calibri", size=12, italic=True, color="4A4A55")
    subtitle.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    sheet.row_dimensions[2].height = 44

    row = 4
    for heading, color, lines in INSTRUCTION_BLOCKS:
        fill = PatternFill("solid", fgColor=color)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        cell = sheet.cell(row=row, column=1, value=heading)
        cell.font = Font(name="Calibri", size=14, bold=True, color="1B1B24")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="left", vertical="center")
        sheet.row_dimensions[row].height = 26
        row += 1
        for line in lines:
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
            cell = sheet.cell(row=row, column=1, value=f"• {line}")
            cell.font = Font(name="Calibri", size=12, color="1B1B24")
            cell.fill = fill
            cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            sheet.row_dimensions[row].height = 34
            row += 1
        row += 1

    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    footer = sheet.cell(row=row, column=1)
    footer.value = (
        "Файл — копия для заполнения. В Google Таблицы его заливают один раз, "
        "дальше команда правит таблицу в браузере: инструкция — "
        "docs/08-deploy-and-sheets.md."
    )
    footer.font = Font(name="Calibri", size=10, italic=True, color="6B6B76")
    footer.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    return sheet


def _column_width(column: str, title: str, values: list[str]) -> int:
    base = COLUMN_WIDTHS.get(column)
    if base:
        return base
    width = max(len(title) + 2, MIN_WIDTH)
    if values:
        width = max(width, min(max(len(value) for value in values) + 2, MAX_WIDTH))
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def _choices(section: str, column: str) -> tuple[str, ...]:
    choices = SELECTS.get((section, column)) or SELECTS.get(("*", column))
    return tuple(choices) if choices else ()


def _build_sheet(
    workbook: Workbook, title: str, section: str, rows: list[dict[str, str]]
) -> Worksheet:
    """Рабочая вкладка: шапка, подсказка, данные и выпадающие списки."""

    sheet = workbook.create_sheet(title)
    columns = _columns_for(section)
    titles = [COLUMN_TITLES[section][column] for column in columns]
    hints = [COLUMN_HINTS[section].get(column, "") for column in columns]

    sheet.append(titles)
    sheet.append(hints)
    for row in rows:
        sheet.append([_display(section, column, row.get(column, "")) for column in columns])

    for index, (column, column_title) in enumerate(zip(columns, titles, strict=True), start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        values = [_display(section, column, row.get(column, "")) for row in rows if row.get(column)]
        sheet.column_dimensions[letter].width = _column_width(column, column_title, values)
        header = sheet.cell(row=1, column=index)
        header.font = LIME_FONT if index == 1 else HEADER_FONT
        sheet.cell(row=2, column=index).font = HINT_FONT
        choices = _choices(section, column)
        if choices:
            validation = DataValidation(
                type="list",
                formula1='"' + ",".join(choices) + '"',
                allow_blank=True,
            )
            validation.error = "Выберите значение из списка — так строка гарантированно прочитается"
            validation.errorTitle = "Значение не из списка"
            validation.prompt = hints[index - 1]
            validation.promptTitle = column_title
            last_row = max(60, len(rows) + 20)
            validation.add(f"{letter}3:{letter}{last_row}")
            sheet.add_data_validation(validation)

    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = HEADER_BORDER
    sheet.row_dimensions[1].height = 40

    for cell in sheet[2]:
        cell.fill = HINT_FILL
        cell.alignment = HINT_ALIGNMENT
    sheet.row_dimensions[2].height = 46

    for row in sheet.iter_rows(min_row=3):
        for cell in row:
            cell.font = CELL_FONT
            cell.alignment = CELL_ALIGNMENT

    sheet.freeze_panes = "A3"
    sheet.sheet_properties.tabColor = "1B1B24"
    return sheet


def build_workbook(tables_dir: Path = DEFAULT_TABLES_DIR, target: Path = DEFAULT_OUT) -> Path:
    """Собирает книгу из CSV: инструкция плюс десять рабочих вкладок."""

    workbook = Workbook()
    workbook.properties.title = "ЗАРУБА 2026 — мастер-таблица организаторов"
    workbook.properties.creator = "ЗАРУБА"
    _instructions_sheet(workbook)
    for title, section in WORK_SHEETS:
        _build_sheet(workbook, title, section, _rows_for(section, tables_dir))
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/export_master_xlsx.py",
        description="Мастер-таблица организаторов → docs/templates/ZARUBA_2026_MASTER.xlsx",
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=DEFAULT_TABLES_DIR,
        help="папка с CSV (по умолчанию docs/templates)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="путь к xlsx (по умолчанию docs/templates/ZARUBA_2026_MASTER.xlsx)",
    )
    args = parser.parse_args(argv)

    path = build_workbook(args.tables_dir, args.out)
    print(f"OK  мастер-таблица: {path}")
    print(f"OK  вкладок: {len(WORK_SHEETS) + 1} (включая «КАК ЗАПОЛНЯТЬ»)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
