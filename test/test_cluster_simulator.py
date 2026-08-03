import pytest

from tools.cluster_simulator import SimulatedMetricsProvider


def test_generates_the_requested_shape():
    cluster = SimulatedMetricsProvider(node_count=5, pods_per_node=4, seed=1)

    assert len(cluster.list_nodes()) == 5
    assert cluster.pod_count == 20


def test_same_seed_produces_an_identical_cluster():
    """Goal 3 compares runs, so the cluster must not vary between them."""
    a = SimulatedMetricsProvider(node_count=10, pods_per_node=5, seed=42)
    b = SimulatedMetricsProvider(node_count=10, pods_per_node=5, seed=42)

    assert [p.metadata.name for p in a.list_pods()] == [
        p.metadata.name for p in b.list_pods()
    ]
    assert a.planted_anomalies == b.planted_anomalies


def test_different_seeds_produce_different_clusters():
    a = SimulatedMetricsProvider(node_count=10, pods_per_node=5, seed=1)
    b = SimulatedMetricsProvider(node_count=10, pods_per_node=5, seed=2)

    assert [p.metadata.name for p in a.list_pods()] != [
        p.metadata.name for p in b.list_pods()
    ]


def test_anomalies_are_planted_and_recorded():
    cluster = SimulatedMetricsProvider(
        node_count=20, pods_per_node=10, anomaly_rate=0.10, seed=3
    )

    assert cluster.planted_anomalies, "expected some anomalies at a 10% rate"

    for anomaly in cluster.planted_anomalies:
        # The meeting's example case: consuming far more than requested.
        assert anomaly["actual_memory_bytes"] > anomaly["requested_memory_bytes"]

    usage = cluster.pod_usage()
    first = cluster.planted_anomalies[0]
    key = (first["namespace"], first["pod"])

    assert usage[key]["memory_bytes"] == first["actual_memory_bytes"]


def test_zero_anomaly_rate_plants_none():
    cluster = SimulatedMetricsProvider(
        node_count=10, pods_per_node=10, anomaly_rate=0.0, seed=5
    )

    assert cluster.planted_anomalies == []


def test_gather_metrics_matches_the_real_provider_shape():
    """Delegating to the real accounting logic keeps the shapes honest."""
    cluster = SimulatedMetricsProvider(node_count=3, pods_per_node=2, seed=7)
    snapshot = cluster.gather_metrics()

    assert snapshot["source"] == "kubernetes"
    assert snapshot["node_count"] == 3
    assert len(snapshot["nodes"]) == 3

    node = snapshot["nodes"][0]
    assert node["cpu"]["allocatable_cores"] > 0
    assert node["memory"]["allocatable_bytes"] > 0
    assert node["pods"]["scheduled"] == 2

    cluster_totals = snapshot["cluster"]
    assert cluster_totals["cpu"]["requested_cores"] > 0
    assert cluster_totals["scheduled_pods"] == 6


def test_get_capacity_can_target_one_node():
    cluster = SimulatedMetricsProvider(node_count=4, pods_per_node=1, seed=9)
    name = cluster.list_nodes()[2].metadata.name

    capacities = cluster.get_capacity(host=name)

    assert len(capacities) == 1
    assert capacities[0]["instance"] == name


def test_thousand_node_cluster_is_generated_in_reasonable_time():
    """The meeting asks for up to ~1,000 nodes and 10,000 pods."""
    import time

    started = time.perf_counter()
    cluster = SimulatedMetricsProvider(node_count=1000, pods_per_node=10, seed=11)
    elapsed = time.perf_counter() - started

    assert cluster.pod_count == 10_000
    assert elapsed < 30.0, f"generation took {elapsed:.1f}s"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"node_count": 0}, "node_count"),
        ({"pods_per_node": -1}, "pods_per_node"),
        ({"anomaly_rate": 1.5}, "anomaly_rate"),
    ],
)
def test_invalid_parameters_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        SimulatedMetricsProvider(**kwargs)
