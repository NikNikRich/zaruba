"""Посты сообщества ВКонтакте (`docs/09` §9.13): разбор `wall.get` и текст уведомления.

Главная граница проверки — сеть. Ответ VK приходит в тест фикстурой ровно в том
виде, в каком его отдал живой API 18.11.2026: у части постов `text` пустой
(видео без подписи), `date` — unix-время, `owner_id` отрицательный.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from zaruba.config import Settings
from zaruba.services import vk
from zaruba.services.vk import (
    TEXT_LIMIT,
    Post,
    VkError,
    fetch_posts,
    message_text,
    notification_body,
    parse_posts,
    unseen,
    wall_url,
)

OWNER = -185685166
AT = datetime(2026, 9, 3, 8, 48, 8, tzinfo=UTC)


def vk_settings(**overrides: Any) -> Settings:
    """Настройки без чтения `.env`: токен задаёт тест, а не машина."""

    base: dict[str, Any] = {
        "vk_token": "vk1.a.test-token",
        "vk_group": "zaruba72champ",
        "vk_api_version": "5.199",
        "vk_posts": 10,
        "vk_chat_id": 777,
        "max_alert_chat_id": None,
    }
    return Settings(**{**base, **overrides})


def make_item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "owner_id": OWNER,
        "from_id": OWNER,
        "id": 243,
        "date": 1794748171,
        "post_type": "post",
        "text": "Открыта регистрация на «ЗАРУБУ 2026».",
    }
    return {**base, **overrides}


def feed(*items: dict[str, Any]) -> dict[str, Any]:
    return {"response": {"count": len(items), "items": list(items)}}


class FakeResponse:
    """Ответ HTTP в том объёме, который читает клиент: статус и тело."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_wall_url_keeps_the_token_out_of_the_query() -> None:
    """Токен идёт заголовком: в query он попал бы в текст ошибки и в журнал."""

    url = wall_url(vk_settings(), 10)

    assert url.startswith("https://api.vk.com/method/wall.get?")
    assert "domain=zaruba72champ" in url
    assert "filter=owner" in url
    assert "count=10" in url
    assert "v=5.199" in url
    assert "token" not in url


def test_parse_posts_reads_the_live_fields() -> None:
    items = [make_item(attachments=[{"type": "video"}, {"type": "photo"}])]

    posts = parse_posts(feed(*items))

    assert len(posts) == 1
    post = posts[0]
    assert (post.owner_id, post.id) == (OWNER, 243)
    assert post.key == f"{OWNER}_243"
    assert post.url == f"https://vk.com/wall{OWNER}_243"
    assert post.date.tzinfo is not None
    assert post.kinds == ("video", "photo")
    assert post.text.startswith("Открыта регистрация")


def test_api_error_names_the_code_and_the_message() -> None:
    payload = {"error": {"error_code": 5, "error_msg": "User authorization failed"}}

    with pytest.raises(VkError, match="5: User authorization failed"):
        parse_posts(payload)


def test_response_without_items_is_an_error() -> None:
    with pytest.raises(VkError, match="items"):
        parse_posts({"response": {}})


def test_post_without_required_fields_is_an_error() -> None:
    with pytest.raises(VkError, match="обязательных полей"):
        parse_posts(feed({"owner_id": OWNER, "id": 1}))


def test_empty_text_is_described_not_invented() -> None:
    """Видео без подписи — обычное дело в ленте; «примерно» писать нечем."""

    post = Post(owner_id=OWNER, id=242, date=AT, kinds=("video",))

    text = message_text(post)

    assert "Пост без текста" in text
    assert "вложение: video" in text
    assert post.url in text


def test_long_text_is_cut_at_a_word_boundary() -> None:
    post = Post(owner_id=OWNER, id=1, date=AT, text="слово " * 400)

    body = message_text(post).splitlines()[1]

    assert body.endswith("слово…")
    assert len(body) <= TEXT_LIMIT + 1


def test_message_carries_a_link_button_to_the_post() -> None:
    post = Post(owner_id=OWNER, id=243, date=AT, text="Привет")

    body = notification_body(post, vk_settings())

    button = body["attachments"][0]["payload"]["buttons"][0][0]
    assert button["type"] == "link"
    assert button["url"] == f"https://vk.com/wall{OWNER}_243"


