import httpx
import pytest

from job_agent.llm import client as llm_client


def test_is_retryable_on_429_and_5xx():
    def status_error(code):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    assert llm_client._is_retryable(status_error(429))
    assert llm_client._is_retryable(status_error(500))
    assert llm_client._is_retryable(status_error(503))
    assert not llm_client._is_retryable(status_error(400))
    assert not llm_client._is_retryable(status_error(401))


def test_is_retryable_on_transport_error():
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    assert llm_client._is_retryable(httpx.ConnectError("boom", request=request))
    assert not llm_client._is_retryable(ValueError("unrelated"))


def test_build_structured_payload_default_shape():
    payload = llm_client.build_structured_payload(
        model="test/model",
        system_prompt="sys",
        user_prompt="usr",
        json_schema={"type": "object"},
        schema_name="posting_score",
    )
    assert payload["model"] == "test/model"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["reasoning"] == {"effort": "none"}
    assert "provider" not in payload


def test_build_structured_payload_with_max_price():
    payload = llm_client.build_structured_payload(
        model="test/model",
        system_prompt="sys",
        user_prompt="usr",
        json_schema={"type": "object"},
        schema_name="posting_score",
        max_price={"prompt": 1.0, "completion": 2.0},
    )
    assert payload["provider"] == {"max_price": {"prompt": 1.0, "completion": 2.0}}


def test_complete_structured_parses_content_and_usage():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "test/model",
                "choices": [{"message": {"content": '{"fit_score": 80}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0015},
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url=llm_client.API_BASE_URL) as client:
        result = llm_client.complete_structured(client, {"model": "test/model"})

    assert result.raw_content == '{"fit_score": 80}'
    assert result.model == "test/model"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.cost_usd == 0.0015


def test_complete_structured_raises_on_unexpected_shape():
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url=llm_client.API_BASE_URL) as client:
        with pytest.raises(llm_client.OpenRouterError):
            llm_client.complete_structured(client, {"model": "test/model"})


def test_parse_json_content_valid():
    assert llm_client.parse_json_content('{"a": 1}') == {"a": 1}


def test_parse_json_content_invalid_raises():
    with pytest.raises(llm_client.OpenRouterError):
        llm_client.parse_json_content("not json")
