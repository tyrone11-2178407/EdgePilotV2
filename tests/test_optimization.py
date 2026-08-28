"""Tests for the optimization features added to EdgePilotV2.

Covers:
  1. TTL cache on gather_metrics
  2. Async / parallel tool execution
  3. Semantic query cache (with mocked embeddings to avoid model download)
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ====================================================================== #
# 1. TTL Cache on gather_metrics                                          #
# ====================================================================== #


class TestMetricsTTLCache:
    """Verify that gather_metrics returns cached results within the TTL."""

    def test_cached_within_ttl(self) -> None:
        """Two rapid calls should return the exact same dict object."""
        from tools.metrics import gather_metrics, _metrics_cache

        # Clear any pre-existing cache
        _metrics_cache.clear()

        first = gather_metrics(top_n=3)
        second = gather_metrics(top_n=3)

        # Same object reference means the cache was used
        assert first is second, (
            "Expected the second call within the TTL to return the cached object"
        )

    def test_different_params_not_cached(self) -> None:
        """Different (top_n, all_processes) combos get their own cache slots."""
        from tools.metrics import gather_metrics, _metrics_cache

        _metrics_cache.clear()

        result_3 = gather_metrics(top_n=3)
        result_5 = gather_metrics(top_n=5)

        # They should be distinct snapshots (different top_n)
        assert result_3 is not result_5


# ====================================================================== #
# 2. Async / Parallel Tool Execution                                      #
# ====================================================================== #


class TestAsyncToolExecution:
    """Verify the async and batch execution paths on ToolExecutor."""

    def test_execute_async_returns_result(self) -> None:
        """execute_async should produce the same result as synchronous execute."""
        from MCP.tool_executor import ToolExecutor

        executor = ToolExecutor()
        sync_result = executor.execute("gather_metrics", {"top_n": 2})

        async_result = asyncio.run(
            executor.execute_async("gather_metrics", {"top_n": 2})
        )

        assert sync_result["success"] == async_result["success"]
        assert sync_result["tool"] == async_result["tool"]

    def test_execute_batch_runs_concurrently(self) -> None:
        """execute_batch should run multiple calls and return results in order."""
        from MCP.tool_executor import ToolExecutor

        executor = ToolExecutor()

        calls = [
            {"name": "gather_metrics", "arguments": {"top_n": 1}},
            {"name": "list_apps", "arguments": {"filter_term": "zzz_nonexistent"}},
        ]

        results = asyncio.run(
            executor.execute_batch(calls)
        )

        assert len(results) == 2
        assert results[0]["tool"] == "gather_metrics"
        assert results[1]["tool"] == "list_apps"

    def test_execute_batch_unknown_tool(self) -> None:
        """Batch should gracefully handle unknown tool names."""
        from MCP.tool_executor import ToolExecutor

        executor = ToolExecutor()

        calls = [
            {"name": "nonexistent_tool", "arguments": {}},
        ]

        results = asyncio.run(
            executor.execute_batch(calls)
        )

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "Unknown tool" in results[0]["error"]


# ====================================================================== #
# 3. Semantic Query Cache                                                  #
# ====================================================================== #


def _make_mock_cache():
    """Create a SemanticCache with a mocked embedding model.

    Instead of downloading a real transformer model, we use a deterministic
    mock that maps specific query strings to known embedding vectors.
    This makes tests fast, offline, and reproducible.
    """
    from core.semantic_cache import SemanticCache

    cache = SemanticCache(similarity_threshold=0.90)

    # Pre-build a mock model that returns deterministic embeddings
    mock_model = MagicMock()

    # Helper: generate a normalized random vector seeded by the query string
    def _mock_encode(text: str, normalize_embeddings: bool = True):
        seed = sum(ord(c) for c in text) % (2**31)
        rng = np.random.RandomState(seed)
        vec = rng.randn(384).astype(np.float32)
        if normalize_embeddings:
            vec = vec / np.linalg.norm(vec)
        return vec

    mock_model.encode = _mock_encode
    cache._model = mock_model  # Bypass lazy loading

    return cache


class TestSemanticCache:
    """Verify cache lookup, store, and eviction logic."""

    def test_store_and_lookup_identical_query(self) -> None:
        """An identical query should produce a cache hit."""
        cache = _make_mock_cache()

        cache.store("What is my CPU usage?", "Your CPU is at 12%.")
        result = cache.lookup("What is my CPU usage?")

        assert result == "Your CPU is at 12%."

    def test_miss_on_unrelated_query(self) -> None:
        """A completely different query should miss."""
        cache = _make_mock_cache()

        cache.store("What is my CPU usage?", "Your CPU is at 12%.")
        result = cache.lookup("Launch Minecraft for me")

        assert result is None

    def test_state_changing_tools_not_cached(self) -> None:
        """Responses that triggered state-changing tools must not be stored."""
        cache = _make_mock_cache()

        cache.store(
            "Launch Chrome",
            "Launched Chrome!",
            tool_names=["launch"],
        )

        # Cache should be empty because 'launch' is a state-changing tool
        assert cache.size == 0

    def test_ttl_eviction(self) -> None:
        """Entries older than TTL should be evicted."""
        cache = _make_mock_cache()
        cache.ttl_seconds = 0.1  # 100ms for testing

        cache.store("test query", "test response")
        assert cache.size == 1

        time.sleep(0.15)

        # Lookup triggers eviction
        result = cache.lookup("test query")
        assert result is None
        assert cache.size == 0

    def test_max_entries_eviction(self) -> None:
        """Cache should not exceed max_entries."""
        cache = _make_mock_cache()
        cache.max_entries = 3

        for i in range(5):
            cache.store(f"query {i}", f"response {i}")

        assert cache.size <= 3

    def test_clear(self) -> None:
        """clear() should flush all entries."""
        cache = _make_mock_cache()
        cache.store("a", "b")
        cache.store("c", "d")
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0

    def test_stats(self) -> None:
        """stats() should return valid diagnostic info."""
        cache = _make_mock_cache()
        cache.store("x", "y")

        info = cache.stats()
        assert info["entries"] == 1
        assert info["model_loaded"] is True
        assert "similarity_threshold" in info
