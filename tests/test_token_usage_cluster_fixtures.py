import pytest

from evaluations.token_usage.cluster_fixtures import (
    SUPPORTED_NODE_COUNTS,
    build_cluster_fixture,
)


def test_supported_cluster_sizes_are_fixed():
    assert SUPPORTED_NODE_COUNTS == (10, 100, 1000)


@pytest.mark.parametrize("node_count", SUPPORTED_NODE_COUNTS)
def test_fixture_contains_requested_number_of_nodes(node_count):
    fixture = build_cluster_fixture(node_count)

    assert fixture["node_count"] == node_count
    assert len(fixture["nodes"]) == node_count


def test_every_node_has_identical_capacity_and_requested_resources():
    fixture = build_cluster_fixture(10)

    for node in fixture["nodes"]:
        assert node["ready"] is True
        assert node["schedulable"] is True
        assert node["allocatable"] == {
            "cpu_cores": 8.0,
            "memory_bytes": 16 * 1024**3,
            "pods": 110,
        }
        assert node["requested"] == {
            "cpu_cores": 2.0,
            "memory_bytes": 4 * 1024**3,
            "pods": 20,
        }


def test_larger_fixtures_preserve_smaller_fixture_prefix():
    ten = build_cluster_fixture(10)
    hundred = build_cluster_fixture(100)
    thousand = build_cluster_fixture(1000)

    assert hundred["nodes"][:10] == ten["nodes"]
    assert thousand["nodes"][:100] == hundred["nodes"]


def test_fixture_contains_only_the_dedicated_test_deployment():
    fixture = build_cluster_fixture(10)

    assert fixture["deployments"] == [
        {
            "namespace": "edgepilot-token-eval",
            "name": "edgepilot-token-eval-nginx",
            "replicas": 1,
            "ready_replicas": 1,
        }
    ]


def test_unsupported_cluster_size_fails():
    with pytest.raises(ValueError, match="unsupported node count"):
        build_cluster_fixture(50)
