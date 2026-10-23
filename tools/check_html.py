"""Проверка HTML-презентации: баланс тегов, число слайдов, отсутствие маркеров сборки."""
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"строка {self.getpos()[0]}: лишний </{tag}>")
            return
        top, line = self.stack.pop()
        if top != tag:
            self.errors.append(f"строка {self.getpos()[0]}: </{tag}> закрывает <{top}> (открыт в {line})")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    path = root / "presentation" / "index.html"
    if not path.exists():
        print(f"FAIL: нет файла {path}")
        return 1
    text = path.read_text(encoding="utf-8")
    parser = Balance()
    parser.feed(text)
    for tag, line in parser.stack:
        parser.errors.append(f"не закрыт <{tag}> (открыт в {line})")
    for marker in ("<!--SLIDES-->", "<!--CSS-->", "<!--CSS2-->", "<!--JS-->"):
        if marker in text:
            parser.errors.append(f"остался маркер сборки {marker}")

    slides = text.count('class="slide')
    print(f"слайдов: {slides}, размер: {len(text)} символов, строк: {text.count(chr(10)) + 1}")
    for err in parser.errors:
        print("  " + err)
    print("OK: разметка корректна" if not parser.errors else f"FAIL: {len(parser.errors)} проблем(ы)")
    return 0 if not parser.errors else 1


if __name__ == "__main__":
    sys.exit(main())
