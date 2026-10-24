"""§6.21: ошибки запроса — человеческим языком, а не текстом pydantic."""

from __future__ import annotations

from typing import Any

from fastapi.exceptions import RequestValidationError

from zaruba.errors import MAX_EXPECTED, query_errors


def _errors(*items: dict[str, Any]) -> RequestValidationError:
    return RequestValidationError(list(items))


def _only_message(*items: dict[str, Any]) -> str:
    return query_errors(_errors(*items))[0]["message"]


def test_expected_values_lose_machine_quotes_and_or() -> None:
    """`"'3-6', '18+' or 'any'"` → «3-6, 18+ или any»: так это читает человек."""

    message = _only_message(
        {"type": "enum", "loc": ("query", "age"), "ctx": {"expected": "'3-6', '18+' or 'any'"}}
    )

    assert message == "допустимые значения: 3-6, 18+ или any"


def test_single_expected_value_stays_as_is() -> None:
    message = _only_message({"type": "enum", "loc": ("query", "day"), "ctx": {"expected": "'1'"}})

    assert message == "допустимые значения: 1"


def test_empty_expected_list_falls_back_to_raw_text() -> None:
    """Строка из одних разделителей не превращается в пустоту: сырой текст честнее."""

    message = _only_message({"type": "enum", "loc": ("query", "style"), "ctx": {"expected": ","}})

    assert message == "допустимые значения: ,"


def test_long_expected_list_is_truncated() -> None:
    """Длинный `expected` не растягивает страницу на экран: обрезаем и ставим «…»."""

    raw = ", ".join(f"'вариант-{index}'" for index in range(40))
    message = _only_message({"type": "enum", "loc": ("query", "style"), "ctx": {"expected": raw}})

    assert message.endswith("…")
    assert len(message) == len("допустимые значения: ") + MAX_EXPECTED


def test_numeric_bounds_become_phrases() -> None:
    assert (
        _only_message({"type": "int", "loc": ("query", "day"), "ctx": {"ge": 1, "le": 3}})
        == "допустимо от 1 до 3"
    )
    assert _only_message({"type": "int", "loc": (), "ctx": {"le": 3}}) == "допустимо не больше 3"
    assert _only_message({"type": "int", "loc": (), "ctx": {"ge": 1}}) == "допустимо не меньше 1"
    assert _only_message({"type": "int", "loc": (), "ctx": {"lt": 5}}) == "должно быть меньше 5"
    assert _only_message({"type": "int", "loc": (), "ctx": {"gt": 0}}) == "должно быть больше 0"
    assert (
        _only_message({"type": "str", "loc": (), "ctx": {"max_length": 80}})
        == "не длиннее 80 символов"
    )
    assert (
        _only_message({"type": "str", "loc": (), "ctx": {"min_length": 2}})
        == "не короче 2 символов"
    )


def test_known_kinds_get_a_hint() -> None:
    assert _only_message({"type": "int_parsing", "loc": ("query", "day")}) == "нужно целое число"
    assert (
        _only_message({"type": "string_too_long", "loc": ("query", "q")})
        == "слишком длинное значение"
    )
    assert _only_message({"type": "missing", "loc": ("query", "q")}) == "параметр обязателен"


def test_unknown_kind_gets_a_generic_message() -> None:
    assert (
        _only_message({"type": "something_else", "loc": ("query", "q")}) == "значение не подходит"
    )


def test_duplicates_are_collapsed_and_nested_path_is_joined() -> None:
    """Одна и та же ошибка дважды — один пункт; вложенный путь читается стрелкой."""

    assert query_errors(
        _errors(
            {"type": "int_parsing", "loc": ("query", "filters", "day")},
            {"type": "int_parsing", "loc": ("query", "filters", "day")},
            {"type": "int_parsing", "loc": ("query", "age")},
        )
    ) == [
        {"param": "filters → day", "message": "нужно целое число"},
        {"param": "age", "message": "нужно целое число"},
    ]


def test_error_without_location_is_called_a_request() -> None:
    assert query_errors(_errors({"type": "value_error", "loc": ()})) == [
        {"param": "запрос", "message": "значение не подходит"}
    ]
