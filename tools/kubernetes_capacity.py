from abc import ABC, abstractmethod
from typing import Any, Dict

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException
from kubernetes.client.exceptions import ApiException


class MetricsProvider(ABC):
    @abstractmethod
    def gather_metrics(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_capacity(
        self,
        host: str | None = None,
    ) -> list[Dict[str, Any]]:
        raise NotImplementedError

def _node_is_ready(node) -> bool:
    """Return True when the Kubernetes node reports Ready=True."""
    for condition in node.status.conditions or []:
        if condition.type == "Ready":
            return condition.status == "True"

    return False


def _node_is_schedulable(node) -> bool:
    """Return True when the node has not been cordoned."""
    return not bool(node.spec.unschedulable)

class LocalMetricsProvider(MetricsProvider):
    def gather_metrics(
        self,
        top_n: int = 10,
        all_processes: bool = False,
    ) -> Dict[str, Any]:
        from .metrics import gather_metrics

        return gather_metrics(
            top_n=top_n,
            all_processes=all_processes,
        )

    def get_capacity(
            self,
            host: str | None = None,
    ) -> list[Dict[str, Any]]:
        """Return normalized local machine capacity."""

        import socket

        snapshot = self.gather_metrics()
        hostname = socket.gethostname()

        if host is not None and host != hostname:
            return []

        cpu_count = snapshot["cpu"]["cores_logical"] or 1
        cpu_available_fraction = max(
            0.0,
            1.0 - snapshot["cpu"]["percent"] / 100.0,
        )

        return [
            {
                "instance": hostname,
                "source": "local",
                "headroom": {
                    "cpu_cores": cpu_count * cpu_available_fraction,
                    "memory_bytes": snapshot["memory"]["available"],
                    "disk_free_bytes": snapshot["filesystem"]["free"],
                },
                "requested": {},
                "allocatable": {
                    "cpu_cores": cpu_count,
                    "memory_bytes": snapshot["memory"]["total"],
                    "disk_bytes": snapshot["filesystem"]["total"],
                },
            }
        ]


def _parse_cpu_quantity(value: str) -> float:
    """Convert a Kubernetes CPU quantity to CPU cores."""
    value = str(value)

    if value.endswith("n"):
        return float(value[:-1]) / 1_000_000_000
    if value.endswith("u"):
        return float(value[:-1]) / 1_000_000
    if value.endswith("m"):
        return float(value[:-1]) / 1_000

    return float(value)


def _parse_memory_quantity(value: str) -> int:
    """Convert a Kubernetes memory quantity to bytes."""
    value = str(value)

    suffixes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }

    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * multiplier)

    return int(float(value))


def _container_resource_totals(containers) -> Dict[str, float | int]:
    totals = {
        "cpu_requests_cores": 0.0,
        "cpu_limits_cores": 0.0,
        "memory_requests_bytes": 0,
        "memory_limits_bytes": 0,
    }

    for container in containers or []:
        resources = container.resources

        if resources is None:
            requests = {}
            limits = {}
        else:
            requests = resources.requests or {}
            limits = resources.limits or {}

        totals["cpu_requests_cores"] += _parse_cpu_quantity(
            requests.get("cpu", "0")
        )
        totals["cpu_limits_cores"] += _parse_cpu_quantity(
            limits.get("cpu", "0")
        )
        totals["memory_requests_bytes"] += _parse_memory_quantity(
            requests.get("memory", "0")
        )
        totals["memory_limits_bytes"] += _parse_memory_quantity(
            limits.get("memory", "0")
        )

    return totals

