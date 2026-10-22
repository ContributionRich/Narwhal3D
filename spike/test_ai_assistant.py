#!/usr/bin/env python3
"""Pure security, provider, transcript, and proposal tests for AI Assistant."""
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

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    if not condition:
        failures.append(f"{label}: {detail}" if detail else label)


check(tuple(pid for pid, _label in ai.PROVIDERS) ==
      ("openai", "anthropic", "openai_compatible"),
      "the provider model exposes OpenAI, Anthropic, then local-compatible")
local = ai.set_provider("openai_compatible", model="local-model")
check(ai.validate_endpoint(local.endpoint) == "http://127.0.0.1:1234/v1",
      "LM Studio defaults to the loopback OpenAI-compatible endpoint")
check(ai.validate_endpoint("http://localhost:1234/v1/") == "http://localhost:1234/v1",
      "loopback HTTP is accepted and normalized")
check(ai.validate_endpoint("https://models.example.test/v1/") ==
      "https://models.example.test/v1", "remote HTTPS is accepted")
for bad in ("http://models.example.test/v1", "ftp://localhost/model",
            "https://key:secret@example.test/v1", "https://example.test/v1?q=secret"):
    try:
        ai.validate_endpoint(bad)
    except ValueError:
        pass
    else:
        check(False, "unsafe or malformed endpoints are refused", bad)
check(ai.validate_endpoint("http://models.example.test/v1", True) ==
      "http://models.example.test/v1", "remote HTTP requires an explicit opt-in")

secret = "sk-this-must-never-appear"
openai = ai.set_provider("openai", model="gpt-test", credential_env="TEST_AI_KEY")
configured = ai.credential_status(openai, {"TEST_AI_KEY": secret})
check(configured.credential_configured and ai.masked_credential(configured) ==
      "Configured (hidden)", "credential lookup records only hidden configured status")
st = ai.AssistantState(provider=configured, status="configured")
public = ai.public_state(st)
visible = repr(st) + repr(configured) + json.dumps(public, sort_keys=True)
check(secret not in visible and public["credential"] == "Configured (hidden)",
      "raw credentials cannot leak through state repr or public diagnostics", visible)
check("api_key" not in configured.__dataclass_fields__ and "secret" not in configured.__dataclass_fields__,
      "provider state has no field in which to persist a raw key")

st.input_text = "  How do I fillet this?  "
request = ai.submit_input(st, {"selection": [{"name": "Curve1", "type": "NurbsCurve"}]})
check(st.input_text == "" and st.messages[-1].text == "How do I fillet this?",
      "submitting moves bounded input into the transcript")
check(request["provider"] == "openai" and request["model"] == "gpt-test"
      and request["messages"] == [{"role": "user", "content": "How do I fillet this?"}],
      "request data is provider-neutral and contains the chat")
check(request["context"]["selection"][0]["name"] == "Curve1" and secret not in repr(request),
      "selected context is explicit and request data stays secret-free")
st = ai.AssistantState(provider=configured, include_selection=False)
st.messages.append(ai.ChatMessage("user", "question"))
check("context" not in ai.build_request(st, {"private": "geometry"}),
      "selection context is omitted when its visible toggle is off")
try:
    ai.build_request(ai.AssistantState(provider=configured), {"blob": "x" * ai.MAX_CONTEXT_CHARS})
except ValueError:
    pass
else:
    check(False, "oversized selected-object context is refused")

bounded = ai.AssistantState()
for number in range(ai.MAX_MESSAGES + 7):
    bounded.add_message("assistant", f"message {number}")
check(len(bounded.messages) == ai.MAX_MESSAGES and bounded.messages[0].text == "message 7",
      "the transcript retains only the newest bounded messages")
long_message = bounded.add_message("status", "x" * (ai.MAX_MESSAGE_CHARS + 4))
check(len(long_message.text) == ai.MAX_MESSAGE_CHARS, "each transcript message is bounded")
bounded.input_text = "x" * (ai.MAX_INPUT_CHARS + 9)
check(len(ai.field_of(bounded).text) == ai.MAX_INPUT_CHARS, "the multiline input is bounded")

payload = json.dumps({
    "type": "command",
    "lines": ["Circle", "0,0,0", "5"],
    "summary": "Create a radius-5 circle at the origin",
})
proposal = ai.parse_tool_proposal(payload, ("Line", "Circle", "Fillet"))
check(proposal.command_name == "Circle" and proposal.lines == ("Circle", "0,0,0", "5"),
      "strict command JSON becomes a reviewable allowlisted proposal")
st = ai.AssistantState(provider=local)
ai.stage_tool_proposal(st, payload, ("Circle",))
check(st.status == "awaiting_approval" and st.pending_proposal == proposal,
      "staging waits for approval and does not execute anything")
ai.reject_tool_proposal(st)
check(st.pending_proposal is None and st.status == "configured",
      "rejecting clears a proposal without running it")

