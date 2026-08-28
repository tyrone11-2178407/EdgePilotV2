from evaluations.token_usage.simulated_tools import (
    SyntheticKubernetesTools,
)


NAMESPACE = "edgepilot-token-eval"
DEPLOYMENT = "edgepilot-token-eval-nginx"


def test_cluster_inspection_matches_production_shape():
    tools = SyntheticKubernetesTools(node_count=10)

    response = tools.execute("inspect_kubernetes_cluster", {})
    result = response["result"]

    assert response["success"] is True
    assert response["tool"] == "inspect_kubernetes_cluster"
    assert result["source"] == "kubernetes"
    assert result["node_count"] == 10
    assert len(result["nodes"]) == 10

    first = result["nodes"][0]
    assert first["status"] == {
        "ready": True,
        "schedulable": True,
    }
    assert first["cpu"]["allocatable_cores"] == 8.0
    assert first["cpu"]["requested_cores"] == 2.0
    assert first["cpu"]["available_cores"] == 6.0
    assert first["cpu"]["requested_percent"] == 25.0
    assert first["memory"]["allocatable_bytes"] == 16 * 1024**3
    assert first["memory"]["requested_bytes"] == 4 * 1024**3
    assert first["memory"]["available_bytes"] == 12 * 1024**3
    assert first["pods"]["scheduled"] == 20


def test_cluster_aggregate_scales_with_node_count():
    tools = SyntheticKubernetesTools(node_count=100)

    result = tools.execute(
        "inspect_kubernetes_cluster",
        {},
    )["result"]["cluster"]

    assert result["cpu"]["allocatable_cores"] == 800.0
    assert result["cpu"]["requested_cores"] == 200.0
    assert result["cpu"]["available_cores"] == 600.0
    assert result["memory"]["allocatable_bytes"] == 1600 * 1024**3
    assert result["memory"]["requested_bytes"] == 400 * 1024**3
    assert result["memory"]["available_bytes"] == 1200 * 1024**3
    assert result["scheduled_pods"] == 2000


def test_deployment_inspection_starts_at_one_replica():
    tools = SyntheticKubernetesTools(node_count=10)

    response = tools.execute(
        "inspect_kubernetes_deployment",
        {
            "namespace": NAMESPACE,
            "deployment_name": DEPLOYMENT,
        },
    )
    result = response["result"]

    assert response["success"] is True
    assert result == {
        "namespace": NAMESPACE,
        "deployment_name": DEPLOYMENT,
        "desired_replicas": 1,
        "ready_replicas": 1,
        "available_replicas": 1,
        "updated_replicas": 1,
        "unavailable_replicas": 0,
        "observed_generation": 1,
    }


def test_scaling_updates_state_and_verification_result():
    tools = SyntheticKubernetesTools(node_count=10)

    scale = tools.execute(
        "scale_workload",
        {
            "namespace": NAMESPACE,
            "deployment_name": DEPLOYMENT,
            "replicas": 2,
        },
    )
    verified = tools.execute(
        "inspect_kubernetes_deployment",
        {
            "namespace": NAMESPACE,
            "deployment_name": DEPLOYMENT,
        },
    )

    assert scale["success"] is True
    assert scale["result"]["success"] is True
    assert verified["result"]["desired_replicas"] == 2
    assert verified["result"]["ready_replicas"] == 2
    assert verified["result"]["available_replicas"] == 2
    assert verified["result"]["observed_generation"] == 2


def test_wrong_target_fails_without_mutating_test_deployment():
    tools = SyntheticKubernetesTools(node_count=10)

    failed = tools.execute(
        "scale_workload",
        {
            "namespace": "default",
            "deployment_name": "other",
            "replicas": 2,
        },
    )
    verified = tools.execute(
        "inspect_kubernetes_deployment",
        {
            "namespace": NAMESPACE,
            "deployment_name": DEPLOYMENT,
        },
    )

    assert failed["success"] is True
    assert failed["result"]["success"] is False
    assert verified["result"]["desired_replicas"] == 1


def test_unknown_tool_fails_closed():
    tools = SyntheticKubernetesTools(node_count=10)

    response = tools.execute("invented_tool", {})

    assert response["success"] is False
    assert "Unknown tool" in response["error"]
