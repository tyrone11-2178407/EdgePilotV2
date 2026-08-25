from unittest.mock import MagicMock, patch

import httpx
import pytest

from MCP.tool_schemas import format_tools_for_claude
from providers.base import ProviderConfig
from providers.claude import ClaudeProvider


@pytest.fixture
def provider():
    config = ProviderConfig(
        api_key="test-key-not-real",
        model="test-claude-model",
        timeout_sec=10,
    )
    return ClaudeProvider(config)


def make_mock_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = str(payload)
    response.raise_for_status.return_value = None
    return response


@patch("providers.claude.httpx.Client")
def test_claude_sends_tool_schemas(mock_client_class, provider):
    client = mock_client_class.return_value.__enter__.return_value

    client.post.return_value = make_mock_response(
        {
            "content": [
                {
                    "type": "text",
                    "text": "I will inspect capacity.",
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            },
            "stop_reason": "end_turn",
        }
    )

    schemas = format_tools_for_claude()
    provider.enable_tools(schemas)

    provider.generate(
        [{"role": "user", "content": "Can this workload fit?"}]
    )

    client.post.assert_called_once()

    request_payload = client.post.call_args.kwargs["json"]

    # Every enabled schema is sent, in order and unchanged. The only permitted
    # difference is the prompt-cache breakpoint on the final tool, which caches
    # the whole ~22,000-character tool block; strip it and the payload must be
    # byte-identical to what was enabled.
    sent = [dict(tool) for tool in request_payload["tools"]]
    assert "cache_control" in sent[-1], "tool block should be cached"
    sent[-1].pop("cache_control")

    assert sent == schemas
    assert request_payload["tool_choice"] == {"type": "auto"}


@patch("providers.claude.httpx.Client")
def test_claude_parses_tool_use_response(
    mock_client_class,
    provider,
):
    client = mock_client_class.return_value.__enter__.return_value

    client.post.return_value = make_mock_response(
        {
            "content": [
                {
                    "type": "text",
                    "text": "I recommend checking capacity.",
                },
                {
                    "type": "tool_use",
                    "id": "tool-call-123",
                    "name": "scale_workload",
                    "input": {
                        "namespace": "production",
                        "deployment_name": "frontend",
                        "replicas": 3,
                    },
                },
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 12,
            },
            "stop_reason": "tool_use",
        }
    )

    provider.enable_tools(format_tools_for_claude())

    result = provider.generate(
        [{"role": "user", "content": "Scale frontend to 3 replicas."}]
    )

    assert result.has_tool_calls is True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "tool-call-123"
    assert result.tool_calls[0].name == "scale_workload"
    assert result.tool_calls[0].arguments == {
        "namespace": "production",
        "deployment_name": "frontend",
        "replicas": 3,
    }
    assert result.finish_reason == "tool_use"
    assert result.prompt_tokens == 20
    assert result.response_tokens == 12


@patch("providers.claude.httpx.Client")
def test_claude_handles_text_only_response(
    mock_client_class,
    provider,
):
    client = mock_client_class.return_value.__enter__.return_value

    client.post.return_value = make_mock_response(
        {
            "content": [
                {
                    "type": "text",
                    "text": "The cluster has sufficient capacity.",
                }
            ],
            "usage": {
                "input_tokens": 8,
                "output_tokens": 6,
            },
            "stop_reason": "end_turn",
        }
    )

    result = provider.generate(
        [{"role": "user", "content": "Check capacity."}]
    )

    assert result.text == "The cluster has sufficient capacity."
    assert result.has_tool_calls is False
    assert result.finish_reason == "end_turn"


@patch("providers.claude.httpx.Client")
def test_claude_handles_api_error(
    mock_client_class,
    provider,
):
    client = mock_client_class.return_value.__enter__.return_value

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
    )
    response = httpx.Response(
        status_code=500,
        request=request,
        text="Internal server error",
    )

    client.post.return_value = response

    with pytest.raises(RuntimeError, match="Claude API error 500"):
        provider.generate(
            [{"role": "user", "content": "Check capacity."}]
        )


def test_claude_rejects_missing_api_key():
    config = ProviderConfig(
        api_key="",
        model="test-claude-model",
    )

    with pytest.raises(
        ValueError,
        match="Claude provider requires ANTHROPIC_API_KEY",
    ):
        ClaudeProvider(config)