import pytest
from core.workflows import list_workflows, load_workflow, run_workflow

def test_list_workflows():
    workflows = list_workflows()
    assert len(workflows) >= 3
    
    names = [w["name"] for w in workflows]
    assert "memory_anomaly" in names
    assert "security_audit" in names
    assert "health_check" in names

def test_load_workflow():
    workflow = load_workflow("memory_anomaly")
    assert workflow is not None
    assert "steps" in workflow
    assert len(workflow["steps"]) == 5
    
    first_step = workflow["steps"][0]
    assert first_step["name"] == "inspect_cluster"
    assert "inspect_kubernetes_cluster" in first_step["tools"]

def test_run_workflow_generator():
    import asyncio
    
    async def _test():
        class MockProvider:
            def enable_tools(self, schemas):
                pass
                
            def generate(self, messages):
                class MockResponse:
                    text = "Mock LLM text"
                    has_tool_calls = False
                return MockResponse()

        async def mock_wait(approval_id):
            return True
            
        async def mock_execute(calls):
            return []

        events = []
        async for event in run_workflow("health_check", MockProvider(), [], mock_execute, mock_wait, "test-chat-id"):
            events.append(event)
            
        types = [e["type"] for e in events]
        assert "status" in types
        assert "workflow_step_start" in types
        assert "workflow_step_complete" in types
        assert "done" in types

    asyncio.run(_test())


# ====================================================================== #
# Regression tests for defects found in AI Workflow V1                    #
# ====================================================================== #

import textwrap
from typing import Any, Dict, List

from providers.base import BaseLLM, LLMResponse, ToolCall


def _write_workflow(tmp_path, monkeypatch, body: str, name: str = "probe"):
    """Point the loader at a throwaway workflow written for one test."""
    import core.workflows as wf

    (tmp_path / f"{name}.yaml").write_text(textwrap.dedent(body))
    monkeypatch.setattr(wf, "WORKFLOWS_DIR", tmp_path)
    return name


class _ToolCallingProvider:
    """Non-streaming provider that emits a tool call on its first turn."""

    def __init__(self, tool_name: str, arguments: Dict[str, Any] | None = None):
        self._tool_name = tool_name
        self._arguments = arguments or {}
        self._turns = 0

    def enable_tools(self, schemas):
        pass

    def generate(self, messages):
        self._turns += 1
        if self._turns == 1:
            return LLMResponse(
                text="calling a tool",
                prompt_tokens=100,
                response_tokens=20,
                tool_calls=[ToolCall(name=self._tool_name, arguments=self._arguments)],
            )
        return LLMResponse(text="summary", prompt_tokens=50, response_tokens=10)


class _SyncOnlyProvider(BaseLLM):
    """Mirrors ClaudeProvider: subclasses the Protocol, no generate_stream.

    The inherited `generate_stream` keeps the Protocol's `...` body, so it
    returns None and iterating it raises TypeError.
    """

    def __init__(self):  # noqa: D107 - BaseLLM wants a config we don't need
        pass

    def enable_tools(self, schemas):
        pass

    def generate(self, messages):
        return LLMResponse(text="sync text", prompt_tokens=7, response_tokens=3)


def test_sync_only_provider_reproduces_the_protocol_stub_hazard():
    """Guards the assumption the streaming fix rests on."""
    provider = _SyncOnlyProvider()

    assert hasattr(provider, "generate_stream")      # looks streamable
    assert provider.generate_stream([]) is None      # but returns None


async def _drain(gen):
    return [event async for event in gen]


def test_dangerous_tool_is_gated_even_without_step_flag(tmp_path, monkeypatch):
    """A step that omits requires_approval must still gate a dangerous call.

    security_audit.yaml's scan_actual_ports does exactly this with
    run_shell_commands, so before the fix it executed unapproved.
    """
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "shell_step"
            instruction: "run a shell command"
            tools: ["run_shell_commands"]
    """)

    executed: List[Any] = []
    approvals: List[str] = []

    async def execute(calls):
        executed.append(calls)
        return [{"success": True, "tool": c["name"], "result": {}} for c in calls]

    async def approve(approval_id):
        approvals.append(approval_id)
        return True

    events = asyncio.run(_drain(run_workflow(
        name, _ToolCallingProvider("run_shell_commands", {"commands": ["ss -tuln"]}),
        [], execute, approve, "chat",
    )))

    assert approvals, "dangerous tool executed without ever requesting approval"

    prompts = [e for e in events if e["type"] == "approval_required"]
    assert prompts, "no approval_required event emitted"

    # The prompt must name the real call and its real arguments.
    tools = prompts[0]["data"]["tools"]
    assert tools[0]["name"] == "run_shell_commands"
    assert tools[0]["arguments"] == {"commands": ["ss -tuln"]}
    assert executed, "approved call should still run"


def test_denied_dangerous_tool_is_never_executed(tmp_path, monkeypatch):
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "shell_step"
            instruction: "run a shell command"
            tools: ["run_shell_commands"]
    """)

    executed: List[Any] = []

    async def execute(calls):
        executed.append(calls)
        return [{"success": True, "tool": c["name"], "result": {}} for c in calls]

    async def deny(approval_id):
        return False

    events = asyncio.run(_drain(run_workflow(
        name, _ToolCallingProvider("run_shell_commands"), [], execute, deny, "chat",
    )))

    assert executed == [], "denied tool was executed anyway"
    assert any(
        e["type"] == "workflow_step_complete" and e["data"]["status"] == "denied"
        for e in events
    )