bad_payloads = (
    {"type": "command", "lines": ["Delete"], "summary": "Not in allowlist"},
    {"type": "command", "lines": ["Circle; rm -rf /"], "summary": "shell"},
    {"type": "command", "lines": ["Circle", "$(touch bad)"], "summary": "shell input"},
    {"type": "command", "lines": ["Python"], "summary": "arbitrary code"},
    {"type": "command", "lines": ["Circle 0,0,0 5"], "summary": "macro text"},
    {"type": "url", "lines": ["Circle"], "summary": "wrong tool"},
    {"type": "command", "lines": ["Circle"], "summary": "extra", "execute": True},
)
for raw in bad_payloads:
    try:
        ai.parse_tool_proposal(json.dumps(raw), ("Circle", "Python"))
    except ValueError:
        continue
    check(False, "unsafe, unknown, macro, or extended tool JSON is refused", repr(raw))
try:
    ai.parse_tool_proposal("```json\n{}\n```", ("Circle",))
except ValueError:
    pass
else:
    check(False, "proposals must be JSON data, not model prose or fenced text")

empty = ai.AssistantState(provider=local, input_text="  \n ")
check(ai.submit_input(empty) is None and not empty.messages,
      "blank chat input does not create a request")
empty.add_message("assistant", "hello")
empty.pending_proposal = proposal
empty.status = "awaiting_approval"
empty.new_chat()
check(not empty.messages and empty.pending_proposal is None and empty.status == "configured",
      "New Chat clears transcript and pending actions while retaining configuration")

# The visible right-dock shell has real hit targets and a multiline editor.
layout = ai.panel_layout((0, 0, 179, 700))
check(set(layout) == {"title", "connection", "context", "transcript",
                       "input", "status", "buttons"},
      "the chat body contains connection status, context, transcript, input, and controls")
connection = layout["connection"]
check(ai.panel_hit(layout, (connection[0] + connection[2]) / 2,
                   (connection[1] + connection[3]) / 2) is None,
      "connection status is informative, not dead provider-selection chrome")
context = layout["context"]
check(ai.panel_hit(layout, context[0] + 2, (context[1] + context[3]) / 2)
      == ("context", None), "selected-object context has an explicit clickable toggle")
check([button_id for button_id, _rect in layout["buttons"]] ==
      ["send", "new", "settings"],
      "Send, New Chat, and Settings are three distinct controls")
for button_id, rect in layout["buttons"]:
    check(ai.panel_hit(layout, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
          == ("button", button_id), f"the {button_id} chat control is clickable")
check(ai.transcript_row_count((0, 100, 170, 100)) == 0
      and ai.transcript_row_count((0, 0, 170, 36)) == 2,
      "a collapsed transcript draws zero rows instead of overflowing into controls")

ui_source = open(os.path.join(ROOT, "narwhal3d", "ui.py"), encoding="utf-8").read()
settings_source = ui_source.split("class NARWHAL_OT_ai_settings", 1)[1].split(
    "def _draw_ai_panel", 1)[0]
check("credential_env: StringProperty" in settings_source
      and "api_key: StringProperty" not in settings_source
      and "Raw API keys are never stored" in settings_source,
      "Settings configures an environment-variable name and has no raw-key input")
check("model_choice: EnumProperty" in settings_source
      and 'column.prop(self, "model_choice")' in settings_source
      and 'column.prop(self, "model")' in settings_source,
      "Settings shows discovered models while retaining explicit manual model entry")
check("class NARWHAL_OT_ai_refresh_models" in ui_source
      and "threading.Thread(target=_ai_discovery_worker" in ui_source
      and "ai_transport.discover_models" in ui_source
      and 'bpy.app.timers.register(_ai_poll_model_discovery' in ui_source,
      "model refresh runs through a worker and returns through a Blender timer")
check('"Discovering available models..."' in ui_source
      and '"Model discovery failed:' in ui_source
      and '"Connected, but no chat models were reported."' in ui_source,
      "Settings exposes bounded model loading, empty, and error states")
panel_source = ui_source.split("def _draw_ai_panel", 1)[1].split("def _ai_send", 1)[0]
check("credential_env" not in panel_source and "masked_credential" not in panel_source
      and "Model:" not in panel_source and "Connection:" in panel_source,
      "the chat body shows only connection status; setup details stay in Settings")

measure = lambda value: len(value) * 7.0  # noqa: E731
editor_state = ai.AssistantState(input_text="first line\nsecond line", focus=True)
view = ai.input_view(editor_state, layout["input"], measure)
ai.input_click(editor_state, view, view["x0"] + 7, view["top"] - 7, measure)
ai.input_key(editor_state, "char", "X")
check(editor_state.input_text == "fXirst line\nsecond line",
      "clicking places the chat caret and typing edits at that position",
      repr(editor_state.input_text))
ai.input_key(editor_state, "enter")
check("\n" in editor_state.input_text, "Enter adds a chat newline rather than sending")
check(ai.input_key(editor_state, "submit") == "submit",
      "Ctrl+Enter is represented by an explicit Send request")
ai.input_release(editor_state)
check(not editor_state.extra.get("dragging"), "input release ends chat drag-selection")

if failures:
    print(f"FAIL: {len(failures)}/{checks}")
    for failure in failures:
        print(" -", failure)
    raise SystemExit(1)
print(f"AI assistant model: {checks}/{checks} passed")
