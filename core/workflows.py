import os
import yaml
import json
import logging
import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path

from core.settings import DANGEROUS_TOOLS
from providers.base import BaseLLM

logger = logging.getLogger(__name__)

WORKFLOWS_DIR = Path(__file__).parent.parent / "skills" / "workflows"


def _supports_streaming(provider: Any) -> bool:
    """True only when *provider* genuinely implements ``generate_stream``.

    ``BaseLLM`` is a ``Protocol``, and ClaudeProvider/GPTProvider subclass
    it explicitly without implementing this method — so they inherit its
    ``...`` body, which returns ``None``. ``hasattr`` reports ``True`` and
    iterating the result then raises ``TypeError``. Compare the bound
    implementation against the Protocol's to tell them apart.
    """

    own = getattr(type(provider), "generate_stream", None)

    if own is None:
        return False

    return own is not getattr(BaseLLM, "generate_stream", None)

def list_workflows() -> List[Dict[str, str]]:
    """Return a list of available workflows."""
    if not WORKFLOWS_DIR.exists():
        return []
        
    workflows = []
    for file in WORKFLOWS_DIR.glob("*.yaml"):
        try:
            with open(file, "r") as f:
                data = yaml.safe_load(f)
                workflows.append({
                    "name": file.stem,
                    "description": data.get("description", "No description provided.")
                })
        except Exception as e:
            logger.warning(f"Could not load workflow {file}: {e}")
            
    return workflows

def load_workflow(name: str) -> Dict[str, Any]:
    """Load a workflow definition by name."""
    file_path = WORKFLOWS_DIR / f"{name}.yaml"
    if not file_path.exists():
        raise ValueError(f"Workflow '{name}' not found.")
        
    with open(file_path, "r") as f:
        return yaml.safe_load(f)

