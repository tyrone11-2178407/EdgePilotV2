"""Shared assistant and scheduling helpers for CLI and REST endpoints."""

from __future__ import annotations

import json
import os
import platform
import time
from typing import Any, Dict, List, Optional

from core.tool_executor import execute_tool
from core.settings import DEFAULT_PROVIDER, SYSTEM_PROMPT, provider_config
from providers import get_provider
from providers.base import ChatMessage
from tools.metrics import gather_metrics
from tools.scheduler import (
    launch,
    run_python_script,
    run_shell_commands,
    _REGISTRY,
)


def _recent_tasks(limit: int = 5) -> List[Dict[str, Any]]:
    return _REGISTRY.list_recent(action=None, limit=limit)

def os_profile() -> str:
    """Return a short description of the current operating system."""
    return platform.platform()


def summarize_tasks(
    action: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return recent scheduled tasks, optionally filtered by action."""
    tasks = _REGISTRY.list_recent(action=action, limit=limit)

    return [
        {
            "task_id": task.get("task_id"),
            "action": task.get("action"),
            "target": task.get("target"),
            "status": task.get("status"),
            "created_at": task.get("created_at"),
            "result": task.get("result"),
            "error": task.get("error"),
        }
        for task in tasks
    ]


def _metrics_context(metrics: Dict[str, Any], tasks: List[Dict[str, Any]], extra_context: Optional[Dict[str, Any]]) -> str:
    summary = {
        "os": platform.platform(),
        "cpu_percent": metrics.get("cpu", {}).get("percent"),
        "memory_percent": metrics.get("memory", {}).get("percent"),
        "top_processes": metrics.get("top_processes", [])[:3],
        "recent_tasks": [
            {
                "task_id": task.get("task_id"),
                "action": task.get("action"),
                "status": task.get("status"),
                "target": task.get("target"),
            }
            for task in tasks[:5]
        ],
    }
    if extra_context:
        summary["user_context"] = extra_context
    return json.dumps(summary, indent=2)


def _fallback_answer(query: str, metrics: Dict[str, Any], tasks: List[Dict[str, Any]], cause: Optional[Exception]) -> str:
    cpu = metrics.get("cpu", {}).get("percent", 0.0)
    memory = metrics.get("memory", {}).get("percent", 0.0)
    recommendation = "System utilization looks healthy. Feel free to run your job."
    if cpu and cpu > 80:
        recommendation = "CPU usage is high; delay heavy workloads or reduce concurrency."
    elif memory and memory > 85:
        recommendation = "Memory pressure detected; free memory or downsize jobs before running more work."
    recent = ", ".join(f"{task.get('action')}:{task.get('status')}" for task in tasks[:3]) or "no scheduled tasks"
    hint = f"Recent tasks: {recent}"
    error_note = f"(Falling back to offline heuristics: {cause})" if cause else ""
    return f"{recommendation}\nCPU: {cpu:.1f}% | Memory: {memory:.1f}%\n{hint}\n{error_note}".strip()


def ask_question(
    query: str,
    *,
    provider: Optional[str] = None,
    response_format: str = "text",
    context: Optional[Dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
    context_window: int = 5,
) -> Dict[str, Any]:
    """
    Answer a user query using the configured provider or an offline fallback.
    """
    metrics = gather_metrics(top_n=max(5, context_window))
    tasks = _recent_tasks(limit=max(1, context_window))
    provider_name = (provider or DEFAULT_PROVIDER).lower()
    response_text = ""
    used_remote_provider = False
    total_prompt_tokens = 0
    total_response_tokens = 0
    tool_calls = 0

    try:
        config = provider_config(provider_name)
        if not config.api_key:
            raise ValueError("Provider API key is not configured.")
        provider_impl = get_provider(provider_name, config)
        messages: List[ChatMessage] = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
        messages.append({"role": "system", "content": f"Context:\n{_metrics_context(metrics, tasks, context)}"})
        user_message: ChatMessage = {"role": "user", "content": query, "created_at": time.time()}
        messages.append(user_message)
        llm_response = provider_impl.generate(messages)
        response_text = llm_response.text.strip()
        total_prompt_tokens = getattr(llm_response, "prompt_tokens", 0)
        total_response_tokens = getattr(llm_response, "response_tokens", 0)
        if getattr(llm_response, "has_tool_calls", False):
            for tool_call in getattr(llm_response, "tool_calls", []):
                tool_calls += 1
                execute_tool(tool_call.name, tool_call.arguments)
        used_remote_provider = True
    except Exception as exc:  # noqa: BLE001
        response_text = _fallback_answer(query, metrics, tasks, exc)
        provider_name = "offline"
        used_remote_provider = False

    payload = {
        "question": query,
        "answer": response_text,
        "provider": provider_name,
        "used_remote_provider": used_remote_provider,
        "metrics": {
            "cpu_percent": metrics.get("cpu", {}).get("percent"),
            "memory_percent": metrics.get("memory", {}).get("percent"),
        },
        "recent_tasks": [
            {
                "task_id": task.get("task_id"),
                "action": task.get("action"),
                "target": task.get("target"),
                "status": task.get("status"),
            }
            for task in tasks
        ],
        "tokens": {
            "prompt": total_prompt_tokens,
            "response": total_response_tokens,
            "tool_calls": tool_calls,
        },
        "response_format": response_format,
    }
    return payload


def schedule_operation(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    action_normalized = action.lower()
    delay = int(payload.get("delay_seconds", 0) or 0)

    if action_normalized in {"run_shell", "run_shell_commands"}:
        command = payload.get("command")
        if not command:
            raise ValueError("command is required for run_shell_commands")
        cwd = payload.get("cwd")
        return run_shell_commands(command, cwd=cwd, delay_seconds=delay)

    if action_normalized in {"run_python", "run_python_script"}:
        script = payload.get("script_path")
        if not script:
            raise ValueError("script_path is required for run_python_script")
        args = payload.get("args") or []
        cwd = payload.get("cwd")
        return run_python_script(script, args=args, cwd=cwd, delay_seconds=delay)

    if action_normalized == "launch":
        application = payload.get("application") or payload.get("app_name") or payload.get("command")
        if not application:
            raise ValueError("application is required for launch")
        launch(application, delay_seconds=delay)
        return {"status": "scheduled", "action": "launch", "application": application, "delay_seconds": delay}

    raise ValueError(f"Unsupported action '{action}'")
