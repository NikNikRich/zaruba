"""Собирает `content/results/<год>.yaml` из разбора судейских листов (CSV).

Лист — таблица одного судьи по одной номинации: пять критериев и «общий балл».
Балл судьи здесь — сумма критериев (её можно пересчитать), а расхождение
с напечатанным итогом становится звёздочкой, а не поводом выбросить число.

  python tools/import_results.py ../Public/zaruba_parsed.csv \
      --edition content/editions/2025.yaml --out content/results/2025.yaml

Скрипт офлайн: приложение его не импортирует. Полученный YAML попадает
в репозиторий и проверяется `python -m zaruba.content check`.

Приватность: в детских номинациях (соло и дуэты не 18+) имя сокращается
до инициалов — «Абабкова Елизавета» → «А. Е.». Названия команд не трогаем:
это коллективы, а не фамилии детей (`docs/06` §6.1 п.6).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CRITERIA = ("sl", "te", "cr", "zr", "vn")
# Порядок судей — как в таблицах отчёта и на странице.
JUDGES: tuple[dict[str, str | None], ...] = (
    {"code": "mor", "name": "Андрей Морыженков", "judge": "moryzhenkov"},
    {"code": "mate", "name": "MATÈ · Анастасия Юрасова", "judge": "yurasova"},
    {"code": "nas", "name": "Настя", "judge": None},
    {"code": "mafia", "name": "Наталья Мафия", "judge": "mafiya"},
)
ADULTS = "18+"
# Форматы, где участник — человек, а не коллектив: только здесь сокращаем имя.
PERSONAL_FORMATS = {"solo", "duet"}

NOTES = [
    "Место — расчёт по судейским листам: официального протокола с местами "
    "и баллами организаторы не публиковали.",
    "Балл судьи — сумма пяти критериев листа. Звёздочка значит, что напечатанный "
    "на листе «общий балл» с суммой не сошёлся.",
    "«н/ч» — цифра не читается: участник выпадает из рейтинга, остальные места сохраняют.",
    "«—» — листа этого судьи нет в источнике: в такой номинации места не присваиваются никому.",
    "В детских номинациях (соло и дуэты до 18) фамилий нет: только инициалы "
    "или сценическое имя — согласий на публикацию данных детей у проекта нет.",
]


def number(value: str) -> float | None:
    """Клетка листа: пустая и «н/ч» — это отсутствие числа, а не ноль."""

    text = (value or "").strip().replace(",", ".")
    if not text or text in {"н/ч", "—", "-"}:
        return None
    return float(text)


def judge_score(row: dict[str, str]) -> tuple[float | None, bool]:
    """Балл судьи и признак расхождения с напечатанным итогом."""

    values = [number(row.get(key, "")) for key in CRITERIA]
    if any(value is None for value in values):
        return None, False
    total = round(sum(value for value in values if value is not None), 1)
    written = number(row.get("total_written", ""))
    return total, written is not None and abs(total - written) > 0.05


def initials(name: str) -> str:
    """«Абабкова Елизавета» → «А. Е.»; односложный псевдоним оставляем.

    Псевдоним из одного слова («Emily», «Менострелка») не фамилия: сокращать
    его до буквы — значит сделать таблицу нечитаемой и не защитить никого.
    """

    words = [word for word in name.split() if word[:1].isalpha()]
    if len(words) < 2:
        return name
    return f"{words[0][0].upper()}. {words[1][0].upper()}."


def visible(name: str, nomination: dict) -> str:
    """Имя для публикации: детей в соло и дуэтах показываем инициалами."""

    if nomination["format"] not in PERSONAL_FORMATS:
        return name
    if nomination["age_group"] == ADULTS:
        return name
    return " и ".join(initials(part) for part in name.split(" и "))


def build(csv_path: Path, edition_path: Path) -> dict:
    edition = yaml.safe_load(edition_path.read_text(encoding="utf-8"))
    nominations = [item for item in edition["nominations"] if not item.get("demo")]
    order: dict[int, list[str]] = {}
    by_slug: dict[str, dict] = {}
    for item in nominations:
        order.setdefault(item["day"], []).append(item["slug"])
        by_slug[item["slug"]] = item

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    groups: OrderedDict[tuple[int, int], OrderedDict[str, dict[str, dict[str, str]]]] = (
        OrderedDict()
    )
    for row in rows:
        key = (int(row["day"]), int(row["nom"]))
        groups.setdefault(key, OrderedDict()).setdefault(row["participant"], {})[row["judge"]] = row
    for key in sorted(groups):
        groups[key] = OrderedDict(sorted(groups[key].items()))

    result: dict = {
        "edition": edition["edition"],
        "updated_at": datetime.now(ZoneInfo(edition["timezone"])).isoformat(timespec="seconds"),
        "source_id": "judge-sheets-2025",
        "status": "unconfirmed",
        "judges": [
            {"code": item["code"], "name": item["name"], "judge": item["judge"]} for item in JUDGES
        ],
        "nominations": [],
        "notes": NOTES,
    }

    for (day, nom), participants in groups.items():
        slug = order[day][nom - 1]
        nomination = by_slug[slug]
        # Состав жюри — те, чьи листы в этой номинации вообще есть: у «Насти»
        # листов нет в части номинаций, и это факт источника, а не пропуск.
        present = [
            item["code"]
            for item in JUDGES
            if any(item["code"] in per for per in participants.values())
        ]
        prepared = []
        for name, per in participants.items():
            cells: dict[str, float | None] = {}
            unreadable: list[str] = []
            flagged: list[str] = []
            for code in present:
                if code not in per:
                    cells[code] = None  # листа судьи нет: клетка «—»
                    continue
                value, diff = judge_score(per[code])
                if value is None:
                    # Цифра не читается: код идёт в `unreadable`, а в `scores`
                    # его нет — иначе строка одновременно с баллом и без него.
                    unreadable.append(code)
                    continue
                cells[code] = value
                if diff:
                    flagged.append(code)
            known = [value for value in cells.values() if value is not None]
            total = round(sum(known), 1) if known else None
            prepared.append(
                {
                    "participant": visible(name, nomination),
                    "scores": cells,
                    "unreadable": unreadable,
                    "total": total,
                    "flagged": flagged,
                }
            )
        # Нет листа судьи — суммы несопоставимы, места не присваиваются никому.
        blocked = any(value is None for row in prepared for value in row["scores"].values())
        prepared.sort(key=lambda item: (-(item["total"] or 0), item["participant"]))
        place = 0
        for row in prepared:
            if not blocked and not row["unreadable"]:
                place += 1
                row["place"] = place
            else:
                row["place"] = None
        result["nominations"].append(
            {
                "nomination": slug,
                "day": day,
                "number": nom,
                "judges": present,
                "rows": prepared,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Судейские листы CSV → YAML результатов")
    parser.add_argument("csv", type=Path, help="zaruba_parsed.csv")
    parser.add_argument("--edition", type=Path, required=True, help="content/editions/2025.yaml")
    parser.add_argument("--out", type=Path, required=True, help="content/results/2025.yaml")
    args = parser.parse_args(argv)

    data = build(args.csv, args.edition)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    rows = sum(len(item["rows"]) for item in data["nominations"])
    print(f"OK: {args.out} — {len(data['nominations'])} номинаций, {rows} строк")
    return 0


if __name__ == "__main__":
    sys.exit(main())
