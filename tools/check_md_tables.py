"""Проверка markdown-таблиц в docs/: одинаковое число ячеек и строка-разделитель."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def cells(line: str) -> int:
    return len(re.sub(r"\\\|", "", line).strip().strip("|").split("|"))


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "docs"
    problems = 0
    for path in sorted(root.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        i, fence = 0, False
        while i < len(lines):
            line = lines[i]
            if line.startswith("```"):
                fence = not fence
                i += 1
                continue
            if not fence and line.lstrip().startswith("|"):
                start = i
                block = []
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    block.append(lines[i])
                    i += 1
                counts = {cells(b) for b in block}
                ok_sep = len(block) > 1 and SEP.match(block[1]) is not None
                if len(counts) != 1 or not ok_sep:
                    problems += 1
                    print(f"{path.name}:{start + 1}: cells={sorted(counts)} sep_ok={ok_sep}")
            else:
                i += 1
    print("OK: all tables consistent" if problems == 0 else f"FAIL: {problems} table(s)")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
