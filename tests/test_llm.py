"""يتأكد من أن طلب Claude يُبنى بشكل صحيح وأن الرد يُحوَّل إلى Analysis (بدون اتصال حقيقي)."""

import json

import httpx2 as httpx
import pytest

from study_bot.llm import Analysis, ClaudeAnalyzer

ANALYSIS = {
    "intent": "add",
    "tasks": [{
        "kind": "exam", "subject": "فيزياء", "title": "الفصل 3", "date_expression": "الخميس الجاي",
        "date": "2026-11-26", "time": None, "notes": None,
    }],
    "target_task_id": None, "changes": None, "query": None, "reply": None,
}


def _response(stop_reason="end_turn", text=json.dumps(ANALYSIS, ensure_ascii=False)):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _analyzer(handler):
    analyzer = ClaudeAnalyzer("test-key", "claude-opus-5-5", "low")
    analyzer.client = analyzer.client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=0
    )
    return analyzer


@pytest.mark.asyncio
async def test_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx.Response(200, json=_response())

    result = await _analyzer(handler).analyze("رسالة")
    assert isinstance(result, Analysis)
    assert result.tasks[0].subject == "فيزياء"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]


@pytest.mark.asyncio
async def test_refusal_and_errors_return_none():
    def refusal(request):
        return httpx.Response(200, json=_response(stop_reason="refusal", text=""))

    def server_error(request):
        return httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}})

    assert await _analyzer(refusal).analyze("x") is None
    assert await _analyzer(server_error).analyze("x") is None
