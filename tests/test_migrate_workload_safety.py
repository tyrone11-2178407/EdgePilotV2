"""migrate_workload must not claim success it has not verified.

The original version patched the deployment and immediately returned
"Successfully migrated". All that had actually happened was that Kubernetes
accepted the request — the rollout could fail seconds later and the user had
already been told it worked.

The team's own workflow evaluation recorded the related failure: the AI
"blindly migrated the workload to the first node it saw" without checking the
target had room.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools.kubernetes_actions import migrate_workload


def _node(name: str, cpu: str = "8", memory: str = "32Gi", schedulable: bool = True):
    node = MagicMock()
    node.metadata.name = name
    node.status.allocatable = {"cpu": cpu, "memory": memory}
    node.spec.unschedulable = not schedulable
    return node


def _deployment(replicas: int = 1, ready: int = 1, cpu: str = "500m", memory: str = "1Gi"):
    dep = MagicMock()
    dep.spec.replicas = replicas
    dep.status.ready_replicas = ready
    dep.status.updated_replicas = ready
    dep.status.replicas = replicas
    container = MagicMock()
    container.resources.requests = {"cpu": cpu, "memory": memory}
    dep.spec.template.spec.containers = [container]
    return dep


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_refuses_when_the_target_node_does_not_exist(mock_apps, mock_core):
    core = MagicMock()
    core.read_node.side_effect = Exception("nodes 'node-zz' not found")
    mock_core.return_value = core
    mock_apps.return_value = MagicMock()

    result = migrate_workload("default", "api", "node-zz")

    assert result["success"] is False
    assert "node-zz" in result["error"]
    mock_apps.return_value.patch_namespaced_deployment.assert_not_called()


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_refuses_when_the_target_node_is_cordoned(mock_apps, mock_core):
    """Moving work onto a node marked unschedulable strands the pod."""
    core = MagicMock()
    core.read_node.return_value = _node("node-b", schedulable=False)
    mock_core.return_value = core
    mock_apps.return_value = MagicMock()

    result = migrate_workload("default", "api", "node-b")

    assert result["success"] is False
    assert "unschedulable" in result["error"].lower()
    mock_apps.return_value.patch_namespaced_deployment.assert_not_called()


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_refuses_when_the_target_node_lacks_capacity(mock_apps, mock_core):
    """The documented failure: migrating without checking the target has room.

    A pod that cannot be scheduled sits Pending forever and the deployment is
    stuck half-moved.
    """
    core = MagicMock()
    core.read_node.return_value = _node("node-b", cpu="1", memory="2Gi")
    # Existing pod already consuming most of the node.
    pod = MagicMock()
    existing = MagicMock()
    existing.resources.requests = {"cpu": "900m", "memory": "1900Mi"}
    pod.spec.containers = [existing]
    core.list_pod_for_all_namespaces.return_value.items = [pod]
    mock_core.return_value = core

    apps = MagicMock()
    apps.read_namespaced_deployment.return_value = _deployment(cpu="500m", memory="1Gi")
    mock_apps.return_value = apps

    result = migrate_workload("default", "api", "node-b")

    assert result["success"] is False
    assert "capacity" in result["error"].lower() or "room" in result["error"].lower()
    apps.patch_namespaced_deployment.assert_not_called()


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_reports_failure_when_the_rollout_does_not_complete(mock_apps, mock_core):
    """Patching is not migrating. Success must mean the pods actually moved."""
    core = MagicMock()
    core.read_node.return_value = _node("node-b")
    core.list_pod_for_all_namespaces.return_value.items = []
    mock_core.return_value = core

    apps = MagicMock()
    # Before: healthy. After: the new pod never becomes ready.
    stuck = _deployment(replicas=2, ready=1)
    apps.read_namespaced_deployment.return_value = stuck
    mock_apps.return_value = apps

    result = migrate_workload("default", "api", "node-b", wait_seconds=0)

    assert result["success"] is False
    assert "roll" in result["error"].lower() or "ready" in result["error"].lower()


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_succeeds_only_after_the_rollout_completes(mock_apps, mock_core):
    core = MagicMock()
    core.read_node.return_value = _node("node-b")
    core.list_pod_for_all_namespaces.return_value.items = []
    mock_core.return_value = core

    apps = MagicMock()
    apps.read_namespaced_deployment.return_value = _deployment(replicas=2, ready=2)
    mock_apps.return_value = apps

    result = migrate_workload("default", "api", "node-b", wait_seconds=0)

    assert result["success"] is True
    apps.patch_namespaced_deployment.assert_called_once()


@patch("tools.kubernetes_actions._get_core_client")
@patch("tools.kubernetes_actions._get_client")
def test_uses_a_soft_preference_not_a_permanent_pin(mock_apps, mock_core):
    """A hard nodeSelector pins the workload forever.

    If that node later fails, the pod cannot reschedule anywhere and the
    workload is down until a human notices. Affinity expresses the same intent
    without removing every fallback.
    """
    core = MagicMock()
    core.read_node.return_value = _node("node-b")
    core.list_pod_for_all_namespaces.return_value.items = []
    mock_core.return_value = core

    apps = MagicMock()
    apps.read_namespaced_deployment.return_value = _deployment(replicas=1, ready=1)
    mock_apps.return_value = apps

    migrate_workload("default", "api", "node-b", wait_seconds=0)

    body = apps.patch_namespaced_deployment.call_args.kwargs["body"]
    spec = body["spec"]["template"]["spec"]

    assert "nodeSelector" not in spec, "a hard pin leaves no fallback if the node dies"
    assert "affinity" in spec
