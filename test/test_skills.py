from unittest.mock import patch

import pytest

from core.skills import list_project_skills, load_project_skill
from core.tool_executor import ToolExecutor
from core.tool_schemas import get_tool_schema


def test_kubernetes_skill_is_discoverable():
    skills = list_project_skills()

    matching = [
        skill
        for skill in skills
        if skill["name"] == "kubernetes-control"
    ]

    assert len(matching) == 1
    assert matching[0]["description"]


def test_kubernetes_skill_can_be_loaded():
    skill = load_project_skill("kubernetes-control")

    assert skill["name"] == "kubernetes-control"
    assert skill["description"]
    assert "human approval" in skill["instructions"].lower()
    assert "inspect_kubernetes_cluster" in skill["instructions"]
    assert "evaluate_kubernetes_workload" in skill["instructions"]
    assert "inspect_kubernetes_deployment" in skill["instructions"]


@pytest.mark.parametrize(
    "name",
    [
        "../secrets",
        "../../etc/passwd",
        "Kubernetes Control",
        "",
    ],
)
def test_invalid_skill_names_are_rejected(name):
    with pytest.raises(ValueError):
        load_project_skill(name)


def test_unknown_skill_is_rejected():
    with pytest.raises(
        ValueError,
        match="Unknown skill",
    ):
        load_project_skill("nonexistent-skill")


def test_skill_tool_schemas_are_registered():
    assert get_tool_schema("list_skills") is not None
    assert get_tool_schema("load_skill") is not None


@patch("core.tool_executor.list_skills")
def test_executor_lists_skills(mock_list):
    mock_list.return_value = {
        "skills": [
            {
                "name": "kubernetes-control",
                "description": "Kubernetes workflow",
            }
        ],
        "count": 1,
    }

    result = ToolExecutor().execute("list_skills", {})

    assert result["success"] is True
    assert result["result"]["count"] == 1
    mock_list.assert_called_once_with()


@patch("core.tool_executor.load_skill")
def test_executor_loads_skill(mock_load):
    mock_load.return_value = {
        "name": "kubernetes-control",
        "description": "Kubernetes workflow",
        "instructions": "Inspect before mutation.",
    }

    result = ToolExecutor().execute(
        "load_skill",
        {"name": "kubernetes-control"},
    )

    assert result["success"] is True
    assert result["result"]["name"] == "kubernetes-control"
    mock_load.assert_called_once_with("kubernetes-control")


def test_executor_requires_skill_name():
    result = ToolExecutor().execute("load_skill", {})

    assert result["success"] is False
    assert "name is required" in result["error"]