from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from tools.kubernetes_actions import (
    cordon_node,
    restart_workload,
    scale_workload,
)


@patch("tools.kubernetes_actions._get_client")
def test_scale_workload_success(mock_get_client):
    api = MagicMock()
    mock_get_client.return_value = api

    result = scale_workload(
        namespace="production",
        deployment_name="frontend",
        replicas=3,
    )

    assert result["success"] is True

    api.patch_namespaced_deployment_scale.assert_called_once_with(
        name="frontend",
        namespace="production",
        body={"spec": {"replicas": 3}},
    )


def test_scale_workload_rejects_negative_replicas():
    result = scale_workload(
        namespace="default",
        deployment_name="frontend",
        replicas=-1,
    )

    assert result["success"] is False
    assert result["error"] == "Replicas must be >= 0"


@patch("tools.kubernetes_actions._get_client")
def test_scale_workload_handles_api_error(mock_get_client):
    api = MagicMock()
    mock_get_client.return_value = api

    api.patch_namespaced_deployment_scale.side_effect = ApiException(
        status=403,
        reason="Forbidden",
    )

    result = scale_workload(
        namespace="production",
        deployment_name="frontend",
        replicas=3,
    )

    assert result["success"] is False
    assert "Forbidden" in result["error"]
    assert "403" in result["error"]


@patch("tools.kubernetes_actions._get_client")
def test_restart_workload_success(mock_get_client):
    api = MagicMock()
    mock_get_client.return_value = api

    result = restart_workload(
        namespace="production",
        deployment_name="frontend",
    )

    assert result["success"] is True

    api.patch_namespaced_deployment.assert_called_once()

    call = api.patch_namespaced_deployment.call_args

    assert call.kwargs["name"] == "frontend"
    assert call.kwargs["namespace"] == "production"

    body = call.kwargs["body"]

    restarted_at = (
        body["spec"]["template"]["metadata"]["annotations"]
        ["kubectl.kubernetes.io/restartedAt"]
    )

    assert isinstance(restarted_at, str)
    assert restarted_at


@patch("tools.kubernetes_actions._get_client")
def test_restart_workload_handles_api_error(mock_get_client):
    api = MagicMock()
    mock_get_client.return_value = api

    api.patch_namespaced_deployment.side_effect = ApiException(
        status=404,
        reason="Not Found",
    )

    result = restart_workload(
        namespace="production",
        deployment_name="missing-deployment",
    )

    assert result["success"] is False
    assert "Not Found" in result["error"]
    assert "404" in result["error"]


@patch("tools.kubernetes_actions._get_core_client")
def test_cordon_node_success(mock_get_core_client):
    api = MagicMock()
    mock_get_core_client.return_value = api

    result = cordon_node("worker-1")

    assert result["success"] is True

    api.patch_node.assert_called_once_with(
        name="worker-1",
        body={"spec": {"unschedulable": True}},
    )


@patch("tools.kubernetes_actions._get_core_client")
def test_cordon_node_handles_api_error(mock_get_core_client):
    api = MagicMock()
    mock_get_core_client.return_value = api

    api.patch_node.side_effect = ApiException(
        status=403,
        reason="Forbidden",
    )

    result = cordon_node("worker-1")

    assert result["success"] is False
    assert "Forbidden" in result["error"]
    assert "403" in result["error"]


@patch("tools.kubernetes_actions._get_client")
def test_apps_client_reports_configuration_error(mock_get_client):
    mock_get_client.side_effect = RuntimeError("Could not load Kubernetes configuration")

    result = None

    try:
        scale_workload(
            namespace="default",
            deployment_name="frontend",
            replicas=2,
        )
    except RuntimeError as exc:
        result = str(exc)

    assert result is not None
    assert "Could not load Kubernetes configuration" in result


@patch("tools.kubernetes_actions._get_core_client")
def test_core_client_reports_configuration_error(mock_get_core_client):
    mock_get_core_client.side_effect = RuntimeError("Could not load Kubernetes configuration")

    result = None

    try:
        cordon_node("worker-1")
    except RuntimeError as exc:
        result = str(exc)

    assert result is not None
    assert "Could not load Kubernetes configuration" in result