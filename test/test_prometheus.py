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