def test_unseen_orders_oldest_first_and_skips_known() -> None:
    later = Post(owner_id=OWNER, id=243, date=AT, text="позже")
    earlier = Post(owner_id=OWNER, id=242, date=AT.replace(year=2025), text="раньше")

    fresh = unseen([later, earlier], {earlier.key: "2026-01-01T00:00:00+00:00"})

    assert [post.key for post in fresh] == [later.key]


def opener_for(payload: Any, calls: list[Request] | None = None) -> vk.Opener:
    def open_(request: Request, timeout: float) -> FakeResponse:
        if calls is not None:
            calls.append(request)
        return FakeResponse(payload)

    return open_


def test_fetch_sends_bearer_token_and_no_token_in_url() -> None:
    calls: list[Request] = []

    posts = fetch_posts(vk_settings(), opener=opener_for(feed(make_item()), calls))

    assert [post.id for post in posts] == [243]
    request = calls[0]
    assert request.get_header("Authorization") == "Bearer vk1.a.test-token"
    assert "test-token" not in request.full_url


def test_network_failure_is_an_empty_feed_not_a_crash() -> None:
    def broken(request: Request, timeout: float) -> FakeResponse:
        raise urllib.error.URLError("сеть недоступна")

    assert fetch_posts(vk_settings(), opener=broken) == []


def test_fetch_without_token_names_the_variable() -> None:
    with pytest.raises(VkError, match="ZARUBA_VK_TOKEN"):
        fetch_posts(vk_settings(vk_token=""), opener=opener_for(feed()))


def test_non_json_answer_is_reported() -> None:
    class Garbage(FakeResponse):
        def __init__(self) -> None:
            self.status = 200

        def read(self) -> bytes:
            return b"<html>captcha</html>"

    def open_(request: Request, timeout: float) -> FakeResponse:
        return Garbage()

    with pytest.raises(VkError, match="не JSON"):
        fetch_posts(vk_settings(), opener=open_)


def test_missing_ca_file_is_reported_before_the_network(tmp_path: Path) -> None:
    settings = vk_settings(vk_ca_file=tmp_path / "нет-такого.pem")

    with pytest.raises(FileNotFoundError):
        fetch_posts(settings)


def test_payload_without_response_is_an_error() -> None:
    with pytest.raises(VkError, match="ни response"):
        parse_posts({"count": 0})


def test_non_mapping_items_are_skipped_and_kinds_are_cleaned() -> None:
    payload = feed(
        "мусор",
        make_item(id=243, attachments=[{"type": "video"}, {"type": 5}, {"type": "video"}]),
    )

    posts = parse_posts(payload)

    assert [post.id for post in posts] == [243]
    assert posts[0].kinds == ("video",)


def test_post_without_text_and_without_attachments() -> None:
    body = message_text(Post(owner_id=OWNER, id=1, date=AT))

    assert body.splitlines()[1] == "Пост без текста"


def test_json_array_is_not_a_valid_answer() -> None:
    with pytest.raises(VkError, match="не объект JSON"):
        fetch_posts(vk_settings(), opener=opener_for([]))


def test_ca_file_is_passed_to_the_tls_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca = tmp_path / "russian-trust.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    loaded: list[str] = []

    class Context:
        def load_verify_locations(self, cafile: str) -> None:
            loaded.append(cafile)

    monkeypatch.setattr(vk.ssl, "create_default_context", Context)

    assert isinstance(vk._ssl_context(vk_settings(vk_ca_file=ca)), Context)
    assert loaded == [str(ca)]


def test_default_opener_is_used_when_no_opener_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def fake_urlopen(request: Request, timeout: float, context: Any) -> FakeResponse:
        called.append(request.full_url)
        return FakeResponse(feed(make_item()))

    monkeypatch.setattr(vk.urllib.request, "urlopen", fake_urlopen)

    assert [post.id for post in fetch_posts(vk_settings())] == [243]
    assert "wall.get" in called[0]
