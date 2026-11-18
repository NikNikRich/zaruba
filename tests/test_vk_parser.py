"""Разбор постов ВК в черновики (`docs/09` §9.14).

Проверки держат границу «бот ничего не выдумывает»: тип блока определяется по
вложениям и ссылкам, заголовок и текст укладываются в лимиты моделей `Alert` и
`MediaItem` (иначе `content check` отклонит публикацию), а нераспознанный пост
не превращается в плашку с придуманным разделом.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from zaruba.services import max_bot, vk_parser
from zaruba.services.vk import Post

OWNER = -185685166
AT = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
ALERT_POST = (
    "Внимание! Перенос блока\n"
    "Блок 3 переносится с 15:00 на 16:30, расписание на сайте обновим. #заруба #тюмень"
)
VIDEO_POST = "Смотрите тизер ЗАРУБА 2026 — https://vk.com/video-185685166_456239017 #заруба"


def post(text: str = "", *, post_id: int = 243, kinds: tuple[str, ...] = ()) -> Post:
    return Post(OWNER, post_id, AT, text, kinds)


def draft_of(text: str, *, kinds: tuple[str, ...] = ()) -> vk_parser.Draft:
    draft = vk_parser.build_draft(post(text, kinds=kinds))
    assert draft is not None, "пост должен был стать черновиком"
    return draft


def test_announcement_becomes_an_alert_draft_with_the_schedule_section() -> None:
    draft = draft_of(ALERT_POST)

    assert draft.kind is vk_parser.DraftKind.ALERT
    assert draft.id == "vk-243"
    assert draft.status is vk_parser.DraftStatus.PENDING
    assert draft.title == "Внимание! Перенос блока"
    assert draft.urgent is True
    assert draft.link_slug == "schedule"
    assert draft.post_url == f"https://vk.com/wall{OWNER}_243"


def test_alert_text_stays_in_limits_and_names_its_source() -> None:
    """`Alert.text` — 10…280 символов, и факт без ссылки на первоисточник нельзя."""

    draft = draft_of(ALERT_POST + " " + "Очень длинное продолжение поста. " * 40)

    assert draft.text.endswith(draft.post_url)
    assert "Источник: https://vk.com/wall" in draft.text
    assert len(draft.text) <= vk_parser.TEXT_LIMIT
    assert len(draft.title) <= vk_parser.TITLE_LIMIT
    assert "#заруба" not in draft.text, "хештеги на сайте не нужны"


def test_video_post_becomes_a_media_draft() -> None:
    draft = draft_of(VIDEO_POST, kinds=("video",))

    assert draft.kind is vk_parser.DraftKind.MEDIA
    assert draft.media_kind == "video"
    assert draft.platform == "VK Видео"
    assert draft.media_url == "https://vk.com/video-185685166_456239017"
    assert draft.link_slug == "media"
    assert draft.urgent is False, "медиа не поднимает плашку на главной"
    assert draft.title.startswith("Смотрите тизер")


def test_plain_post_is_not_a_draft() -> None:
    """Поздравление без сигнальных слов: раздел и текст плашки пришлось бы выдумать."""

    assert vk_parser.build_draft(post("Спасибо всем, кто танцевал с нами в этом сезоне!")) is None


def test_photo_report_on_yandex_disk_is_a_photo_card() -> None:
    draft = draft_of("Фотоотчёт с прошлого сезона: https://disk.yandex.ru/d/zaruba-2025")

    assert draft.kind is vk_parser.DraftKind.MEDIA
    assert draft.media_kind == "photo"
    assert draft.platform == "Яндекс Диск"
    assert draft.media_url == "https://disk.yandex.ru/d/zaruba-2025"


def test_rutube_stream_is_named_by_its_host() -> None:
    draft = draft_of("Прямой эфир финала — https://rutube.ru/video/zaruba-final-2026/")

    assert draft.media_kind == "stream"
    assert draft.platform == "Rutube"


def test_video_post_without_text_gets_an_honest_title_and_no_invented_words() -> None:
    draft = draft_of("", kinds=("video",))

    assert draft.kind is vk_parser.DraftKind.MEDIA
    assert draft.title == "Материал «ЗАРУБА72» от 18.11.2026"
    assert draft.text == "Пост без текста (вложение: video)"
    assert draft.media_url == f"https://vk.com/wall{OWNER}_243"


def test_title_skips_a_line_of_hashtags_and_clips_a_long_one() -> None:
    assert (
        vk_parser.extract_title("#заруба\n\n  Внимание! Отмена блока  ") == "Внимание! Отмена блока"
    )
    title = vk_parser.extract_title("Слово " * 40)
    assert len(title) <= vk_parser.TITLE_LIMIT
    assert title.endswith("…")


def test_section_is_found_by_keywords() -> None:
    assert vk_parser.guess_slug("Открыта регистрация на чемпионат") == "tickets"
    assert vk_parser.guess_slug("Вход в зал через служебную дверь") == "tickets"
    assert vk_parser.guess_slug("Итоги и протоколы уже на сайте") == "results"
    assert vk_parser.guess_slug("Перенос блока на час позже") == "schedule"
    assert vk_parser.guess_slug("Просто важная новость") is None


def test_picture_in_the_post_goes_to_the_owner_card() -> None:
    draft = draft_of(ALERT_POST, kinds=("photo",))

    assert draft.has_picture is True
    assert vk_parser.PICTURE_NOTE in vk_parser.card_body(draft)["text"]


def test_document_attachment_counts_as_a_picture() -> None:
    assert draft_of(ALERT_POST, kinds=("doc",)).has_picture is True


def test_card_shows_what_goes_to_the_site_and_two_commands() -> None:
    body = vk_parser.card_body(draft_of(ALERT_POST))

    assert "Оповещение на сайт" in body["text"]
    assert "Заголовок: Внимание! Перенос блока" in body["text"]
    assert "/schedule — Расписание" in body["text"]
    assert "Опубликовать vk-243" in body["text"]
    assert "Оригинал: https://vk.com/wall" in body["text"]
    buttons = body["attachments"][0]["payload"]["buttons"]
    assert buttons[0][0]["payload"] == "publish:vk-243"
    assert buttons[1][0]["payload"] == "reject:vk-243"
    assert buttons[2][0]["url"].startswith("https://vk.com/wall")


def test_card_without_section_says_so_instead_of_inventing_one() -> None:
    body = vk_parser.card_body(draft_of("Внимание! Важная новость без раздела"))

    assert "не определён" in body["text"]


def test_card_section_titles_match_real_pages() -> None:
    """Раздел в карточке — настоящий slug сайта, иначе ссылка вела бы на 404."""

    slugs = {section.slug for section in max_bot.SECTIONS}
    assert set(vk_parser.SECTION_TITLES) <= slugs


def test_drafts_round_trip_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    draft = draft_of(ALERT_POST)

    vk_parser.upsert_draft(path, draft)

    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == vk_parser.DRAFT_VERSION
    assert payload["drafts"][0]["status"] == "pending"
    assert vk_parser.load_drafts(path) == [draft]
    assert not list(tmp_path.glob(".*.tmp")), "временный файл не должен оставаться"


def test_missing_drafts_file_is_an_empty_log(tmp_path: Path) -> None:
    assert vk_parser.load_drafts(tmp_path / "нет-такого.json") == []


def test_broken_drafts_file_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(vk_parser.DraftError, match="vk_drafts.json"):
        vk_parser.load_drafts(path)


def test_upsert_keeps_one_row_per_post(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    vk_parser.upsert_draft(path, draft_of(ALERT_POST))
    second = draft_of("Внимание! Вторая новость того же поста")

    vk_parser.upsert_draft(path, second)

    drafts = vk_parser.load_drafts(path)
    assert [draft.id for draft in drafts] == ["vk-243"]
    assert drafts[0].title == second.title


def test_mark_draft_moves_it_once_and_reports_an_unknown_id(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    vk_parser.upsert_draft(path, draft_of(ALERT_POST))

    updated = vk_parser.mark_draft(path, "vk-243", vk_parser.DraftStatus.REJECTED)

    assert updated.status is vk_parser.DraftStatus.REJECTED
    assert vk_parser.load_drafts(path)[0].status is vk_parser.DraftStatus.REJECTED
    with pytest.raises(vk_parser.DraftError, match="vk-999"):
        vk_parser.mark_draft(path, "vk-999", vk_parser.DraftStatus.PUBLISHED)


def test_unknown_host_is_labelled_other_without_inventing_a_platform() -> None:
    """Хостинг не из списка не превращается в «VK Видео»: подпись будет «Другое»."""

    assert vk_parser.platform_for("https://example.com/zaruba.mp4").value == "Другое"


def test_video_attachment_without_a_link_is_platformed_as_vk_video() -> None:
    draft = draft_of("Наш новый ролик", kinds=("short_video",))

    assert draft.platform == "VK Видео"
    assert draft.media_url == f"https://vk.com/wall{OWNER}_243"


def test_repeated_media_link_is_kept_once() -> None:
    link = "https://vk.com/video-185685166_456239017"

    assert vk_parser.media_links(f"{link} и ещё раз {link}.") == [link]


def test_long_word_without_spaces_is_clipped_not_broken() -> None:
    title = vk_parser.extract_title("Заруба" * 40)

    assert len(title) <= vk_parser.TITLE_LIMIT
    assert title.endswith("…")


def test_link_only_post_admits_that_it_has_no_text() -> None:
    draft = draft_of("https://rutube.ru/video/zaruba-final-2026/")

    assert draft.text == "Пост без текста (вложение: нет)"


def test_draft_row_without_id_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    path.write_text(json.dumps({"version": 1, "drafts": [{"kind": "alert"}]}), encoding="utf-8")

    with pytest.raises(vk_parser.DraftError, match="нет id"):
        vk_parser.load_drafts(path)


def test_draft_row_with_unknown_kind_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    path.write_text(
        json.dumps({"version": 1, "drafts": [{"id": "vk-1", "kind": "news"}]}), encoding="utf-8"
    )

    with pytest.raises(vk_parser.DraftError, match="vk-1"):
        vk_parser.load_drafts(path)


def test_drafts_field_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    path.write_text(json.dumps({"version": 1, "drafts": {}}), encoding="utf-8")

    with pytest.raises(vk_parser.DraftError, match="drafts"):
        vk_parser.load_drafts(path)


def test_json_array_instead_of_an_object_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vk_drafts.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(vk_parser.DraftError, match="JSON-объект"):
        vk_parser.load_drafts(path)


def test_album_link_without_the_word_photo_is_a_photo_card() -> None:
    draft = draft_of("Смотрите тут: https://vk.com/album-185685166_0")

    assert draft.kind is vk_parser.DraftKind.MEDIA
    assert draft.media_kind == "photo"
