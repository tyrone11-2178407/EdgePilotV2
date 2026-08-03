"""Synthetic Kubernetes clusters for scalability measurement.

The 7/29 meeting asked for cluster sizes from 1 to ~1,000 nodes and 10 to
10,000 jobs, measured for AI token usage and time-to-resolution against a
human baseline. Nobody has a 1,000-node cluster to hand, so this generates
one that is good enough for the measurement: node and pod resource state
shaped exactly as ``KubernetesMetricsProvider`` returns it.

Generation is seeded. Goal 3 compares runs against each other, so the same
parameters must always produce the same cluster — otherwise a difference
between two runs could be the cluster rather than the thing being measured.

Anomalies are planted deliberately and recorded on the instance, so a run
can be scored on whether it actually found them. "Tokens per anomaly
correctly identified" is a far more useful number than tokens alone.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from .kubernetes_capacity import MetricsProvider

# Node hardware classes the generator draws from, loosely modelled on a
# mixed research cluster: mostly standard, some high-memory, a few GPU.
NODE_CLASSES = [
    {"name": "standard", "weight": 0.70, "cpu_cores": 64, "memory_gib": 256, "gpus": 0},
    {"name": "himem", "weight": 0.20, "cpu_cores": 64, "memory_gib": 512, "gpus": 0},
    {"name": "gpu", "weight": 0.10, "cpu_cores": 52, "memory_gib": 384, "gpus": 4},
]

WORKLOAD_NAMES = [
    "api", "worker", "ingest", "trainer", "indexer",
    "scheduler", "cache", "exporter", "gateway", "batch",
]

_GIB = 1024**3


class SimulatedMetricsProvider(MetricsProvider):
    """A generated cluster that satisfies the KubernetesMetricsProvider surface.

    Parameters
    ----------
    node_count:
        Number of nodes to generate.
    pods_per_node:
        Pods scheduled onto each node.
    anomaly_rate:
        Fraction of pods that consume far more memory than they requested —
        the condition the memory_anomaly workflow is meant to find.
    seed:
        Fixes generation so repeated runs measure the same cluster.
    """

    def __init__(
        self,
        node_count: int = 10,
        pods_per_node: int = 10,
        anomaly_rate: float = 0.01,
        seed: int = 7,
    ) -> None:
        if node_count < 1:
            raise ValueError("node_count must be at least 1")
        if pods_per_node < 0:
            raise ValueError("pods_per_node cannot be negative")
        if not 0.0 <= anomaly_rate <= 1.0:
            raise ValueError("anomaly_rate must be between 0 and 1")

        self.node_count = node_count
        self.pods_per_node = pods_per_node
        self.anomaly_rate = anomaly_rate
        self.seed = seed

        self._random = random.Random(seed)
        self.planted_anomalies: List[Dict[str, Any]] = []

        self._nodes = self._build_nodes()
        self._pods = self._build_pods()

    # ── Generation ──────────────────────────────────────────────────────

    def _pick_class(self) -> Dict[str, Any]:
        roll = self._random.random()
        cumulative = 0.0

        for node_class in NODE_CLASSES:
            cumulative += node_class["weight"]
            if roll <= cumulative:
                return node_class

        return NODE_CLASSES[-1]

    def _build_nodes(self) -> List[Any]:
        nodes = []

        for index in range(self.node_count):
            spec = self._pick_class()
            name = f"node-{index:04d}"
            capacity = {
                "cpu": str(spec["cpu_cores"]),
                "memory": f"{spec['memory_gib']}Gi",
                "pods": "110",
            }
            # Allocatable is always slightly below capacity in a real
            # cluster; the kubelet reserves some for the system.
            allocatable = {
                "cpu": str(spec["cpu_cores"] - 1),
                "memory": f"{spec['memory_gib'] - 8}Gi",
                "pods": "110",
            }

            nodes.append(SimpleNamespace(
                metadata=SimpleNamespace(name=name),
                spec=SimpleNamespace(unschedulable=False, taints=[]),
                status=SimpleNamespace(
                    conditions=[SimpleNamespace(type="Ready", status="True")],
                    capacity=capacity,
                    allocatable=allocatable,
                ),
                _hardware_class=spec["name"],
            ))

        return nodes

    def _build_pods(self) -> List[Any]:
        pods = []

        for node in self._nodes:
            node_name = node.metadata.name

            for pod_index in range(self.pods_per_node):
                workload = self._random.choice(WORKLOAD_NAMES)
                pod_name = f"{workload}-{node_name}-{pod_index:03d}"

                requested_cpu = self._random.choice([0.5, 1, 2, 4])
                requested_memory_gib = self._random.choice([1, 2, 4, 8])

                is_anomalous = self._random.random() < self.anomaly_rate

                if is_anomalous:
                    # The meeting's example: a pod using ~20x what it asked
                    # for. Recorded so a run can be scored on finding it.
                    actual_memory_gib = requested_memory_gib * 20
                    self.planted_anomalies.append({
                        "pod": pod_name,
                        "namespace": "default",
                        "node": node_name,
                        "requested_memory_bytes": int(requested_memory_gib * _GIB),
                        "actual_memory_bytes": int(actual_memory_gib * _GIB),
                    })
                else:
                    actual_memory_gib = requested_memory_gib * self._random.uniform(0.3, 0.8)

                pods.append(SimpleNamespace(
                    metadata=SimpleNamespace(
                        name=pod_name,
                        namespace="default",
                        owner_references=[
                            SimpleNamespace(kind="ReplicaSet", name=f"{workload}-7d9f8b6c5d")
                        ],
                    ),
                    spec=SimpleNamespace(
                        node_name=node_name,
                        containers=[SimpleNamespace(
                            name="app",
                            resources=SimpleNamespace(
                                requests={
                                    "cpu": str(requested_cpu),
                                    "memory": f"{requested_memory_gib}Gi",
                                },
                                limits={
                                    "cpu": str(requested_cpu * 2),
                                    "memory": f"{requested_memory_gib * 2}Gi",
                                },
                            ),
                        )],
                        init_containers=[],
                        overhead={},
                    ),
                    status=SimpleNamespace(phase="Running", container_statuses=[]),
                    _actual_memory_bytes=int(actual_memory_gib * _GIB),
                ))

        return pods

    # ── MetricsProvider surface ─────────────────────────────────────────

    def list_nodes(self) -> List[Any]:
        return self._nodes

    def list_pods(self) -> List[Any]:
        return self._pods

    def gather_metrics(self, top_n: int = 10, all_processes: bool = False) -> Dict[str, Any]:
        """Delegate to the real accounting logic so shapes cannot drift."""

        from .kubernetes_capacity import KubernetesMetricsProvider

        return KubernetesMetricsProvider.gather_metrics(self)

    def get_capacity(self, host: Optional[str] = None) -> List[Dict[str, Any]]:
        from .kubernetes_capacity import KubernetesMetricsProvider

        return KubernetesMetricsProvider.get_capacity(self, host=host)

    # ── Measurement support ─────────────────────────────────────────────

    def pod_usage(self) -> Dict[tuple, Dict[str, Any]]:
        """Usage keyed like ``tools.cluster_usage.fetch_pod_usage`` output."""

        return {
            (pod.metadata.namespace, pod.metadata.name): {
                "cpu_cores": 0.1,
                "memory_bytes": pod._actual_memory_bytes,
            }
            for pod in self._pods
        }

    @property
    def pod_count(self) -> int:
        return len(self._pods)
