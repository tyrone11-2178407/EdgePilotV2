from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.cluster_usage import MetricsServerUnavailable
from tools.kubernetes_source import _workload_identity, records_from_cluster


# Distinguishes "caller did not specify owners" from "this Pod genuinely has
# none" — `owners or [default]` would collapse the two.
_UNSET = object()


def owner(kind, name):
    return SimpleNamespace(kind=kind, name=name)


def make_container(*, name="app", cpu_request="1", memory_request="1Gi"):
    requests = {}
    if cpu_request:
        requests["cpu"] = cpu_request
    if memory_request:
        requests["memory"] = memory_request

    return SimpleNamespace(
        name=name,
        resources=SimpleNamespace(requests=requests, limits={}),
    )


def make_pod(
    *,
    name="web-7d9f8b6c5d-x4k2p",
    namespace="prod",
    containers=None,
    owners=_UNSET,
    container_statuses=None,
    phase="Running",
    node_name="node-a",
):
    if owners is _UNSET:
        owners = [owner("ReplicaSet", "web-7d9f8b6c5d")]

    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            owner_references=owners,
        ),
        spec=SimpleNamespace(
            node_name=node_name,
            containers=containers or [make_container()],
            init_containers=[],
            overhead={},
        ),
        status=SimpleNamespace(
            phase=phase, container_statuses=container_statuses or []
        ),
    )


def oom_status(name="app", restart_count=3):
    return SimpleNamespace(
        name=name,
        restart_count=restart_count,
        last_state=SimpleNamespace(
            terminated=SimpleNamespace(reason="OOMKilled")
        ),
    )


def test_replicaset_owner_resolves_to_deployment():
    assert _workload_identity(make_pod()) == ("prod", "Deployment", "web")


def test_statefulset_owner_used_directly():
    pod = make_pod(namespace="db", owners=[owner("StatefulSet", "postgres")])

    assert _workload_identity(pod) == ("db", "StatefulSet", "postgres")


def test_bare_pod_is_its_own_workload():
    pod = make_pod(name="debug-shell", owners=None)

    assert _workload_identity(pod) == ("prod", "Pod", "debug-shell")


def test_replicaset_name_without_hash_is_left_intact():
    pod = make_pod(owners=[owner("ReplicaSet", "legacy")])

    assert _workload_identity(pod) == ("prod", "Deployment", "legacy")


def test_records_join_requests_with_live_usage():
    provider = MagicMock()
    provider.list_pods.return_value = [make_pod()]

    records = records_from_cluster(
        provider=provider,
        usage_fetcher=lambda namespace=None: {
            ("prod", "web-7d9f8b6c5d-x4k2p"): {
                "cpu_cores": 0.1,
                "memory_bytes": 200 * 1024**2,
            }
        },
    )

    assert len(records) == 1

    record = records[0]
    assert record.source == "kubernetes"
    assert record.workload_name == "web"
    assert record.requested_cpu_cores == pytest.approx(1.0)
    assert record.used_cpu_cores == pytest.approx(0.1)
    assert record.peak_memory_bytes == 200 * 1024**2


def test_pods_of_one_workload_collapse_to_a_single_record():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(name="web-7d9f8b6c5d-aaaaa"),
        make_pod(name="web-7d9f8b6c5d-bbbbb"),
    ]

    records = records_from_cluster(
        provider=provider,
        usage_fetcher=lambda namespace=None: {
            ("prod", "web-7d9f8b6c5d-aaaaa"): {
                "cpu_cores": 0.1,
                "memory_bytes": 100 * 1024**2,
            },
            ("prod", "web-7d9f8b6c5d-bbbbb"): {
                "cpu_cores": 0.3,
                "memory_bytes": 400 * 1024**2,
            },
        },
    )

    assert len(records) == 1
    assert records[0].instance_count == 2
    # Mean CPU, peak memory.
    assert records[0].used_cpu_cores == pytest.approx(0.2)
    assert records[0].peak_memory_bytes == 400 * 1024**2


def test_oomkilled_container_sets_the_flag():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(container_statuses=[oom_status()])
    ]

    records = records_from_cluster(
        provider=provider, usage_fetcher=lambda namespace=None: {}
    )

    assert records[0].failed_oom is True


def test_completed_and_unscheduled_pods_are_skipped():
    provider = MagicMock()
    provider.list_pods.return_value = [
        make_pod(name="done", phase="Succeeded"),
        make_pod(name="pending", node_name=None),
    ]

    records = records_from_cluster(
        provider=provider, usage_fetcher=lambda namespace=None: {}
    )

    assert records == []


def test_missing_metrics_server_leaves_usage_unknown():
    provider = MagicMock()
    provider.list_pods.return_value = [make_pod()]

    def usage_fetcher(namespace=None):
        raise MetricsServerUnavailable("metrics.k8s.io is unavailable")

    records = records_from_cluster(
        provider=provider, usage_fetcher=usage_fetcher
    )

    assert records[0].used_cpu_cores is None
    assert records[0].peak_memory_bytes is None
