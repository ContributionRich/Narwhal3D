"""Pure state and security boundary for Narwhal 3D's AI Assistant.

This module deliberately performs no network requests and executes no commands. It builds
bounded provider-neutral request data and turns a narrowly-shaped JSON object into a
reviewable :class:`ToolProposal`. Legacy string proposals are compatibility data,
not executable commands. The chat uses WorkflowSession; only separately approved
Box/Cube creation is enabled. Other primitive publishers remain disabled prototypes.

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
    ("openai", "OpenAI Chat Completions"),
    ("anthropic", "Anthropic"),
    ("openai_compatible", "OpenAI-compatible / LM Studio"),
    ("openai_responses", "OpenAI Responses"),
)
_PROVIDER_IDS = frozenset(provider_id for provider_id, _label in PROVIDERS)
DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openai_compatible": "http://127.0.0.1:1234/v1",
}
DEFAULT_CREDENTIAL_ENVS = {
    "openai": "OPENAI_API_KEY",
    "openai_responses": "OPENAI_API_KEY",
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
        workflow = self.extra.get("workflow")
        if workflow is not None:
            workflow.invalidate("new_chat")
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


def metadata_permission(config: ProviderConfig) -> dict:
    """Remember the data recipient, not a model/document or any credentials."""
    return {'version': 1, 'provider_id': config.provider_id,
            'endpoint': validate_endpoint(config.endpoint, config.allow_insecure_remote),
            'credential_env': config.credential_env,
            'allow_insecure_remote': config.allow_insecure_remote}


def connection_label(state: AssistantState) -> str:
    """HTTP chat has no persistent connection; report configuration/last outcome."""
    if state.status == 'sending':
        return 'Sending...'
    if state.status == 'awaiting_approval':
        return 'Awaiting your approval'
    if state.status == 'awaiting_review':
        return 'Awaiting your review'
    if state.status == 'error':
        return 'Request failed'
    key = (metadata_permission(state.provider), state.provider.model)
    if state.extra.get('last_successful_provider') == key:
        return 'Ready · Last request succeeded'
    return 'Ready to send' if state.provider.model else 'Choose a model in Settings'


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
BOX_PAD = 10
LINE_H = 14


def panel_layout(body: tuple[float, float, float, float], scale: float = 1.0):
    """Responsive boxes for the visible Assistant shell, in region coordinates."""
    x0, y0, x1, y1 = body
    s = max(0.5, float(scale))
    pad, gap = min(10 * s, max(0, x1 - x0) / 2), 7 * s
    left, right = x0 + pad, max(x0 + pad, x1 - pad)
    height = max(0, y1 - y0)
    top_pad, bottom_pad = min(9 * s, height / 2), min(11 * s, height / 2)
    available = max(0, height - top_pad - bottom_pad)
    heights = {'title': 23*s, 'connection': 27*s, 'context': 0,
               'transcript': 0, 'input': 86*s, 'status': 29*s, 'buttons': 32*s}
    # The owner requested automatic selection context, not an opt-in checkbox.
    # Retain a collapsed compatibility box for older UI probe clients.
    active = [key for key in heights if key != 'context']

    def required():
        return sum(heights.values()) + max(0, len(active) - 1) * gap

    # OURS compact fallback: shrink the composer before hiding informational rows.
    # Draw and hit-testing share these zero-height boxes; controls never overlap.
    heights['input'] -= min(50*s, max(0, required() - available))
    for key in ('title', 'connection', 'status', 'input'):
        if required() <= available:
            break
        heights[key] = 0
        active.remove(key)
    if required() > available:
        active.remove('transcript')
        heights['buttons'] = min(32*s, available)
    heights['transcript'] = max(0, available - required())
    result = {}
    top = y1 - top_pad
    for key in heights:
        bottom = top - heights[key]
        result[key] = (left, bottom, right, top)
        if key in active:
            top = bottom - (gap if key != active[-1] else 0)
    button_box = result['buttons']
    width = max(0.0, right - left)
    button_gap = min(6*s, width / 2)
    button_w = max(0.0, (width - 2 * button_gap) / 3)
    result['buttons'] = [(name, (left + i*(button_w+button_gap), button_box[1],
                                left + i*(button_w+button_gap)+button_w, button_box[3]))
                         for i, name in enumerate(('send', 'new', 'settings'))]
    return result


def _inside(rect, x: float, y: float) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def panel_hit(layout, x: float, y: float):
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


class WorkflowUnavailable(RuntimeError):
    """New workflow fails closed when its reviewed native backend is unavailable."""


def _load_workflow_library(library_path=None):
    """Trusted installed-path resolver shared by conversation and read-only adapters."""
    import ctypes
    import pathlib
    import platform
    import sys

    if library_path is None:
        root = pathlib.Path(__file__).resolve().parents[1]
        system = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform)
        arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
        if system is None:
            raise WorkflowUnavailable("workflow_platform_not_validated")
        name = {"macos": "libnarwhal_automation.dylib", "linux": "libnarwhal_automation.so",
                "windows": "narwhal_automation.dll"}[system]
        library_path = next((p for p in (
            root / "runtime" / f"{system}-{arch}" / "lib" / name,
            root / "runtime" / f"{system}-{arch}" / "bin" / name,
            root / "artifacts" / "automation" / f"{system}-{arch}" / name) if p.is_file()), None)
    if library_path is None:
        raise WorkflowUnavailable("native_workflow_not_installed")
    try:
        lib = ctypes.CDLL(str(library_path))
        lib.narwhal_workflow_abi.restype = ctypes.c_uint32
        if lib.narwhal_workflow_abi() != 1:
            raise ValueError
        call = lib.narwhal_workflow_json
        call.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        call.restype = ctypes.c_ssize_t
    except (OSError, AttributeError, ValueError):
        raise WorkflowUnavailable("native_workflow_unavailable") from None
    return lib


def workspace_layer_flags(layers, targets, *, library_path=None):
    """Read-only native calculation; no conversation, consent or document state created."""
    import ctypes
    WorkflowSession._main_thread()
    if len(layers) > 512 or len(targets) > 32:
        raise WorkflowUnavailable("layer_snapshot_limit")
    raw = json.dumps({"op": "workspace_layers", "layers": layers, "targets": targets},
                     ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > 131072:
        raise WorkflowUnavailable("layer_snapshot_limit")
    lib = _load_workflow_library(library_path)
    output = ctypes.create_string_buffer(524288)
    size = lib.narwhal_workflow_json(0, raw, len(raw), output, len(output))
    if not 0 < size <= len(output):
        raise WorkflowUnavailable("native_layer_flags_unavailable")
    result = json.loads(output.raw[:size])
    if result.get("status") != "layers":
        raise WorkflowUnavailable("native_layer_flags_unavailable")
    return result["flags"]


class WorkflowSession:
    """Thin main-thread orchestration around Rust-owned conversation policy.

    The existing chat wires lifetime/consent and polls finish() on the main thread.
    library_path is a trusted test/host injection, never a model argument. Legacy
    text-chat APIs remain for compatibility, not as a native-unavailable fallback.
    """

    def __init__(self, state, document_context, *, allow_reads=False, library_path=None):
        import threading

        self._main_thread()
        if state.extra.get("workflow") is not None:
            raise WorkflowUnavailable("workflow_already_attached")
        self._state = state
        self._document_context = self._context_id(document_context)
        self._allow_reads = bool(allow_reads)
        self._cancel_event = threading.Event()
        self._handle = 0
        self._disabled = False
        self._lib = _load_workflow_library(library_path)
        created = self._exchange({"op": "create", "binding": self._binding()})
        self._handle = created["handle"]
        state.extra["workflow"] = self

    @staticmethod
    def _context_id(value):
        if type(value) is not str or not value or len(value) > 256:
            raise WorkflowUnavailable("document_context_identity_required")
        return value

    @staticmethod
    def _main_thread():
        import threading
        if threading.current_thread() is not threading.main_thread():
            raise WorkflowUnavailable("workflow_requires_main_thread")

    def _binding(self, document_context=...):
        context = self._document_context if document_context is ... else self._context_id(document_context)
        config = self._state.provider
        return {"config": {"provider_id": config.provider_id, "model": config.model,
                           "endpoint": validate_endpoint(config.endpoint, config.allow_insecure_remote),
                           "credential_env": config.credential_env,
                           "allow_insecure_remote": config.allow_insecure_remote,
                           "include_selection": self._state.include_selection},
                "document_context": context,
                "allow_reads": self._allow_reads and context == self._document_context}

    def _exchange(self, data):
        import ctypes
        self._main_thread()
        if self._disabled and data.get("op") != "close":
            raise WorkflowUnavailable("workflow_disabled")
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > 1_048_576:
            raise WorkflowUnavailable("workflow_input_limit")
        output = ctypes.create_string_buffer(524_288)
        size = self._lib.narwhal_workflow_json(self._handle, raw, len(raw), output, len(output))
        if size < 0 or size > len(output):
            raise WorkflowUnavailable("workflow_backend_failed")
        result = json.loads(output.raw[:size])
        if result.get("status") == "failed" and "token" not in result:
            error = WorkflowUnavailable(result.get("code", "workflow_backend_failed"))
            error.diagnostic = result.get("diagnostic", {"category": "local_rejection", "automatic_retry": False})
            raise error
        return result

    def prepare(self, document_context, *, user_text=None, context=None,
                timeout_ms=30_000, max_tokens=4096, continuation=False):
        from .ai_transport import PreparedTurn
        from dataclasses import replace
        import threading

        self._main_thread()
        context_id = self._context_id(document_context)
        if context_id != self._document_context:
            self._allow_reads = False
        self._document_context = context_id
        if getattr(self, "_active_binding", None) != self._binding():
            self._cancel_event.set()
        text = None if continuation else (self._state.input_text if user_text is None else user_text)
        if not self._state.include_selection:
            context = None
        ready = self._exchange({"op": "begin", "binding": self._binding(), "text": text,
                                "context": context, "timeout_ms": timeout_ms, "max_tokens": max_tokens})
        self._cancel_event = threading.Event()
        self._active_binding = ready["binding"]
        if text is not None:
            self._state.add_message("user", text)
            self._state.input_text = ""
            field_of(self._state).set_text("")
        self._state.status = "sending"
        return PreparedTurn(replace(self._state.provider), ready, self._cancel_event)

    def finish(self, receipt, document_context):
        """Only this main-thread gate may publish a worker's answer into the chat."""
        self._main_thread()
        if self._disabled or not self._handle:
            return {"status": "stale"}
        completion = {"op": "complete", "token": receipt.token,
                      "binding": self._binding(document_context), "raw": receipt.raw, "failure": receipt.failure}
        try:
            result = self._exchange(completion)
        except WorkflowUnavailable as exc:
            if str(exc) != "workflow_input_limit":
                raise
            # Retire the matching pending request even when its envelope cannot
            # cross the ABI; never leave the conversation permanently pending.
            completion.update(raw=None, failure="response_too_large")
            result = self._exchange(completion)
        status = result["status"]
        if status == "stale":
            return result
        if status in ("completed", "tool_calls"):
            # Rust owns display bounds/notice; older libraries retain their
            # original envelope. Python only routes native projection to UI rows.
            view = result.get("presentation")
            text = view["text"] if view is not None else result["response"]["text"]
            if text:
                self._state.add_message("assistant", text)
            if view is not None and view["notice"]:
                self._state.add_message("status", view["notice"])
            self._state.status = "connected"
            self._state.extra['last_successful_provider'] = (
                metadata_permission(self._state.provider), self._state.provider.model)
            self._state.error = ""
        else:
            self._state.status = "error"
            self._state.error = result.get("code", status)
        return result

    def poll_timeout(self):
        """Main's timer can expire logical work even while urllib is still blocked."""
        result = self._exchange({"op": "poll"})
        if result["status"] == "timeout":
            self._cancel_event.set()
            self._state.status = "error"
            self._state.error = "timeout"
        return result

    def record_results(self, token, results, document_context):
        self._main_thread()
        return self._exchange({"op": "results", "token": token,
                               "binding": self._binding(document_context), "results": results})

    def public_workflow_state(self):
        """Bounded Rust counters/categories, not a persistent HTTP connection claim."""
        return self._exchange({"op": "state"})

    def stage_primitive_prototype(self, owner, document_context, arguments, context):
        """Development-only gate; not registered in any live tool catalog."""
        return self._exchange({'op':'stage_primitive_prototype','owner':owner,
            'binding':self._binding(document_context),'arguments':arguments,'context':context})

    def decide_primitive_prototype(self, proposal, document_context, current, *, accepted=False):
        return self._exchange({'op':'decide_primitive_prototype','proposal':proposal,
            'binding':self._binding(document_context),'current':current,'accepted':accepted})

    def check_primitive_geometry(self, arguments, facts):
        return self._exchange({'op':'check_primitive_geometry','arguments':arguments,'facts':facts})

    def take_primitive_prototype(self, proposal, document_context, current):
        return self._exchange({'op':'take_primitive_prototype','proposal':proposal,
            'binding':self._binding(document_context),'current':current})

    def _accepted_review_exchange(self, data):
        """Trusted-host adapter; never a provider tool or automatic approval."""
        try:
            return self._exchange(data)
        except WorkflowUnavailable as exc:
            if str(exc) == 'invalid_request':
                raise WorkflowUnavailable('accepted_review_unavailable') from exc
            raise

    def stage_accepted_review(self, owner, call_id, document_context, context):
        return self._accepted_review_exchange({'op': 'stage_accepted_review',
            'owner': owner, 'call_id': call_id, 'binding': self._binding(document_context),
            'context': context})

    def accepted_review_state(self, owner, call_id, document_context):
        return self._accepted_review_exchange({'op': 'accepted_review_state',
            'owner': owner, 'call_id': call_id, 'binding': self._binding(document_context)})

    def decide_accepted_review(self, token, action, document_context, current, *, accepted=False):
        """A separate human decision; metadata consent is not action approval."""
        return self._accepted_review_exchange({'op': 'decide_accepted_review',
            'token': token, 'action': action, 'binding': self._binding(document_context),
            'current': current, 'allow': accepted})

    def consume_accepted_review(self, token, document_context, current):
        return self._accepted_review_exchange({'op': 'consume_accepted_review',
            'token': token, 'binding': self._binding(document_context), 'current': current})

    def record_review_publication(self, token, outcome, document_context):
        """Actual publisher callback only, not provider/read-tool supplied facts."""
        return self._accepted_review_exchange({'op': 'record_review_publication',
            'token': token, 'binding': self._binding(document_context), 'outcome': outcome})

    def accepted_review_result(self, owner, call_id, document_context):
        return self._accepted_review_exchange({'op': 'accepted_review_result',
            'owner': owner, 'call_id': call_id, 'binding': self._binding(document_context)})

    def validate_plan(self, plan):
        """Native schema validation only; never approval, execution or verification."""
        return self._exchange({"op": "validate_plan", "plan": plan})

    def operation_catalog(self):
        return self._exchange({"op": "catalog"})

    def read_tool_result(self, token, call_id, reader, document_context):
        """Resolve an accepted call in Rust, then read the existing main-thread API.

        reader is Main's trusted ApplicationReader, never supplied by model data.
        No operation/proposal can be dispatched through this read-only method.
        """
        from .automation_tools import ReadError, SAFE_ERRORS
        self._main_thread()
        plan = self._exchange({"op": "resolve_call", "token": token, "binding": self._binding(document_context), "call_id": call_id})
        if plan["kind"] == "proposal_only":
            raise WorkflowUnavailable("proposal_requires_review")
        if plan['kind'] == 'box_proposal':
            raise WorkflowUnavailable('box_requires_review')
        try:
            if plan["kind"] == "result":
                output = plan["output"]
            else:
                output = reader.read(plan["tool"], plan["arguments"])
            return {"call_id": call_id, "output": output, "is_error": False}
        except ReadError as exc:
            code = exc.args[0] if exc.args else "read_failed"
            return {"call_id": call_id, "output": {"code": code if code in SAFE_ERRORS else "read_failed"}, "is_error": True}

    def propose_box(self, token, call_id, document_context, context):
        self._main_thread()
        return self._exchange({'op': 'propose_box', 'token': token,
            'binding': self._binding(document_context), 'call_id': call_id, 'context': context})

    def decide_box(self, proposal, document_context, current, *, accepted=False):
        self._main_thread()
        return self._exchange({'op': 'decide_box', 'binding': self._binding(document_context),
            'proposal': proposal, 'current': current, 'accepted': accepted})

    def propose_circle(self, token, call_id, document_context, *, expected=None):
        self._main_thread()
        return self._exchange({"op": "propose_circle", "token": token, "binding": self._binding(document_context),
                               "call_id": call_id, "expected": expected})

    def review_proposal(self, proposal, document_context, *, current=None, accepted=False):
        """Main's explicit review decision only; never driven by provider content."""
        self._main_thread()
        return self._exchange({"op": "review_proposal", "binding": self._binding(document_context),
                               "proposal": proposal, "current": current, "accepted": accepted})

    def check_proposal(self, proposal_id, document_context, *, current=None):
        """Validate review/preconditions but ALWAYS refuse production dispatch."""
        self._main_thread()
        return self._exchange({"op": "check_proposal", "binding": self._binding(document_context),
                               "id": proposal_id, "current": current})

    def invalidate(self, reason="cancelled"):
        self._main_thread()
        self._cancel_event.set()
        if reason == "document_replaced":
            self._allow_reads = False
        try:
            result = self._exchange({"op": "cancel"} if reason == "cancelled" else
                                    {"op": "reset", "binding": self._binding()})
        except (WorkflowUnavailable, ValueError):
            self._disabled = True
            result = {"status": "cancelled"}
        result["physical_http_cancelled"] = False
        self._state.status = "configured" if self._state.provider.model else "disconnected"
        return result

    def close(self):
        self._main_thread()
        self._cancel_event.set()
        if self._handle:
            try:
                self._exchange({"op": "close"})
            finally:
                self._handle = 0
                if self._state.extra.get("workflow") is self:
                    self._state.extra.pop("workflow")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
