"""Runtime configuration helpers shared across CLI and API."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from providers.base import ProviderConfig


SYSTEM_PROMPT = (
    "You are EdgePilot, an on-premises AI copilot that proactively monitors and manages the user's system. "
    "You have access to powerful tools - USE THEM AUTOMATICALLY without asking permission.\n\n"

    "TOOL USAGE GUIDELINES:\n"
    "- When users ask about performance, slowness, or resource usage → IMMEDIATELY call gather_metrics()\n"
    "- When users ask 'can I run X' or 'is it safe to start Y' → call gather_metrics() to check current load, then provide guidance based on typical requirements\n"
    "- When users want to open/launch an app → call launch() with the app name\n"
    "- When users ask what apps are installed or available → call list_apps() or search()\n"
    "- When users want to close/end a process → call end_task()\n"
    "- When users ask for a health check, maintenance audit, or complex diagnosis → call run_workflow() with the appropriate workflow name\n\n"

    "DECISION MAKING:\n"
    "- Make reasonable assumptions based on general knowledge (e.g., heavy builds need ~50%+ CPU, video editing needs 8GB+ RAM)\n"
    "- Don't ask follow-up questions when you can gather data with tools\n"
    "- Be direct and actionable - if system has 90% CPU usage, say so and recommend closing apps\n\n"

    "RESPONSE STYLE:\n"
    "- Succinct and engineering-focused\n"
    "- Lead with the answer, then provide supporting data\n"
    "- Use actual metrics from tools when available, general estimates when not"
)

PROVIDER_ENV_SETTINGS = {
    "gemini": {
        "api_key": "GEMINI_API_KEY",
        "model": "GEMINI_MODEL",
        "default_model": "gemini-3.1-flash-lite",
        "base_url": "GEMINI_BASE_URL",
    },
    "claude": {
        "api_key": "ANTHROPIC_API_KEY",
        "model": "CLAUDE_MODEL",
        "default_model": "claude-3-5-haiku-20241022",
        "base_url": "CLAUDE_BASE_URL",
    },
    # GPT provider not implemented yet
    # "gpt": {
    #     "api_key": "OPENAI_API_KEY",
    #     "model": "GPT_MODEL",
    #     "default_model": "gpt-4o-mini",
    #     "base_url": "GPT_BASE_URL",
    # },
}


def load_env() -> None:
    env_path = Path("env") / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


load_env()
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "gemini").lower()


def provider_config(name: str) -> ProviderConfig:
    key = name.lower()
    settings = PROVIDER_ENV_SETTINGS.get(key)
    if not settings:
        raise ValueError(f"Unsupported provider '{name}'")

    api_key = os.getenv(settings["api_key"], "").strip()
    model = os.getenv(settings["model"], settings["default_model"]).strip()
    base_url = os.getenv(settings["base_url"], "").strip() or None
    timeout = int(os.getenv("LLM_TIMEOUT_SEC", "60"))
    return ProviderConfig(api_key=api_key, model=model or settings["default_model"], timeout_sec=timeout, base_url=base_url)
