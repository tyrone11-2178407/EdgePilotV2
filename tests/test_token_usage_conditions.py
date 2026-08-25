from evaluations.token_usage.conditions import CONDITIONS


def test_experiment_defines_exactly_three_conditions():
    assert set(CONDITIONS) == {
        "no_skill",
        "skill_with_approval",
        "fully_agentic",
    }


def test_no_skill_uses_human_approval_without_skill():
    condition = CONDITIONS["no_skill"]

    assert condition.include_skill is False
    assert condition.approval_mode == "human"


def test_supervised_condition_uses_skill_and_human_approval():
    condition = CONDITIONS["skill_with_approval"]

    assert condition.include_skill is True
    assert condition.approval_mode == "human"


def test_fully_agentic_condition_uses_automatic_approval():
    condition = CONDITIONS["fully_agentic"]

    assert condition.include_skill is True
    assert condition.approval_mode == "automatic"