async def run_workflow(
    workflow_name: str,
    provider: Any,
    tool_schemas: List[Dict[str, Any]],
    execute_tools_batch_fn: Any,
    wait_for_approval_fn: Any,
    chat_id: str
):
    """
    Async generator that executes a multi-step workflow.
    Yields dictionary events that main.py can format as SSE.
    """
    try:
        workflow = load_workflow(workflow_name)
    except Exception as e:
        yield {"type": "error", "data": {"detail": str(e)}}
        return

    steps = workflow.get("steps", [])
    if not steps:
        yield {"type": "error", "data": {"detail": "Workflow has no steps."}}
        return

    yield {"type": "status", "data": {"text": f"Starting workflow: {workflow_name}"}}

    # Goal 3 of the 7/29 meeting measures token usage and time-to-resolution
    # per workflow, so the run has to account for what it spends.
    usage = {
        "prompt_tokens": 0,
        "response_tokens": 0,
        "llm_turns": 0,
        "tool_calls": 0,
        "steps": 0,
    }

    def _record(response: Any) -> None:
        """Accumulate token usage from one LLM turn."""
        usage["llm_turns"] += 1
        usage["prompt_tokens"] += getattr(response, "prompt_tokens", 0) or 0
        usage["response_tokens"] += getattr(response, "response_tokens", 0) or 0

    streaming = _supports_streaming(provider)

    # Keep track of context across steps
    context_messages = [
        {"role": "system", "content": f"You are executing the '{workflow_name}' workflow. Follow the step instructions carefully."}
    ]

    for step_idx, step in enumerate(steps):
        step_name = step.get("name", f"step_{step_idx}")
        instruction = step.get("instruction", "")
        allowed_tools = step.get("tools", [])
        requires_approval = step.get("requires_approval", False)

        yield {"type": "workflow_step_start", "data": {"step": step_name, "instruction": instruction}}
        yield {"type": "status", "data": {"text": f"Executing step {step_idx+1}/{len(steps)}: {step_name}"}}

        # Coarse pre-gate declared in the workflow YAML. The per-call gate
        # below is the control that actually holds, since a step can call a
        # dangerous tool without setting this flag.
        if requires_approval:
            approval_id = str(uuid.uuid4())
            yield {"type": "approval_required", "data": {
                "approval_id": approval_id,
                "tools": [{"name": step_name, "arguments": {"tools": allowed_tools}}]
            }}
            yield {"type": "status", "data": {"text": f"Step '{step_name}' requires approval."}}
            approved = await wait_for_approval_fn(approval_id)
            if not approved:
                yield {"type": "status", "data": {"text": "Workflow execution denied by user."}}
                yield {"type": "workflow_step_complete", "data": {"step": step_name, "status": "denied"}}
                return

        # Prepare tools for this step
        step_schemas = [schema for schema in tool_schemas if schema["name"] in allowed_tools]
        if hasattr(provider, "enable_tools"):
            provider.enable_tools(step_schemas)

        # Add step instruction to context
        step_prompt = f"Workflow Step: {step_name}\nInstruction: {instruction}"
        if step_idx > 0:
            step_prompt = f"Previous step output is above. Now execute the next step.\n{step_prompt}"
            
        context_messages.append({"role": "user", "content": step_prompt})

        yield {"type": "status", "data": {"text": f"Thinking..."}}
        
        # Execute LLM turn
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            
            llm_response = None
            final_text = ""
            
            if streaming:
                q = asyncio.Queue()
                def _worker():
                    try:
                        for item in provider.generate_stream(context_messages):
                            loop.call_soon_threadsafe(q.put_nowait, item)
                        loop.call_soon_threadsafe(q.put_nowait, None)
                    except Exception as e:
                        loop.call_soon_threadsafe(q.put_nowait, e)
                        
                loop.run_in_executor(None, _worker)
                
                yield {"type": "chunk", "data": {"text": f"\n\n**Step {step_name} result:**\n"}}
                
                while True:
                    item = await q.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    if hasattr(item, 'text') and hasattr(item, 'tool_calls'):
                        llm_response = item
                        break
                    elif isinstance(item, str):
                        final_text += item
                        yield {"type": "chunk", "data": {"text": item}}
                        
                if llm_response:
                    final_text = llm_response.text or final_text
            else:
                llm_response = await loop.run_in_executor(None, provider.generate, context_messages)
                final_text = llm_response.text or ""
                yield {"type": "chunk", "data": {"text": f"\n\n**Step {step_name} result:**\n{final_text}"}}

            if llm_response is None:
                # A stream that ended without a final LLMResponse. Treat it
                # as text-only rather than crashing on .has_tool_calls.
                yield {"type": "status", "data": {
                    "text": f"Step '{step_name}' returned no structured response."
                }}
            else:
                _record(llm_response)

        except Exception as exc:
            yield {"type": "error", "data": {"detail": f"Provider error: {exc}"}}
            return

        # If tools were called, execute them
        if llm_response is not None and llm_response.has_tool_calls:
            tool_names = [tc.name for tc in llm_response.tool_calls]
            yield {"type": "status", "data": {"text": f"Executing tools: {', '.join(tool_names)}"}}
            
            calls = [{"name": tc.name, "arguments": dict(tc.arguments or {})} for tc in llm_response.tool_calls]

            # Gate on the call the model actually made. The step-level
            # requires_approval flag fires before the model has decided
            # anything, and a step can reach a dangerous tool without it —
            # security_audit.yaml's scan_actual_ports does exactly that with
            # run_shell_commands.
            dangerous = [c for c in calls if c["name"] in DANGEROUS_TOOLS]

            if dangerous:
                approval_id = str(uuid.uuid4())
                yield {"type": "approval_required", "data": {
                    "approval_id": approval_id,
                    # Real names and real arguments, so the user can see what
                    # they are approving rather than just a step name.
                    "tools": [
                        {"name": c["name"], "arguments": c["arguments"]}
                        for c in dangerous
                    ],
                }}
                yield {"type": "status", "data": {"text": "Waiting for your approval..."}}

                if not await wait_for_approval_fn(approval_id):
                    yield {"type": "status", "data": {"text": "Execution denied by user."}}
                    yield {"type": "workflow_step_complete", "data": {
                        "step": step_name,
                        "status": "denied",
                    }}
                    yield {"type": "usage", "data": usage}
                    return

            tool_results = await execute_tools_batch_fn(calls)
            usage["tool_calls"] += len(calls)

            for tc, result in zip(llm_response.tool_calls, tool_results):
                yield {"type": "tool", "data": {"name": tc.name, "success": result.get("success", False)}}

            failed = [r for r in tool_results if not r.get("success", False)]

            if failed and step.get("halt_on_failure", True):
                # A verify step that fails must stop the workflow, not be
                # decorated with a success marker. Set halt_on_failure:false
                # in the YAML for steps that may legitimately fail.
                yield {"type": "workflow_step_complete", "data": {
                    "step": step_name,
                    "status": "failed",
                    "failed_tools": [r.get("tool") for r in failed],
                }}
                yield {"type": "error", "data": {"detail": (
                    f"Step '{step_name}' failed: "
                    + ", ".join(str(r.get("tool", "?")) for r in failed)
                )}}
                yield {"type": "usage", "data": usage}
                return

            tool_results_text = "Tool Results:\n"
            for result in tool_results:
                if result["success"]:
                    tool_results_text += f"- {result['tool']}: {json.dumps(result['result'])}\n"
                else:
                    tool_results_text += f"- {result['tool']} ERROR: {result['error']}\n"
                    
            context_messages.append({"role": "assistant", "content": f"[Called tools: {', '.join(tool_names)}]\n{final_text}"})
            context_messages.append({"role": "user", "content": tool_results_text + "\nPlease summarize the tool results."})
            
            # Follow up turn to summarize tool results
            try:
                if hasattr(provider, "enable_tools"):
                    provider.enable_tools([])

                llm_response_2 = None

                if streaming:
                    q2 = asyncio.Queue()
                    def _worker2():
                        try:
                            for item in provider.generate_stream(context_messages):
                                loop.call_soon_threadsafe(q2.put_nowait, item)
                            loop.call_soon_threadsafe(q2.put_nowait, None)
                        except Exception as e:
                            loop.call_soon_threadsafe(q2.put_nowait, e)
                            
                    loop.run_in_executor(None, _worker2)
                    
                    final_text = ""
                    while True:
                        item = await q2.get()
                        if item is None:
                            break
                        if isinstance(item, Exception):
                            raise item
                        if hasattr(item, 'text') and hasattr(item, 'tool_calls'):
                            llm_response_2 = item
                            break
                        elif isinstance(item, str):
                            final_text += item
                            yield {"type": "chunk", "data": {"text": item}}
                            
                    if llm_response_2:
                        final_text = llm_response_2.text or final_text
                else:
                    llm_response_2 = await loop.run_in_executor(None, provider.generate, context_messages)
                    final_text = llm_response_2.text or "Completed tool execution."
                    yield {"type": "chunk", "data": {"text": final_text}}
                    
                if llm_response_2 is not None:
                    _record(llm_response_2)

                context_messages.append({"role": "assistant", "content": final_text})
            except Exception as exc:
                yield {"type": "error", "data": {"detail": f"Provider error during follow-up: {exc}"}}
                return
        else:
            context_messages.append({"role": "assistant", "content": final_text})

        usage["steps"] += 1

        yield {"type": "chunk", "data": {"text": "\n"}}
        yield {"type": "workflow_step_complete", "data": {"step": step_name, "status": "success"}}

    yield {"type": "status", "data": {"text": "Workflow completed successfully."}}
    yield {"type": "usage", "data": usage}
    yield {"type": "message", "data": {"text": "Workflow completed."}}
    yield {"type": "done", "data": {}}
