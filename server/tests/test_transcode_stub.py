"""Tests for the transcode endpoints using the stub manager."""

from fastapi.testclient import TestClient

from server.http.app import app, transcode_manager, TRANSCODE_USE_STUBS


client = TestClient(app)


def test_transcode_start_stop_cycle():
    if not TRANSCODE_USE_STUBS:
        return  # real pipelines require external binaries; skip in live mode

    response = client.post("/play/start", json={"channel": "amigadev", "profile": "stub"})
    assert response.status_code == 200
    data = response.json()
    op_id = data["op_id"]
    assert data["status"] == "ffmpeg erfolgreich gestartet…"
    assert data["messages"][0] == "Transcoding gestartet…"
    assert data["messages"][1] == "ffmpeg erfolgreich gestartet…"
    assert data["messages"][2].startswith("Streaming-Server aktiv (Port")
    assert data["details"]["restart_count"] == 0
    assert data["details"]["last_error"] is None

    status = client.get(f"/play/status/{op_id}")
    assert status.status_code == 200
    status_data = status.json()
    assert status_data["details"]["state"] == "running"
    assert status_data["details"]["restart_count"] == 0
    assert status_data["messages"][0] == "Statusabfrage laufender Prozesse…"

    stop = client.post("/play/stop", json={"op_id": op_id})
    assert stop.status_code == 200
    stop_payload = stop.json()
    assert stop_payload["status"] == "gestoppt"
    assert stop_payload["messages"][0] == "Stop-Befehl ausgeführt."
    assert stop_payload["messages"][1] == "Transcoding gestoppt."
    transcode_manager.remove_pipeline(op_id)


def test_transcode_profiles():
    response = client.get("/play/profiles")
    assert response.status_code == 200
    data = response.json()
    assert "profiles" in data
    assert data["count"] == len(data["profiles"])


def test_transcode_metrics_endpoint():
    if not TRANSCODE_USE_STUBS:
        return
    response = client.post("/play/start", json={"channel": "metricsuser", "profile": "stub"})
    assert response.status_code == 200
    op_id = response.json()["op_id"]

    metrics = client.get(f"/op/{op_id}/metrics")
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["status"] == "ok"
    assert payload["metrics"]["op_id"] == op_id
    assert "bytes_total" in payload["metrics"]
    assert payload["messages"][0] == "Statusabfrage laufender Prozesse…"
    assert payload["state"] == "running"
    assert payload["restart_count"] == 0

    # cleanup
    client.post("/play/stop", json={"op_id": op_id})
    transcode_manager.remove_pipeline(op_id)
