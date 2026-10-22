"""Bounded HTTP transport adapters for the AI Assistant.

Transport is intentionally separate from UI state. Credentials are read from the supplied
environment mapping only while constructing one request and are never returned, logged, or
stored. Tool execution is outside this module and remains subject to explicit approval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ai_assistant import ProviderConfig, validate_endpoint

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 512 * 1024
MAX_MODELS = 256


class TransportError(RuntimeError):
    """A user-safe provider failure that contains no request credentials."""


@dataclass(frozen=True)
class TransportResult:
    text: str
    provider_id: str


@dataclass(frozen=True)
class ModelInfo:
    """Bounded public model metadata returned by a provider; never contains credentials."""

    model_id: str
    model_type: str = ""
    state: str = ""
    max_context_length: int | None = None
    tool_use: bool = False


def _endpoint(base: str, suffix: str) -> str:
    normalized = base.rstrip("/")
    if normalized.endswith(suffix):
        return normalized
    return normalized + suffix


def _request_parts(config: ProviderConfig, request_data: dict, environ) -> tuple[str, dict, dict]:
    endpoint = validate_endpoint(config.endpoint, config.allow_insecure_remote)
    messages = request_data.get("messages")
    if not isinstance(messages, list):
        raise TransportError("assistant request has no valid messages")
    context = request_data.get("context")
    if context is not None:
        messages = list(messages) + [{
            "role": "user",
            "content": "Selected Narwhal context (structured JSON):\n" + json.dumps(
                context, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        }]
    key = str(environ.get(config.credential_env, ""))
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.provider_id in ("openai", "openai_compatible"):
        if config.provider_id == "openai" and not key:
            raise TransportError(f"set {config.credential_env} before connecting")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {"model": config.model, "messages": messages, "stream": False}
        return _endpoint(endpoint, "/chat/completions"), headers, body
    if config.provider_id == "anthropic":
        if not key:
            raise TransportError(f"set {config.credential_env} before connecting")
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": config.model, "max_tokens": 4096, "messages": messages}
        return _endpoint(endpoint, "/v1/messages"), headers, body
    raise TransportError("unsupported assistant provider")


def _model_headers(config: ProviderConfig, environ) -> dict[str, str]:
    key = str(environ.get(config.credential_env, ""))
    headers = {"Accept": "application/json"}
    if config.provider_id in ("openai", "openai_compatible"):
        if config.provider_id == "openai" and not key:
            raise TransportError(f"set {config.credential_env} before connecting")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    elif config.provider_id == "anthropic":
        if not key:
            raise TransportError(f"set {config.credential_env} before connecting")
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        raise TransportError("unsupported assistant provider")
    return headers


def _decode_response(response, invalid_message: str):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TransportError("provider response exceeded the size limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(invalid_message) from exc


def discover_models(config: ProviderConfig, environ, opener=urlopen,
                    timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[ModelInfo, ...]:
    """Return bounded chat-capable models from the configured provider's models endpoint."""
    endpoint = validate_endpoint(config.endpoint, config.allow_insecure_remote)
    suffix = "/v1/models" if config.provider_id == "anthropic" else "/models"
    outbound = Request(_endpoint(endpoint, suffix), headers=_model_headers(config, environ),
                       method="GET")
    try:
        response = opener(outbound, timeout=max(0.1, float(timeout)))
        decoded = _decode_response(response, "provider returned invalid model JSON")
    except HTTPError as exc:
        raise TransportError(f"provider models HTTP error {exc.code}") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise TransportError(f"provider connection failed: {type(exc).__name__}") from None
    data = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(data, list):
        raise TransportError("provider returned an invalid models response")
    models = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or len(model_id) > 200 or any(ord(ch) < 32 for ch in model_id):
            continue
        model_type = item.get("type", "")
        model_type = model_type.strip().lower() if isinstance(model_type, str) else ""
        if model_type in ("embedding", "embeddings") or model_id in seen:
            continue
        state = item.get("state", "")
        state = state.strip().lower() if isinstance(state, str) else ""
        context = item.get("max_context_length")
        if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
            context = None
        capabilities = item.get("capabilities", ())
        tool_use = isinstance(capabilities, list) and "tool_use" in capabilities
        models.append(ModelInfo(model_id, model_type, state, context, tool_use))
        seen.add(model_id)
        if len(models) >= MAX_MODELS:
            break
    return tuple(models)


def _response_text(provider_id: str, payload) -> str:
    try:
        if provider_id in ("openai", "openai_compatible"):
            text = payload["choices"][0]["message"]["content"]
        else:
            blocks = payload["content"]
            text = "\n".join(block["text"] for block in blocks
                             if isinstance(block, dict) and block.get("type") == "text")
    except (KeyError, IndexError, TypeError) as exc:
        raise TransportError("provider returned an invalid response shape") from exc
    if not isinstance(text, str) or not text.strip():
        raise TransportError("provider returned no assistant text")
    return text.strip()


def send_chat(config: ProviderConfig, request_data: dict, environ,
              opener=urlopen, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> TransportResult:
    """Send one non-streaming chat request with strict size and timeout bounds."""
    if not config.model:
        raise TransportError("choose a model in AI Assistant Settings")
    url, headers, payload = _request_parts(config, request_data, environ)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise TransportError("assistant request is too large")
    outbound = Request(url, data=encoded, headers=headers, method="POST")
    try:
        response = opener(outbound, timeout=max(0.1, float(timeout)))
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise TransportError(f"provider HTTP error {exc.code}") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise TransportError(f"provider connection failed: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TransportError("provider response exceeded the size limit")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError("provider returned invalid JSON") from exc
    return TransportResult(_response_text(config.provider_id, decoded), config.provider_id)
