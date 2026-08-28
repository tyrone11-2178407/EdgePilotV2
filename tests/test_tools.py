import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from MCP.tool_executor import ToolExecutor

"""
Platform-aware smoke tests for MCP tools.
These tests avoid assuming Windows-only apps and treat empty results as acceptable on minimal hosts.
"""


@pytest.fixture(scope="module")
def executor() -> ToolExecutor:
    return ToolExecutor()


def _extract_list(payload: Any) -> List[str]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("apps", "matches", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def test_search_platform_aware(executor: ToolExecutor) -> None:
    query = "term"
    result = executor.execute("search", {"app_name": query})
    assert "success" in result
    if result.get("success"):
        matches = _extract_list(result.get("result") or {})
        assert isinstance(matches, list)


def test_list_apps_platform_aware(executor: ToolExecutor) -> None:
    result = executor.execute("list_apps", {"filter_term": "term"})
    assert "success" in result
    if result.get("success"):
        apps = _extract_list(result.get("result") or {})
        assert isinstance(apps, list)
        sample = ", ".join(apps[:5])
        print(f"list_apps sample: {sample}")


def test_launch_exists_but_skip_real_launch(executor: ToolExecutor) -> None:
    plat = "win" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux"
    candidates_by_platform: Dict[str, List[str]] = {
        "win": ["notepad", "windows terminal", "calc"],
        "darwin": ["Safari", "TextEdit", "Notes", "Terminal", "Calculator"],
        "linux": ["Calculator", "Firefox", "Files", "Terminal", "Chromium"],
    }
    allow_launch = os.environ.get("ALLOW_LAUNCH", "") == "1"

    chosen: Optional[str] = None
    for candidate in candidates_by_platform.get(plat, []):
        res = executor.execute("search", {"app_name": candidate})
        if res.get("success"):
            matches = _extract_list(res.get("result") or {})
            if matches:
                chosen = matches[0]
                break

    if chosen and allow_launch:
        out = executor.execute("launch", {"app_name": chosen, "delay_seconds": 0})
        print(f"launch('{chosen}') -> {out}")
    else:
        reason = "ALLOW_LAUNCH!=1" if not allow_launch else "no candidate found"
        print(f"Skipping launch test ({reason}).")
