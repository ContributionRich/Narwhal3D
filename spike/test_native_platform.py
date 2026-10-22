#!/usr/bin/env python3
"""Hermetic ABI tests for the minimal platform_core ctypes boundary."""
from __future__ import annotations

import ctypes
import inspect
import os
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("narwhal3d")
PACKAGE.__path__ = [str(ROOT / "narwhal3d")]
sys.modules.setdefault("narwhal3d", PACKAGE)

from narwhal3d import filedialog
from narwhal3d import native_platform as native


def _text(value):
    return ctypes.string_at(value.data, value.length).decode("utf-8")


class _Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeCDLL:
    def __init__(self, abi=1):
        self.abi = abi
        self.dialog_status = native._STATUS_OK
        self.dialog_path = "C:\\模型 files\\part one.narwhal"
        self.dialog_token = 0
        self.message_status = native._STATUS_OK
        self.message_choice = native._CHOICE_YES
        self.dialogs = []
        self.messages = []
        self.discarded = []
        self.narwhal_platform_core_abi_version = _Function(lambda: self.abi)
        self.narwhal_platform_file_dialog_v1 = _Function(self._dialog)
        self.narwhal_platform_dialog_result_discard_v1 = _Function(self._discard)
        self.narwhal_platform_message_box_v1 = _Function(self._message)

    def _dialog(self, request_pointer, result_pointer):
        request = ctypes.cast(request_pointer, ctypes.POINTER(native._FileRequest)).contents
        pairs = []
        for index in range(request.filter_count):
            pair = request.filters[index]
            pairs.append((pair.struct_size, pair.abi_version,
                          _text(pair.description), _text(pair.glob)))
        self.dialogs.append({
            "size": request.struct_size,
            "abi": request.abi_version,
            "kind": request.dialog_kind,
            "reserved": request.reserved,
            "owner": request.owner_window,
            "title": _text(request.title),
            "start": _text(request.start_path),
            "pairs": pairs,
            "capacity": request.output_capacity,
        })
        result = ctypes.cast(result_pointer, ctypes.POINTER(native._DialogResult)).contents
        result.struct_size = ctypes.sizeof(native._DialogResult)
        result.abi_version = native.ABI_VERSION
        result.status = self.dialog_status
        result.retry_token = self.dialog_token
        if self.dialog_status == native._STATUS_OK:
            encoded = self.dialog_path.encode("utf-8")
            ctypes.memmove(request.output, encoded + b"\0", len(encoded) + 1)
            result.required_capacity = len(encoded) + 1
            result.written_length = len(encoded)
        return self.dialog_status

    def _discard(self, token):
        self.discarded.append(int(token))
        return native._STATUS_OK

    def _message(self, request_pointer, result_pointer):
        request = ctypes.cast(request_pointer, ctypes.POINTER(native._MessageRequest)).contents
        self.messages.append((request.struct_size, request.abi_version,
                              request.reserved0, request.reserved1,
                              request.owner_window, _text(request.title),
                              _text(request.message)))
        result = ctypes.cast(result_pointer, ctypes.POINTER(native._MessageResult)).contents
        result.struct_size = ctypes.sizeof(native._MessageResult)
        result.abi_version = native.ABI_VERSION
        result.status = self.message_status
        result.choice = self.message_choice
        return self.message_status


def _install(fake):
    os.environ[native._ENV] = native.__file__
    native._reset_for_tests(lambda _path: fake)


class NativePlatformTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(native._ENV, None)
        native._reset_for_tests()
        filedialog.set_backend(None)

    def test_exact_struct_field_order(self):
        expected = {
            native._Utf8Slice: ("data", "length"),
            native._FilterPair: ("struct_size", "abi_version", "description", "glob"),
            native._FileRequest: ("struct_size", "abi_version", "dialog_kind", "reserved",
                                  "owner_window", "title", "start_path", "filters",
                                  "filter_count", "output", "output_capacity"),
            native._DialogResult: ("struct_size", "abi_version", "status", "error_code",
                                   "system_error", "selected_filter_index",
                                   "required_capacity", "written_length", "retry_token"),
            native._MessageRequest: ("struct_size", "abi_version", "reserved0", "reserved1",
                                     "owner_window", "title", "message"),
            native._MessageResult: ("struct_size", "abi_version", "status", "choice",
                                    "error_code", "system_error"),
        }
        for record, names in expected.items():
            self.assertEqual(tuple(field[0] for field in record._fields_), names)

    def test_exact_file_structs_utf8_filters_and_kinds(self):
        fake = FakeCDLL()
        _install(fake)
        filters = (("Narwhal 模型", "*.narwhal;*.3dm"),
                   ("Spaced images", "*.png *.jpg"))
        start = "C:\\工作 files\\a model.narwhal"

        self.assertEqual(native.open_file("Open 模型", start, filters),
                         (native.OK, fake.dialog_path))
        self.assertEqual(native.save_file("Save As", start, filters),
                         (native.OK, fake.dialog_path))
        self.assertEqual([row["kind"] for row in fake.dialogs],
                         [native._DIALOG_OPEN, native._DIALOG_SAVE])
        row = fake.dialogs[0]
        self.assertEqual((row["size"], row["abi"], row["reserved"], row["owner"]),
                         (ctypes.sizeof(native._FileRequest), 1, 0, 0))
        self.assertEqual((row["title"], row["start"]), ("Open 模型", start))
        self.assertEqual(row["capacity"], 512 * 1024)
        self.assertEqual(row["pairs"], [
            (ctypes.sizeof(native._FilterPair), 1, *filters[0]),
            (ctypes.sizeof(native._FilterPair), 1, *filters[1]),
        ])

    def test_dialog_status_mapping_and_token_discard_on_every_path(self):
        cases = ((native._STATUS_OK, native.OK),
                 (native._STATUS_CANCEL, native.CANCEL),
                 (native._STATUS_UNAVAILABLE, native.UNAVAILABLE),
                 (native._STATUS_ERROR, native.ERROR))
        for token, (raw_status, expected) in enumerate(cases, 41):
            fake = FakeCDLL()
            fake.dialog_status = raw_status
            fake.dialog_token = token
            _install(fake)
            status, value = native.open_file("Open", "C:\\", (("All", "*.*"),))
            self.assertEqual(status, expected)
            self.assertEqual(value, fake.dialog_path if expected == native.OK else None)
            self.assertEqual(fake.discarded, [token])

    def test_message_yes_no_cancel_and_unavailable_error(self):
        cases = (
            (native._STATUS_OK, native._CHOICE_YES, (native.OK, "yes")),
            (native._STATUS_OK, native._CHOICE_NO, (native.OK, "no")),
            (native._STATUS_CANCEL, native._CHOICE_CANCEL, (native.CANCEL, None)),
            (native._STATUS_UNAVAILABLE, 0, (native.UNAVAILABLE, None)),
            (native._STATUS_ERROR, 0, (native.ERROR, None)),
        )
        fake = FakeCDLL()
        _install(fake)
        for status, choice, expected in cases:
            fake.message_status, fake.message_choice = status, choice
            self.assertEqual(native.ask_yes_no_cancel("Narwhal 3D", "Save café 模型?"),
                             expected)
        self.assertTrue(all(row[:5] == (ctypes.sizeof(native._MessageRequest), 1, 0, 0, 0)
                            for row in fake.messages))
        self.assertEqual(fake.messages[0][5:], ("Narwhal 3D", "Save café 模型?"))

    def test_wrong_abi_is_unavailable(self):
        _install(FakeCDLL(abi=99))
        self.assertEqual(native.save_file("Save", "C:\\", ()),
                         (native.UNAVAILABLE, None))

    def test_windows_backend_is_native_only_and_delegates(self):
        old_platform = filedialog.sys.platform
        old_headless = filedialog.headless
        old_which = filedialog.shutil.which
        calls = []
        old_save = native.save_file
        old_open = native.open_file
        old_ask = native.ask_yes_no_cancel
        try:
            native.save_file = lambda *args: calls.append(("save", args)) or (native.OK, "s")
            native.open_file = lambda *args: calls.append(("open", args)) or (native.OK, "o")
            native.ask_yes_no_cancel = lambda *args: calls.append(("ask", args)) or (native.OK, "yes")
            filedialog.sys.platform = "win32"
            filedialog.headless = lambda: False
            filedialog.shutil.which = lambda name: self.fail(f"Linux probe on Windows: {name}")
            filedialog.set_backend(None)
            backend = filedialog.backend()
            if not isinstance(backend, filedialog._WindowsNative):
                self.fail(f"Windows chose {backend!r} instead of _WindowsNative")
            self.assertEqual(backend.save("T", "S", (("D", "G"),)), (native.OK, "s"))
            self.assertEqual(backend.open("T", "S", ()), (native.OK, "o"))
            self.assertEqual(backend.ask("T", "M"), (native.OK, "yes"))
            self.assertEqual([item[0] for item in calls], ["save", "open", "ask"])
            native.open_file = lambda *_args: (native.ERROR, None)
            native.ask_yes_no_cancel = lambda *_args: (native.ERROR, None)
            self.assertEqual(backend.open("T", "S", ()), (filedialog.UNAVAILABLE, None))
            self.assertEqual(backend.ask("T", "M"), (filedialog.UNAVAILABLE, None))
            self.assertIn("native Windows", filedialog.unavailable_hint())
        finally:
            filedialog.sys.platform = old_platform
            filedialog.headless = old_headless
            filedialog.shutil.which = old_which
            native.save_file = old_save
            native.open_file = old_open
            native.ask_yes_no_cancel = old_ask

    def test_source_boundaries(self):
        native_source = Path(native.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "WinDLL", "windll", "GetOpenFileName",
                          "GetSaveFileName", "MessageBoxW", "tkinter"):
            self.assertNotIn(forbidden, native_source)
        windows_source = inspect.getsource(filedialog._WindowsNative)
        self.assertNotIn("ctypes", windows_source)
        self.assertNotIn("shutil", windows_source)
        self.assertNotIn("subprocess", windows_source)
        self.assertNotIn("os.path", windows_source)
        self.assertIn("native_platform.save_file", windows_source)
        self.assertIn("native_platform.open_file", windows_source)
        self.assertIn("native_platform.ask_yes_no_cancel", windows_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
