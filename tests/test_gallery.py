"""§6.5: фотоархив — превью со ссылкой в источник, а не копия чужого снимка.

§6.11: портреты жюри — одного формата; рисунок подписан «иллюстрация»,
а не выдан за фотографию человека.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zaruba.config import Settings
from zaruba.content import ContentError, load_edition, load_history, load_photos, load_site
from zaruba.main import create_app
from zaruba.models import Judge, Photo, PhotoKind
from zaruba.services.gallery import file_photos, gallery
from zaruba.services.history import history_rows
from zaruba.templating import initials

REPO_ROOT = Path(__file__).resolve().parents[1]
PHOTOS_FILE = REPO_ROOT / "content" / "photos.yaml"
HISTORY_FILE = REPO_ROOT / "content" / "history.yaml"
EDITION_FILE = REPO_ROOT / "content" / "editions" / "2026.yaml"
STATIC_DIR = REPO_ROOT / "src" / "zaruba" / "static"
TOOLS_DIR = REPO_ROOT / "tools"

SOURCE = {
    "id": "vk-community",
    "title": "Сообщество «ЗАРУБА72»",
    "url": "https://vk.com/zaruba72champ",
    "retrieved_at": "2026-10-24",
    "trust": "official",
}


def _photo(**overrides: object) -> Photo:
    payload: dict[str, object] = {
        "slug": "2024-stage",
        "year": 2024,
        "title": "«ЗАРУБА 2024» — сцена",
        "caption": "Второй блок 8 декабря: выход на сцену и судейский стол.",
        "source": dict(SOURCE),
    }
    payload.update(overrides)
    return Photo.model_validate(payload)


def test_history_page_has_photo_archive(client: TestClient) -> None:
    html = client.get("/history").text

    assert "Фотоархив" in html
    assert 'class="shots"' in html
    assert html.count('class="shot__frame"') == 4, "карточка на каждый подтверждённый выпуск"
    assert "открыть в источнике" in html
    assert "https://vk.com/zaruba72champ" in html
    assert 'rel="noopener noreferrer nofollow"' in html, "чужие ссылки — nofollow (§6.9)"


def test_history_page_does_not_hotlink_foreign_images(client: TestClient) -> None:
    """Ни одного `src` на чужой домен: CSP `img-src 'self' data:` не пропустит."""

    html = client.get("/history").text

    for attribute in ('src="', 'srcset="'):
        for chunk in html.split(attribute)[1:]:
            value = chunk.split('"')[0]
            assert value.startswith(("/static/", "http://testserver/static/")), value
            assert "//vk.com" not in value and "//example." not in value
    assert "/static/style.css?v=" in html


def test_gallery_sorts_by_year_desc() -> None:
    cards = gallery(load_photos(PHOTOS_FILE))
    years = [card.year for card in cards]

    assert years == sorted(years, reverse=True)
    assert cards[0].year == 2025


def test_file_photos_counts_cards_with_file() -> None:
    without_file = _photo()
    with_file = _photo(
        slug="2024-hall",
        file="img/history/2024-hall.webp",
        alt="Зрительный зал на «ЗАРУБЕ 2024»",
        author="Имя Фотографа",
        license="разрешение организаторов",
    )

    assert file_photos([without_file, with_file]) == 1


def test_citation_photo_needs_purpose_author_and_source() -> None:
    """Оба условия ст. 1274 ГК РФ проверяются на входе, а не «потом»."""

    with pytest.raises(ValidationError, match="purpose"):
        _photo(file="img/history/2024-stage.webp", alt="Сцена", author="Фотограф", use="citation")
    with pytest.raises(ValidationError, match="author"):
        _photo(file="img/history/2024-stage.webp", alt="Сцена", use="citation", purpose="info")
    with pytest.raises(ValidationError, match="license"):
        _photo(
            file="img/history/2024-stage.webp",
            alt="Сцена",
            author="Фотограф",
            use="citation",
            purpose="info",
            license="разрешение организаторов",
        )


def test_citation_photo_credit_names_author_and_law() -> None:
    """Подпись называет норму: читатель видит, что это цитата, а не разрешение."""

    photo = _photo(
        file="img/history/2024-stage.webp",
        alt="Танцоры на сцене во время блока «ЗАРУБА 2024»",
        author="Имя Фотографа",
        use="citation",
        purpose="info",
    )

    assert photo.is_citation
    assert photo.credit == (
        "Фото: Имя Фотографа · цитирование, ст. 1274 ГК РФ, информационная цель"
    )


def test_photo_without_file_cannot_be_citation() -> None:
    """Карточка-ссылка ничего не цитирует: файла нет, значит и прав нет."""

    with pytest.raises(ValidationError, match="только с файлом"):
        _photo(use="citation", purpose="info")


def test_citation_card_shows_author_law_and_source(
    content_settings: Settings, tmp_path: Path
) -> None:
    """На странице видны все три части цитаты: автор, норма и источник."""

    payload = yaml.safe_load(PHOTOS_FILE.read_text(encoding="utf-8"))
    payload["photos"][0].update(
        {
            "file": "img/history/2025-anniversary.webp",
            "alt": "Зрители на юбилейной «ЗАРУБЕ 2025»",
            "author": "Имя Фотографа",
            "use": "citation",
            "purpose": "info",
        }
    )
    path = tmp_path / "photos.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    settings = content_settings.model_copy(update={"photos_file": path})

    with TestClient(create_app(settings)) as test_client:
        html = test_client.get("/history").text

    assert "Фото: Имя Фотографа · цитирование, ст. 1274 ГК РФ" in html
    assert "цитата · ст. 1274" in html
    assert "источник заимствования: Сообщество «ЗАРУБА72» во ВКонтакте" in html


def test_photo_years_are_confirmed_in_history() -> None:
    """Фото «призрачного» года (которого нет в хронологии) — ошибка данных."""

    rows = history_rows(load_site(), load_history(HISTORY_FILE))
    confirmed = {row.year for row in rows}

    for photo in load_photos(PHOTOS_FILE):
        assert photo.year in confirmed, photo.slug


def test_photo_with_file_requires_credit() -> None:
    """Файл без alt/автора/лицензии не загружается: «права потом» не работает."""

    with pytest.raises(ValidationError, match="обязательны"):
        _photo(file="img/history/2024-stage.webp")
    with pytest.raises(ValidationError, match="license"):
        _photo(file="img/history/2024-stage.webp", alt="Сцена", author="Фотограф")


def test_photo_accepts_full_credit() -> None:
    photo = _photo(
        file="img/history/2024-stage.webp",
        alt="Танцоры на сцене во время блока «ЗАРУБА 2024»",
        author="Имя Фотографа",
        license="разрешение организаторов",
    )

    assert photo.credit == "Фото: Имя Фотографа · разрешение организаторов"
    assert (photo.width, photo.height) == (960, 720)


@pytest.mark.parametrize(
    "bad_path",
    [
        "https://example.com/photo.webp",
        "../secrets/photo.webp",
        "img/other/photo.webp",
        "img/history/Photo.webp",
        "img/history/../../app.py",
    ],
)
def test_photo_rejects_foreign_or_escaping_path(bad_path: str) -> None:
    with pytest.raises(ValidationError):
        _photo(file=bad_path, alt="Сцена", author="Фотограф", license="CC BY")


def test_declared_photo_files_exist() -> None:
    for photo in load_photos(PHOTOS_FILE):
        if photo.file is None:
            continue
        path = STATIC_DIR / photo.file
        assert path.is_file(), f"{photo.slug}: нет файла {path}"
        assert path.suffix == ".webp", "шаблон ссылается только на WebP: JPEG никто не отдаёт"


def test_photos_yaml_rejects_duplicate_slug(tmp_path: Path) -> None:
    payload = yaml.safe_load(PHOTOS_FILE.read_text(encoding="utf-8"))
    payload["photos"].append(dict(payload["photos"][0]))
    path = tmp_path / "photos.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentError, match="описано дважды"):
        load_photos(path)


def test_initials_filter() -> None:
    assert initials("Александр Бурцев") == "АБ"
    assert initials("Фара") == "Ф"
    assert initials("Гео (GEO)") == "Г", "псевдоним в скобках не даёт вторую букву"
    assert initials(None) == ""


def test_judges_page_shows_photos_in_one_format(client: TestClient) -> None:
    """Портреты — пресет `portrait` (480×640, 3:4) и только свои файлы: хотлинков нет (§6.11)."""

    html = client.get("/judges").text

    assert html.count('<img class="person-photo') == 5
    assert 'width="480" height="640"' in html
    assert 'src="/static/img/people/' in html
    assert 'src="http' not in html, "чужие домены не грузим: CSP img-src 'self'"
    assert html.count(".webp") == 5, "карточки ссылаются на WebP-пресет"


def test_judge_photos_are_cited_with_source(client: TestClient) -> None:
    """Портрет — цитата: под каждым автор-публикатор, норма права и ссылка на публикацию."""

    html = client.get("/judges").text

    assert html.count('person-photo__tag--law">цитата</span>') == 5
    assert html.count("Фото: сообщество «ЗАРУБА72» · цитирование, ст. 1274 ГК РФ") == 3
    assert "Фото: Московский танцевальный центр MDC NRG · цитирование, ст. 1274 ГК РФ" in html, (
        "у Бурцева снимок со страницы MDC NRG"
    )
    assert "Фото: PROТАНЦЫ CAMP · цитирование, ст. 1274 ГК РФ" in html
    assert html.count(">источник снимка</a>") == 5
    assert "vk.com/wall-185685166_" in html
    assert "nrg-style.ru/teachers/burcev-aleksandr" in html
    assert "moscow.protancycamp.com/geo" in html
    assert "иллюстрация" not in html, "постеров в выпуске 2026 больше нет"


def test_judge_photo_requires_author_and_basis() -> None:
    """Снимок без автора и основания не загружается — как и в фотоархиве."""

    payload = {"slug": "fara", "name": "Фара", "role": "judge", "photo": "img/people/fara.webp"}

    with pytest.raises(ValidationError, match="автор"):
        Judge.model_validate(payload)
    payload["photo_author"] = "Имя Фотографа"
    with pytest.raises(ValidationError, match="license"):
        Judge.model_validate(payload)


def test_judge_citation_needs_source_url() -> None:
    """У цитаты обязателен источник: «цитата из ниоткуда» — не цитата."""

    payload = {
        "slug": "fara",
        "name": "Фара",
        "role": "judge",
        "photo": "img/people/fara.webp",
        "photo_author": "Имя Фотографа",
        "photo_use": "citation",
        "photo_purpose": "info",
    }

    with pytest.raises(ValidationError, match="source_url"):
        Judge.model_validate(payload)

    payload["photo_source_url"] = "https://vk.com/wall-1_2"
    judge = Judge.model_validate(payload)

    assert judge.photo_credit == (
        "Фото: Имя Фотографа · цитирование, ст. 1274 ГК РФ, информационная цель"
    )


def test_illustration_credit_is_built_from_fields() -> None:
    """Тип картинки решает подпись: рисунок не может оказаться «Фото: …»."""

    payload = {
        "slug": "fara",
        "name": "Фара",
        "role": "judge",
        "photo": "img/people/fara.webp",
        "photo_author": "проект «ЗАРУБА»",
        "photo_license": "CC BY 4.0",
    }

    assert Judge.model_validate(payload).photo_credit == "Фото: проект «ЗАРУБА» · CC BY 4.0"

    payload["photo_nature"] = "illustration"
    judge = Judge.model_validate(payload)

    assert judge.photo_credit == "Иллюстрация: проект «ЗАРУБА» · CC BY 4.0"


def test_licensed_photo_shows_source_link(content_settings: Settings) -> None:
    """Источник виден и у кадра с разрешения, а не только у цитаты."""

    path = content_settings.content_dir / "2026.yaml"
    raw = path.read_text(encoding="utf-8")
    marker = (
        "    photo_use: citation\n"
        "    photo_purpose: info\n"
        "    photo_source_url: https://vk.com/wall-185685166_223\n"
    )
    assert marker in raw, "у жюри изменились поля прав — поправьте фикстуру"
    path.write_text(
        raw.replace(
            marker,
            "    photo_license: 'разрешение организаторов'\n"
            "    photo_source_url: https://vk.com/wall-185685166_223\n",
            1,
        ),
        encoding="utf-8",
    )

    with TestClient(create_app(content_settings)) as page:
        html = page.get("/judges").text

    assert html.count(">источник</a>") == 1
    assert html.count(">источник снимка</a>") == 4, "остальные карточки — по-прежнему цитаты"


def test_illustration_cannot_be_citation() -> None:
    """Наш рисунок цитировать нечего: для него только `use: own`."""

    payload = {
        "slug": "fara",
        "name": "Фара",
        "role": "judge",
        "photo": "img/people/fara.webp",
        "photo_author": "проект «ЗАРУБА»",
        "photo_use": "citation",
        "photo_purpose": "info",
        "photo_source_url": "https://vk.com/wall-1_2",
        "photo_nature": "illustration",
    }

    with pytest.raises(ValidationError, match="иллюстрация"):
        Judge.model_validate(payload)


def test_judge_photo_files_exist() -> None:
    edition = load_edition(EDITION_FILE).edition

    for judge in edition.judges:
        if judge.photo is None:
            continue
        path = STATIC_DIR / judge.photo
        assert path.is_file(), f"{judge.slug}: нет файла {path}"
        assert path.suffix == ".webp", "портрет — пресет `portrait`, WebP 480×640"


def test_judge_photos_match_portrait_preset() -> None:
    """Карточка ждёт 480×640: в ней кроп 3:4 по лицу, а не афиша целиком."""

    image_module = pytest.importorskip("PIL.Image")

    for judge in load_edition(EDITION_FILE).edition.judges:
        if judge.photo is None:
            continue
        with image_module.open(STATIC_DIR / judge.photo) as image:
            assert image.size == (480, 640), f"{judge.slug}: {image.size} вместо 480×640"


def test_poster_is_deterministic() -> None:
    """Slug — это seed: пересборка не меняет уже опубликованные файлы."""

    pytest.importorskip("PIL")
    sys.path.insert(0, str(TOOLS_DIR))
    from photo_fx import poster  # noqa: PLC0415  (Pillow проверен выше)

    first = poster("fara", (120, 160))
    second = poster("fara", (120, 160))

    assert first.tobytes() == second.tobytes()
    assert poster("burtsev", (120, 160)).tobytes() != first.tobytes()


def test_dance_fx_keeps_size() -> None:
    """Эффекты меняют свет, но не геометрию: размер кадра остаётся пресетным."""

    image_module = pytest.importorskip("PIL.Image")
    sys.path.insert(0, str(TOOLS_DIR))
    from photo_fx import apply_dance_fx  # noqa: PLC0415

    source = image_module.new("RGB", PhotoKind.PORTRAIT.size, (120, 120, 130))
    result = apply_dance_fx(source, seed="fara")

    assert result.size == source.size
    assert result.tobytes() != source.tobytes(), "эффекты должны быть видны"


def test_photo_presets_are_fixed() -> None:
    """Размеры — контракт с prepare_photos.py и с шириной/высотой в шаблоне."""

    assert PhotoKind.GALLERY.size == (960, 720)
    assert PhotoKind.PORTRAIT.size == (480, 640)


def test_prepare_photos_skips_people_by_default(tmp_path: Path) -> None:
    """Прогон без аргументов не пересжимает афиши: копия из поста остаётся как есть."""

    sys.path.insert(0, str(TOOLS_DIR))
    from prepare_photos import collect  # noqa: PLC0415

    history = tmp_path / "history"
    people = tmp_path / "people"
    history.mkdir()
    (people / "portrait").mkdir(parents=True)
    (history / "stage.jpg").write_bytes(b"")
    (people / "fara.jpg").write_bytes(b"")
    (people / "portrait" / "fara.jpg").write_bytes(b"")

    assert [path.name for path in collect([tmp_path], {people})] == ["stage.jpg"]
    assert len(collect([people])) == 2, "явный путь защиту снимает: кроп прогоняют отдельно"


def test_portrait_frames_fit_preset() -> None:
    """Рамки кропа из афиш — 3:4 и внутри кадра: иначе лицо снова обрежется."""

    sys.path.insert(0, str(TOOLS_DIR))
    from crop_portraits import FRAMES, frame_problem  # noqa: PLC0415

    for slug, box in FRAMES.items():
        assert frame_problem(box, (10_000, 10_000)) is None, slug


def test_portrait_frame_rejects_wrong_geometry() -> None:
    """Кривую рамку видно до запуска: карточка обрезала бы её ещё раз по 3:4."""

    sys.path.insert(0, str(TOOLS_DIR))
    from crop_portraits import frame_problem  # noqa: PLC0415

    assert frame_problem((0, 0, 300, 400), (1024, 1280)) is None
    assert "3:4" in (frame_problem((0, 0, 300, 500), (1024, 1280)) or "")
    assert "выходит за исходник" in (frame_problem((0, 0, 300, 400), (200, 300)) or "")
