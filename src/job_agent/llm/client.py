"""OpenRouter (OpenAI-compatible) chat completions client.

Parameter names verified live against OpenRouter's docs on 2026-09-16, and
against real requests on 2026-09-19 once a key was available:
  - reasoning: {"effort": "none"} disables reasoning tokens (billed as
    output otherwise) for most endpoints; {"effort": "minimal"/"low"/
    "medium"/"high"} sets it explicitly. Confirmed live: some endpoints
    (e.g. z-ai/glm-5.3-flash) reject an explicit effort with a 400
    ("Reasoning is mandatory for this endpoint and cannot be disabled.")
    and require the `reasoning` key to be absent entirely -- see
    `complete_structured`'s automatic one-time fallback below.
  - response_format: {"type": "json_schema", "json_schema": {"name",
    "strict": true, "schema": {...}}} for structured outputs.
  - provider: {"max_price": {"prompt": N, "completion": N}} caps per-request
    price (rejects if no matching provider).
  - usage.{prompt_tokens,completion_tokens,cost} are always included in the
    response now (the old `usage: {include: true}` request flag is a
    deprecated no-op). Confirmed live: mandatory-reasoning tokens are
    billed as completion_tokens like any other output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

API_BASE_URL = "https://openrouter.ai/api/v1"
REFERER = "https://github.com/Samuts-Nazar/job-search-agent"
APP_TITLE = "job-search-agent"


class OpenRouterError(Exception):
    """Raised when a model returns a non-retryable error or invalid JSON."""


@dataclass
class CompletionResult:
    raw_content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    reasoning_fallback_used: bool = False


def make_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": REFERER,
            "X-Title": APP_TITLE,
        },
        timeout=60.0,
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _is_reasoning_mandatory_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 400:
        return False
    try:
        body = exc.response.json()
    except ValueError:
        return False
    message = str(body.get("error", {}).get("message", "")).lower()
    return "reasoning" in message and "mandatory" in message


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception(_is_retryable),
)
def _post_chat_completion(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/chat/completions", json=payload)
    response.raise_for_status()
    return response.json()


def build_structured_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any],
    schema_name: str,
    reasoning: str | None = "none",
    max_price: dict[str, float] | None = None,
) -> dict[str, Any]:
    """`reasoning` is an effort string ("none", "minimal", "low", "medium",
    "high") or None to omit the `reasoning` param entirely -- some
    endpoints reject an explicit effort value and require it to be absent
    (see module docstring)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        },
    }
    if reasoning is not None:
        payload["reasoning"] = {"effort": reasoning}
    if max_price:
        payload["provider"] = {"max_price": max_price}
    return payload


def complete_structured(client: httpx.Client, payload: dict[str, Any]) -> CompletionResult:
    reasoning_fallback_used = False
    try:
        data = _post_chat_completion(client, payload)
    except httpx.HTTPStatusError as exc:
        if "reasoning" in payload and _is_reasoning_mandatory_error(exc):
            retry_payload = {k: v for k, v in payload.items() if k != "reasoning"}
            data = _post_chat_completion(client, retry_payload)
            reasoning_fallback_used = True
        else:
            raise

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"Unexpected response shape: {data!r}") from exc

    usage = data.get("usage") or {}
    return CompletionResult(
        raw_content=content,
        model=data.get("model", payload["model"]),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("cost"),
        reasoning_fallback_used=reasoning_fallback_used,
    )


def parse_json_content(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenRouterError(f"Model did not return valid JSON: {content!r}") from exc
