import pytest

from evaluations.token_usage.conditions import CONDITIONS
from evaluations.token_usage.prompting import (
    SkillRequiredError,
    build_system_prompt,
)


BASE_PROMPT = "You manage a Kubernetes cluster."
SKILL_TEXT = "Inspect the cluster before taking action."


def test_no_skill_condition_omits_skill_text():
    result = build_system_prompt(
        base_prompt=BASE_PROMPT,
        condition=CONDITIONS["no_skill"],
        skill_text=SKILL_TEXT,
    )

    assert result == BASE_PROMPT
    assert SKILL_TEXT not in result


def test_supervised_condition_includes_skill_text():
    result = build_system_prompt(
        base_prompt=BASE_PROMPT,
        condition=CONDITIONS["skill_with_approval"],
        skill_text=SKILL_TEXT,
    )

    assert BASE_PROMPT in result
    assert SKILL_TEXT in result


def test_fully_agentic_condition_includes_skill_text():
    result = build_system_prompt(
        base_prompt=BASE_PROMPT,
        condition=CONDITIONS["fully_agentic"],
        skill_text=SKILL_TEXT,
    )

    assert BASE_PROMPT in result
    assert SKILL_TEXT in result


@pytest.mark.parametrize(
    "condition_name",
    ["skill_with_approval", "fully_agentic"],
)
def test_skill_condition_fails_if_skill_text_is_missing(condition_name):
    with pytest.raises(SkillRequiredError):
        build_system_prompt(
            base_prompt=BASE_PROMPT,
            condition=CONDITIONS[condition_name],
            skill_text="",
        )
