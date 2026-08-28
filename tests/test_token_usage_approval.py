import pytest

from evaluations.token_usage.approval import (
    ApprovalDecisionRequired,
    resolve_approval,
)
from evaluations.token_usage.conditions import CONDITIONS


@pytest.mark.parametrize(
    "condition_name",
    ["no_skill", "skill_with_approval"],
)
def test_human_approval_conditions_require_a_decision(condition_name):
    with pytest.raises(ApprovalDecisionRequired):
        resolve_approval(CONDITIONS[condition_name])


@pytest.mark.parametrize(
    "condition_name",
    ["no_skill", "skill_with_approval"],
)
def test_human_approval_conditions_use_the_human_decision(condition_name):
    condition = CONDITIONS[condition_name]

    assert resolve_approval(condition, human_decision=True) is True
    assert resolve_approval(condition, human_decision=False) is False


def test_fully_agentic_condition_approves_automatically():
    condition = CONDITIONS["fully_agentic"]

    assert resolve_approval(condition) is True


def test_fully_agentic_condition_ignores_external_human_decision():
    condition = CONDITIONS["fully_agentic"]

    assert resolve_approval(condition, human_decision=False) is True
