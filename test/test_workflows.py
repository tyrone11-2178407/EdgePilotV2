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
