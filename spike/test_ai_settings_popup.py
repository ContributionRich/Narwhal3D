#!/usr/bin/env python3
"""Pure popup model/layout/input + stub GPU/window tests. No Blender or network.

python3 -B spike/test_ai_settings_popup.py
python3 -B spike/test_ai_settings_popup.py --mutations
Mutations run ONLY temporary source copies, never production files.
"""
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
pkg: Any = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
SOURCE = ROOT / "narwhal3d/ai_settings_popup.py"
spec = importlib.util.spec_from_file_location(
    "narwhal3d.ai_settings_popup", os.environ.get("AI_SETTINGS_POPUP_SOURCE", SOURCE))
assert spec is not None and spec.loader is not None
ui: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ui
spec.loader.exec_module(ui)
ai = importlib.import_module("narwhal3d.ai_assistant")


def event(kind, value="PRESS", **kw):
    return types.SimpleNamespace(type=kind, value=value, mouse_x=kw.pop("x", 0),
                                 mouse_y=kw.pop("y", 0), **kw)


def center(rect):
    return (rect[0]+rect[2])/2, (rect[1]+rect[3])/2


class PopupTests(unittest.TestCase):
    saved: list[Any]
    refreshed: list[Any]
    config: Any
    m: Any
    context: types.SimpleNamespace

    def setUp(self):
        self.saved, self.refreshed = [], []
        self.config = ai.set_provider("openai_compatible", model="original")
        self.m = self.make()
        self.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(clipboard=""))

    def make(self, **kwargs):
        args = dict(anchor=(1140, 770), window_size=(1280, 800), scale=1,
                    on_save=self.saved.append,
                    on_refresh=lambda c: self.refreshed.append(c) is None)
        args.update(kwargs)
        return ui.PopupModel(self.config, **args)

    def key(self, kind, **kw):
        return self.m.handle_event(event(kind, **kw), self.context)

    def click(self, key):
        self.m.ensure_visible(key)
        x, y = center(self.m.controls[key])
        self.key("LEFTMOUSE", x=x, y=y)
        self.key("LEFTMOUSE", value="RELEASE", x=x, y=y)

    def test_real_providers_and_no_raw_key(self):
        self.m.activate("provider")
        self.assertEqual(self.m.choices(), ai.PROVIDERS)
        self.assertEqual(len(ai.PROVIDERS), 3)
        self.assertIn(("anthropic", "Anthropic"), self.m.choices())
        self.assertEqual(set(self.m.fields), {"endpoint", "model", "credential_env"})
        self.assertFalse(self.refreshed, "open must not start networking")
        self.assertEqual(self.m.fields["model"].text, "original")

    def test_cancel_never_writes(self):
        for dismiss in ("cancel", "close", "esc", "outside"):
            self.m = self.make()
            self.m.fields["model"].set_text("not saved")
            if dismiss == "esc":
                self.key("ESC")
            elif dismiss == "outside":
                self.key("LEFTMOUSE", x=1, y=1)
            else:
                self.click(dismiss)
            self.assertFalse(self.m.is_open)
            self.assertFalse(self.saved)
            self.assertEqual(self.config.model, "original")

    def test_invalid_save_does_not_call_callback(self):
        for field, value in (("endpoint", "ftp://localhost"), ("endpoint", ""),
                             ("endpoint", "http://remote.example"),
                             ("endpoint", "https://key:secret@example.org"),
                             ("credential_env", "not a name"), ("credential_env", "")):
            with self.subTest(field=field, value=value):
                self.m = self.make()
                self.m.fields[field].set_text(value)
                self.assertFalse(self.m.save())
                self.assertTrue(self.m.is_open)
                self.assertEqual(self.m.error_field, field)
                self.assertEqual(self.m.focus, field)
                self.assertTrue(self.m.error)
                self.assertFalse(self.saved)

    def test_save_calls_backend_then_callback(self):
        with patch.object(ai, "set_provider", wraps=ai.set_provider) as validate:
            self.m.fields["model"].set_text("new-model")
            self.assertTrue(self.m.save())
            validate.assert_called_once()
        self.assertIsInstance(self.saved[0], ai.ProviderConfig)
        self.assertEqual(self.saved[0].model, "new-model")
        self.assertFalse(self.m.is_open)

    def test_save_callback_failures_keep_open(self):
        self.m.on_save = lambda c: "Permission denied. "*150
        self.assertFalse(self.m.save())
        self.assertTrue(self.m.is_open)
        self.assertEqual(self.m.error_field, "save")
        self.assertEqual(self.m.scroll, 0)
        self.assertGreater(len([n for n in self.m.items if n.key == "save_error"]), 1)
        self.m.on_save = lambda c: (_ for _ in ()).throw(RuntimeError("secret-key"))
        self.assertFalse(self.m.save())
        self.assertNotIn("secret-key", self.m.error)

    def test_provider_defaults_preserve_custom_fields(self):
        self.m.choose_provider("anthropic")
        self.assertEqual(self.m.fields["endpoint"].text, ai.DEFAULT_ENDPOINTS["anthropic"])
        self.assertEqual(self.m.fields["credential_env"].text, "ANTHROPIC_API_KEY")
        self.m.fields["endpoint"].set_text("https://custom.example/api")
        self.m.choose_provider("openai")
        self.assertEqual(self.m.fields["endpoint"].text, "https://custom.example/api")
        self.assertEqual(self.m.fields["credential_env"].text, "OPENAI_API_KEY")
        self.m.fields["credential_env"].set_text("CUSTOM_KEY")
        self.m.choose_provider("openai_compatible")
        self.assertEqual(self.m.fields["credential_env"].text, "CUSTOM_KEY")
        self.assertEqual(self.m.fields["model"].text, "original")

    def test_side_anchor_not_center(self):
        self.assertEqual(self.m.rect[2], 1140-8)
        self.assertEqual(self.m.rect[2]-self.m.rect[0], 520)
        self.assertNotEqual(center(self.m.rect)[0], 640)
        left = self.make(anchor=(20, 780))
        self.assertEqual(left.rect[0], 28)

    def test_bounds_and_pixel_alignment(self):
        for size in ((1280, 800), (640, 480), (360, 260), (900, 360)):
            for scale in (1, 1.25, 1.5, 2):
                for anchor in ((-10, -10), (size[0]-30, size[1]-15), (9999, 9999)):
                    with self.subTest(size=size, scale=scale, anchor=anchor):
                        m = self.make(anchor=anchor, window_size=size, scale=scale)
                        a, b, c, d = m.rect
                        self.assertGreaterEqual(a, 0)
                        self.assertGreaterEqual(b, 0)
                        self.assertLessEqual(c, size[0])
                        self.assertLessEqual(d, size[1])
                        self.assertGreater(c, a)
                        self.assertGreater(d, b)
                        for item in m.items+m.fixed:
                            self.assertTrue(all(v == round(v) for v in item.rect))
                        for field in m.fields:
                            self.assertLessEqual(abs((m.controls[field][3]-m.controls[field][1])-35*scale), 1)

    def test_scroll_and_tab_ensure_visible(self):
        self.m = self.make(window_size=(800, 320), anchor=(770, 300))
        self.assertGreater(self.m.max_scroll, 0)
        self.m.focus = "model"
        self.key("TAB")
        self.assertEqual(self.m.focus, "credential_env")
        r = self.m.controls["credential_env"]
        self.assertGreaterEqual(r[1], self.m.body[1])
        self.assertLessEqual(r[3], self.m.body[3])
        self.assertGreater(self.m.scroll, 0)
        self.key("TAB", shift=True)
        self.assertEqual(self.m.focus, "model")
        self.m.scroll = 0
        self.m.layout()
        x, y = center(self.m.body)
        self.key("WHEELDOWNMOUSE", x=x, y=y)
        self.assertEqual(self.m.scroll, 48)
        for _ in range(100):
            self.key("WHEELDOWNMOUSE", x=x, y=y)
        self.assertEqual(self.m.scroll, self.m.max_scroll)
        self.m.resize((800, 600))
        self.assertLessEqual(self.m.scroll, self.m.max_scroll)

    def test_long_status_wrap_never_squeezes_fields(self):
        # Compact-status design: only discovery is capped, not its data
        # or actionable validation help. Exercise both toolbar layout branches.
        for size in ((1280, 800), (360, 260)):
            for scale in (1, 1.25, 2):
                with self.subTest(size=size, scale=scale):
                    m = self.make(window_size=size, scale=scale)
                    widths = {k: r[2]-r[0]
                             for k, r in m.controls.items() if k in m.fields}
                    for message in ("No models reported.", "Connection failed at remote endpoint. "*40):
                        self.assertTrue(m.set_discovery(self.config, (), message, True))
                        status = [n for n in m.items if n.key == "status"]
                        self.assertGreater(len(status), 0)
                        self.assertLessEqual(len(status), 3)
                        self.assertEqual(m.status, message, "paint cap must preserve the full status")
                        if len(message) > 400:
                            self.assertEqual(len(status), 3)
                            self.assertTrue(status[-1].text.endswith("…"))
                        else:
                            self.assertFalse(any(n.text.endswith("…") for n in status))
                        for n in status:
                            # wrap_spans retains whitespace past the ink's word break.
                            self.assertLessEqual(m.measure(n.text.rstrip(" "), 11*scale), n.rect[2]-n.rect[0]+1)
                        for key, width in widths.items():
                            r = m.controls[key]
                            self.assertEqual(r[2]-r[0], width)
                            self.assertLessEqual(abs(r[3]-r[1]-35*scale), 1)
                        self.assertTrue(m.status_error)
                    m.on_save = lambda _: "Actionable save failure details. "*30
                    self.assertFalse(m.save())
                    self.assertGreater(len([n for n in m.items if n.key == "save_error"]), 3)

    def test_discovery_key_excludes_only_model_and_status(self):
        for change in (dict(provider_id="anthropic"), dict(endpoint="https://example.org"),
                       dict(credential_env="OTHER_KEY"), dict(allow_insecure_remote=True)):
            self.assertFalse(self.m.set_discovery(replace(self.config, **change), ["stale"], "stale"))
            self.assertFalse(self.m.models)
        self.m.fields["model"].set_text("typed while waiting")
        self.assertTrue(self.m.set_discovery(replace(self.config, model="different", credential_configured=True), ["real-id"], "Found 1 model"))
        self.assertEqual(self.m.fields["model"].text, "typed while waiting")
        self.m.close()
        self.assertFalse(self.m.set_discovery(self.config, ["late"], "late"))

    def test_refresh_worker_contract_and_edit_invalidation(self):
        self.assertTrue(self.m.refresh())
        self.assertTrue(self.m.busy)
        self.assertFalse(self.m.refresh())
        self.assertEqual(len(self.refreshed), 1)
        self.m.set_discovery(self.config, ["real"], "Ready")
        self.m.focus = "endpoint"
        self.key("END")
        self.key("A", unicode="/")
        self.assertFalse(self.m.models)
        self.assertFalse(self.m.busy)
        self.assertFalse(self.m.set_discovery(self.config, ["stale"], "late"))
        self.m.on_refresh = lambda c: False
        self.assertFalse(self.m.refresh())
        self.assertTrue(self.m.status_error)
        self.assertFalse(self.m.busy)

    def test_refresh_synchronous_delivery_not_overwritten(self):
        def refresh(c):
            self.m.set_discovery(c, ["model-x"], "Ready")
            return True
        self.m.on_refresh = refresh
        self.m.refresh()
        self.assertEqual(self.m.status, "Ready")
        self.assertFalse(self.m.busy)

    def test_available_dropdown_manual_edit_and_menu_bounds(self):
        self.m.set_discovery(self.config, [f"actual-{i}" for i in range(25)], "Ready")
        self.click("available")
        _a, b, _c, d = self.m.menu_rect
        self.assertGreaterEqual(b, self.m.body[1])
        self.assertLessEqual(d, self.m.body[3])
        self.key("END")
        self.assertEqual(self.m.choice_index, 24)
        self.key("RET")
        self.assertEqual(self.m.fields["model"].text, "actual-24")
        self.assertEqual(self.m.focus, "model")
        self.key("A", ctrl=True)
        self.key("TEXTINPUT", unicode="手動-模型")
        self.assertEqual(self.m.fields["model"].text, "手動-模型")
        self.assertFalse(self.saved)

    def test_dropdown_escape_does_not_dismiss_popup(self):
        self.m.activate("provider")
        self.key("ESC")
        self.assertTrue(self.m.is_open)
        self.assertIsNone(self.m.dropdown)
        self.key("ESC")
        self.assertFalse(self.m.is_open)

    def test_unicode_clipboard_caret_and_selection(self):
        self.m.focus = "model"
        self.key("A", ctrl=True)
        self.key("TEXTINPUT", unicode="aé猫🙂z")
        f = self.m.fields["model"]
        self.key("HOME")
        self.key("RIGHT_ARROW", shift=True)
        self.key("RIGHT_ARROW", shift=True)
        self.key("C", ctrl=True)
        self.assertEqual(self.context.window_manager.clipboard, "aé")
        self.key("X", ctrl=True)
        self.assertEqual(f.text, "猫🙂z")
        self.key("END")
        self.key("V", ctrl=True)
        self.assertEqual(f.text, "猫🙂zaé")
        self.key("BACK_SPACE")
        self.key("HOME")
        self.key("DEL")
        self.assertEqual(f.text, "🙂za")
        self.key("A", oskey=True)
        self.key("TEXTINPUT", unicode="one two")
        self.key("LEFT_ARROW", ctrl=True)
        self.assertEqual(f.caret, 4)
        self.key("HOME")
        self.key("RIGHT_ARROW", ctrl=True, shift=True)
        self.assertEqual(f.selected_text(), "one")

    def test_mouse_caret_drag_and_horizontal_scroll(self):
        self.m.focus = "model"
        f = self.m.fields["model"]
        f.set_text("abcdef")
        self.m.ensure_visible("model")
        r = self.m.controls["model"]
        x, y = r[0]+11, center(r)[1]
        self.key("LEFTMOUSE", x=x, y=y)
        self.assertEqual(f.caret, 0)
        self.key("MOUSEMOVE", x=x+self.m.measure("abc", 13), y=y)
        self.key("LEFTMOUSE", value="RELEASE")
        self.assertEqual(f.selected_text(), "abc")
        f.set_text("long-model-"*18)
        self.assertGreater(self.m.field_offset("model"), 0)
        self.key("HOME")
        self.assertEqual(self.m.field_offset("model"), 0)

    def test_long_field_caret_remains_inside_clip(self):
        for scale in (1, 1.25, 1.5, 2):
            m = self.make(scale=scale)
            f = m.fields["model"]
            f.set_text("W"*200)
            r = m.controls["model"]
            x = round(round(r[0]+11*scale)-m.field_offset("model"))
            caret = round(x+m.measure(f.text, 13*scale))
            self.assertLess(caret, round(r[2]-11*scale), scale)

    def test_enter_in_field_validates_and_saves(self):
        self.m.focus = "model"
        self.key("RET")
        self.assertEqual(len(self.saved), 1)
        self.assertFalse(self.m.is_open)
        self.assertTrue(self.key("RET", value="RELEASE"))

    def test_security_warning_only_when_enabled(self):
        self.assertFalse(any(n.key == "warning" for n in self.m.items))
        self.m.activate("insecure")
        self.assertTrue(any(n.key == "warning" for n in self.m.items))
        self.m.fields["endpoint"].set_text("http://remote.example")
        self.assertIsNotNone(self.m.validated_config())
        self.m.activate("insecure")
        self.assertIsNone(self.m.validated_config())
        self.assertFalse(any(n.key == "warning" for n in self.m.items))

    def test_dismiss_release_and_held_drag_shield(self):
        self.assertTrue(self.key("LEFTMOUSE", x=1, y=1))
        self.assertFalse(self.m.is_open)
        self.assertTrue(self.key("MOUSEMOVE", x=15, y=15))
        self.assertTrue(self.key("LEFTMOUSE", value="RELEASE", x=1, y=1))
        self.assertFalse(self.key("LEFTMOUSE", x=1, y=1))
        self.m = self.make()
        self.key("ESC")
        self.assertTrue(self.key("ESC"), "held key repeats must not leak")
        self.assertTrue(self.key("ESC", value="RELEASE"))
        self.assertFalse(self.key("ESC"))

    def test_all_open_input_consumed(self):
        for kind in ("RIGHTMOUSE", "MIDDLEMOUSE", "WHEELDOWNMOUSE", "G", "TIMER", "NDOF_MOTION"):
            self.assertTrue(self.key(kind, x=1, y=1))
        self.assertFalse(self.saved)

    def test_invalid_dimensions(self):
        for kwargs in (dict(scale=0), dict(scale=float("nan")), dict(window_size=(0, 200)),
                       dict(anchor=(float("inf"), 10))):
            with self.assertRaises(ValueError):
                self.make(**kwargs)


