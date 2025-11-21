"""Offline stub tests for channel/top/favorites endpoints."""

from fastapi.testclient import TestClient

from server.http.app import USE_OFFLINE_STUBS, app


client = TestClient(app)


def test_channel_info_offline():
    if not USE_OFFLINE_STUBS:
        return
    response = client.get("/channel/amigadev")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Kanalinfo erfolgreich abgerufen."
    assert data["messages"][0] == "Kanalinfo abrufen…"
    assert data["messages"][-1] == "Kanalinfo erfolgreich abgerufen."
    assert data["item"]["login"] == "amigadev"
    assert "/thumbnail/channel/" in data["item"]["thumb_160_url"]


def test_channel_not_found_offline():
    if not USE_OFFLINE_STUBS:
        return
    response = client.get("/channel/unknownuser")
    assert response.status_code == 404
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["messages"][-1].startswith("Fehler:")


def test_top_channels_offline():
    if not USE_OFFLINE_STUBS:
        return
    response = client.get("/top?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Top-Liste bereit."
    assert data["count"] == 2
    assert data["messages"][0] == "Top-Liste abrufen…"
    assert data["messages"][-1] == "Top-Liste bereit."
    assert data["items"][0]["viewers"] >= data["items"][1]["viewers"]


def test_favorites_offline():
    if not USE_OFFLINE_STUBS:
        return
    response = client.get("/favorites?format=kv")
    assert response.status_code == 200
    kv = dict(line.split("=", 1) for line in response.text.splitlines())
    assert kv["STATUS"] == "Favoriten geladen."
    assert kv["COUNT"] == "2"
    assert kv["MESSAGE0"] == "Favoritenliste abrufen…"
    assert kv["MESSAGE1"] == "Favoritenliste geladen."
