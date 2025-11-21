"""Tests for the thumbnail endpoint and cache behaviour."""

from pathlib import Path

from fastapi.testclient import TestClient

from server.http.app import THUMB_CACHE_DIR, app


client = TestClient(app)


def _purge_cache(login: str) -> None:
    """Remove cached thumbnails for a specific login to avoid cross-test bleed."""
    login = login.lower()
    for suffix in ("jpg", "png"):
        cached = THUMB_CACHE_DIR / f"{login}.{suffix}"
        if cached.exists():
            cached.unlink()


def test_thumbnail_serves_image() -> None:
    login = "testthumbuser"
    _purge_cache(login)
    response = client.get(f"/thumbnail/channel/{login}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["X-Status"] == "Vorschaubild bereit."
    assert response.headers["X-Cache"] == "miss"
    assert response.headers["X-Thumbnail-Source"] == "placeholder"
    assert len(response.content) > 0


def test_thumbnail_cache_hit_and_force_refresh() -> None:
    login = "cachetestuser"
    _purge_cache(login)

    first = client.get(f"/thumbnail/channel/{login}")
    assert first.headers["X-Cache"] == "miss"
    assert first.headers["X-Thumbnail-Source"] == "placeholder"

    second = client.get(f"/thumbnail/channel/{login}")
    assert second.headers["X-Cache"] == "hit"
    assert second.headers["X-Thumbnail-Source"] == "cache"

    forced = client.get(f"/thumbnail/channel/{login}?force=1")
    assert forced.headers["X-Cache"] == "miss"
    assert forced.headers["X-Thumbnail-Source"] == "placeholder"


def test_thumbnail_metadata_json() -> None:
    login = "metatestuser"
    _purge_cache(login)
    response = client.get(f"/thumbnail/channel/{login}?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Vorschaubild bereit."
    assert data["cache"] in {"hit", "miss"}
    assert data["format"] in {"jpg", "png"}
    path = Path(data["path"])
    assert data["source"] in {"cache", "placeholder", "remote_preview", "profile"}
    assert path.exists()


def test_thumbnail_metadata_kv() -> None:
    login = "metatestuserkv"
    _purge_cache(login)
    response = client.get(f"/thumbnail/channel/{login}?format=kv")
    assert response.status_code == 200
    lines = dict(line.split("=", 1) for line in response.text.splitlines())
    assert lines["STATUS"] == "Vorschaubild bereit."
    assert lines["LOGIN"] == login
    assert lines["FORMAT"] in {"jpg", "png"}
    assert lines["SOURCE"] in {"cache", "placeholder", "remote_preview", "profile"}