def _pod_resource_totals(pod) -> Dict[str, float | int]:
    """Calculate the effective resource requests and limits for a Pod."""

    regular = _container_resource_totals(
        pod.spec.containers or []
    )

    init_containers = pod.spec.init_containers or []

    init_cpu_requests = 0.0
    init_memory_requests = 0
    init_cpu_limits = 0.0
    init_memory_limits = 0

    for container in init_containers:
        totals = _container_resource_totals([container])

        init_cpu_requests = max(
            init_cpu_requests,
            float(totals["cpu_requests_cores"]),
        )
        init_memory_requests = max(
            init_memory_requests,
            int(totals["memory_requests_bytes"]),
        )
        init_cpu_limits = max(
            init_cpu_limits,
            float(totals["cpu_limits_cores"]),
        )
        init_memory_limits = max(
            init_memory_limits,
            int(totals["memory_limits_bytes"]),
        )

    overhead = pod.spec.overhead or {}

    overhead_cpu = _parse_cpu_quantity(
        overhead.get("cpu", "0")
    )
    overhead_memory = _parse_memory_quantity(
        overhead.get("memory", "0")
    )

    effective_cpu_limit = max(
        float(regular["cpu_limits_cores"]),
        init_cpu_limits,
    )

    effective_memory_limit = max(
        int(regular["memory_limits_bytes"]),
        init_memory_limits,
    )

    return {
        "cpu_requests_cores": (
                max(
                    float(regular["cpu_requests_cores"]),
                    init_cpu_requests,
                )
                + overhead_cpu
        ),
        "memory_requests_bytes": (
                max(
                    int(regular["memory_requests_bytes"]),
                    init_memory_requests,
                )
                + overhead_memory
        ),
        "cpu_limits_cores": (
            effective_cpu_limit + overhead_cpu
            if effective_cpu_limit > 0
            else 0.0
        ),
        "memory_limits_bytes": (
            effective_memory_limit + overhead_memory
            if effective_memory_limit > 0
            else 0
        ),
    }

def _node_taints(node) -> list[Dict[str, str]]:
    """Return normalized taints applied to a Kubernetes node."""

    taints = []

    for taint in node.spec.taints or []:
        taints.append(
            {
                "key": taint.key or "",
                "value": taint.value or "",
                "effect": taint.effect or "",
            }
        )

    return taints


