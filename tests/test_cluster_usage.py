from unittest.mock import patch

import pytest
from kubernetes.client.exceptions import ApiException

from tools.cluster_usage import (
    MetricsServerUnavailable,
    fetch_node_usage,
    fetch_pod_usage,
)

POD_PAYLOAD = {
    "items": [
        {
            "metadata": {"name": "web-abc123-xyz", "namespace": "prod"},
            "containers": [
                {"name": "app", "usage": {"cpu": "250m", "memory": "512Mi"}},
                {"name": "sidecar", "usage": {"cpu": "50m", "memory": "64Mi"}},
            ],
        }
    ]
}


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_sums_containers(mock_api):
    mock_api.return_value.list_cluster_custom_object.return_value = POD_PAYLOAD

    entry = fetch_pod_usage()[("prod", "web-abc123-xyz")]

    assert entry["cpu_cores"] == pytest.approx(0.3)
    assert entry["memory_bytes"] == 576 * 1024**2


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_scopes_to_namespace(mock_api):
    mock_api.return_value.list_namespaced_custom_object.return_value = POD_PAYLOAD

    fetch_pod_usage(namespace="prod")

    mock_api.return_value.list_namespaced_custom_object.assert_called_once_with(
        "metrics.k8s.io", "v1beta1", "prod", "pods"
    )
    mock_api.return_value.list_cluster_custom_object.assert_not_called()


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_pod_usage_raises_when_api_missing(mock_api):
    mock_api.return_value.list_cluster_custom_object.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    with pytest.raises(MetricsServerUnavailable, match="metrics.k8s.io"):
        fetch_pod_usage()


@patch("tools.cluster_usage.client.CustomObjectsApi")
def test_fetch_node_usage_keyed_by_node_name(mock_api):
    mock_api.return_value.list_cluster_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "node-a"},
                "usage": {"cpu": "1500m", "memory": "4Gi"},
            }
        ]
    }

    usage = fetch_node_usage()

    assert usage["node-a"]["cpu_cores"] == pytest.approx(1.5)
    assert usage["node-a"]["memory_bytes"] == 4 * 1024**3
