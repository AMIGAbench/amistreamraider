"""Tests for the /search endpoint using a mocked service."""

from fastapi.testclient import TestClient

from server.api.search import ChannelSearchResult
from server.http.app import app, get_search_service


class DummySearchService:
    async def search_channels(self, *, query, limit, thumb_url_builder, **kwargs):
        return [
            ChannelSearchResult(
                id="123",
                login="nerdcastle",
                name="NerdCastle",
                title="Retro Dev Stream",
                live=True,
                viewers=987,
                language="en",
                thumbnail_url="https://example.com/image.jpg",
                thumb_160_url=thumb_url_builder("nerdcastle"),
                started_at="2025-01-01T12:34:56Z",
                game_id="12345",
                game_name="Retro",
            ),
            ChannelSearchResult(
                id="456",
                login="nerdoffline",
                name="NerdOffline",
                title="Working On Scripts",
                live=False,
                viewers=0,
                language="en",
                thumbnail_url="https://example.com/offline.jpg",
                thumb_160_url=thumb_url_builder("nerdoffline"),
                started_at=None,
                game_id="67890",
                game_name="Art",
            ),
        ]


def override_search_service() -> DummySearchService:
    return DummySearchService()


client = TestClient(app)


def setup_module(module):
    app.dependency_overrides[get_search_service] = override_search_service


def teardown_module(module):
    app.dependency_overrides.pop(get_search_service, None)


CLIENT_HEADERS = {
    "X-Twitch-Client-Id": "cid",
    "X-Twitch-Client-Secret": "secret",
}


def test_search_returns_results() -> None:
    response = client.get("/search?q=nerd", headers=CLIENT_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["count"] == 2
    assert data["messages"][0] == "Suche gestartet…"
    assert data["messages"][-1] == "Suche erfolgreich beendet."
    if len(data["messages"]) > 2:
        assert "Authentifiziere bei Twitch…" in data["messages"]
        assert "Authentifizierung erfolgreich." in data["messages"]
    assert data["live_only"] is False
    item = data["items"][0]
    assert item["login"] == "nerdcastle"
    assert item["live"] is True
    assert item["viewers"] == 987


def test_search_kv_format() -> None:
    response = client.get("/search?q=nerd&format=kv", headers=CLIENT_HEADERS)
    assert response.status_code == 200
    kv = dict(line.split("=", 1) for line in response.text.splitlines())
    assert kv["STATUS"] == "ok"
    assert kv["COUNT"] == "2"
    assert kv["ITEM0.LOGIN"] == "nerdcastle"
    assert kv["ITEM0.LIVE"] == "1"
    assert kv["MESSAGE0"] == "Suche gestartet…"
    message_keys = sorted(
        (
            k
            for k in kv
            if k.startswith("MESSAGE") and k != "MESSAGE"
        ),
        key=lambda name: int(name.replace("MESSAGE", "")),
    )
    assert message_keys, "Expected MESSAGE* keys in KV payload"
    assert kv[message_keys[-1]] == "Suche erfolgreich beendet."
    assert kv["LIVE_ONLY"] == "0"


def test_search_live_only_filters_offline() -> None:
    response = client.get("/search?q=nerd&live_only=1", headers=CLIENT_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["items"][0]["login"] == "nerdcastle"
    assert data["live_only"] is True
