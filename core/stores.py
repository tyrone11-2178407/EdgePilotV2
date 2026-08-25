"""Thread-safe JSON persistence for chat sessions, usage logs, and tool calls.

Extracted from ``main.py`` to keep the FastAPI routes focused on HTTP
concerns.  All three stores share the same lock-guard + atomic-write
pattern so they are co-located here.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def ensure_data_dir(path: Path) -> None:
    """Create directory if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)


class _JSONStore:
    """Base class for lock + atomic-write JSON stores."""

    def __init__(self, path: Path, root_key: str) -> None:
        self.path = path
        ensure_data_dir(self.path.parent)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write({root_key: []})
        self._data = self._read()

    def _read(self) -> Dict[str, object]:
        with self.path.open("r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError:
                return {}

    def _write(self, data: Dict[str, object]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self.path)


class ChatStore(_JSONStore):
    """Thread-safe JSON storage for chat sessions."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, "sessions")

    def list_sessions(self) -> List[Dict[str, object]]:
        with self._lock:
            return self._data.get("sessions", [])

    def get_session(self, chat_id: str) -> Dict[str, object]:
        with self._lock:
            for session in self._data.get("sessions", []):
                if session["id"] == chat_id:
                    return session
        raise KeyError(chat_id)

    def create_session(self, title: Optional[str] = None) -> Dict[str, object]:
        session = {
            "id": str(uuid.uuid4()),
            "title": title or f"Chat {time.strftime('%H:%M:%S')}",
            "messages": [],
            "tokens_used": 0,
            "tool_calls_count": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        with self._lock:
            sessions = self._data.setdefault("sessions", [])
            sessions.insert(0, session)
            self._write(self._data)
        return session

    def append_messages(self, chat_id: str, messages: List[Dict[str, object]], token_delta: int, tool_calls_delta: int = 0) -> Dict[str, object]:
        with self._lock:
            sessions = self._data.get("sessions", [])
            for session in sessions:
                if session["id"] == chat_id:
                    session["messages"].extend(messages)
                    session["tokens_used"] = int(session.get("tokens_used", 0)) + max(token_delta, 0)
                    session["tool_calls_count"] = int(session.get("tool_calls_count", 0)) + max(tool_calls_delta, 0)
                    session["updated_at"] = time.time()
                    title = (session.get("title") or "").strip().lower()
                    if not title or title.startswith("new chat") or title.startswith("chat "):
                        first_user = next(
                            (m for m in session["messages"] if m.get("role") == "user" and m.get("content")), None
                        )
                        if first_user:
                            snippet = first_user["content"].strip().splitlines()[0][:50]
                            if snippet:
                                session["title"] = snippet if len(snippet) > 2 else "Conversation"
                    self._write(self._data)
                    return session
        raise KeyError(chat_id)

    def delete_session(self, chat_id: str) -> bool:
        """Delete a chat session by ID."""
        with self._lock:
            sessions = self._data.get("sessions", [])
            original_length = len(sessions)
            self._data["sessions"] = [s for s in sessions if s["id"] != chat_id]
            if len(self._data["sessions"]) < original_length:
                self._write(self._data)
                return True
        return False


class UsageLogger(_JSONStore):
    """Track usage statistics per call."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, "records")

    def log(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        response_tokens: int,
        latency_ms: float,
        ok: bool,
    ) -> None:
        record = {
            "ts": time.time(),
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "latency_ms": latency_ms,
            "ok": ok,
        }
        with self._lock:
            self._data.setdefault("records", []).append(record)
            self._write(self._data)


class ToolCallLogger(_JSONStore):
    """Track tool calls for debugging purposes."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, "tool_calls")

    def log(
        self,
        *,
        provider: str,
        model: str,
        tool_name: str,
        arguments: Dict[str, object],
        result: Dict[str, object],
        success: bool,
        latency_ms: float,
        chat_id: str = None,
    ) -> None:
        """Log a tool call execution."""
        record = {
            "ts": time.time(),
            "chat_id": chat_id,
            "provider": provider,
            "model": model,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "success": success,
            "latency_ms": latency_ms,
        }
        with self._lock:
            self._data.setdefault("tool_calls", []).append(record)
            # Keep only last 1000 tool calls to prevent file from growing too large
            if len(self._data["tool_calls"]) > 1000:
                self._data["tool_calls"] = self._data["tool_calls"][-1000:]
            self._write(self._data)
