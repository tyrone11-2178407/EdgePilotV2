import os
import pytest
from tools.prometheus import query_prometheus, query_pod_resources

@pytest.fixture(autouse=True)
def enable_mock():
    # Force mock mode for testing
    os.environ["PROMETHEUS_MOCK"] = "true"
    import tools.prometheus
    tools.prometheus.USE_MOCK = True
    yield
    os.environ.pop("PROMETHEUS_MOCK", None)

def test_query_prometheus_mock():
    res = query_prometheus("container_memory_working_set_bytes{pod='test-pod'}", time_range="1h", step="5m")
    
    assert res["success"] is True
    assert "results" in res
    assert len(res["results"]) > 0
    
    data = res["results"][0]
    assert data["metric"]["pod"] == "mock-pod-1"
    
    # Check that we have a timeseries of values
    values = data["values"]
    assert len(values) > 0
    assert len(values[0]) == 2  # [timestamp, value_string]

def test_query_pod_resources_mock():
    # Since limits might try to query the actual K8s API, this might log a warning
    # but should still return the mock prometheus data
    res = query_pod_resources("default", "test-pod", window="1h")
    
    assert res["success"] is True
    assert res["pod"] == "test-pod"
    assert res["namespace"] == "default"
    assert "cpu_usage_history" in res
    assert "memory_usage_history" in res
    assert "limits" in res
    
    assert len(res["cpu_usage_history"]) > 0
    assert len(res["memory_usage_history"]) > 0


# ====================================================================== #
# Regression tests for defects found in AI Workflow V1                    #
# ====================================================================== #

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.prometheus import _downsample, _summarize_series


def test_summary_is_attached_by_default():
    res = query_prometheus("whatever")
    series = res["results"][0]

    assert "summary" in series
    assert series["summary"]["points"] == 12
    assert series["summary"]["min"] < series["summary"]["max"]
    assert series["summary"]["last"] == float(series["values"][-1][1])


def test_summarize_can_be_turned_off():
    res = query_prometheus("whatever", summarize=False)

    assert "summary" not in res["results"][0]


def test_long_series_is_thinned_but_keeps_the_endpoints():
    values = [[i, str(i)] for i in range(500)]
    thinned = _downsample(values, 20)

    assert len(thinned) == 20
    assert thinned[0] == values[0]
    assert thinned[-1] == values[-1]


def test_downsample_leaves_short_series_alone():
    values = [[i, str(i)] for i in range(5)]

    assert _downsample(values, 20) == values


def test_summarize_handles_unparseable_points():
    assert _summarize_series([[0, "not-a-number"]]) == {"points": 0}
    assert _summarize_series([]) == {"points": 0}


def _pod_with_limits(*limit_pairs):
    return SimpleNamespace(
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    resources=SimpleNamespace(limits={"cpu": cpu, "memory": mem})
                )
                for cpu, mem in limit_pairs
            ]
        )
    )


@patch("kubernetes.client.CoreV1Api")
@patch("kubernetes.config.load_kube_config")
@patch("kubernetes.config.load_incluster_config")
def test_pod_limits_are_summed_across_containers(
    mock_incluster, mock_kubeconfig, mock_core_api
):
    """A two-container pod must report the total, not just the last one."""
    mock_core_api.return_value.read_namespaced_pod.return_value = _pod_with_limits(
        ("500m", "1Gi"), ("1500m", "512Mi")
    )

    res = query_pod_resources("default", "test-pod")
    limits = res["limits"]

    assert limits["cpu_cores"] == pytest.approx(2.0)
    assert limits["memory_bytes"] == 1024**3 + 512 * 1024**2
    assert limits["container_count"] == 2
    assert limits["source"] == "kubernetes"


@patch("kubernetes.client.CoreV1Api")
@patch("kubernetes.config.load_kube_config")
@patch("kubernetes.config.load_incluster_config")
def test_incluster_config_is_preferred(
    mock_incluster, mock_kubeconfig, mock_core_api
):
    """Running inside the cluster must not fall back to a kubeconfig file."""
    mock_core_api.return_value.read_namespaced_pod.return_value = _pod_with_limits(
        ("1", "1Gi")
    )

    query_pod_resources("default", "test-pod")

    mock_incluster.assert_called_once()
    mock_kubeconfig.assert_not_called()


@patch("kubernetes.client.CoreV1Api")
@patch("kubernetes.config.load_kube_config")
@patch("kubernetes.config.load_incluster_config")
def test_unreachable_k8s_api_degrades_instead_of_raising(
    mock_incluster, mock_kubeconfig, mock_core_api
):
    mock_core_api.side_effect = RuntimeError("no cluster")

    res = query_pod_resources("default", "test-pod")

    assert res["success"] is True
    assert res["limits"]["source"] == "unavailable"
    assert res["limits"]["cpu_cores"] is None
