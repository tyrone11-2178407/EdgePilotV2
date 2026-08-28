from unittest.mock import patch

from MCP.tool_executor import ToolExecutor
from MCP.tool_schemas import get_tool_schema


def test_read_only_kubernetes_schemas_are_registered():
    names = [
        "inspect_kubernetes_cluster",
        "evaluate_kubernetes_workload",
        "inspect_kubernetes_deployment",
    ]

    for name in names:
        schema = get_tool_schema(name)
        assert schema is not None
        assert schema["name"] == name


@patch("MCP.tool_executor.inspect_kubernetes_cluster")
def test_executor_inspects_cluster(mock_inspect):
    mock_inspect.return_value = {
        "source": "kubernetes",
        "node_count": 2,
    }

    result = ToolExecutor().execute(
        "inspect_kubernetes_cluster",
        {},
    )

    assert result["success"] is True
    assert result["result"]["node_count"] == 2
    mock_inspect.assert_called_once_with()


@patch("MCP.tool_executor.evaluate_kubernetes_workload")
def test_executor_evaluates_workload(mock_evaluate):
    mock_evaluate.return_value = {
        "can_run_now": True,
        "results": [],
    }

    requirements = {
        "cpu_cores": 2,
        "memory_bytes": 4 * 1024**3,
        "pods": 1,
    }

    result = ToolExecutor().execute(
        "evaluate_kubernetes_workload",
        {
            "requirements": requirements,
            "node": "worker-1",
        },
    )

    assert result["success"] is True

    mock_evaluate.assert_called_once_with(
        requirements=requirements,
        node="worker-1",
    )


def test_executor_rejects_missing_requirements():
    result = ToolExecutor().execute(
        "evaluate_kubernetes_workload",
        {},
    )

    assert result["success"] is False
    assert "requirements must be an object" in result["error"]


@patch("MCP.tool_executor.inspect_kubernetes_deployment")
def test_executor_inspects_deployment(mock_inspect):
    mock_inspect.return_value = {
        "namespace": "production",
        "deployment_name": "frontend",
        "ready_replicas": 3,
    }

    result = ToolExecutor().execute(
        "inspect_kubernetes_deployment",
        {
            "namespace": "production",
            "deployment_name": "frontend",
        },
    )

    assert result["success"] is True

    mock_inspect.assert_called_once_with(
        namespace="production",
        deployment_name="frontend",
    )


def test_executor_requires_exact_deployment_target():
    executor = ToolExecutor()

    missing_namespace = executor.execute(
        "inspect_kubernetes_deployment",
        {
            "deployment_name": "frontend",
        },
    )

    missing_deployment = executor.execute(
        "inspect_kubernetes_deployment",
        {
            "namespace": "production",
        },
    )

    assert missing_namespace["success"] is False
    assert "namespace is required" in missing_namespace["error"]

    assert missing_deployment["success"] is False
    assert "deployment_name is required" in missing_deployment["error"]