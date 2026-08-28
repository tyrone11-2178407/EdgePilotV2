"""Kubernetes workloads as normalized resource records.

Joins what Pods request (``tools.metrics_providers``) with what they actually
consume (``tools.cluster_usage``), grouped by the controller that owns
them — Pods are ephemeral, so recommendations must target the Deployment.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .cluster_usage import MetricsServerUnavailable, fetch_pod_usage
from .metrics_providers import KubernetesMetricsProvider, _pod_resource_totals
from .resource_records import ResourceRecord

# Kubernetes builds pod-template-hash from a vowel-free base32 alphabet so
# the generated suffix can never spell a word.
_HASH_ALPHABET = set("0123456789bcdfghjklmnpqrstvwxz")


def _looks_like_pod_template_hash(segment: str) -> bool:
    """Return True when *segment* looks like a generated ReplicaSet hash."""

    return 5 <= len(segment) <= 10 and all(
        character in _HASH_ALPHABET for character in segment
    )


def _strip_pod_template_hash(name: str) -> str:
    """Recover a Deployment name from its ReplicaSet name."""

    head, separator, tail = name.rpartition("-")

    if separator and _looks_like_pod_template_hash(tail):
        return head

    return name


def _workload_identity(pod) -> tuple[str, str, str]:
    """Return ``(namespace, kind, name)`` for the workload owning *pod*."""

    namespace = pod.metadata.namespace or "default"

    for owner in pod.metadata.owner_references or []:
        if owner.kind == "ReplicaSet":
            return (
                namespace,
                "Deployment",
                _strip_pod_template_hash(owner.name),
            )

        return (namespace, owner.kind, owner.name)

    return (namespace, "Pod", pod.metadata.name)


def _was_oom_killed(pod) -> bool:
    """Return True when any container's last termination was an OOM kill."""

    for status in pod.status.container_statuses or []:
        last_state = getattr(status, "last_state", None)
        terminated = getattr(last_state, "terminated", None)

        if terminated is not None and (
            getattr(terminated, "reason", None) == "OOMKilled"
        ):
            return True

    return False


def records_from_cluster(
    *,
    namespace: Optional[str] = None,
    provider: Any = None,
    usage_fetcher: Optional[Callable[..., Dict]] = None,
) -> List[ResourceRecord]:
    """Return one record per Kubernetes workload.

    CPU usage is averaged across a workload's Pods; memory takes the peak.
    When metrics-server is unavailable, usage fields stay ``None`` so the
    engines report the gap instead of estimating it.
    """

    provider = provider or KubernetesMetricsProvider()
    usage_fetcher = usage_fetcher or fetch_pod_usage

    try:
        usage = usage_fetcher(namespace=namespace)
    except MetricsServerUnavailable:
        usage = {}

    grouped: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    for pod in provider.list_pods():
        # Mirror the accounting rules in tools/providers.py: unscheduled
        # and completed Pods hold no resources.
        if not pod.spec.node_name:
            continue

        if pod.status.phase in {"Succeeded", "Failed"}:
            continue

        if namespace and pod.metadata.namespace != namespace:
            continue

        key = _workload_identity(pod)
        bucket = grouped.setdefault(
            key,
            {
                "pod_count": 0,
                "cpu_requests_cores": 0.0,
                "memory_requests_bytes": 0,
                "cpu_samples": [],
                "memory_samples": [],
                "containers": [],
                "nodes": [],
                "failed_oom": False,
            },
        )

        totals = _pod_resource_totals(pod)

        bucket["pod_count"] += 1
        bucket["cpu_requests_cores"] += totals["cpu_requests_cores"]
        bucket["memory_requests_bytes"] += totals["memory_requests_bytes"]
        bucket["failed_oom"] = bucket["failed_oom"] or _was_oom_killed(pod)

        if pod.spec.node_name not in bucket["nodes"]:
            bucket["nodes"].append(pod.spec.node_name)

        for container in pod.spec.containers or []:
            if container.name not in bucket["containers"]:
                bucket["containers"].append(container.name)

        pod_usage = usage.get((pod.metadata.namespace, pod.metadata.name))

        if pod_usage:
            bucket["cpu_samples"].append(pod_usage["cpu_cores"])
            bucket["memory_samples"].append(pod_usage["memory_bytes"])

    records: List[ResourceRecord] = []

    for (pod_namespace, kind, name), bucket in sorted(grouped.items()):
        pod_count = bucket["pod_count"]
        cpu_samples = bucket["cpu_samples"]
        memory_samples = bucket["memory_samples"]

        records.append(
            ResourceRecord(
                source="kubernetes",
                record_id=f"{pod_namespace}/{name}",
                workload_name=name,
                group=pod_namespace,
                requested_cpu_cores=bucket["cpu_requests_cores"] / pod_count,
                requested_memory_bytes=int(
                    bucket["memory_requests_bytes"] / pod_count
                ),
                instance_count=pod_count,
                used_cpu_cores=(
                    sum(cpu_samples) / len(cpu_samples)
                    if cpu_samples
                    else None
                ),
                peak_memory_bytes=(
                    max(memory_samples) if memory_samples else None
                ),
                state="Running",
                failed_oom=bucket["failed_oom"],
                nodes=bucket["nodes"],
                containers=bucket["containers"],
                labels={"kind": kind},
            )
        )

    return records
