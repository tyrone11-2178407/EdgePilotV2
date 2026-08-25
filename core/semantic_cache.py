"""Semantic query cache — skips the LLM round-trip for near-duplicate questions.

Embeds queries via sentence-transformers, compares with numpy cosine similarity,
and returns cached responses when similarity ≥ threshold. State-changing tool
responses are never cached.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import numpy as np

logger = logging.getLogger(__name__)

# ── Tools whose results should NOT be cached ────────────────────────────
# Derived from the single source of truth (MCP.tool_schemas.MUTATING_TOOLS)
# plus read-only tools whose output is volatile and must not be served stale.
from MCP.tool_schemas import MUTATING_TOOLS

# Read-only but volatile — caching these would serve stale system state.
_VOLATILE_READ_ONLY: Set[str] = frozenset({
    "gather_metrics",
    "report_edge_status",
    "evaluate_capacity",
    "suggest_capacity_window",
    "recommend_rightsizing",
    "analyze_bottlenecks",
    "analyze_workload_families",
    "inspect_kubernetes_cluster",
})

_STATE_CHANGING_TOOLS: Set[str] = frozenset(MUTATING_TOOLS | _VOLATILE_READ_ONLY)


@dataclass
class _CacheEntry:
    """Single cached query → response pair."""
    query: str
    embedding: np.ndarray
    response: str
    created_at: float
    tool_names: List[str] = field(default_factory=list)


class SemanticCache:
    """In-memory semantic similarity cache for LLM responses.

    Parameters
    ----------
    model_name:
        HuggingFace sentence-transformers model to use for embeddings.
        ``all-MiniLM-L6-v2`` is ~80 MB and provides excellent speed/quality.
    similarity_threshold:
        Minimum cosine similarity (0–1) to consider a cache hit.
    max_entries:
        Maximum number of cached entries (oldest evicted first).
    ttl_seconds:
        Time-to-live for each cache entry in seconds.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.92,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
    ) -> None:
        self.model_name = model_name
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds

        self._entries: List[_CacheEntry] = []
        self._model: Any = None  # Lazy-loaded SentenceTransformer

    # ── Lazy model loading ──────────────────────────────────────────────

    def _ensure_model(self) -> Any:
        """Load the embedding model on first use (not at import time).

        This keeps startup fast and avoids pulling the model into memory
        if the cache is never queried.
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                logger.info(
                    "SemanticCache: loaded model '%s'", self.model_name
                )
            except ImportError:
                logger.warning(
                    "sentence-transformers is not installed — "
                    "semantic caching is disabled.  Install with: "
                    "pip install sentence-transformers"
                )
                raise
        return self._model

    # ── Embedding ───────────────────────────────────────────────────────

    def _embed(self, text: str) -> np.ndarray:
        """Return the L2-normalized embedding vector for *text*."""
        model = self._ensure_model()
        vec = model.encode(text, normalize_embeddings=True)
        return np.asarray(vec, dtype=np.float32)

    # ── Eviction ────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        """Remove entries older than TTL."""
        cutoff = time.time() - self.ttl_seconds
        self._entries = [e for e in self._entries if e.created_at > cutoff]

    # ── Public API ──────────────────────────────────────────────────────

    def lookup(self, query: str) -> Optional[str]:
        """Search the cache for a semantically similar query.

        Returns the cached response string on a hit, or ``None`` on a miss.
        """
        try:
            query_vec = self._embed(query)
        except (ImportError, Exception) as exc:
            logger.debug("SemanticCache.lookup failed: %s", exc)
            return None

        self._evict_expired()

        best_score = -1.0
        best_entry: Optional[_CacheEntry] = None

        for entry in self._entries:
            # Cosine similarity (vectors are already L2-normalized)
            score = float(np.dot(query_vec, entry.embedding))
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= self.similarity_threshold:
            logger.info(
                "SemanticCache HIT (score=%.4f): '%s' ≈ '%s'",
                best_score,
                query[:60],
                best_entry.query[:60],
            )
            return best_entry.response

        logger.debug(
            "SemanticCache MISS (best_score=%.4f, threshold=%.2f)",
            best_score,
            self.similarity_threshold,
        )
        return None

    def store(
        self,
        query: str,
        response: str,
        tool_names: Optional[List[str]] = None,
    ) -> None:
        """Cache a query/response pair.

        Entries are *not* stored if any of the tools invoked during the
        response are state-changing (see ``_STATE_CHANGING_TOOLS``).
        """
        tool_names = tool_names or []

        # Never cache responses that triggered state-changing tools
        if any(name in _STATE_CHANGING_TOOLS for name in tool_names):
            logger.debug(
                "SemanticCache: skipping store — state-changing tool used (%s)",
                tool_names,
            )
            return

        try:
            embedding = self._embed(query)
        except (ImportError, Exception) as exc:
            logger.debug("SemanticCache.store failed: %s", exc)
            return

        self._evict_expired()

        # Enforce max size (FIFO eviction)
        while len(self._entries) >= self.max_entries:
            self._entries.pop(0)

        self._entries.append(
            _CacheEntry(
                query=query,
                embedding=embedding,
                response=response,
                created_at=time.time(),
                tool_names=tool_names,
            )
        )
        logger.debug(
            "SemanticCache: stored entry (size=%d)", len(self._entries)
        )

    def clear(self) -> None:
        """Flush all cached entries."""
        self._entries.clear()
        logger.info("SemanticCache: cleared")

    @property
    def size(self) -> int:
        """Return the number of entries currently in the cache."""
        return len(self._entries)

    def stats(self) -> Dict[str, Any]:
        """Return diagnostic information about the cache."""
        self._evict_expired()
        return {
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "similarity_threshold": self.similarity_threshold,
            "model": self.model_name,
            "model_loaded": self._model is not None,
        }
