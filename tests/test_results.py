"""§6.24: `/results` показывает расчёт по листам — с оговорками, а не как протокол."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from zaruba.config import Settings
from zaruba.content import load_results_for, load_site
from zaruba.main import create_app
from zaruba.templating import score

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_results_page_renders_every_nomination(client: TestClient) -> None:
    html = client.get("/results").text

    assert html.count('class="table table--results"') == 21
    assert "Результаты 2025" in html
    assert "Места расчётные" in html
    assert "BEST ZARUBA SOLO KIDS 3-9" in html
    assert "Андрей Морыженков" in html, "состав жюри виден у каждой таблицы"
    assert "disk.yandex.ru" in html, "у таблиц есть ссылка на первоисточник"
    assert "н/ч" in html, "нечитаемая клетка не превращается в ноль"
    assert "Как это разобрано" in html


def test_minors_are_initials_and_adults_keep_names(client: TestClient) -> None:
    """Дети без согласия — без фамилии, взрослые — как в листах (`docs/06` §6.1 п.6)."""

    html = client.get("/results").text

    assert "А. Е." in html, "детская номинация: фамилия и имя сокращены"
    assert "Абабкова" not in html, "фамилия ребёнка не публикуется"
    assert "ТАТАРКО АЛЕКСАНДРА" in html, "взрослая номинация остаётся узнаваемой"
    assert "фамилий нет" in html
    assert "инициалы или сценическое имя" in html


def test_missing_sheet_withholds_places(client: TestClient) -> None:
    """Номинация без листа судьи: места никому и честная подпись."""

    html = client.get("/results").text
    results = load_results_for(Settings(), 2025)
    assert results is not None
    blocked = [
        item
        for item in results.nominations
        if not item.complete
        and any(value is None for row in item.rows for value in row.scores.values())
    ]

    assert len(blocked) == 2, "без листа остались ровно две номинации"
    assert html.count("места не присваиваются") >= len(blocked)
    assert "нет листа одного из судей" in html


def test_unreadable_cell_drops_only_its_row(client: TestClient) -> None:
    html = client.get("/results").text
    results = load_results_for(Settings(), 2025)
    assert results is not None
    partial = [item for item in results.nominations if any(row.unreadable for row in item.rows)]

    assert partial, "в данных есть нечитаемые клетки — иначе тест ничего не проверяет"
    for item in partial:
        placed = [row.place for row in item.rows if row.place is not None]
        assert sorted(placed) == list(range(1, len(placed) + 1)), "места остальных идут подряд"
        for row in item.rows:
            if row.unreadable:
                assert row.place is None
    assert html.count("неполные данные") >= 1


def test_results_page_without_data_explains_why(tmp_path: Path) -> None:
    """Пустая папка результатов — законное состояние, а не 404."""

    settings = Settings(results_dir=tmp_path / "results")
    with TestClient(create_app(settings)) as client:
        response = client.get("/results")

    assert response.status_code == 200
    assert "Почему таблиц нет" in response.text
    assert "архив" in response.text.lower()
    assert "table--results" not in response.text


def test_results_nav_and_sitemap(client: TestClient) -> None:
    html = client.get("/results").text
    sitemap = client.get("/sitemap.xml").text

    assert 'href="/results"' in html, "пункт меню ведёт на существующую страницу"
    assert "<loc>http://testserver/results</loc>" in sitemap


def test_history_links_results_only_for_parsed_year(client: TestClient) -> None:
    html = client.get("/history").text

    assert 'href="/results">результаты</a>' in html
    assert "результаты\n            2025" in html or "результаты 2025" in html


def test_results_service_keeps_order_and_totals() -> None:
    from zaruba.services.results import results_view

    bundle = next(item for item in load_site() if item.year == 2025)
    results = load_results_for(Settings(), 2025)
    assert results is not None
    view = results_view(bundle, results)

    assert [day.day for day in view.days] == [1, 2]
    for day in view.days:
        numbers = [item.number for item in day.nominations]
        assert numbers == sorted(numbers), "номинации идут по номерам листов"
        for item in day.nominations:
            for line in item.lines:
                values = [cell.value for cell in line.cells if cell.state == "value"]
                if line.total is not None:
                    assert abs(round(sum(values), 1) - line.total) <= 0.05


def test_score_filter_shows_dash_for_unknown() -> None:
    assert score(None) == "—"
    assert score(123.0) == "123,0"
    assert score(100.9) == "100,9"
