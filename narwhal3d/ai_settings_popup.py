"""Side-anchored AI settings, with no trigger, shell edits, or network activity.

OURS: Python is only Blender UI/layout/input orchestration. Text editing, glyphs
and provider validation reuse existing modules; transport stays in Main's workers.
MEASURED styling: Narwhal_AI_Settings_Handoff.zip's small source/ai_settings.css.
The compact side-popup design replaces that prototype's modal backdrop.
See docs/automation/AI_SETTINGS_UI.md for the public wiring contract.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from math import isfinite

from . import ai_assistant as ai
from . import textfield

Point = tuple[float, float]
Rect = tuple[float, float, float, float]

# Local CSS tokens, never applied to the shell. Values are display-space sRGB.
# OURS: light muted/error ink slightly darker than CSS for >=4.5:1 small text.
PALETTES = {
    "light": dict(bg="#f6f8fb", card="#ffffff", border="#d8e1ed", text="#23344b",
                  muted="#60728a", input="#f9fbfd", accent="#2674df", badge="#e7f0ff",
                  hover="#eff4fc", error="#ac571e", warning="#fff4e7"),
    "dark": dict(bg="#303030", card="#383838", border="#515151", text="#e4e4e4",
                 muted="#aaaaaa", input="#2c2c2c", accent="#65aaff", badge="#263c54",
                 hover="#424242", error="#f1b878", warning="#453a2e"),
}
ORDER = ("provider", "endpoint", "refresh", "available", "model", "credential_env",
         "insecure", "cancel", "save", "close")
LABELS = {"endpoint": "API endpoint", "model": "Model ID",
          "credential_env": "API key environment variable"}


def connection_key(config: ai.ProviderConfig):
    """Deliberately excludes model ID and credential-present status."""
    return (config.provider_id, config.endpoint.strip(), config.credential_env.strip(),
            config.allow_insecure_remote)


def _inside(rect: Rect, x: float, y: float) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def _intersect(a: Rect, b: Rect) -> Rect:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


@dataclass
class Item:
    key: str
    kind: str
    rect: Rect
    text: str = ""
    size: int = 12
    tone: str = "text"


class PopupModel:
    """Inspectable, bpy-free draft. Rectangles use physical WINDOW pixels, y up.

    ``measure(text, physical_font_size)`` is supplied by Blender or a pure test.
    ``items`` is the clipped scroll-body display list, ``fixed`` header/footer;
    ``controls`` includes full field rectangles, ``body`` is their visible clip.
    """

    def __init__(self, config: ai.ProviderConfig, *, anchor: Point, window_size: Point,
                 scale: float, on_save: Callable[[ai.ProviderConfig], str | None],
                 on_refresh: Callable[[ai.ProviderConfig], bool],
                 measure: Callable[[str, float], float] | None = None,
                 on_revoke: Callable[[], str | None] | None = None):
        values = (*anchor, *window_size, scale)
        if not all(isfinite(v) for v in values) or min(*window_size, scale) <= 0:
            raise ValueError("anchor/size/scale must be finite; size and scale positive")
        if not callable(on_save) or not callable(on_refresh):
            raise TypeError("on_save and on_refresh callbacks are required")
        self.anchor, self.window_size, self.scale = tuple(anchor), tuple(window_size), scale
        measure = measure or (lambda text, size: len(text) * size * 0.6)
        self.measure: Callable[[str, float], float] = lambda text, size: measure(text, round(size))
        self.on_save, self.on_refresh = on_save, on_refresh
        self.on_revoke = on_revoke
        self.original = config
        self.provider_id = config.provider_id
        self.fields = {key: textfield.TextField(getattr(config, key), max_chars=limit)
                       for key, limit in (("endpoint", 2048), ("model", 200),
                                          ("credential_env", 200))}
        self.allow_insecure_remote = config.allow_insecure_remote
        self.is_open = True
        self.focus = "provider"
        self.scroll = 0
        self.dropdown: str | None = None
        self.menu_rows: list[tuple[int, Rect]] = []
        self.menu_rect: Rect | None = None
        self.choice_index = 0
        self.choice_offset = 0
        self.models: tuple[str, ...] = ()
        self.status = "Refresh to discover models."
        self.status_error = False
        self.busy = False
        self.error_field = None
        self.error = ""
        self._key = self.draft_key()
        self._releases: set[str] = set()
        self._drag: str | None = None
        self._offsets: dict[str, float] = dict.fromkeys(self.fields, 0.0)
        self.layout()

    def draft_key(self):
        return (self.provider_id, self.fields["endpoint"].text.strip(),
                self.fields["credential_env"].text.strip(), self.allow_insecure_remote)

    def _changed(self):
        self.error_field, self.error = None, ""
        if self.draft_key() != self._key:
            self._key = self.draft_key()
            self.models, self.busy = (), False
            self.status, self.status_error = "Connection changed; refresh models.", False
            self.dropdown = None
        self.layout()
        self.ensure_visible(self.focus)

    def choose_provider(self, provider_id):
        if provider_id not in dict(ai.PROVIDERS):
            raise ValueError("unsupported AI provider")
        for key, defaults in (("endpoint", ai.DEFAULT_ENDPOINTS),
                              ("credential_env", ai.DEFAULT_CREDENTIAL_ENVS)):
            if self.fields[key].text == defaults[self.provider_id]:
                self.fields[key].set_text(defaults[provider_id])
        self.provider_id = provider_id
        self._changed()

    def validated_config(self):
        """Validation is exclusively the existing backend, not a second URL parser."""
        self.error_field, self.error = None, ""
        for key in ("endpoint", "credential_env"):
            if not self.fields[key].text.strip():
                self.error_field, self.error = key, f"Enter {LABELS[key].lower()}."
                break
        if not self.error:
            try:
                return ai.set_provider(
                    self.provider_id, self.fields["model"].text,
                    self.fields["endpoint"].text.strip(),
                    self.fields["credential_env"].text.strip(),
                    credential_configured=(self.original.credential_configured
                                           and self.draft_key() == connection_key(self.original)),
                    allow_insecure_remote=self.allow_insecure_remote)
            except ValueError as exc:
                self.error = str(exc)
                self.error_field = ("credential_env" if "credential_env" in self.error else
                                    "model" if "model" in self.error else "endpoint")
        self.focus = self.error_field
        self.layout()
        self.ensure_visible(self.focus)
        return None

    def save(self):
        config = self.validated_config()
        if config is None:
            return False
        try:
            error = self.on_save(config)
        except Exception:
            # Never echo arbitrary backend exception text: it may contain credentials.
            error = "Could not save settings. Check the application settings service."
        if error is not None:
            self.error = str(error)[:2000] or "Settings were not saved. Try again."
            self.error_field = "save"
            self.scroll = 0
            self.layout()
            return False
        self.close()
        return True

    def refresh(self):
        if self.busy and self.draft_key() == self._key:
            return False
        config = self.validated_config()
        if config is None:
            return False
        self._key = self.draft_key()
        self.busy, self.status_error = True, False
        self.status = "Discovering models…"
        try:
            started = self.on_refresh(config)
        except Exception:
            started = False
        if not started and self.busy:
            self.busy, self.status_error = False, True
            self.status = "Discovery did not start. Try Refresh again; manual entry is available."
        self.layout()
        return bool(started)

    def set_discovery(self, config, models, status, error=False):
        if not self.is_open or connection_key(config) != self.draft_key():
            return False
        # UI rows only; Main supplies real IDs, not EnumProperty sentinel entries.
        self.models = tuple(str(model) for model in islice(models, 500))
        self.status, self.status_error = str(status)[:2000], bool(error)
        self.busy, self._key = False, self.draft_key()
        self.dropdown = None
        self.layout()
        self.ensure_visible(self.focus)
        return True

    def close(self):
        self.is_open = False
        self.dropdown, self._drag = None, None
        # Keep all press/release guards, including the dismissing event.

    def resize(self, window_size, *, anchor=None):
        values = (*window_size, *(anchor or self.anchor))
        if not all(isfinite(v) for v in values) or min(window_size) <= 0:
            raise ValueError("window_size must be positive and anchor finite")
        self.window_size = tuple(window_size)
        if anchor is not None:
            self.anchor = tuple(anchor)
        self.layout()
        self.ensure_visible(self.focus)

    def layout(self):
        s = self.scale
        ww, wh = self.window_size
        margin = min(round(8 * s), int(min(ww, wh) / 4))
        width = min(round(520 * s), int(ww) - 2 * margin)
        height = min(round(720 * s), int(wh) - 2 * margin)
        # Prefer LEFT of the right-dock button, never centred over the document.
        x = self.anchor[0] - width - round(8 * s)
        if x < margin and self.anchor[0] + round(8 * s) + width <= ww - margin:
            x = self.anchor[0] + round(8 * s)
        x = round(max(margin, min(x, ww - margin - width)))
        top = round(max(margin + height, min(self.anchor[1], wh - margin)))
        self.rect = (x, top - height, x + width, top)
        head = min(round(66 * s), height // 4)
        foot = min(round(64 * s), height // 4)
        self.body = (x, top - height + foot, x + width, top - head)
        self.header = (x, self.body[3], x + width, top)
        self.footer = (x, top - height, x + width, self.body[1])
        self.fixed = []
        self.controls = {}
        w = width / s
        pad = min(18, w / 12)

        def fixed(key, kind, rect: Rect, text="", size=12, tone="text"):
            r = (round(rect[0]), round(rect[1]), round(rect[2]), round(rect[3]))
            self.fixed.append(Item(key, kind, r, text, size, tone))
            if kind == "button":
                self.controls[key] = r

        fixed("title", "text", (x + pad*s, top - 32*s, x + width - 50*s, top - 8*s),
              "AI Assistant Settings", 18)
        fixed("subtitle", "text", (x + pad*s, top - head + 8*s, x + width - 45*s, top - 35*s),
              "Connect Narwhal to your language model.", 11, "muted")
        fixed("close", "button", (x + width - 42*s, top - 42*s, x + width - 12*s, top - 12*s))
        y = self.rect[1] + min(14*s, foot/5)
        bw = min(120*s, max(1, (width - 3*pad*s)/2))
        fixed("cancel", "button", (x + width - pad*s - 2*bw - 8*s, y,
                                    x + width - pad*s - bw - 8*s, y + min(35*s, foot-2*(y-self.rect[1]))), "Cancel")
        fixed("save", "button", (x + width - pad*s - bw, y, x + width - pad*s,
                                  y + min(35*s, foot-2*(y-self.rect[1]))), "Save settings")
        fixed("save_hint", "text", (x + pad*s, y, self.controls["cancel"][0] - 8*s, y + 35*s),
              "Saved only on Save", 11, "muted")
        nodes = []
        cursor = 12.0
        left, right = pad + 14, w - pad - 14
        inner = max(1, right - left)

        def add(key, kind, t, h, text="", size=12, tone="text", l=None, r=None):
            nodes.append(Item(key, kind, (left if l is None else l, t,
                                          right if r is None else r, t+h), text, size, tone))

        def lines(key, text, tone="muted", l=None, r=None, limit=None):
            nonlocal cursor
            a, b = left if l is None else l, right if r is None else r
            available = max(1, (b-a)*s)
            spans = textfield.wrap_spans(text, available,
                                         lambda t: self.measure(t, 11*s))
            for index, (lo, hi) in enumerate(spans[:limit]):
                value = text[lo:hi]
                if limit is not None and index == limit - 1 and len(spans) > limit:
                    while value and self.measure(value + "…", 11*s) > available:
                        value = value[:-1]
                    value += "…"
                add(key, "text", cursor, 16, value, 11, tone, a, b)
                cursor += 16

        def field(key, label, kind="field", text="", help_text=""):
            nonlocal cursor
            add(key+"_label", "text", cursor, 17, label)
            cursor += 21
            add(key, kind, cursor, 35, text)
            cursor += 41
            if self.error_field == key:
                lines(key+"_error", self.error, "error")
            elif help_text:
                lines(key+"_help", help_text)
            cursor += 8

        def start_card(key, title, subtitle):
            nonlocal cursor
            node = Item(key, "card", (pad, cursor, w-pad, cursor))
            nodes.append(node)
            add(key+"_badge", "badge", cursor+12, 28, key, l=left, r=left+28)
            add(key+"_title", "text", cursor+10, 20, title, 14, l=left+38)
            add(key+"_subtitle", "text", cursor+30, 16, subtitle, 11, "muted", l=left+38)
            cursor += 58
            return node

        def end_card(node):
            nonlocal cursor
            cursor += 5
            node.rect = (*node.rect[:3], cursor)
            cursor += 12

        if self.error_field == "save":
            lines("save_error", self.error, "error")
            cursor += 8
        card = start_card("connection", "Connection", "Provider and API endpoint")
        field("provider", "Provider", "button", dict(ai.PROVIDERS)[self.provider_id])
        field("endpoint", LABELS["endpoint"], help_text="Local HTTP is supported. Use HTTPS for remote servers.")
        end_card(card)
        card = start_card("models", "Model", "Discover models or enter an ID manually")
        add("refresh", "button", cursor, 35, "Refreshing…" if self.busy else "Refresh models",
            r=left+min(138, inner))
        toolbar_top = cursor
        if inner >= 300:
            lines("status", self.status, "error" if self.status_error else "muted", l=left+150, limit=3)
            cursor = max(cursor, toolbar_top+35)
        else:
            cursor += 41
            lines("status", self.status, "error" if self.status_error else "muted", limit=3)
        cursor += 12
        field("available", "Available models", "button",
              "Choose a discovered model" if self.models else "No models discovered — enter an ID below")
        field("model", LABELS["model"], help_text="Manual entry is always available, even if discovery fails.")
        end_card(card)
        card = start_card("security", "Credentials & security", "Authentication and connection security")
        field("credential_env", LABELS["credential_env"],
              help_text="Only the variable name is saved, never the secret key.")
        add("insecure", "button", cursor, 35, "Allow unencrypted remote HTTP")
        cursor += 43
        if self.allow_insecure_remote:
            warning_start = cursor
            index = len(nodes)
            cursor += 8
            lines("warning_text", "Security warning: remote HTTP can expose prompts and credentials. Use HTTPS whenever possible.", "error", left+10, right-10)
            cursor += 8
            nodes.insert(index, Item("warning", "warning", (left, warning_start, right, cursor)))
        if self.on_revoke is not None:
            add('revoke', 'button', cursor, 35, 'Forget workspace-sharing permission')
            cursor += 43
        end_card(card)
        lines("note", "Set the named environment variable before launching Narwhal 3D.")
        self.content_height = round((cursor+12)*s)
        self.max_scroll = max(0, self.content_height - (self.body[3]-self.body[1]))
        self.scroll = round(max(0, min(self.scroll, self.max_scroll)))
        self.items = []
        for node in nodes:
            a, t, b, bottom = node.rect
            node.rect = (round(x+a*s), round(self.body[3]-bottom*s+self.scroll),
                         round(x+b*s), round(self.body[3]-t*s+self.scroll))
            self.items.append(node)
            if node.kind in ("field", "button"):
                self.controls[node.key] = node.rect
        self._layout_dropdown()

    def control_clip(self, key):
        if key in ("close", "title", "subtitle"):
            return self.header
        if key in ("cancel", "save", "save_hint"):
            return self.footer
        return self.body

    def ensure_visible(self, key):
        if key not in self.controls or key in ("cancel", "save", "close"):
            return
        r = self.controls[key]
        if r[1] < self.body[1]:
            self.scroll += self.body[1] - r[1] + round(6*self.scale)
        elif r[3] > self.body[3]:
            self.scroll -= r[3] - self.body[3] + round(6*self.scale)
        self.layout()

    def choices(self):
        return ai.PROVIDERS if self.dropdown == "provider" else tuple((m, m) for m in self.models)

    def _layout_dropdown(self):
        self.menu_rows, self.menu_rect = [], None
        if not self.dropdown:
            return
        choices = self.choices()
        if not choices:
            self.dropdown = None
            return
        self.choice_index = max(0, min(self.choice_index, len(choices)-1))
        row = max(1, round(30*self.scale))
        count = max(1, min(7, len(choices), (self.body[3]-self.body[1])//row))
        self.choice_offset = max(0, min(self.choice_offset, len(choices)-count))
        r = self.controls[self.dropdown]
        height = min(count*row, self.body[3]-self.body[1])
        top = r[1] if r[1]-height >= self.body[1] else min(self.body[3], r[3]+height)
        top = max(self.body[1]+height, top)
        self.menu_rect = (r[0], top-height, r[2], top)
        for index in range(self.choice_offset, self.choice_offset+count):
            y = top-(index-self.choice_offset)*row
            self.menu_rows.append((index, (r[0], max(top-height, y-row), r[2], y)))

    def _menu_move(self, delta):
        choices = self.choices()
        self.choice_index = max(0, min(self.choice_index+delta, len(choices)-1))
        count = max(1, len(self.menu_rows))
        self.choice_offset = max(min(self.choice_offset, self.choice_index), self.choice_index-count+1)
        self._layout_dropdown()

    def activate(self, key):
        if key in ("cancel", "close"):
            self.close()
        elif key == "save":
            self.save()
        elif key == 'revoke' and self.on_revoke is not None:
            error = self.on_revoke()
            self.status = error or 'Permission forgotten. Your next message will ask again.'
            self.status_error = error is not None
            self.layout()
        elif key == "refresh":
            self.refresh()
        elif key == "insecure":
            self.allow_insecure_remote = not self.allow_insecure_remote
            self._changed()
        elif key in ("provider", "available"):
            self.dropdown = None if self.dropdown == key else key
            self.choice_index = self.choice_offset = 0
            if key == "provider":
                self.choice_index = [p for p, _ in ai.PROVIDERS].index(self.provider_id)
            self._layout_dropdown()

    def _choose(self, index):
        value = self.choices()[index][0]
        if self.dropdown == "provider":
            self.choose_provider(value)
        else:
            self.fields["model"].set_text(value)
            self.focus = "model"
            self._changed()
        self.dropdown = None
        self.layout()

    def field_offset(self, key):
        f = self.fields[key]
        r = self.controls[key]
        # Reserve the caret's physical pixel inside the text scissor, even at End.
        width = max(1, round(r[2]-11*self.scale)-round(r[0]+11*self.scale)-1)
        caret = self.measure(f.text[:f.caret], 13*self.scale)
        # Replacing long text (or widening the window) must not leave a short
        # value scrolled entirely behind the left clip edge.
        maximum = max(0, self.measure(f.text, 13*self.scale)-width)
        offset = max(0, min(self._offsets[key], caret, maximum))
        self._offsets[key] = max(offset, caret-width)
        return self._offsets[key]

    def _click_field(self, key, x, select=False):
        f = self.fields[key]
        local = x-round(self.controls[key][0]+11*self.scale)+self.field_offset(key)
        f.set_caret(textfield.index_at([(0, len(f.text))], f.text, local, 0,
                                      lambda t: self.measure(t, 13*self.scale)), select)

    def handle_event(self, event, context=None):
        kind, value = event.type, getattr(event, "value", "")
        if kind in self._releases and value == "RELEASE":
            self._releases.discard(kind)
            if kind == "LEFTMOUSE":
                self._drag = None
            return True
        if not self.is_open:
            return kind in self._releases or (kind in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE")
                                             and any(k.endswith("MOUSE") for k in self._releases))
        if value == "PRESS" and kind not in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            self._releases.add(kind)
        x, y = getattr(event, "mouse_x", -1), getattr(event, "mouse_y", -1)
        shift = getattr(event, "shift", False)
        ctrl = getattr(event, "ctrl", False) or getattr(event, "oskey", False)
        if kind in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
            if self._drag:
                self._click_field(self._drag, x, True)
            return True
        if kind in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            delta = -1 if kind == "WHEELUPMOUSE" else 1
            if self.dropdown:
                self._menu_move(delta)
            elif _inside(self.body, x, y):
                self.scroll += delta*48*self.scale
                self.layout()
            return True
        if value != "PRESS":
            return True
        if kind == "ESC":
            if self.dropdown:
                self.dropdown = None
            else:
                self.close()
            return True
        if kind == "LEFTMOUSE":
            if not _inside(self.rect, x, y):
                self.close()
                return True
            if self.dropdown:
                for index, rect in self.menu_rows:
                    if _inside(rect, x, y):
                        self._choose(index)
                        return True
                self.dropdown = None
                return True
            for key, rect in self.controls.items():
                clip = self.control_clip(key)
                if _inside(_intersect(rect, clip), x, y):
                    self.focus = key
                    if key in self.fields:
                        self._click_field(key, x, shift)
                        self._drag = key
                    else:
                        self.activate(key)
                    break
            return True
        if kind == "TAB":
            self.dropdown = None
            order = ORDER[:7] + ('revoke',) + ORDER[7:] if self.on_revoke is not None else ORDER
            self.focus = order[(order.index(self.focus) + (-1 if shift else 1)) % len(order)]
            self.ensure_visible(self.focus)
            return True
        if self.dropdown:
            if kind in ("UP_ARROW", "DOWN_ARROW"):
                self._menu_move(-1 if kind == "UP_ARROW" else 1)
            elif kind in ("HOME", "END"):
                self._menu_move(-len(self.choices()) if kind == "HOME" else len(self.choices()))
            elif kind in ("RET", "NUMPAD_ENTER", "SPACE"):
                self._choose(self.choice_index)
            return True
        if self.focus not in self.fields:
            if kind in ("RET", "NUMPAD_ENTER", "SPACE", "DOWN_ARROW"):
                self.activate(self.focus)
            return True
        f = self.fields[self.focus]
        before = f.text
        wm = getattr(context, "window_manager", None)
        if ctrl and kind == "A":
            f.select_all()
        elif ctrl and kind in ("C", "X", "V"):
            if wm is not None:
                if kind == "V":
                    f.insert(wm.clipboard)
                elif f.selection():
                    wm.clipboard = f.cut() if kind == "X" else f.selected_text()
        elif kind in ("LEFT_ARROW", "RIGHT_ARROW"):
            direction = -1 if kind == "LEFT_ARROW" else 1
            if ctrl:
                i = f.caret
                while 0 <= i+min(direction, 0) < len(f.text) and f.text[i+min(direction, 0)].isspace():
                    i += direction
                while 0 <= i+min(direction, 0) < len(f.text) and not f.text[i+min(direction, 0)].isspace():
                    i += direction
                f.set_caret(i, shift)
            else:
                f.move(direction, shift)
        elif kind in ("HOME", "UP_ARROW"):
            f.home(shift)
        elif kind in ("END", "DOWN_ARROW"):
            f.end(shift)
        elif kind == "BACK_SPACE":
            f.backspace()
        elif kind == "DEL":
            f.delete()
        elif kind in ("RET", "NUMPAD_ENTER"):
            self.save()
        elif not ctrl and not getattr(event, "alt", False):
            f.insert(getattr(event, "unicode", ""))
        if f.text != before:
            self._changed()
        self.ensure_visible(self.focus)
        return True


_model: PopupModel | None = None
_handler = None
_window = None


def _redraw():
    if _window is not None:
        for area in _window.screen.areas:
            area.tag_redraw()


def open(config: ai.ProviderConfig, *, anchor: Point, window_size: Point, scale: float,
         on_save: Callable[[ai.ProviderConfig], str | None],
         on_refresh: Callable[[ai.ProviderConfig], bool],
         on_revoke: Callable[[], str | None] | None = None) -> PopupModel:
    """Open only in the current window; returns the inspectable PopupModel.

    No discovery is started implicitly. Requires the native Window POST_PIXEL API.
    """
    global _model, _handler, _window
    import bpy
    from . import ui
    owner = bpy.types.Window
    if not all(callable(getattr(owner, name, None)) for name in
               ("draw_handler_add", "draw_handler_remove")):
        raise RuntimeError("AI settings requires Window POST_PIXEL draw handlers; update the Narwhal runtime.")
    model = PopupModel(config, anchor=anchor, window_size=window_size, scale=scale,
                       on_save=on_save, on_refresh=on_refresh, measure=ui._text_width,
                       on_revoke=on_revoke)
    if bpy.context.window is None:
        raise RuntimeError("AI settings requires an active application window.")
    close()
    if _model is not None:
        model._releases.update(_model._releases)
    _model, _window = model, bpy.context.window
    try:
        _handler = (owner, owner.draw_handler_add(_draw, (), "POST_PIXEL"))
    except Exception as exc:
        model.close()
        raise RuntimeError("Could not register AI settings Window POST_PIXEL overlay.") from exc
    _redraw()
    return model


def close():
    """Unregister our own handler; keep the closed model for trailing releases."""
    global _handler
    if _model is not None:
        _model.close()
    if _handler is not None:
        owner, handle = _handler
        owner.draw_handler_remove(handle)
        _handler = None
    _redraw()


def is_open():
    return bool(_model is not None and _model.is_open)


def handle_event(event, context=None):
    """Return consumed bool. Main MUST route here even after is_open() is false."""
    if _model is None:
        return False
    if context is None:
        import bpy
        context = bpy.context
    if context.window != _window:
        return False
    if is_open() and _model.window_size != (context.window.width, context.window.height):
        _model.resize((context.window.width, context.window.height))
    consumed = _model.handle_event(event, context)
    if not _model.is_open and _handler is not None:
        close()
    elif consumed:
        _redraw()
    return consumed


def set_discovery(config: ai.ProviderConfig, models: Iterable[str], status: str,
                  error: bool = False) -> bool:
    """Main-thread delivery of real model-ID strings and secret-free status text.

    Returns False for closed/stale connection results. No worker/RNA access here.
    """
    accepted = bool(_model is not None and _model.set_discovery(config, models, status, error))
    if accepted:
        _redraw()
    return accepted


def _draw():
    import bpy
    if _model is None or _window is None or not _model.is_open or bpy.context.window != _window:
        return
    size = (_window.width, _window.height)
    if size != _model.window_size:
        _model.resize(size)
    from . import design
    draw_model(_model, design.THEME)


def draw_model(model: PopupModel, theme="light"):
    """Lazy GPU painting; also usable with stub drawing APIs in the pure suite."""
    import gpu
    from . import glyphs, ui
    p = {key: tuple(bytes.fromhex(value[1:])[i]/255 for i in range(3))+(1,)
         for key, value in PALETTES[theme].items()}
    s = model.scale
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    old_clip, old_blend = gpu.state.scissor_get(), gpu.state.blend_get()

    def clip(rect):
        a, b, c, d = (round(v) for v in rect)
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(a, b, max(0, c-a), max(0, d-b))

    def box(rect: Rect, fill, border=None, radius=6):
        shader.bind()
        gpu.state.blend_set("ALPHA")
        ui._draw_round_rect(shader, *rect, round(radius*s), fill, border, 1)

    def rect(bounds: Rect, color):
        shader.bind()
        gpu.state.blend_set("ALPHA")
        ui._draw_rect(shader, *bounds, color)

    def glyph(tris, color):
        gpu.state.blend_set("ALPHA")
        ui._draw_glyph(shader, tris, color)

    def text(rect, value, size, color):
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            return
        # Scissor also clips long unbroken strings and oversized font glyphs.
        ui._draw_text(round(rect[0]), round((rect[1]+rect[3]-size*s)/2+2*s),
                      value, round(size*s), color)

    def paint(item: Item, boundary: Rect):
        r, key = item.rect, item.key
        visible = _intersect(r, boundary)
        if visible[2] <= visible[0] or visible[3] <= visible[1]:
            return
        clip(visible)
        if item.kind in ("card", "warning"):
            box(r, p["card" if item.kind == "card" else "warning"], p["border"], 10)
        elif item.kind == "badge":
            box(r, p["badge"], radius=8)
            cx, cy = round((r[0]+r[2])/2), round((r[1]+r[3])/2)
            if item.text == "security":
                tris = glyphs.lock(cx, cy, round(17*s))
            elif item.text == "connection":
                d = round(6*s)
                tris = glyphs.segment(cx-d, cy, cx+d, cy+d, max(1, round(s)))
                tris += glyphs.segment(cx-d, cy, cx+d, cy-d, max(1, round(s)))
                for dx, dy in ((-d, 0), (d, d), (d, -d)):
                    tris += glyphs.disc(cx+dx, cy+dy, round(2*s))
            else:
                tris = glyphs.disc(cx, cy+round(3*s), round(6*s))
                tris += glyphs.segment(cx-round(3*s), cy-round(6*s), cx+round(3*s), cy-round(6*s), max(1, round(2*s)))
            glyph(tris, p["accent"])
        elif item.kind in ("button", "field"):
            focused = model.focus == key
            fill = p["input"] if item.kind == "field" else p["card"]
            if key == "save":
                fill = (0x26/255, 0x74/255, 0xdf/255, 1)  # CSS accent; white text >=4.5:1.
            border = p["error"] if model.error_field == key else p["accent"] if focused else p["border"]
            box(r, fill, border)
            inner = (round(r[0]+11*s), r[1], round(r[2]-11*s), r[3])
            clip(_intersect(inner, boundary))
            if key == "close":
                clip(visible)
                cx, cy = round((r[0]+r[2])/2), round((r[1]+r[3])/2)
                d = round(5*s)
                glyph(glyphs.segment(cx-d, cy-d, cx+d, cy+d, max(1, round(s))) +
                      glyphs.segment(cx-d, cy+d, cx+d, cy-d, max(1, round(s))), p["text"])
            elif item.kind == "field":
                f = model.fields[key]
                offset = model.field_offset(key)
                tx = round(inner[0]-offset)
                if focused and f.selection():
                    lo, hi = f.selection()
                    a = round(tx+model.measure(f.text[:lo], 13*s))
                    b = round(tx+model.measure(f.text[:hi], 13*s))
                    rect((a, r[1]+round(5*s), b, r[3]-round(5*s)), p["badge"])
                text((tx, r[1], max(tx+1, inner[2]), r[3]), f.text, 13, p["text"])
                if focused:
                    caret = round(tx+model.measure(f.text[:f.caret], 13*s))
                    rect((caret, r[1]+round(8*s), caret+1, r[3]-round(8*s)), p["accent"])
            else:
                label = item.text
                if key == "insecure":
                    cx, cy = round(inner[0]+7*s), round((r[1]+r[3])/2)
                    if model.allow_insecure_remote:
                        glyph(glyphs.tick(cx, cy, round(12*s)), p["accent"])
                    else:
                        box((cx-round(6*s), cy-round(6*s), cx+round(6*s), cy+round(6*s)), p["input"], p["border"], 2)
                    inner = (round(inner[0]+24*s), *inner[1:])
                if key in ("provider", "available"):
                    cx, cy = round(inner[2]-5*s), round((r[1]+r[3])/2)
                    glyph(glyphs.polyline([(cx-round(4*s), cy+round(2*s)), (cx, cy-round(2*s)),
                                          (cx+round(4*s), cy+round(2*s))], max(1, round(s))), p["muted"])
                    inner = (inner[0], inner[1], round(inner[2]-18*s), inner[3])
                clip(_intersect(inner, boundary))
                text(inner, label, 12, (1, 1, 1, 1) if key == "save" else p["text"])
        else:
            text(r, item.text, item.size, p[item.tone])

    try:
        clip(model.rect)
        box(model.rect, p["bg"], p["border"], 13)
        for item in model.items:
            paint(item, model.body)
        for item in model.fixed:
            paint(item, model.control_clip(item.key))
        clip(model.rect)
        for y in (model.body[1], model.body[3]):
            rect((model.rect[0]+1, y, model.rect[2]-1, y+1), p["border"])
        if model.max_scroll:
            clip(model.body)
            _a, b, c, d = model.body
            h = max(round(18*s), round((d-b)**2/model.content_height))
            top = round(d-(d-b-h)*model.scroll/model.max_scroll)
            box((c-round(6*s), top-h, c-round(3*s), top), p["muted"], radius=2)
        if model.dropdown and model.menu_rect:
            clip(model.menu_rect)
            box(model.menu_rect, p["card"], p["border"])
            choices = model.choices()
            for index, r in model.menu_rows:
                clip(r)
                if index == model.choice_index:
                    box(r, p["badge"])
                text((r[0]+round(10*s), r[1], r[2]-round(10*s), r[3]), choices[index][1], 12, p["text"])
    finally:
        gpu.state.scissor_set(*old_clip)
        # Window POST_PIXEL initializes scissor disabled and restores all GPU state
        # around each callback (native overlay contract); Blender has no enable getter.
        gpu.state.scissor_test_set(False)
        gpu.state.blend_set(old_blend)
