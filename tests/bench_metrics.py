import time
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.semantic_cache import SemanticCache
from MCP.tool_executor import ToolExecutor

def measure_cache():
    cache = SemanticCache()
    cache._ensure_model()  # load model into memory
    
    # Pre-warm the cache
    cache.store("What is the CPU usage?", "CPU is at 45%")
    
    # Measure exactly how long a lookup takes using high-precision timers
    start_time = time.perf_counter()
    cache.lookup("What is the CPU usage?")
    end_time = time.perf_counter()
    
    latency_ms = (end_time - start_time) * 1000
    print(f"[CACHE] Semantic lookup took exactly: {latency_ms:.2f} ms")

def measure_async():
    executor = ToolExecutor()
    
    # We will measure the time it takes to run 3 tools sequentially vs in parallel
    calls = [
        {"name": "gather_metrics", "arguments": {"top_n": 1}},
        {"name": "list_apps", "arguments": {"filter_term": "nonexistent"}},
        {"name": "search", "arguments": {"app_name": "discord"}},
    ]
    
    # 1. Measure sequential (Sync) execution
    start_sync = time.perf_counter()
    for c in calls:
        executor.execute(c["name"], c["arguments"])
    sync_time_ms = (time.perf_counter() - start_sync) * 1000
    print(f"[ASYNC] Running 3 tools sequentially took: {sync_time_ms:.2f} ms")
    
    # 2. Measure parallel (Async) execution
    start_async = time.perf_counter()
    asyncio.run(executor.execute_batch(calls))
    async_time_ms = (time.perf_counter() - start_async) * 1000
    print(f"[ASYNC] Running the same 3 tools in parallel took: {async_time_ms:.2f} ms")

if __name__ == '__main__':
    measure_cache()
    print("-" * 40)
    measure_async()