class KubernetesMetricsProvider(MetricsProvider):
    """Collect metrics and resource information from Kubernetes."""

    def __init__(self, kubeconfig: str | None = None):
        self.kubeconfig = kubeconfig

        try:
            if kubeconfig:
                config.load_kube_config(config_file=kubeconfig)
            else:
                try:
                    config.load_incluster_config()
                except ConfigException:
                    config.load_kube_config()

        except ConfigException as exc:
            raise RuntimeError(
                "Kubernetes configuration could not be loaded. "
                "Run inside a Kubernetes cluster, configure ~/.kube/config, "
                "or provide a kubeconfig path."
            ) from exc

        self.core = client.CoreV1Api()

    def list_nodes(self):
        """Return all Kubernetes nodes."""

        try:
            return self.core.list_node().items
        except ApiException as exc:
            raise RuntimeError(
                f"Unable to list Kubernetes nodes: "
                f"status={exc.status}, reason={exc.reason}"
            ) from exc

    def list_pods(self):
        """Return all Kubernetes Pods."""

        try:
            return self.core.list_pod_for_all_namespaces(
                watch=False
            ).items
        except ApiException as exc:
            raise RuntimeError(
                f"Unable to list Kubernetes Pods: "
                f"status={exc.status}, reason={exc.reason}"
            ) from exc

    def gather_metrics(
            self,
            top_n: int = 10,
            all_processes: bool = False,
    ) -> Dict[str, Any]:
        """Collect node capacity and scheduled Pod resource commitments."""

        nodes = self.list_nodes()
        pods = self.list_pods()

        pods_by_node: Dict[str, list] = {}

        for pod in pods:
            node_name = pod.spec.node_name

            # Ignore Pods that have not yet been scheduled.
            if not node_name:
                continue

            # Completed Pods no longer consume schedulable resources.
            if pod.status.phase in {"Succeeded", "Failed"}:
                continue

            pods_by_node.setdefault(node_name, []).append(pod)

        node_metrics = []

        cluster_allocatable_cpu = 0.0
        cluster_allocatable_memory = 0
        cluster_requested_cpu = 0.0
        cluster_requested_memory = 0
        cluster_cpu_limits = 0.0
        cluster_memory_limits = 0

        for node in nodes:
            ready = _node_is_ready(node)
            schedulable = _node_is_schedulable(node)
            node_name = node.metadata.name
            node_taints = _node_taints(node)

            allocatable = node.status.allocatable or {}
            capacity = node.status.capacity or {}

            allocatable_cpu = _parse_cpu_quantity(
                allocatable.get("cpu", "0")
            )
            allocatable_memory = _parse_memory_quantity(
                allocatable.get("memory", "0")
            )

            requested_cpu = 0.0
            requested_memory = 0
            cpu_limits = 0.0
            memory_limits = 0

            node_pods = pods_by_node.get(node_name, [])

            for pod in node_pods:
                totals = _pod_resource_totals(pod)

                requested_cpu += totals["cpu_requests_cores"]
                requested_memory += totals["memory_requests_bytes"]
                cpu_limits += totals["cpu_limits_cores"]
                memory_limits += totals["memory_limits_bytes"]

            available_cpu = max(
                allocatable_cpu - requested_cpu,
                0.0,
            )
            available_memory = max(
                allocatable_memory - requested_memory,
                0,
            )

            node_metrics.append(
                {
                    "name": node_name,
                    "cpu": {
                        "capacity_cores": _parse_cpu_quantity(
                            capacity.get("cpu", "0")
                        ),
                        "allocatable_cores": allocatable_cpu,
                        "requested_cores": requested_cpu,
                        "available_cores": available_cpu,
                        "limit_cores": cpu_limits,
                        "requested_percent": (
                            requested_cpu / allocatable_cpu * 100
                            if allocatable_cpu
                            else 0.0
                        ),
                    },
                    "status": {
                        "ready": ready,
                        "schedulable": schedulable,
                    },
                    "taints": node_taints,
                    "memory": {
                        "capacity_bytes": _parse_memory_quantity(
                            capacity.get("memory", "0")
                        ),
                        "allocatable_bytes": allocatable_memory,
                        "requested_bytes": requested_memory,
                        "available_bytes": available_memory,
                        "limit_bytes": memory_limits,
                        "requested_percent": (
                            requested_memory
                            / allocatable_memory
                            * 100
                            if allocatable_memory
                            else 0.0
                        ),
                    },
                    "pods": {
                        "capacity": int(capacity.get("pods", 0)),
                        "allocatable": int(
                            allocatable.get("pods", 0)
                        ),
                        "scheduled": len(node_pods),
                    },
                }
            )

            cluster_allocatable_cpu += allocatable_cpu
            cluster_allocatable_memory += allocatable_memory
            cluster_requested_cpu += requested_cpu
            cluster_requested_memory += requested_memory
            cluster_cpu_limits += cpu_limits
            cluster_memory_limits += memory_limits

        return {
            "source": "kubernetes",
            "node_count": len(node_metrics),
            "cluster": {
                "cpu": {
                    "allocatable_cores": cluster_allocatable_cpu,
                    "requested_cores": cluster_requested_cpu,
                    "available_cores": max(
                        cluster_allocatable_cpu
                        - cluster_requested_cpu,
                        0.0,
                    ),
                    "limit_cores": cluster_cpu_limits,
                    "requested_percent": (
                        cluster_requested_cpu
                        / cluster_allocatable_cpu
                        * 100
                        if cluster_allocatable_cpu
                        else 0.0
                    ),
                },
                "memory": {
                    "allocatable_bytes": cluster_allocatable_memory,
                    "requested_bytes": cluster_requested_memory,
                    "available_bytes": max(
                        cluster_allocatable_memory
                        - cluster_requested_memory,
                        0,
                    ),
                    "limit_bytes": cluster_memory_limits,
                    "requested_percent": (
                        cluster_requested_memory
                        / cluster_allocatable_memory
                        * 100
                        if cluster_allocatable_memory
                        else 0.0
                    ),
                },
                "scheduled_pods": sum(
                    len(node_pods)
                    for node_pods in pods_by_node.values()
                ),
            },
            "nodes": node_metrics,
        }

    def get_capacity(
            self,
            host: str | None = None,
    ) -> list[Dict[str, Any]]:
        """Return normalized per-node Kubernetes capacity."""

        snapshot = self.gather_metrics()
        capacities: list[Dict[str, Any]] = []

        for node in snapshot["nodes"]:
            node_name = node["name"]

            if host is not None and node_name != host:
                continue

            capacities.append(
                {
                    "instance": node_name,
                    "source": "kubernetes",
                    "status": node["status"],
                    "taints": node.get("taints", []),
                    "headroom": {
                        "cpu_cores": node["cpu"]["available_cores"],
                        "memory_bytes": node["memory"]["available_bytes"],
                        "pods": max(
                            node["pods"]["allocatable"]
                            - node["pods"]["scheduled"],
                            0,
                        ),
                    },
                    "requested": {
                        "cpu_cores": node["cpu"]["requested_cores"],
                        "memory_bytes": node["memory"]["requested_bytes"],
                        "pods": node["pods"]["scheduled"],
                    },
                    "allocatable": {
                        "cpu_cores": node["cpu"]["allocatable_cores"],
                        "memory_bytes": node["memory"]["allocatable_bytes"],
                        "pods": node["pods"]["allocatable"],
                    },
                }
            )

        return capacities


