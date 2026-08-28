import pytest

from evaluations.reliability.tasks import PROMPT_LEVELS, TASKS, build_cluster


def test_the_five_prompt_levels_from_the_notes_are_present():
    assert PROMPT_LEVELS == [
        "detailed", "simple", "vague", "multi_action", "goal_oriented"
    ]


def test_every_task_has_a_unique_id():
    ids = [task.task_id for task in TASKS]

    assert len(ids) == len(set(ids)), "duplicate task ids"


def test_every_task_defines_at_least_one_valid_prompt_level():
    for task in TASKS:
        assert task.prompts, f"{task.task_id} has no prompts"

        for level in task.prompts:
            assert level in PROMPT_LEVELS, f"{task.task_id}: bad level {level}"


def test_safety_tasks_expect_no_action():
    """The notes' failure cases: refusing or asking is the correct outcome."""
    safety = [task for task in TASKS if task.category == "safety"]

    assert safety, "no safety cases defined"

    for task in safety:
        assert task.expected_tool is None, (
            f"{task.task_id} is a safety case but expects a tool call"
        )
        assert task.allow_clarification is True


def test_action_tasks_name_a_real_tool():
    from MCP.tool_schemas import get_all_tool_schemas

    known = {schema["name"] for schema in get_all_tool_schemas()}

    for task in TASKS:
        if task.expected_tool is not None:
            assert task.expected_tool in known, (
                f"{task.task_id} expects {task.expected_tool!r}, "
                f"which no schema defines"
            )


def test_expected_state_predicate_matches_the_expected_action():
    """A task whose predicate disagrees with its own expected call is broken."""
    for task in TASKS:
        if task.expected_tool is None:
            continue

        cluster = build_cluster()
        getattr(cluster, task.expected_tool)(**task.expected_arguments)

        assert task.expected_state(cluster), (
            f"{task.task_id}: performing the expected action does not satisfy "
            f"its own expected_state predicate"
        )


def test_unchanged_cluster_fails_the_predicate():
    """Guards against a predicate that trivially returns True."""
    for task in TASKS:
        if task.expected_tool is None:
            continue

        assert not task.expected_state(build_cluster()), (
            f"{task.task_id}: predicate passes on an untouched cluster"
        )


def test_build_cluster_is_deterministic():
    assert build_cluster().snapshot() == build_cluster().snapshot()


def test_api_is_ambiguous_across_namespaces_on_purpose():
    """The ambiguity the safety case depends on must actually exist."""
    cluster = build_cluster()

    assert cluster.replicas("default", "api") == 3
    assert cluster.replicas("payments", "api") == 1
