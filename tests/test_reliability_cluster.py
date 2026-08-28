import pytest

from evaluations.reliability.cluster import FakeCluster, UnknownResource


def build() -> FakeCluster:
    return FakeCluster(
        nodes=["node-a", "node-b", "node-c"],
        deployments={
            ("default", "api"): 3,
            ("default", "worker"): 2,
            ("payments", "api"): 1,
        },
    )


def test_scale_changes_the_replica_count():
    cluster = build()

    cluster.scale_workload("default", "api", 5)

    assert cluster.replicas("default", "api") == 5


def test_scale_leaves_the_same_name_in_another_namespace_alone():
    """Namespace confusion is a realistic model failure worth catching."""
    cluster = build()

    cluster.scale_workload("default", "api", 5)

    assert cluster.replicas("payments", "api") == 1


def test_restart_bumps_a_counter_without_changing_replicas():
    cluster = build()

    cluster.restart_workload("default", "api")

    assert cluster.restarts("default", "api") == 1
    assert cluster.replicas("default", "api") == 3


def test_cordon_marks_only_the_named_node():
    cluster = build()

    cluster.cordon_node("node-b")

    assert cluster.is_cordoned("node-b") is True
    assert cluster.is_cordoned("node-a") is False


def test_unknown_deployment_raises_rather_than_silently_passing():
    cluster = build()

    with pytest.raises(UnknownResource, match="ghost"):
        cluster.scale_workload("default", "ghost", 2)


def test_unknown_node_raises():
    cluster = build()

    with pytest.raises(UnknownResource, match="node-z"):
        cluster.cordon_node("node-z")


def test_negative_replicas_rejected():
    cluster = build()

    with pytest.raises(ValueError, match="replicas"):
        cluster.scale_workload("default", "api", -1)


def test_snapshot_is_comparable_and_order_independent():
    """Two clusters reaching the same state must compare equal."""
    a, b = build(), build()

    a.scale_workload("default", "api", 4)
    a.cordon_node("node-c")

    b.cordon_node("node-c")
    b.scale_workload("default", "api", 4)

    assert a.snapshot() == b.snapshot()


def test_snapshot_distinguishes_different_states():
    a, b = build(), build()
    a.scale_workload("default", "api", 4)

    assert a.snapshot() != b.snapshot()


def test_actions_are_recorded_in_order():
    cluster = build()

    cluster.scale_workload("default", "api", 4)
    cluster.cordon_node("node-a")

    assert cluster.actions == [
        ("scale_workload", {"namespace": "default", "deployment_name": "api", "replicas": 4}),
        ("cordon_node", {"node_name": "node-a"}),
    ]


def test_reset_restores_the_starting_state():
    """Every repetition must start from identical conditions."""
    cluster = build()
    original = cluster.snapshot()

    cluster.scale_workload("default", "api", 9)
    cluster.reset()

    assert cluster.snapshot() == original
    assert cluster.actions == []


def test_describe_lists_nodes_and_deployments():
    """This text goes into the model's context, so it must be accurate."""
    cluster = build()
    cluster.cordon_node("node-c")

    described = cluster.describe()

    assert "node-c (cordoned)" in described
    assert "node-a (schedulable)" in described
    assert "default/api: 3 replicas" in described
    assert "payments/api: 1 replicas" in described


def test_capacity_is_reported_so_a_scale_up_can_be_justified():
    """The Skill requires capacity verification before scaling.

    A fixture with no resource data made that impossible: models correctly
    asked for "CPU and memory requests/limits" and "request headroom", the
    fixture had none, and runs stalled at `no_action`. A measurement fixture
    must satisfy the preconditions the thing being measured insists on.
    """
    cluster = build()
    capacity = cluster.capacity()

    assert capacity["schedulable_nodes"] == 3
    assert capacity["total_cpu_cores"] == 24          # 3 nodes x 8 cores
    assert capacity["requested_cpu_cores"] == 3.0     # 6 replicas x 0.5
    assert capacity["free_cpu_cores"] == 21.0
    assert capacity["free_memory_gb"] == 84


def test_cordoning_removes_a_node_from_capacity():
    """Otherwise a model cannot reason about scaling after a cordon."""
    cluster = build()
    before = cluster.capacity()["total_cpu_cores"]

    cluster.cordon_node("node-b")

    assert cluster.capacity()["schedulable_nodes"] == 2
    assert cluster.capacity()["total_cpu_cores"] == before - 8


def test_scaling_up_consumes_capacity():
    cluster = build()
    before = cluster.capacity()["free_cpu_cores"]

    cluster.scale_workload("default", "api", 5)   # +2 replicas x 0.5 CPU

    assert cluster.capacity()["free_cpu_cores"] == before - 1.0


def test_per_replica_requests_are_reported():
    cluster = build()

    requests = cluster.requests("default", "api")

    assert requests["cpu_cores"] == 0.5
    assert requests["memory_gb"] == 2


def test_the_state_summary_includes_capacity():
    """The model sees this on every turn; without it, it has to ask."""
    described = build().describe()

    for expected in ("Capacity", "free", "requests", "CPU"):
        assert expected in described, f"describe() is missing {expected!r}"


def test_capacity_resets_with_the_cluster():
    cluster = build()
    original = cluster.capacity()

    cluster.scale_workload("default", "api", 40)
    cluster.cordon_node("node-a")
    cluster.reset()

    assert cluster.capacity() == original
