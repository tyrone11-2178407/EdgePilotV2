from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from kubernetes.config.config_exception import ConfigException

import pytest
from kubernetes.client.exceptions import ApiException

from tools.kubernetes_capacity import (
    KubernetesMetricsProvider,
    _pod_resource_totals,
    evaluate_kubernetes_capacity,
)



@patch("tools.kubernetes_capacity.client.CoreV1Api")
@patch("tools.kubernetes_capacity.config.load_kube_config")
@patch("tools.kubernetes_capacity.config.load_incluster_config")
def test_kubernetes_provider_prefers_incluster_config(
    mock_incluster,
    mock_kubeconfig,
    mock_core_api,
):
    KubernetesMetricsProvider()

    mock_incluster.assert_called_once_with()
    mock_kubeconfig.assert_not_called()
    mock_core_api.assert_called_once_with()


@patch("tools.kubernetes_capacity.client.CoreV1Api")
@patch("tools.kubernetes_capacity.config.load_kube_config")
@patch("tools.kubernetes_capacity.config.load_incluster_config")
def test_kubernetes_provider_falls_back_to_local_kubeconfig(
    mock_incluster,
    mock_kubeconfig,
    mock_core_api,
):
    mock_incluster.side_effect = ConfigException()

    KubernetesMetricsProvider()

    mock_incluster.assert_called_once_with()
    mock_kubeconfig.assert_called_once_with()
    mock_core_api.assert_called_once_with()


@patch("tools.kubernetes_capacity.client.CoreV1Api")
@patch("tools.kubernetes_capacity.config.load_kube_config")
@patch("tools.kubernetes_capacity.config.load_incluster_config")
def test_explicit_kubeconfig_skips_incluster_config(
    mock_incluster,
    mock_kubeconfig,
    mock_core_api,
):
    KubernetesMetricsProvider(kubeconfig="/tmp/test-config")

    mock_incluster.assert_not_called()
    mock_kubeconfig.assert_called_once_with(
        config_file="/tmp/test-config"
    )
    mock_core_api.assert_called_once_with()


def make_container(
    cpu_request="0",
    memory_request="0",
    cpu_limit="0",
    memory_limit="0",
    resources=True,
):
    if not resources:
        return SimpleNamespace(resources=None)

    return SimpleNamespace(
        resources=SimpleNamespace(
            requests={
                "cpu": cpu_request,
                "memory": memory_request,
            },
            limits={
                "cpu": cpu_limit,
                "memory": memory_limit,
            },
        )
    )


def make_pod(
    containers=None,
    init_containers=None,
    overhead=None,
):
    return SimpleNamespace(
        spec=SimpleNamespace(
            containers=[] if containers is None else containers,
            init_containers=(
                []
                if init_containers is None
                else init_containers
            ),
            overhead={} if overhead is None else overhead,
        )
    )


