"""Smoke tests for the initial FastAPI application."""

from fastapi.testclient import TestClient

from server.http.app import app


client = TestClient(app)


def test_health_json() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_kv() -> None:
    response = client.get("/health?format=kv")
    assert response.status_code == 200
    body = response.text.strip().splitlines()
    assert "STATUS=ok" in body
