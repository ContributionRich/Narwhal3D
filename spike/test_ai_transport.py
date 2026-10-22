#!/usr/bin/env python3
"""Bounded, secret-safe provider transport tests with no real network access."""
import importlib
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg
ai = importlib.import_module("narwhal3d.ai_assistant")
transport = importlib.import_module("narwhal3d.ai_transport")

checks = 0
failures = []


def check(ok, label, detail=""):
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}: {detail}" if detail else label)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self, amount):
        return self.payload[:amount]


captured = []
secret = "secret-must-not-escape"


def openai_opener(request, timeout):
    captured.append((request, timeout))
    return Response(json.dumps({"choices": [{"message": {"content": "Hello CAD"}}]}).encode())


config = ai.set_provider("openai_compatible", "local-model")

models_payload = {"data": [
    {"id": "qwen/qwen3.5-9b", "type": "vlm", "state": "not-loaded",
     "max_context_length": 262144, "capabilities": ["tool_use"]},
    {"id": "text-embedding-nomic", "type": "embeddings", "state": "loaded"},
    {"id": "dolphin3", "type": "llm", "state": "loaded", "max_context_length": 131072},
]}
models = transport.discover_models(
    ai.set_provider("openai_compatible", endpoint="http://localhost:1234/api/v0"), {},
    opener=lambda request, timeout: (
        captured.append((request, timeout)) or Response(json.dumps(models_payload).encode())))
request, timeout = captured[-1]
check(request.full_url == "http://localhost:1234/api/v0/models"
      and request.get_method() == "GET" and timeout == transport.DEFAULT_TIMEOUT_SECONDS,
      "LM Studio model discovery preserves the configured API version")
check([model.model_id for model in models] == ["qwen/qwen3.5-9b", "dolphin3"],
      "model discovery excludes embedding-only entries")
check(models[0].tool_use and models[0].max_context_length == 262144
      and models[1].state == "loaded",
      "LM Studio model state, context length, and tool capability remain available")

captured.clear()
request_data = {"messages": [{"role": "user", "content": "Help"}],
                "context": {"selection": [{"name": "Curve1"}]}}
result = transport.send_chat(config, request_data, {}, opener=openai_opener, timeout=4)
request, timeout = captured[-1]
body = json.loads(request.data)
check(result.text == "Hello CAD" and result.provider_id == "openai_compatible",
      "OpenAI-compatible response text is returned")
check(request.full_url == "http://127.0.0.1:1234/v1/chat/completions"
      and timeout == 4 and body["model"] == "local-model" and not body["stream"],
      "LM Studio uses the configured bounded non-streaming chat endpoint")
check("Curve1" in body["messages"][-1]["content"],
      "explicit selected context is included as structured JSON")

captured.clear()

def anthropic_opener(request, timeout):
    captured.append((request, timeout))
    return Response(json.dumps({"content": [{"type": "text", "text": "Answer"}]}).encode())


anthropic = ai.set_provider("anthropic", "claude-test", credential_env="TEST_KEY")
result = transport.send_chat(anthropic, {"messages": []}, {"TEST_KEY": secret},
                             opener=anthropic_opener)
request = captured[-1][0]
check(result.text == "Answer" and request.full_url ==
      "https://api.anthropic.com/v1/messages", "Anthropic uses its messages endpoint")
check(request.headers.get("X-api-key") == secret
      and request.headers.get("Anthropic-version") == "2023-06-01",
      "Anthropic credential exists only in the outbound request headers")

for provider in (ai.set_provider("openai", "gpt-test", credential_env="MISSING"),
                 ai.set_provider("anthropic", "claude-test", credential_env="MISSING")):
    try:
        transport.send_chat(provider, {"messages": []}, {}, opener=openai_opener)
    except transport.TransportError as exc:
        check(secret not in str(exc) and "MISSING" in str(exc),
              "missing remote credentials are named safely")
    else:
        check(False, "remote providers refuse missing credentials")

oversized = b"x" * (transport.MAX_RESPONSE_BYTES + 1)
try:
    transport.send_chat(config, {"messages": []}, {},
                        opener=lambda _request, timeout: Response(oversized))
except transport.TransportError as exc:
    check("size limit" in str(exc) and secret not in str(exc),
          "oversized responses are refused without leaking secrets")
else:
    check(False, "oversized provider response must be refused")

for payload, label in ((b"not json", "invalid JSON"),
                       (b'{"choices":[]}', "invalid response shape")):
    try:
        transport.send_chat(config, {"messages": []}, {},
                            opener=lambda _request, timeout, p=payload: Response(p))
    except transport.TransportError as exc:
        check(label in str(exc), f"{label} is surfaced safely")
    else:
        check(False, f"{label} must be refused")

visible = repr(result) + repr(config)
check(secret not in visible and "secret" not in transport.TransportResult.__dataclass_fields__,
      "transport results and provider state retain no credential")
check(not hasattr(transport, "execute") and not hasattr(transport, "run_command"),
      "HTTP transport exposes no command execution path")

if failures:
    print(f"FAIL: {len(failures)}/{checks}")
    for failure in failures:
        print(" -", failure)
    raise SystemExit(1)
print(f"AI transport: {checks}/{checks} passed")