def test_regular_container_requests_are_summed():
    pod = make_pod(
        containers=[
            make_container("500m", "256Mi"),
            make_container("250m", "128Mi"),
        ]
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_requests_cores"] == 0.75
    assert totals["memory_requests_bytes"] == 384 * 1024**2


def test_largest_init_container_request_is_used():
    pod = make_pod(
        containers=[
            make_container("500m", "256Mi"),
        ],
        init_containers=[
            make_container("1", "512Mi"),
            make_container("2", "256Mi"),
        ],
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_requests_cores"] == 2.0
    assert totals["memory_requests_bytes"] == 512 * 1024**2


def test_pod_overhead_is_added():
    pod = make_pod(
        containers=[
            make_container("500m", "256Mi"),
        ],
        overhead={
            "cpu": "100m",
            "memory": "64Mi",
        },
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_requests_cores"] == 0.6
    assert totals["memory_requests_bytes"] == 320 * 1024**2


def test_list_nodes_converts_api_exception():
    provider = object.__new__(KubernetesMetricsProvider)
    provider.core = MagicMock()

    provider.core.list_node.side_effect = ApiException(
        status=403,
        reason="Forbidden",
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to list Kubernetes nodes",
    ):
        provider.list_nodes()


def test_list_pods_converts_api_exception():
    provider = object.__new__(KubernetesMetricsProvider)
    provider.core = MagicMock()

    provider.core.list_pod_for_all_namespaces.side_effect = (
        ApiException(
            status=500,
            reason="Internal Server Error",
        )
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to list Kubernetes Pods",
    ):
        provider.list_pods()


def test_capacity_rejects_node_without_pod_slots():
    provider = MagicMock()

    provider.get_capacity.return_value = [
        {
            "instance": "node-a",
            "status": {
                "ready": True,
                "schedulable": True,
            },
            "headroom": {
                "cpu_cores": 4.0,
                "memory_bytes": 8 * 1024**3,
                "pods": 0,
            },
        }
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 0.5,
            "memory_bytes": 256 * 1024**2,
            "pods": 1,
        },
    )

    assert result["results"][0]["can_run_now"] is False
    assert "Pod slots available 0 < required 1" in (
        result["results"][0]["reasons"]
    )


def test_capacity_accepts_node_with_enough_pod_slots():
    provider = MagicMock()

    provider.get_capacity.return_value = [
        {
            "instance": "node-a",
            "status": {
                "ready": True,
                "schedulable": True,
            },
            "headroom": {
                "cpu_cores": 4.0,
                "memory_bytes": 8 * 1024**3,
                "pods": 10,
            },
        }
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 0.5,
            "memory_bytes": 256 * 1024**2,
            "pods": 1,
        },
    )

    assert result["results"][0]["can_run_now"] is True


def test_pod_overhead_is_added_to_existing_limits():
    pod = make_pod(
        containers=[
            make_container(
                cpu_request="500m",
                memory_request="256Mi",
                cpu_limit="1",
                memory_limit="512Mi",
            ),
        ],
        overhead={
            "cpu": "100m",
            "memory": "64Mi",
        },
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_requests_cores"] == pytest.approx(0.6)
    assert totals["memory_requests_bytes"] == 320 * 1024**2
    assert totals["cpu_limits_cores"] == pytest.approx(1.1)
    assert totals["memory_limits_bytes"] == 576 * 1024**2


def test_pod_overhead_does_not_create_missing_limits():
    pod = make_pod(
        containers=[
            make_container(
                cpu_request="500m",
                memory_request="256Mi",
            ),
        ],
        overhead={
            "cpu": "100m",
            "memory": "64Mi",
        },
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_limits_cores"] == 0.0
    assert totals["memory_limits_bytes"] == 0

def test_regular_container_limits_are_summed():
    pod = make_pod(
        containers=[
            make_container(
                cpu_limit="1",
                memory_limit="512Mi",
            ),
            make_container(
                cpu_limit="500m",
                memory_limit="256Mi",
            ),
        ]
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_limits_cores"] == pytest.approx(1.5)
    assert totals["memory_limits_bytes"] == 768 * 1024**2

def test_largest_init_container_limit_is_used():
    pod = make_pod(
        containers=[
            make_container(
                cpu_limit="1",
                memory_limit="256Mi",
            ),
        ],
        init_containers=[
            make_container(
                cpu_limit="2",
                memory_limit="512Mi",
            ),
            make_container(
                cpu_limit="1500m",
                memory_limit="1Gi",
            ),
        ],
    )

    totals = _pod_resource_totals(pod)

    assert totals["cpu_limits_cores"] == pytest.approx(2.0)
    assert totals["memory_limits_bytes"] == 1024**3

def make_capacity(
    *,
    instance="node-a",
    ready=True,
    schedulable=True,
    taints=None,
    cpu_cores=4.0,
    memory_bytes=8 * 1024**3,
    pods=10,
):
    return {
        "instance": instance,
        "status": {
            "ready": ready,
            "schedulable": schedulable,
        },
        "taints": [] if taints is None else taints,
        "headroom": {
            "cpu_cores": cpu_cores,
            "memory_bytes": memory_bytes,
            "pods": pods,
        },
    }


@pytest.mark.parametrize(
    ("ready", "schedulable", "expected_reason"),
    [
        (False, True, "Node is not Ready"),
        (True, False, "Node is unschedulable"),
    ],
)
def test_capacity_rejects_unavailable_node(
    ready,
    schedulable,
    expected_reason,
):
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(
            ready=ready,
            schedulable=schedulable,
        )
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
        },
    )

    node_result = result["results"][0]

    assert node_result["can_run_now"] is False
    assert expected_reason in node_result["reasons"]

def test_capacity_rejects_insufficient_cpu():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(cpu_cores=0.25)
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1.0,
            "memory_bytes": 128 * 1024**2,
            "pods": 1,
        },
    )

    node_result = result["results"][0]

    assert node_result["can_run_now"] is False
    assert any(
        reason.startswith("CPU available")
        for reason in node_result["reasons"]
    )


