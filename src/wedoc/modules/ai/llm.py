"""OpenAI-compatible LLM connectivity test — ports the AI SDK generateText path
used by setting-open-api.service.testLLM and space.service.testIntegrationLLM.

The reference calls the Vercel AI SDK, which POSTs to `{baseURL}/chat/completions`
and, on failure, surfaces the provider's `error.message` verbatim. wedoc issues
the same request over httpx and extracts the same field, so the wrapped
`LLM test failed with error: <message>` envelope matches byte-for-byte for the
deterministic auth-failure paths. A genuinely successful call still needs a live
key (deferred: external verification pending credentials).
"""

from typing import Any

import httpx

_TEST_PROMPT = 'Hello, please respond with "Connection successful!"'
_UNKNOWN_ERROR = "Unknown error occurred while testing LLM"


def _first_model(models: str, model_key: str | None) -> str:
    if model_key and "@" in model_key:
        return model_key.split("@", 1)[0]
    if model_key:
        return model_key
    return models.split(",")[0].strip()


def _extract_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
        message = body.get("message")
        if isinstance(message, str) and message:
            return message
    return f"HTTP {response.status_code}"


async def call_test_llm(ro: Any) -> dict[str, Any]:
    """Run the connectivity probe. Returns the success VO or raises ValueError
    carrying the provider message (caller wraps it in the testLLMFailed envelope)."""
    base_url = (ro.baseUrl or "").rstrip("/")
    model = _first_model(ro.models, ro.modelKey)
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _TEST_PROMPT}],
        "temperature": 1,
    }
    headers = {
        "Authorization": f"Bearer {ro.apiKey}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as error:
        raise ValueError(str(error) or _UNKNOWN_ERROR) from error
    if response.status_code >= 400:
        raise ValueError(_extract_error_message(response))
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError(_UNKNOWN_ERROR) from error
    text = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
    # Ability probing (vision/pdf/tool/reasoning) needs extra successful calls
    # against a live model; only reachable with a valid key (deferred).
    return {"success": True, "response": text}
