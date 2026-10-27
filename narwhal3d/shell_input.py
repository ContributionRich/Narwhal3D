"""Blender event adapter for the native single-line command console.

MEASURED: v4's #command is an HTML input; submission/completion/history are
separate key handlers. OURS: this adapter delegates editing to TextField.
Python is intentional UI glue over passed Blender events and the existing
pure text model; no bpy import, global keymap, focus ownership or drawing.

Call ``handle(event, app_text, clipboard)`` early in modal, AFTER main's focus
and capture gates. In particular main must gate Ctrl+A/C/X/V to the field so
unfocused document copy/paste/select-all keep working. None means unhandled;
otherwise assign result['text'] to the app and consume the event. A 'clipboard'
key means write that string to the clipboard; its absence means leave it alone.
Request redraw even when only the caret/selection changed.

``sync(app_text)`` returns the persistent TextField for drawing/click placement:
``field.caret`` is a Python string index and ``field.selection()`` is (lo, hi)
or None. Main owns mouse placement, focus, clipping and caret visibility.
Echo returned text back before the next call. An external text change resets
the caret to the end and clears selection, once; identical text preserves both.
Identical external assignments are indistinguishable from no change.
"""
from .textfield import TextField


_field = TextField(multiline=False)
_last_text = ""


def sync(text: str) -> TextField:
    """Synchronize model text once per change; return the live editing field."""
    global _last_text
    if text != _last_text:
        _field.set_text(text)
        _last_text = text
    return _field


def handle(event, text: str, clipboard: str):
    """Return None or {'text': str, 'consumed': True, ['clipboard': str]}.

    Only PRESS events edit (including Blender repeat presses). Ctrl+A/C/X/V
    are the only Ctrl combinations handled. Alt/OS-key shortcuts pass through.
    Unicode is preferred; ASCII is used only when Unicode is absent/empty.
    Paste uses TextField's normalization: CRLF/CR/LF and tabs become spaces,
    other nonprintable controls are removed. Empty copy/cut keeps the clipboard.
    """
    global _last_text
    field = sync(text)
    if getattr(event, "value", None) != "PRESS":
        return None
    key = getattr(event, "type", "")
    if key in {"RET", "NUMPAD_ENTER", "TAB", "ESC", "UP_ARROW", "DOWN_ARROW"}:
        return None
    if getattr(event, "alt", False) or getattr(event, "oskey", False):
        return None

    result = {"consumed": True}
    if getattr(event, "ctrl", False):
        if key == "A":
            field.select_all()
        elif key in {"C", "X"}:
            selected = field.selected_text()
            if selected:
                result["clipboard"] = field.cut() if key == "X" else selected
        elif key == "V":
            field.insert(clipboard)
        else:
            return None
    else:
        shift = getattr(event, "shift", False)
        if key == "LEFT_ARROW":
            field.move(-1, select=shift)
        elif key == "RIGHT_ARROW":
            field.move(1, select=shift)
        elif key == "HOME":
            field.home(select=shift)
        elif key == "END":
            field.end(select=shift)
        elif key == "BACK_SPACE":
            field.backspace()
        elif key == "DEL":
            field.delete()
        else:
            chars = getattr(event, "unicode", "") or getattr(event, "ascii", "")
            if not chars or not chars.isprintable():
                return None
            field.insert(chars)

    result["text"] = field.text
    _last_text = field.text
    return result
