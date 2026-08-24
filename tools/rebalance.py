"""Work out how to relieve a node under pressure.

Pure arithmetic. No model, no cluster calls, no side effects — it takes the
current placement and returns a proposed plan. The assistant's job is to
explain that plan and ask for approval, not to compute it: a model asked to
work out which node is short of room and by how much would be guessing at
every number in its own proposal.

Two remedies, and the order matters.

**Reduce an over-sized reservation** first. A workload reserving far more than
it uses blocks that capacity from everyone else even while idle, and reducing
it interrupts nothing. Most "full" clusters are full of reservations rather
than of running work.

**Move a workload** only when reducing is not enough. Moving restarts the
workload: a service survives that, a computation loses everything it has done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# A node is "under pressure" once this much of it is reserved. Below this there
# is nothing to relieve; a cluster is meant to be used.
PRESSURE_THRESHOLD = 0.85

# Leave this much of a workload's observed peak as headroom when shrinking. A
# request set to exactly the peak leaves nothing for an ordinary fluctuation
# and gets the workload evicted.
SAFETY_MARGIN = 1.25

# A reservation must exceed usage by at least this much before it is worth
# reclaiming. Trimming a workload that is roughly right achieves little and
# risks a lot.
WASTE_THRESHOLD = 2.0


@dataclass
class NodeState:
    """One node's total allocatable capacity."""

    name: str
    cpu_cores: float
    memory_bytes: int
    schedulable: bool = True


@dataclass
class Placement:
    """One workload, where it currently runs, and what it reserves vs uses.

    ``used_*`` of ``None`` means not measured — we do not shrink on a guess.

    ``movable`` is decided upstream from the workload's kind: a Deployment with
    no attached storage can be replaced elsewhere, while a StatefulSet, a Job,
    or anything holding a volume cannot be moved without losing something.
    """

    name: str
    namespace: str
    node: str
    requested_cpu_cores: float
    requested_memory_bytes: int
    used_cpu_cores: Optional[float] = None
    peak_memory_bytes: Optional[int] = None
    movable: bool = True

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


def _reserved(placements: List[Placement], node_name: str) -> Dict[str, float]:
    """CPU and memory reserved on one node, by request not by usage.

    Requests are what the scheduler sets aside, so requests are what decide
    whether anything else fits.
    """
    return {
        "cpu": sum(p.requested_cpu_cores for p in placements if p.node == node_name),
        "memory": sum(p.requested_memory_bytes for p in placements if p.node == node_name),
    }


def _pressure(node: NodeState, placements: List[Placement]) -> float:
    """How full a node is, as a fraction, by whichever resource is tightest."""
    reserved = _reserved(placements, node.name)
    cpu = reserved["cpu"] / node.cpu_cores if node.cpu_cores else 0.0
    memory = reserved["memory"] / node.memory_bytes if node.memory_bytes else 0.0
    return max(cpu, memory)


def _shrink_target(placement: Placement) -> Optional[Dict[str, Any]]:
    """A smaller reservation for this workload, or None if it is already honest.

    Returns None when usage was never measured — shrinking on an unmeasured
    workload is a guess, and the cost of guessing low is eviction.
    """
    if placement.used_cpu_cores is None or placement.peak_memory_bytes is None:
        return None

    new_cpu = placement.used_cpu_cores * SAFETY_MARGIN
    new_memory = int(placement.peak_memory_bytes * SAFETY_MARGIN)

    cpu_waste = (
        placement.requested_cpu_cores / placement.used_cpu_cores
        if placement.used_cpu_cores else float("inf")
    )
    memory_waste = (
        placement.requested_memory_bytes / placement.peak_memory_bytes
        if placement.peak_memory_bytes else float("inf")
    )

    if max(cpu_waste, memory_waste) < WASTE_THRESHOLD:
        return None

    # Never propose an increase; this routine exists to return capacity.
    new_cpu = min(new_cpu, placement.requested_cpu_cores)
    new_memory = min(new_memory, placement.requested_memory_bytes)

    if new_cpu >= placement.requested_cpu_cores and new_memory >= placement.requested_memory_bytes:
        return None

    return {"cpu": new_cpu, "memory": new_memory}


