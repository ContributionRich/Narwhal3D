"""Pure state and security boundary for Narwhal 3D's AI Assistant.

This module deliberately performs no network requests and executes no commands. It builds
bounded provider-neutral request data and turns a narrowly-shaped JSON object into a
reviewable :class:`ToolProposal`. UI/transport code must still obtain explicit user
approval and send approved lines through the normal command-line state machine.

Raw API keys never enter these dataclasses. Provider adapters may read the named
environment variable at send time, but persistent, document, report, and debug state sees
only whether a credential is configured.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from . import textfield

PROVIDERS = (
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("openai_compatible", "OpenAI-compatible / LM Studio"),
)
_PROVIDER_IDS = frozenset(provider_id for provider_id, _label in PROVIDERS)
DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openai_compatible": "http://127.0.0.1:1234/v1",
}
DEFAULT_CREDENTIAL_ENVS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible": "LM_STUDIO_API_KEY",
}

MAX_INPUT_CHARS = 8_000
MAX_MESSAGE_CHARS = 16_000
MAX_MESSAGES = 100
MAX_CONTEXT_CHARS = 24_000
MAX_TOOL_LINES = 32
MAX_TOOL_LINE_CHARS = 256
MAX_TOOL_SUMMARY_CHARS = 512

STATUSES = frozenset((
    "disconnected", "configured", "connected", "sending", "error", "awaiting_approval",
))
_FORBIDDEN_COMMANDS = frozenset((
    "python", "exec", "eval", "runscript", "runpythonscript", "shell", "system",
))
_SHELL_MARKERS = (";", "&&", "||", "`", "$(", "${", "\x00")


@dataclass(frozen=True)
class ProviderConfig:
    """Public provider settings. Contains credential *status*, never a credential."""

    provider_id: str = "openai_compatible"
    model: str = ""
    endpoint: str = DEFAULT_ENDPOINTS["openai_compatible"]
    credential_env: str = DEFAULT_CREDENTIAL_ENVS["openai_compatible"]
    credential_configured: bool = False
    allow_insecure_remote: bool = False

    def __post_init__(self):
        if self.provider_id not in _PROVIDER_IDS:
            raise ValueError(f"unsupported AI provider: {self.provider_id}")
        if len(self.model) > 200 or any(ord(ch) < 32 for ch in self.model):
            raise ValueError("invalid AI model name")
        if not self.credential_env or not self.credential_env.replace("_", "A").isalnum():
            raise ValueError("credential_env must be an environment-variable name")


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str

    def __post_init__(self):
        if self.role not in ("user", "assistant", "status"):
            raise ValueError(f"invalid chat role: {self.role}")
        clean = _clean_text(self.text, MAX_MESSAGE_CHARS)
        object.__setattr__(self, "text", clean)


@dataclass(frozen=True)
class ToolProposal:
    """Validated but deliberately non-executable command-line proposal."""

    lines: tuple[str, ...]
    summary: str
    command_name: str


@dataclass
class AssistantState:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    messages: list[ChatMessage] = field(default_factory=list)
    input_text: str = ""
    focus: bool = False
    include_selection: bool = True
    status: str = "disconnected"
    error: str = ""
    pending_proposal: ToolProposal | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"invalid assistant status: {self.status}")
        self.input_text = _clean_text(self.input_text, MAX_INPUT_CHARS)
        self.messages = list(self.messages[-MAX_MESSAGES:])

    def add_message(self, role: str, text: str) -> ChatMessage:
        message = ChatMessage(role, text)
        self.messages.append(message)
        if len(self.messages) > MAX_MESSAGES:
            del self.messages[:-MAX_MESSAGES]
        return message

    def new_chat(self):
        self.messages.clear()
        self.input_text = ""
        self.error = ""
        self.pending_proposal = None
        self.status = "configured" if self.provider.model else "disconnected"
        field_of(self).set_text("")


def _clean_text(value: object, limit: int) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    return text[:limit]


def field_of(state: AssistantState) -> textfield.TextField:
    """Return the bounded multiline editor backing ``state.input_text``."""
    editor = state.extra.get("field")
    if editor is None:
        editor = textfield.TextField(state.input_text, multiline=True,
                                     max_chars=MAX_INPUT_CHARS)
        state.extra["field"] = editor
    elif editor.text != state.input_text:
        editor.set_text(state.input_text)
    return editor


def set_provider(provider_id: str, model: str = "", endpoint: str | None = None,
                 credential_env: str | None = None, credential_configured: bool = False,
                 allow_insecure_remote: bool = False) -> ProviderConfig:
    """Construct checked public settings without accepting a raw key argument."""
    if provider_id not in _PROVIDER_IDS:
        raise ValueError(f"unsupported AI provider: {provider_id}")
    config = ProviderConfig(
        provider_id=provider_id,
        model=str(model).strip(),
        endpoint=endpoint or DEFAULT_ENDPOINTS[provider_id],
        credential_env=credential_env or DEFAULT_CREDENTIAL_ENVS[provider_id],
        credential_configured=bool(credential_configured),
        allow_insecure_remote=bool(allow_insecure_remote),
    )
    validate_endpoint(config.endpoint, config.allow_insecure_remote)
    return config


def credential_status(config: ProviderConfig, environ: Mapping[str, str]) -> ProviderConfig:
    """Return config with a boolean environment-key status; never retain its value."""
    configured = bool(environ.get(config.credential_env, ""))
    return ProviderConfig(
        provider_id=config.provider_id,
        model=config.model,
        endpoint=config.endpoint,
        credential_env=config.credential_env,
        credential_configured=configured,
        allow_insecure_remote=config.allow_insecure_remote,
    )


def masked_credential(config: ProviderConfig) -> str:
    return "Configured (hidden)" if config.credential_configured else "Not configured"


def _is_loopback(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_endpoint(endpoint: str, allow_insecure_remote: bool = False) -> str:
    """Validate and normalize an HTTP(S) provider endpoint.

    Plain HTTP is accepted for loopback model servers. Sending over plain HTTP to a
    non-loopback host requires an explicit opt-in because prompts, selection context, and
    credentials would otherwise cross the network unencrypted.
    """
    value = str(endpoint).strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("AI endpoint must be an http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("AI endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("AI endpoint must not contain a query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AI endpoint has an invalid port") from exc
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname) \
            and not allow_insecure_remote:
        raise ValueError("remote AI endpoints require HTTPS or explicit insecure opt-in")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port is not None else "")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _bounded_context(context: object) -> Any:
    if context is None:
        return None
    try:
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"),
                             sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("selected-object context must be JSON-compatible") from exc
    if len(encoded) > MAX_CONTEXT_CHARS:
        raise ValueError(f"selected-object context exceeds {MAX_CONTEXT_CHARS} characters")
    return json.loads(encoded)


def build_request(state: AssistantState, selected_context: object = None) -> dict[str, Any]:
    """Build secret-free provider-neutral data for a future transport adapter."""
    endpoint = validate_endpoint(state.provider.endpoint,
                                 state.provider.allow_insecure_remote)
    messages = [{"role": item.role, "content": item.text}
                for item in state.messages if item.role in ("user", "assistant")]
    request = {
        "provider": state.provider.provider_id,
        "model": state.provider.model,
        "endpoint": endpoint,
        "messages": messages,
    }
    if state.include_selection:
        context = _bounded_context(selected_context)
        if context is not None:
            request["context"] = context
    return request


def submit_input(state: AssistantState, selected_context: object = None) -> dict[str, Any] | None:
    """Move non-empty input into chat and return request data; does not send it."""
    text = _clean_text(state.input_text, MAX_INPUT_CHARS).strip()
    if not text:
        return None
    state.add_message("user", text)
    state.input_text = ""
    field_of(state).set_text("")
    state.status = "configured"
    state.error = ""
    return build_request(state, selected_context)


def _checked_line(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("every tool line must be text")
    line = value.strip()
    if not line or len(line) > MAX_TOOL_LINE_CHARS or "\n" in line or "\r" in line:
        raise ValueError("tool line is empty, multiline, or too long")
    if any(marker in line for marker in _SHELL_MARKERS):
        raise ValueError("shell syntax is not allowed in tool proposals")
    return line


def parse_tool_proposal(payload: str, command_names: Iterable[str]) -> ToolProposal:
    """Parse strict JSON into a proposal after validating its first command token.

    ``command_names`` must be the caller's explicit safe command allowlist. Remaining
    lines are command responses and are still bounded/shell-free, but are not interpreted
    here. This function never imports the registry and never executes anything.
    """
    try:
        raw = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("tool proposal must be one JSON object") from exc
    if not isinstance(raw, dict) or set(raw) != {"type", "lines", "summary"}:
        raise ValueError("tool proposal must contain only type, lines, and summary")
    if raw["type"] != "command":
        raise ValueError("unsupported tool proposal type")
    lines_value = raw["lines"]
    if not isinstance(lines_value, list) or not (1 <= len(lines_value) <= MAX_TOOL_LINES):
        raise ValueError(f"tool proposal needs 1..{MAX_TOOL_LINES} lines")
    lines = tuple(_checked_line(value) for value in lines_value)
    if any(ch.isspace() for ch in lines[0]):
        raise ValueError("the first tool line must be one command name")
    allowed = {str(name).lower(): str(name) for name in command_names}
    command = allowed.get(lines[0].lower())
    if command is None or command.lower() in _FORBIDDEN_COMMANDS:
        raise ValueError(f"command is not allowlisted: {lines[0]}")
    if not isinstance(raw["summary"], str):
        raise ValueError("tool proposal needs a text summary")
    summary = _clean_text(raw["summary"], MAX_TOOL_SUMMARY_CHARS).strip()
    if not summary:
        raise ValueError("tool proposal needs a text summary")
    return ToolProposal(lines=lines, summary=summary, command_name=command)


def stage_tool_proposal(state: AssistantState, payload: str,
                        command_names: Iterable[str]) -> ToolProposal:
    """Store a validated proposal for UI review without executing it."""
    proposal = parse_tool_proposal(payload, command_names)
    state.pending_proposal = proposal
    state.status = "awaiting_approval"
    return proposal


def reject_tool_proposal(state: AssistantState):
    state.pending_proposal = None
    state.status = "configured" if state.provider.model else "disconnected"


def public_state(state: AssistantState) -> dict[str, Any]:
    """Serializable diagnostics safe for logs/reports/documents."""
    return {
        "provider": state.provider.provider_id,
        "model": state.provider.model,
        "endpoint": validate_endpoint(state.provider.endpoint,
                                      state.provider.allow_insecure_remote),
        "credential": masked_credential(state.provider),
        "message_count": len(state.messages),
        "include_selection": state.include_selection,
        "status": state.status,
        "has_pending_proposal": state.pending_proposal is not None,
    }


# ---------------------------------------------------------------------------
# Pure right-dock layout and multiline input behavior
# ---------------------------------------------------------------------------
BOX_PAD = 4
LINE_H = 14


def panel_layout(body: tuple[float, float, float, float], scale: float = 1.0):
    """Responsive boxes for the visible Assistant shell, in region coordinates."""
    x0, y0, x1, y1 = body
    s = max(0.5, float(scale))
    pad, gap = 6 * s, 4 * s
    left, right = x0 + pad, max(x0 + pad, x1 - pad)
    top = y1 - pad
    title = (left, top - 18 * s, right, top)
    top = title[1] - gap
    connection = (left, top - 20 * s, right, top)
    top = connection[1] - gap
    context = (left, top - 18 * s, right, top)
    top = context[1] - gap

    button_h = 24 * s
    buttons_y0 = y0 + pad
    width = max(0.0, right - left)
    button_w = max(0.0, (width - 2 * gap) / 3)
    buttons = [
        ("send", (left, buttons_y0, left + button_w, buttons_y0 + button_h)),
        ("new", (left + button_w + gap, buttons_y0,
                 left + 2 * button_w + gap, buttons_y0 + button_h)),
        ("settings", (left + 2 * (button_w + gap), buttons_y0,
                      right, buttons_y0 + button_h)),
    ]
    status = (left, buttons_y0 + button_h + gap, right,
              buttons_y0 + button_h + gap + 30 * s)
    input_box = (left, status[3] + gap, right, status[3] + gap + 72 * s)
    transcript = (left, input_box[3] + gap, right, max(input_box[3] + gap, top))
    return {"title": title, "connection": connection, "context": context,
            "transcript": transcript, "input": input_box,
            "status": status, "buttons": buttons}


def _inside(rect, x: float, y: float) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def panel_hit(layout, x: float, y: float):
    if _inside(layout["context"], x, y):
        return "context", None
    if _inside(layout["input"], x, y):
        return "input", None
    for button_id, rect in layout.get("buttons", ()):
        if _inside(rect, x, y):
            return "button", button_id
    return None


def transcript_row_count(box, scale: float = 1.0) -> int:
    """Number of complete text rows wholly inside a transcript rectangle."""
    s = max(0.5, float(scale))
    height = max(0.0, box[3] - box[1] - 8 * s)
    return max(0, int(height // (LINE_H * s)))


def input_view(state: AssistantState, box, measure, scale: float = 1.0):
    """Wrapped input geometry shared by drawing and caret hit-testing."""
    s = max(0.5, float(scale))
    editor = field_of(state)
    width = max(1.0, box[2] - box[0] - 2 * BOX_PAD * s)
    spans = textfield.wrap_spans(editor.text, width, measure)
    line_h = LINE_H * s
    rows = max(1, int(max(0.0, box[3] - box[1] - 2 * BOX_PAD * s) // line_h))
    _caret_x, caret_line = textfield.caret_xy(spans, editor.text, editor.caret,
                                             measure, line_h)
    first = max(0, min(caret_line, len(spans) - rows))
    return {"spans": spans, "first": first, "rows": rows, "line_h": line_h,
            "x0": box[0] + BOX_PAD * s, "top": box[3] - BOX_PAD * s,
            "width": width}


def input_index_at(state: AssistantState, view, x: float, y: float, measure) -> int:
    editor = field_of(state)
    line = view["first"] + int((view["top"] - y) // view["line_h"])
    return textfield.index_at(view["spans"], editor.text, x - view["x0"], line,
                              measure)


def input_click(state: AssistantState, view, x: float, y: float, measure,
                shift: bool = False):
    state.focus = True
    editor = field_of(state)
    editor.set_caret(input_index_at(state, view, x, y, measure), select=shift)
    state.extra["dragging"] = True
    return "focus"


def input_drag(state: AssistantState, view, x: float, y: float, measure) -> bool:
    if not state.focus or not state.extra.get("dragging"):
        return False
    editor = field_of(state)
    before = editor.anchor, editor.caret
    editor.set_caret(input_index_at(state, view, x, y, measure), select=True)
    return before != (editor.anchor, editor.caret)


def input_release(state: AssistantState):
    state.extra.pop("dragging", None)


def input_key(state: AssistantState, kind: str, char: str = "", shift: bool = False,
              measure=None, width: float | None = None):
    """Edit focused chat input; Ctrl+Enter is represented by ``kind='submit'``."""
    if not state.focus:
        return None
    if kind == "esc":
        state.focus = False
        return "blur"
    if kind == "submit":
        return "submit"
    editor = field_of(state)
    result = "edited"
    if kind == "back":
        editor.backspace()
    elif kind == "delete":
        editor.delete()
    elif kind == "select_all":
        editor.select_all()
        result = "moved"
    elif kind in ("copy", "cut"):
        state.extra["clipboard"] = (editor.cut() if kind == "cut"
                                    else editor.selected_text())
        result = "edited" if kind == "cut" else "copy"
    elif kind in ("left", "right", "home", "end", "up", "down"):
        result = "moved"
        if kind == "left":
            editor.move(-1, select=shift)
        elif kind == "right":
            editor.move(1, select=shift)
        elif kind == "home":
            editor.home(select=shift)
        elif kind == "end":
            editor.end(select=shift)
        elif measure is not None and width is not None:
            spans = textfield.wrap_spans(editor.text, width, measure)
            x_pos, line = textfield.caret_xy(spans, editor.text, editor.caret,
                                             measure, 1)
            target = line + (-1 if kind == "up" else 1)
            if 0 <= target < len(spans):
                editor.set_caret(textfield.index_at(spans, editor.text, x_pos,
                                                    target, measure), select=shift)
    else:
        addition = {"char": char, "enter": "\n", "paste": char}.get(kind)
        if addition is None:
            return None
        editor.insert(addition)
    state.input_text = editor.text
    return result