def _toleration_matches_taint(
    toleration: Dict[str, Any],
    taint: Dict[str, str],
) -> bool:
    """Return True when a toleration matches a node taint."""

    toleration_effect = toleration.get("effect")

    if toleration_effect and toleration_effect != taint["effect"]:
        return False

    operator = toleration.get("operator", "Equal")
    key = toleration.get("key", "")

    if operator == "Exists":
        return not key or key == taint["key"]

    if operator == "Equal":
        return (
            key == taint["key"]
            and str(toleration.get("value", "")) == taint["value"]
        )

    return False


def _untolerated_taints(
    taints: list[Dict[str, str]],
    tolerations: list[Dict[str, Any]],
) -> list[Dict[str, str]]:
    """Return hard scheduling taints not matched by a toleration."""

    blocking_effects = {"NoSchedule", "NoExecute"}
    unmatched = []

    for taint in taints:
        if taint["effect"] not in blocking_effects:
            continue

        tolerated = any(
            _toleration_matches_taint(toleration, taint)
            for toleration in tolerations
        )

        if not tolerated:
            unmatched.append(taint)

    return unmatched


def evaluate_kubernetes_capacity(
    provider: MetricsProvider,
    workload: Dict[str, Any],
    node: str | None = None,
) -> Dict[str, Any]:
    """Evaluate whether a workload can run on available Kubernetes nodes."""

    required_cpu = float(workload.get("cpu_cores", 0) or 0)
    required_memory = int(workload.get("memory_bytes", 0) or 0)
    required_pods = int(workload.get("pods", 1) or 0)
    tolerations = workload.get("tolerations") or []

    capacities = provider.get_capacity(host=node)
    results = []

    for capacity in capacities:
        status = capacity.get("status") or {}
        headroom = capacity.get("headroom") or {}
        taints = capacity.get("taints") or []

        available_cpu = float(headroom.get("cpu_cores", 0) or 0)
        available_memory = int(headroom.get("memory_bytes", 0) or 0)
        available_pods = int(headroom.get("pods", 0) or 0)

        reasons = []

        if not status.get("ready", False):
            reasons.append("Node is not Ready")

        if not status.get("schedulable", False):
            reasons.append("Node is unschedulable")

        for taint in _untolerated_taints(taints, tolerations):
            key = taint.get("key", "")
            value = taint.get("value", "")
            effect = taint.get("effect", "")

            if value:
                formatted_taint = f"{key}={value}:{effect}"
            else:
                formatted_taint = f"{key}:{effect}"

            reasons.append(
                f"Untolerated node taint: {formatted_taint}"
            )

        if available_cpu < required_cpu:
            reasons.append(
                f"CPU available {available_cpu} < required {required_cpu}"
            )

        if available_memory < required_memory:
            reasons.append(
                "Memory available "
                f"{available_memory} < required {required_memory}"
            )

        if available_pods < required_pods:
            reasons.append(
                f"Pod slots available {available_pods} "
                f"< required {required_pods}"
            )

        results.append(
            {
                "instance": capacity.get("instance"),
                "can_run_now": not reasons,
                "reasons": reasons,
                "headroom": headroom,
            }
        )

    return {
        "can_run_now": any(
            result["can_run_now"] for result in results
        ),
        "requested": {
            "cpu_cores": required_cpu,
            "memory_bytes": required_memory,
            "pods": required_pods,
        },
        "results": results,
    }
