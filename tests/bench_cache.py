import time
import asyncio
from MCP.tool_executor import ToolExecutor
from core.semantic_cache import SemanticCache

def test_cache():
    cache = SemanticCache()
    # mock the model so we don't have to download sentence transformers
    class MockModel:
        def encode(self, text, *args, **kwargs):
            import numpy as np
            return np.random.randn(384).astype(np.float32)
    cache._model = MockModel()
    
    # Store
    cache.store("How do I check k8s pods?", "You can use kubectl get pods")
    
    # Cache hit benchmark
    start = time.perf_counter()
    res = cache.lookup("How do I check k8s pods?")
    hit_time = time.perf_counter() - start
    
    print(f"Cache lookup time: {hit_time*1000:.2f}ms")
    # Assume a typical LLM API call takes ~1.5s
    api_time = 1.5
    if hit_time > 0:
        improvement = (api_time - hit_time) / api_time * 100
        print(f"Latency reduction vs 1.5s API call: {improvement:.2f}%")

def test_async_tools():
    import json
    executor = ToolExecutor()
    # Let's mock a tool in the executor registry
    async def mock_tool(*args, **kwargs):
        await asyncio.sleep(0.5)
        return {"success": True}
        
    # We can't easily inject async mock into sync registry, let's just mock the execute function
    calls = [
        {"name": "gather_metrics", "arguments": {"top_n": 1}},
        {"name": "list_apps", "arguments": {"filter_term": "nonexistent"}},
        {"name": "search", "arguments": {"app_name": "discord"}},
    ]
    
    start_sync = time.perf_counter()
    for c in calls:
        executor.execute(c["name"], c["arguments"])
    sync_time = time.perf_counter() - start_sync
    
    start_async = time.perf_counter()
    asyncio.run(executor.execute_batch(calls))
    async_time = time.perf_counter() - start_async
    
    print(f"Sync tool execution: {sync_time*1000:.2f}ms")
    print(f"Async tool execution: {async_time*1000:.2f}ms")
    if sync_time > 0:
        improvement = (sync_time - async_time) / sync_time * 100
        print(f"Tool execution time reduction: {improvement:.2f}%")

if __name__ == "__main__":
    print("--- Semantic Cache ---")
    test_cache()
    print("\n--- Async Tool Execution ---")
    test_async_tools()