class AdapterTests(unittest.TestCase):
    calls: list[Any]
    paints: list[Any]
    clip: tuple[int, ...]
    blend: str
    scissor_enabled: bool
    window: types.SimpleNamespace
    context: types.SimpleNamespace
    window_type: types.SimpleNamespace
    bpy: types.SimpleNamespace
    patcher: Any
    config: Any

    def setUp(self):
        self.calls, self.paints = [], []
        self.clip = (0, 0, 1280, 800)
        self.blend = "NONE"
        self.scissor_enabled = False
        self.window = types.SimpleNamespace(width=1280, height=800,
            screen=types.SimpleNamespace(areas=[types.SimpleNamespace(tag_redraw=lambda: None)]))
        self.context = types.SimpleNamespace(window=self.window, window_manager=types.SimpleNamespace(clipboard=""))
        self.window_type = types.SimpleNamespace(draw_handler_add=self.add, draw_handler_remove=self.remove)
        self.bpy = types.SimpleNamespace(types=types.SimpleNamespace(Window=self.window_type), context=self.context)
        gpu_state = types.SimpleNamespace(scissor_get=lambda: self.clip,
            blend_get=lambda: self.blend, scissor_set=self.set_clip,
            scissor_test_set=lambda v: setattr(self, "scissor_enabled", v),
            blend_set=lambda v: setattr(self, "blend", v))
        gpu = types.SimpleNamespace(state=gpu_state,
            shader=types.SimpleNamespace(from_builtin=lambda _: types.SimpleNamespace(bind=lambda: None)))
        drawing: Any = types.ModuleType("narwhal3d.ui")
        drawing._text_width = lambda text, size: len(text)*size*.6
        drawing._draw_round_rect = lambda shader, a, b, c, d, radius, color, border, width: self.record("box", (a,b,c,d), color)
        drawing._draw_rect = lambda shader, a, b, c, d, color: self.record("rect", (a,b,c,d), color)
        drawing._draw_text = lambda x, y, text, size, color: self.record("text", (x,y), color)
        drawing._draw_glyph = lambda shader, tris, color: self.record("glyph", tris, color)
        self.patcher = patch.dict(sys.modules, {"bpy": self.bpy, "gpu": gpu,
            "narwhal3d.ui": drawing, "narwhal3d.design": types.SimpleNamespace(THEME="light")})
        self.patcher.start()
        # from-package imports must use this fixture, not previous test attributes.
        pkg.ui = drawing
        pkg.design = sys.modules["narwhal3d.design"]
        ui._model = ui._handler = ui._window = None
        self.config = ai.ProviderConfig()

    def tearDown(self):
        ui.close()
        ui._model = ui._handler = ui._window = None
        self.patcher.stop()

    def add(self, callback, args, stage):
        handle = object()
        self.calls.append(("add", callback, args, stage, handle))
        return handle

    def remove(self, handle):
        self.calls.append(("remove", handle))

    def set_clip(self, *rect):
        self.clip = rect

    def record(self, kind, rect, color):
        self.paints.append((kind, rect, color, self.clip))

    def open(self):
        return ui.open(self.config, anchor=(1140, 770), window_size=(1280, 800), scale=1.25,
                       on_save=lambda c: None, on_refresh=lambda c: True)

    def test_registration_only_while_open_and_release_after_close(self):
        self.assertFalse(self.calls)
        m = self.open()
        self.assertIs(m, ui._model)
        self.assertEqual(self.calls[0][2:4], ((), "POST_PIXEL"))
        handle = self.calls[0][4]
        self.assertTrue(ui.handle_event(event("ESC"), self.context))
        self.assertFalse(ui.is_open())
        self.assertEqual(self.calls[-1], ("remove", handle))
        self.assertTrue(ui.handle_event(event("ESC", "RELEASE"), self.context))
        self.assertFalse(ui.handle_event(event("ESC"), self.context))
        ui.close()
        self.assertEqual(sum(c[0] == "remove" for c in self.calls), 1)

    def test_reopen_replaces_handler_without_leak(self):
        a = self.open()
        b = self.open()
        self.assertFalse(a.is_open)
        self.assertTrue(b.is_open)
        self.assertEqual([c[0] for c in self.calls], ["add", "remove", "add"])

    def test_missing_window_hooks_fail_clearly(self):
        del self.window_type.draw_handler_add
        with self.assertRaisesRegex(RuntimeError, "Window POST_PIXEL"):
            self.open()
        self.assertFalse(ui.is_open())
        self.assertFalse(self.calls)

    def test_registration_error_no_open_model(self):
        self.window_type.draw_handler_add = lambda *args: (_ for _ in ()).throw(RuntimeError("unsupported"))
        with self.assertRaisesRegex(RuntimeError, "register"):
            self.open()
        self.assertFalse(ui.is_open())
        self.assertIsNone(ui._handler)

    def test_window_isolation_resize_and_status_hook(self):
        m = self.open()
        other = types.SimpleNamespace(window=object())
        self.assertFalse(ui.handle_event(event("ESC"), other))
        self.bpy.context = other
        ui._draw()
        self.assertFalse(self.paints)
        self.bpy.context = self.context
        self.assertTrue(ui.set_discovery(self.config, ["actual"], "Found 1 model"))
        self.window.height = 500
        ui.handle_event(event("MOUSEMOVE"), self.context)
        self.assertEqual(m.window_size, (1280, 500))
        self.assertLessEqual(m.rect[3], 500)
        ui.close()
        self.assertFalse(ui.set_discovery(self.config, [], "late"))

    def test_stub_draw_clips_to_popup_and_restores_state(self):
        m = self.open()
        m.fields["model"].set_text("model-"*30)
        m.focus = "model"
        m.ensure_visible("model")
        m.fields["model"].select_all()
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                self.paints.clear()
                clip = self.clip
                ui.draw_model(m, theme)
                self.assertTrue(self.paints)
                self.assertTrue(any(p[0] == "glyph" for p in self.paints))
                self.assertTrue(any(p[0] == "rect" for p in self.paints))
                self.assertEqual(self.clip, clip)
                self.assertFalse(self.scissor_enabled)
                self.assertEqual(self.blend, "NONE")
                for kind, r, _color, scissor in self.paints:
                    x, y, w, h = scissor
                    self.assertGreaterEqual(x, m.rect[0])
                    self.assertGreaterEqual(y, m.rect[1])
                    self.assertLessEqual(x+w, m.rect[2])
                    self.assertLessEqual(y+h, m.rect[3])
                    self.assertTrue(all(v == round(v) for v in scissor))
                    if kind != "glyph":
                        self.assertTrue(all(v == round(v) for v in r))
                # Main still owns real GPU/font screenshot acceptance.

    def test_short_window_dropdown_paint_stays_bounded(self):
        m = self.open()
        m.resize((360, 260), anchor=(340, 245))
        m.set_discovery(self.config, ["real-model-"+"x"*160]*15, "Unreachable endpoint. "*80, True)
        m.focus = "available"
        m.ensure_visible(m.focus)
        m.activate(m.focus)
        ui.draw_model(m, "dark")
        for _, _, _, (x, y, w, h) in self.paints:
            self.assertGreaterEqual(x, m.rect[0])
            self.assertGreaterEqual(y, m.rect[1])
            self.assertLessEqual(x+w, m.rect[2])
            self.assertLessEqual(y+h, m.rect[3])
        close = next(item for item in m.fixed if item.key == "close")
        self.assertGreaterEqual(ui._intersect(close.rect, m.header)[1], m.body[3])

    def test_font_measurement_matches_physical_paint_size(self):
        measured = []
        m = ui.PopupModel(self.config, anchor=(1140, 770), window_size=(1280, 800),
                         scale=1.25, on_save=lambda c: None, on_refresh=lambda c: False,
                         measure=lambda text, size: measured.append(size) or len(text)*size*.6)
        m.field_offset("model")
        self.assertTrue(measured)
        self.assertTrue(all(size == round(size) for size in measured))

    def test_local_css_text_contrast(self):
        def luminance(hex_color):
            values = [int(hex_color[i:i+2], 16)/255 for i in (1,3,5)]
            values = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
            return sum(a*b for a,b in zip(values, (.2126,.7152,.0722)))
        for theme, palette in ui.PALETTES.items():
            for ink, surface in (("text", "card"), ("text", "input"), ("text", "badge"),
                                 ("muted", "card"), ("muted", "bg"), ("error", "warning")):
                light, dark = sorted((luminance(palette[ink]), luminance(palette[surface])), reverse=True)
                self.assertGreaterEqual((light+.05)/(dark+.05), 4.5, (theme, ink, surface))


