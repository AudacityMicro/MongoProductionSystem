from __future__ import annotations

import json

from app.diagnostics import DiagnosticRecorder


def test_diagnostics_are_redacted_and_survive_recorder_restart(tmp_path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    recorder = DiagnosticRecorder(path, memory_limit=10)
    recorder.record(
        "test",
        "connection",
        "Connection failed.",
        details={"host": "mongo", "password": "do-not-export", "nested": {"api_token": "secret"}},
    )

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["details"]["host"] == "mongo"
    assert stored["details"]["password"] == "[redacted]"
    assert stored["details"]["nested"]["api_token"] == "[redacted]"

    restarted = DiagnosticRecorder(path, memory_limit=10)
    assert restarted.recent(1)[0]["event"] == "connection"


def test_diagnostic_support_endpoint_excludes_credentials(client) -> None:
    response = client.get("/api/debug/diagnostics?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert "events" in body
    assert "supervisor" in body
    serialized = response.text.casefold()
    assert "robot_file_password" not in serialized
    assert "cnc_ssh_password" not in serialized

    export = client.get("/api/debug/diagnostics/export")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