def test_safe_tool_runs_without_an_approval_prompt(tmp_path, monkeypatch):
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "read_step"
            instruction: "inspect"
            tools: ["inspect_kubernetes_cluster"]
    """)

    async def execute(calls):
        return [{"success": True, "tool": c["name"], "result": {}} for c in calls]

    async def approve(approval_id):
        raise AssertionError("read-only tool must not require approval")

    events = asyncio.run(_drain(run_workflow(
        name, _ToolCallingProvider("inspect_kubernetes_cluster"), [], execute, approve, "chat",
    )))

    assert not [e for e in events if e["type"] == "approval_required"]


def test_provider_without_streaming_completes(tmp_path, monkeypatch):
    """ClaudeProvider inherits generate_stream's `...` body from the Protocol.

    hasattr() reports True, the call returns None, and iterating it raises
    TypeError. The engine must fall back to the synchronous path.
    """
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "think"
            instruction: "no tools"
            tools: []
    """)

    async def execute(calls):
        return []

    async def approve(approval_id):
        return True

    events = asyncio.run(_drain(run_workflow(
        name, _SyncOnlyProvider(), [], execute, approve, "chat",
    )))

    errors = [e for e in events if e["type"] == "error"]
    assert not errors, f"sync-only provider errored: {errors}"
    assert any(e["type"] == "done" for e in events)


def test_failing_tool_fails_the_step_and_halts(tmp_path, monkeypatch):
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "verify"
            instruction: "verify the fix"
            tools: ["inspect_kubernetes_cluster"]
          - name: "never_reached"
            instruction: "should not run"
            tools: []
    """)

    async def execute(calls):
        return [{"success": False, "tool": c["name"], "error": "boom"} for c in calls]

    async def approve(approval_id):
        return True

    events = asyncio.run(_drain(run_workflow(
        name, _ToolCallingProvider("inspect_kubernetes_cluster"), [], execute, approve, "chat",
    )))

    completes = [e for e in events if e["type"] == "workflow_step_complete"]
    assert completes[0]["data"]["status"] == "failed"
    assert "inspect_kubernetes_cluster" in completes[0]["data"].get("failed_tools", [])

    started = [e["data"]["step"] for e in events if e["type"] == "workflow_step_start"]
    assert "never_reached" not in started, "workflow continued past a failed step"


def test_usage_is_reported(tmp_path, monkeypatch):
    """Goal 3 cannot be measured unless the run reports its token usage."""
    import asyncio
    from core.workflows import run_workflow

    name = _write_workflow(tmp_path, monkeypatch, """
        description: "probe"
        steps:
          - name: "read_step"
            instruction: "inspect"
            tools: ["inspect_kubernetes_cluster"]
    """)

    async def execute(calls):
        return [{"success": True, "tool": c["name"], "result": {}} for c in calls]

    async def approve(approval_id):
        return True

    events = asyncio.run(_drain(run_workflow(
        name, _ToolCallingProvider("inspect_kubernetes_cluster"), [], execute, approve, "chat",
    )))

    usage = [e for e in events if e["type"] == "usage"]
    assert usage, "no usage event emitted"

    data = usage[0]["data"]
    assert data["prompt_tokens"] > 0
    assert data["response_tokens"] > 0
    assert data["tool_calls"] == 1
    assert data["steps"] == 1
    assert data["llm_turns"] >= 2  # initial turn plus the summarise turn


def test_every_workflow_references_a_real_tool():
    """A typo in a YAML tool name would silently give the model no tools."""
    import pathlib

    import yaml as _yaml

    from core.tool_schemas import get_all_tool_schemas
    from core.workflows import WORKFLOWS_DIR

    known = {schema["name"] for schema in get_all_tool_schemas()}
    unknown = []

    for path in sorted(pathlib.Path(WORKFLOWS_DIR).glob("*.yaml")):
        workflow = _yaml.safe_load(path.read_text())
        for step in workflow.get("steps", []):
            for tool in step.get("tools", []):
                if tool not in known:
                    unknown.append(f"{path.name}:{step['name']} -> {tool}")

    assert not unknown, f"workflows reference undefined tools: {unknown}"


def test_shipped_workflows_gate_their_mutating_steps():
    """Any step allowed a dangerous tool must declare requires_approval.

    The engine gates per call regardless, but a workflow that reads as
    unguarded invites someone to remove the engine-level check later.
    """
    import pathlib

    import yaml as _yaml

    from core.settings import DANGEROUS_TOOLS
    from core.workflows import WORKFLOWS_DIR

    ungated = []

    for path in sorted(pathlib.Path(WORKFLOWS_DIR).glob("*.yaml")):
        workflow = _yaml.safe_load(path.read_text())
        for step in workflow.get("steps", []):
            risky = set(step.get("tools", [])) & DANGEROUS_TOOLS
            if risky and not step.get("requires_approval"):
                ungated.append(f"{path.name}:{step['name']} -> {sorted(risky)}")

    assert not ungated, f"mutating steps without requires_approval: {ungated}"