def _free_capacity(node: NodeState, placements: List[Placement]) -> Dict[str, float]:
    reserved = _reserved(placements, node.name)
    return {
        "cpu": node.cpu_cores - reserved["cpu"],
        "memory": node.memory_bytes - reserved["memory"],
    }


def plan_rebalance(
    nodes: List[NodeState],
    placements: List[Placement],
) -> Dict[str, Any]:
    """Propose an ordered plan to relieve any node under pressure.

    Returns the pressured nodes, the ordered steps, and notes explaining
    anything that was considered and rejected. An empty step list with notes
    means nothing safe could be done — which is a more useful answer than a
    plan that will not work.
    """
    working = [
        Placement(**{**p.__dict__}) for p in placements
    ]  # copy, so planning does not mutate the caller's data
    by_name = {n.name: n for n in nodes}

    pressured = [n.name for n in nodes if _pressure(n, working) > PRESSURE_THRESHOLD]
    steps: List[Dict[str, Any]] = []
    notes: List[str] = []

    if not pressured:
        return {"pressured_nodes": [], "steps": [], "notes": []}

    # ── Remedy 1: reduce over-sized reservations ────────────────────────
    for placement in sorted(
        [p for p in working if p.node in pressured],
        key=lambda p: p.requested_cpu_cores,
        reverse=True,
    ):
        target = _shrink_target(placement)
        if target is None:
            continue

        freed_cpu = placement.requested_cpu_cores - target["cpu"]
        freed_memory = placement.requested_memory_bytes - target["memory"]

        steps.append({
            "action": "shrink",
            "workload": placement.ref,
            "namespace": placement.namespace,
            "name": placement.name,
            "node": placement.node,
            "new_cpu_cores": round(target["cpu"], 3),
            "new_memory_bytes": target["memory"],
            "frees_cpu_cores": round(freed_cpu, 3),
            "frees_memory_bytes": freed_memory,
            "reason": (
                f"{placement.ref} reserves {placement.requested_cpu_cores:.1f} CPU "
                f"and {placement.requested_memory_bytes / 2**30:.1f}Gi but uses "
                f"{placement.used_cpu_cores:.1f} CPU and "
                f"{(placement.peak_memory_bytes or 0) / 2**30:.1f}Gi at peak. "
                f"Reducing the reservation returns "
                f"{freed_cpu:.1f} CPU and {freed_memory / 2**30:.1f}Gi to the "
                f"node without restarting anything."
            ),
        })

        placement.requested_cpu_cores = target["cpu"]
        placement.requested_memory_bytes = target["memory"]

    still_pressured = [
        name for name in pressured
        if _pressure(by_name[name], working) > PRESSURE_THRESHOLD
    ]

    if not still_pressured:
        return {"pressured_nodes": pressured, "steps": steps, "notes": notes}

    # ── Remedy 2: move a workload, if anything can safely go ────────────
    for node_name in still_pressured:
        candidates = sorted(
            [p for p in working if p.node == node_name],
            key=lambda p: p.requested_cpu_cores,
            reverse=True,
        )

        for placement in candidates:
            if _pressure(by_name[node_name], working) <= PRESSURE_THRESHOLD:
                break

            if not placement.movable:
                notes.append(
                    f"{placement.ref} cannot be moved: it would restart and lose "
                    f"any work in progress. Left in place."
                )
                continue

            destination = None
            for node in nodes:
                if node.name == node_name or not node.schedulable:
                    continue
                free = _free_capacity(node, working)
                if (free["cpu"] >= placement.requested_cpu_cores
                        and free["memory"] >= placement.requested_memory_bytes):
                    destination = node
                    break

            if destination is None:
                notes.append(
                    f"No node has room for {placement.ref} "
                    f"({placement.requested_cpu_cores:.1f} CPU, "
                    f"{placement.requested_memory_bytes / 2**30:.1f}Gi). "
                    f"Moving it would leave it unschedulable."
                )
                continue

            steps.append({
                "action": "move",
                "workload": placement.ref,
                "namespace": placement.namespace,
                "name": placement.name,
                "from_node": placement.node,
                "target_node": destination.name,
                "reason": (
                    f"{node_name} is over {PRESSURE_THRESHOLD:.0%} reserved and "
                    f"no further reservation can safely be reduced. "
                    f"{destination.name} has room for {placement.ref}. Moving it "
                    f"restarts the workload; Kubernetes starts the replacement "
                    f"and waits for it to be ready before stopping the original."
                ),
            })
            placement.node = destination.name

    if not steps:
        notes.append(
            f"{', '.join(pressured)} is under pressure, but nothing can be "
            f"safely reduced or moved. Adding capacity, or reducing what is "
            f"asked of this cluster, is the remaining option."
        )

    return {"pressured_nodes": pressured, "steps": steps, "notes": notes}


