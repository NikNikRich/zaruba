"""Поиск битых символов (U+FFFD) и обрывов в docs/."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "docs"
    bad = 0
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for no, line in enumerate(text.splitlines(), start=1):
            if "\ufffd" in line:
                bad += 1
                print(f"{path.name}:{no}: {line}")
        tail = text.rstrip().splitlines()[-1]
        if tail.endswith((",", "и", "в", "на", "с", "-", "—")):
            print(f"{path.name}: подозрительный обрыв в конце: {tail!r}")
    print("OK: битых символов нет" if bad == 0 else f"FAIL: {bad} строк с U+FFFD")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
