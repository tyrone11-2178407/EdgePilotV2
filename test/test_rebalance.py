"""Planning a rebalance is arithmetic, so it belongs in code, not in the model.

The model's job is to explain the plan and ask for approval. If it also had to
work out which node is short of room and by how much, every number in the
proposal would be a guess.
"""

import pytest

from tools.rebalance import NodeState, Placement, plan_rebalance

GB = 2**30


def nodes(*specs):
    return [NodeState(name=n, cpu_cores=c, memory_bytes=m * GB, schedulable=s)
            for n, c, m, s in specs]


def placement(name, node, req_cpu, req_mem_gb, used_cpu=None, used_mem_gb=None,
              movable=True, namespace="default"):
    return Placement(
        name=name, namespace=namespace, node=node,
        requested_cpu_cores=req_cpu, requested_memory_bytes=req_mem_gb * GB,
        used_cpu_cores=used_cpu,
        peak_memory_bytes=None if used_mem_gb is None else used_mem_gb * GB,
        movable=movable,
    )


def test_balanced_cluster_needs_no_plan():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        [placement("api", "node-a", 1, 4, used_cpu=0.9, used_mem_gb=3)],
    )

    assert plan["steps"] == []
    assert plan["pressured_nodes"] == []


def test_shrinking_is_preferred_over_moving():
    """Reducing a reservation interrupts nothing; moving restarts the workload."""
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        # Reserves nearly the whole node, uses almost none of it.
        [placement("hog", "node-a", 7.5, 30, used_cpu=0.5, used_mem_gb=2)],
    )

    assert plan["steps"], "an over-reservation should produce a plan"
    assert plan["steps"][0]["action"] == "shrink"
    assert all(step["action"] != "move" for step in plan["steps"]), (
        "shrinking freed enough room, so nothing should need moving"
    )


def test_a_shrink_never_goes_below_observed_peak():
    """Requesting less than the workload actually uses gets it evicted."""
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        [placement("hog", "node-a", 7.5, 30, used_cpu=2.0, used_mem_gb=8)],
    )

    shrink = plan["steps"][0]
    assert shrink["new_cpu_cores"] >= 2.0
    assert shrink["new_memory_bytes"] >= 8 * GB


def test_moves_only_when_shrinking_is_not_enough():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        # Genuinely busy: reservation is honest, so it cannot be shrunk.
        [placement("busy-1", "node-a", 4, 16, used_cpu=3.9, used_mem_gb=15),
         placement("busy-2", "node-a", 4, 16, used_cpu=3.8, used_mem_gb=15)],
    )

    moves = [s for s in plan["steps"] if s["action"] == "move"]
    assert moves, "nothing can be shrunk, so something must move"
    assert moves[0]["target_node"] == "node-b"


def test_never_moves_a_workload_that_would_lose_work():
    """A computation or a database restarts from nothing. Refuse it."""
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        [placement("training-job", "node-a", 7.9, 31, used_cpu=7.8,
                   used_mem_gb=30, movable=False)],
    )

    assert all(s["action"] != "move" for s in plan["steps"])
    assert any("cannot be moved" in note for note in plan["notes"])


def test_never_moves_onto_a_node_without_room():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 2, 4, True)),
        [placement("busy-1", "node-a", 4, 16, used_cpu=3.9, used_mem_gb=15),
         placement("busy-2", "node-a", 4, 16, used_cpu=3.8, used_mem_gb=15)],
    )

    assert all(s["action"] != "move" for s in plan["steps"]), (
        "node-b is too small; moving there would strand the workload"
    )


def test_never_moves_onto_a_cordoned_node():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, False)),
        [placement("busy-1", "node-a", 4, 16, used_cpu=3.9, used_mem_gb=15),
         placement("busy-2", "node-a", 4, 16, used_cpu=3.8, used_mem_gb=15)],
    )

    assert all(s["action"] != "move" for s in plan["steps"])


def test_says_so_plainly_when_nothing_can_be_done():
    """Better an honest 'I cannot fix this' than a plan that will not work."""
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True)),
        [placement("busy", "node-a", 7.9, 31, used_cpu=7.8, used_mem_gb=30)],
    )

    assert plan["steps"] == []
    assert plan["notes"], "must explain why nothing was proposed"


def test_every_step_carries_a_reason():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        [placement("hog", "node-a", 7.5, 30, used_cpu=0.5, used_mem_gb=2)],
    )

    for step in plan["steps"]:
        assert step.get("reason"), f"step without a reason: {step}"


def test_reports_which_nodes_are_under_pressure():
    plan = plan_rebalance(
        nodes(("node-a", 8, 32, True), ("node-b", 8, 32, True)),
        [placement("hog", "node-a", 7.5, 30, used_cpu=0.5, used_mem_gb=2)],
    )

    assert plan["pressured_nodes"] == ["node-a"]