def test_capacity_rejects_insufficient_memory():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(memory_bytes=128 * 1024**2)
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 0.5,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
        },
    )

    node_result = result["results"][0]

    assert node_result["can_run_now"] is False
    assert any(
        reason.startswith("Memory available")
        for reason in node_result["reasons"]
    )


def test_capacity_accepts_equal_toleration():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(
            taints=[
                {
                    "key": "dedicated",
                    "value": "gpu",
                    "effect": "NoSchedule",
                }
            ]
        )
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
            "tolerations": [
                {
                    "key": "dedicated",
                    "operator": "Equal",
                    "value": "gpu",
                    "effect": "NoSchedule",
                }
            ],
        },
    )

    assert result["results"][0]["can_run_now"] is True


def test_capacity_accepts_exists_toleration():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(
            taints=[
                {
                    "key": "dedicated",
                    "value": "gpu",
                    "effect": "NoSchedule",
                }
            ]
        )
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
            "tolerations": [
                {
                    "key": "dedicated",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
        },
    )

    assert result["results"][0]["can_run_now"] is True

def test_prefer_no_schedule_does_not_block_capacity():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(
            taints=[
                {
                    "key": "workload",
                    "value": "batch",
                    "effect": "PreferNoSchedule",
                }
            ]
        )
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
        },
    )

    assert result["results"][0]["can_run_now"] is True

@patch("tools.kubernetes_capacity.client.CoreV1Api")
@patch("tools.kubernetes_capacity.config.load_kube_config")
@patch("tools.kubernetes_capacity.config.load_incluster_config")
def test_kubernetes_provider_raises_when_all_config_loading_fails(
    mock_incluster,
    mock_kubeconfig,
    mock_core_api,
):
    mock_incluster.side_effect = ConfigException()
    mock_kubeconfig.side_effect = ConfigException()

    with pytest.raises(
        RuntimeError,
        match="Kubernetes configuration could not be loaded",
    ):
        KubernetesMetricsProvider()

    mock_core_api.assert_not_called()

def test_capacity_passes_requested_node_to_provider():
    provider = MagicMock()
    provider.get_capacity.return_value = []

    evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
        },
        node="node-b",
    )

    provider.get_capacity.assert_called_once_with(
        host="node-b"
    )


def test_capacity_rejects_untolerated_noschedule_taint():
    provider = MagicMock()
    provider.get_capacity.return_value = [
        make_capacity(
            taints=[
                {
                    "key": "dedicated",
                    "value": "gpu",
                    "effect": "NoSchedule",
                }
            ]
        )
    ]

    result = evaluate_kubernetes_capacity(
        provider,
        {
            "cpu_cores": 1,
            "memory_bytes": 512 * 1024**2,
            "pods": 1,
            "tolerations": [],
        },
    )

    node_result = result["results"][0]

    assert node_result["can_run_now"] is False
    assert (
        "Untolerated node taint: dedicated=gpu:NoSchedule"
        in node_result["reasons"]
    )