def _movable(kind: str, has_volume: bool) -> bool:
    """Whether a workload can be replaced elsewhere without losing something.

    Only a Deployment is safe: any replica serves any request, so Kubernetes
    can start a new one and retire the old. A StatefulSet, a Job, or anything
    holding a volume keeps state that a restart destroys.
    """
    return kind == "Deployment" and not has_volume


def plan_cluster_rebalance() -> Dict[str, Any]:
    """Read the live cluster and propose a rebalance. Read-only.

    Proposes only. Nothing here changes the cluster — each step still goes
    through its own approval-gated tool.
    """
    from tools.kubernetes_actions import (
        _get_client,
        _get_core_client,
        _quantity_to_number,
    )

    try:
        core = _get_core_client()
        apps = _get_client()
    except Exception as exc:  # noqa: BLE001 - report, do not raise into the model
        return {"success": False, "error": f"Could not reach the cluster: {exc}"}

    try:
        nodes = [
            NodeState(
                name=node.metadata.name,
                cpu_cores=_quantity_to_number((node.status.allocatable or {}).get("cpu", 0)),
                memory_bytes=int(
                    _quantity_to_number((node.status.allocatable or {}).get("memory", 0))
                ),
                schedulable=not getattr(node.spec, "unschedulable", False),
            )
            for node in core.list_node().items
        ]

        owners = {}
        for deployment in apps.list_deployment_for_all_namespaces().items:
            key = (deployment.metadata.namespace, deployment.metadata.name)
            volumes = getattr(deployment.spec.template.spec, "volumes", None) or []
            owners[key] = any(
                getattr(v, "persistent_volume_claim", None) is not None for v in volumes
            )

        placements: List[Placement] = []
        for pod in core.list_pod_for_all_namespaces().items:
            node_name = getattr(pod.spec, "node_name", None)
            if not node_name:
                continue

            refs = getattr(pod.metadata, "owner_references", None) or []
            kind = "Deployment" if any(r.kind == "ReplicaSet" for r in refs) else (
                refs[0].kind if refs else "Pod"
            )

            cpu = mem = 0.0
            for container in pod.spec.containers or []:
                requests = getattr(container.resources, "requests", None) or {}
                cpu += _quantity_to_number(requests.get("cpu", 0))
                mem += _quantity_to_number(requests.get("memory", 0))

            name = pod.metadata.labels.get("app", pod.metadata.name) if pod.metadata.labels else pod.metadata.name
            has_volume = owners.get((pod.metadata.namespace, name), False)

            placements.append(Placement(
                name=name,
                namespace=pod.metadata.namespace,
                node=node_name,
                requested_cpu_cores=cpu,
                requested_memory_bytes=int(mem),
                movable=_movable(kind, has_volume),
            ))
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"Could not read cluster state: {exc}"}

    plan = plan_rebalance(nodes, placements)
    plan["success"] = True
    plan["note"] = (
        "Proposal only — nothing has changed. Each step requires its own "
        "approval before it runs. Usage figures were not available from the "
        "Kubernetes API alone, so reservations were not reduced; supply "
        "Prometheus metrics to enable shrink steps."
        if not any(s["action"] == "shrink" for s in plan["steps"])
        else "Proposal only — nothing has changed. Each step requires approval."
    )
    return plan
