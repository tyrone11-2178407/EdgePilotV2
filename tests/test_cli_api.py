import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edgepilot_cli import cli as edgepilot_cli  # noqa: E402
from main import app, _guard_kubernetes_capacity_response  # noqa: E402


runner = CliRunner()
client = TestClient(app)


def test_cli_ask_offline_answer():
    result = runner.invoke(edgepilot_cli, ["ask", "Can I schedule another job?"])
    assert result.exit_code == 0
    assert "Answer" in result.stdout


def test_cli_schedule_and_status_commands():
    schedule = runner.invoke(
        edgepilot_cli,
        ["schedule", "--action", "run_shell_commands", "--command", "echo EdgePilot CLI test"],
    )
    assert schedule.exit_code == 0
    status = runner.invoke(edgepilot_cli, ["status"])
    assert status.exit_code == 0
    assert "task_id" in status.stdout or "No tasks" in status.stdout


def test_api_ask_and_schedule_endpoints():
    ask_response = client.post("/api/ask", json={"query": "How busy is the system?"})
    assert ask_response.status_code == 200
    ask_payload = ask_response.json()
    assert "answer" in ask_payload
    schedule_response = client.post(
        "/api/schedule",
        json={"action": "run_shell_commands", "command": "echo EdgePilot API test", "delay_seconds": 0},
    )
    assert schedule_response.status_code == 200
    tasks_response = client.get("/api/tasks")
    assert tasks_response.status_code == 200

    tasks_payload = tasks_response.json()

    assert isinstance(tasks_payload, dict)
    assert "tasks" in tasks_payload
    assert "count" in tasks_payload
    assert isinstance(tasks_payload["tasks"], list)
    assert tasks_payload["count"] == len(tasks_payload["tasks"])

def test_vague_cluster_capacity_asks_for_requirements_before_tools():
    create_response = client.post(
        "/api/chats",
        json={"title": "Capacity regression test"},
    )
    assert create_response.status_code == 201
    chat_id = create_response.json()["id"]

    try:
        response = client.post(
            f"/api/chats/{chat_id}/messages/stream",
            json={
                "prompt": "Can this cluster handle more work?",
                "provider": "claude",
            },
        )

        assert response.status_code == 200
        body = response.text.lower()

        assert "cpu request" in body
        assert "memory request" in body
        assert "pod or replica count" in body

        assert "significant headroom" not in body
        assert "comfortably" not in body
        assert "event: tool" not in body
    finally:
        client.delete(f"/api/chats/{chat_id}")

def test_deployment_health_without_namespace_asks_for_namespace():
    create_response = client.post(
        "/api/chats",
        json={"title": "Namespace regression test"},
    )
    assert create_response.status_code == 201
    chat_id = create_response.json()["id"]

    try:
        response = client.post(
            f"/api/chats/{chat_id}/messages/stream",
            json={
                "prompt": "How is the edgepilot-demo deployment doing?",
                "provider": "claude",
            },
        )

        assert response.status_code == 200
        body = response.text.lower()

        assert "what namespace" in body
        assert "default namespace" not in body
        assert "not found" not in body
        assert "event: tool" not in body
    finally:
        client.delete(f"/api/chats/{chat_id}")

def test_capacity_response_guard_removes_scheduling_guarantees():
    original = (
        "The cluster has 6.8 cores available and 7.78 GiB available. "
        "The pods will be admitted by the scheduler."
    )

    guarded = _guard_kubernetes_capacity_response(original)
    lowered = guarded.lower()

    assert "cores of request headroom" in guarded
    assert "gib of request headroom" in lowered
    assert "will be admitted by the scheduler" not in lowered
    assert "does not guarantee admission" in lowered
    assert "live runtime performance" in lowered