def mutations():
    source = SOURCE.read_text()
    cases = [
        ("discovery line cap removed", "enumerate(spans[:limit])", "enumerate(spans)",
         "PopupTests.test_long_status_wrap_never_squeezes_fields"),
        ("caret clipped at End", "round(r[2]-11*self.scale)-round(r[0]+11*self.scale)-1)",
         "round(r[2]-11*self.scale)-round(r[0]+11*self.scale))",
         "PopupTests.test_long_field_caret_remains_inside_clip"),
        ("cancel writes", "    def close(self):\n        self.is_open = False",
         "    def close(self):\n        self.on_save(self.original)\n        self.is_open = False",
         "PopupTests.test_cancel_never_writes"),
        ("invalid save accepted", "        if config is None:\n            return False\n        try:\n            error = self.on_save(config)",
         "        if config is None:\n            config = self.original\n        try:\n            error = self.on_save(config)",
         "PopupTests.test_invalid_save_does_not_call_callback"),
        ("centred instead of anchored", "        x = self.anchor[0] - width - round(8 * s)",
         "        x = (ww - width) / 2", "PopupTests.test_side_anchor_not_center"),
        ("bounds clamp removed", "        x = round(max(margin, min(x, ww - margin - width)))",
         "        x = round(x)", "PopupTests.test_bounds_and_pixel_alignment"),
        ("dismiss release clickthrough", "            return True\n        if not self.is_open:",
         "            return False\n        if not self.is_open:", "PopupTests.test_dismiss_release_and_held_drag_shield"),
        ("stale discovery accepted", "if not self.is_open or connection_key(config) != self.draft_key():",
         "if not self.is_open:", "PopupTests.test_discovery_key_excludes_only_model_and_status"),
        ("custom endpoint overwritten", "if self.fields[key].text == defaults[self.provider_id]:",
         "if True:", "PopupTests.test_provider_defaults_preserve_custom_fields"),
        ("focus scroll disabled", "    def ensure_visible(self, key):\n", "    def ensure_visible(self, key):\n        return\n",
         "PopupTests.test_scroll_and_tab_ensure_visible"),
    ]
    for label, old, new, test in cases:
        if source.count(old) != 1:
            raise AssertionError(f"mutation anchor not unique: {label}")
        with tempfile.TemporaryDirectory(prefix="narwhal-ai-popup-mutation-") as tmp:
            path = Path(tmp)/"ai_settings_popup.py"
            shutil.copyfile(SOURCE, path)
            path.write_text(source.replace(old, new, 1))
            env = dict(os.environ, AI_SETTINGS_POPUP_SOURCE=str(path), PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run([sys.executable, "-B", __file__, test], env=env,
                                    capture_output=True, text=True, timeout=25, check=False)
            if result.returncode == 0 or "FAIL:" not in result.stderr:
                raise AssertionError(f"mutation not killed by assertion: {label}\n{result.stdout}\n{result.stderr}")
            print(f"RED (expected): {label} — {test}")
            # Restore only the disposable copy and prove exactly the same test green.
            shutil.copyfile(SOURCE, path)
            restored = subprocess.run([sys.executable, "-B", __file__, test], env=env,
                                      capture_output=True, text=True, timeout=25, check=False)
            if restored.returncode:
                raise AssertionError(f"restored test not green: {label}\n{restored.stderr}")
            print(f"GREEN (restored copy): {label}")
    print(f"{len(cases)} isolated mutations rejected; production sources untouched.")


if __name__ == "__main__":
    if "--mutations" in sys.argv:
        mutations()
    else:
        unittest.main()
