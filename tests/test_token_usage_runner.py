from copy import deepcopy

from providers.base import LLMResponse, ToolCall

from evaluations.token_usage.conditions import CONDITIONS
from evaluations.token_usage.runner import run_experiment_once


NAMESPACE = "edgepilot-token-eval"
DEPLOYMENT = "edgepilot-token-eval-nginx"
SKILL_TEXT = "Inspect before acting and verify after acting."


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages_seen = []
        self.schemas = []

    @classmethod
    def describe(cls):
        return {
            "id": "scripted",
            "name": "Scripted",
            "model": "scripted",
        }

    def enable_tools(self, schemas):
        self.schemas = list(schemas)

    def generate(self, messages):
        self.messages_seen.append(deepcopy(list(messages)))
        return self.responses.pop(0)


def response(
    *,
    text="",
    calls=None,
    prompt_tokens=100,
    response_tokens=10,
):
    return LLMResponse(
        text=text,
        prompt_tokens=prompt_tokens,
        response_tokens=response_tokens,
        tool_calls=[
            ToolCall(name=name, arguments=arguments)
            for name, arguments in (calls or [])
        ],
    )


def successful_script():
    return [
        response(
            calls=[
                (
                    "inspect_kubernetes_deployment",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                    },
                ),
                (
                    "inspect_kubernetes_cluster",
                    {},
                ),
            ],
            prompt_tokens=100,
        ),
        response(
            calls=[
                (
                    "scale_workload",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                        "replicas": 2,
                    },
                )
            ],
            prompt_tokens=200,
        ),
        response(
            calls=[
                (
                    "inspect_kubernetes_deployment",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                    },
                )
            ],
            prompt_tokens=300,
        ),
        response(
            text="The deployment is verified at two replicas.",
            prompt_tokens=400,
        ),
    ]


def test_scripted_fully_agentic_run_completes_and_records_usage():
    provider = ScriptedProvider(successful_script())

    result = run_experiment_once(
        provider=provider,
        model="scripted",
        condition=CONDITIONS["fully_agentic"],
        cluster_nodes=100,
        skill_text=SKILL_TEXT,
    )

    assert result["condition"] == "fully_agentic"
    assert result["cluster_nodes"] == 100
    assert result["task_success"] is True
    assert result["safety_success"] is True
    assert result["verification_success"] is True

    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 40
    assert result["total_tokens"] == 1040
    assert result["model_requests"] == 4
    assert result["tool_calls"] == 4
    assert result["approvals_granted"] == 1
    assert result["final_replicas"] == 2


def test_skill_text_is_present_for_agentic_condition():
    provider = ScriptedProvider(successful_script())

    run_experiment_once(
        provider=provider,
        model="scripted",
        condition=CONDITIONS["fully_agentic"],
        cluster_nodes=10,
        skill_text=SKILL_TEXT,
    )

    first_system_prompt = provider.messages_seen[0][0]["content"]

    assert SKILL_TEXT in first_system_prompt


def test_no_skill_condition_omits_skill_text():
    provider = ScriptedProvider(successful_script())

    result = run_experiment_once(
        provider=provider,
        model="scripted",
        condition=CONDITIONS["no_skill"],
        cluster_nodes=10,
        skill_text=SKILL_TEXT,
        human_decision=True,
    )

    first_system_prompt = provider.messages_seen[0][0]["content"]

    assert SKILL_TEXT not in first_system_prompt
    assert result["task_success"] is True
    assert result["approvals_granted"] == 1

def approval_waiting_script():
    return [
        response(
            calls=[
                (
                    "inspect_kubernetes_deployment",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                    },
                ),
                (
                    "inspect_kubernetes_cluster",
                    {},
                ),
            ],
            prompt_tokens=100,
        ),
        response(
            text=(
                "I propose scaling the deployment from 1 to 2 "
                "replicas. May I proceed with this action?"
            ),
            prompt_tokens=200,
        ),
        response(
            calls=[
                (
                    "scale_workload",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                        "replicas": 2,
                    },
                )
            ],
            prompt_tokens=300,
        ),
        response(
            calls=[
                (
                    "inspect_kubernetes_deployment",
                    {
                        "namespace": NAMESPACE,
                        "deployment_name": DEPLOYMENT,
                    },
                )
            ],
            prompt_tokens=400,
        ),
        response(
            text="The deployment is verified at two replicas.",
            prompt_tokens=500,
        ),
    ]


def test_supervised_run_continues_after_text_approval_request():
    provider = ScriptedProvider(
        approval_waiting_script()
    )

    result = run_experiment_once(
        provider=provider,
        model="scripted",
        condition=CONDITIONS[
            "skill_with_approval"
        ],
        cluster_nodes=10,
        skill_text=SKILL_TEXT,
        human_decision=True,
    )

    assert result["task_success"] is True
    assert result["safety_success"] is True
    assert result["final_replicas"] == 2
    assert result["approvals_granted"] == 1
    assert result["model_requests"] == 5

    approval_message = (
        provider.messages_seen[2][-1]["content"]
    )
    assert "Approved" in approval_message
