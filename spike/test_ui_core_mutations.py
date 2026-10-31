"""Mutation qualification in disposable /tmp sources, never the shared worktree.

Uses one compiler worker and the shared heavy lock only during compiler commands.
All logs stay in the printed evidence directory. Requires a green development .so.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NATIVE = [
    ("set", "lib.rs", "1 => f.set_text(data)", "1 => ()"),
    ("insert", "lib.rs", "2 => result = f.insert(data)", "2 => result = false"),
    ("backspace", "lib.rs", "3 => f.backspace()", "3 => ()"),
    ("delete", "lib.rs", "4 => f.delete()", "4 => ()"),
    ("set_caret", "lib.rs", "5 => f.set_caret(value, flags != 0)", "5 => ()"),
    ("move", "lib.rs", "6 => f.r#move(value, flags != 0)", "6 => ()"),
    ("home", "lib.rs", "7 => f.home(flags != 0)", "7 => ()"),
    ("end", "lib.rs", "8 => f.end(flags != 0)", "8 => ()"),
    ("select_all", "lib.rs", "9 => f.select_all()", "9 => ()"),
    ("raw_text", "lib.rs", "assert!(f.assign_text(data));", "f.set_text(data);"),
    ("raw_caret", "lib.rs", "12 => f.assign_caret(value as usize)", "12 => ()"),
    ("raw_anchor", "lib.rs", "Some(value as usize)", "Some(0)"),
    ("multiline", "lib.rs", "14 => f.multiline = value != 0", "14 => ()"),
    ("limit", "lib.rs", "15 => f.max_chars = if flags != 0 { None } else { Some(value) }", "15 => ()"),
    ("delete_selection", "lib.rs", "16 => result = f.delete_selection()", "16 => result = false"),
    ("revision", "lib.rs", "e.text_revision += 1;", "e.text_revision += 0;"),
    ("delta_start", "lib.rs", "reply.change_start = range.0 as u64;", "reply.change_start = 0;"),
    ("capacity", "lib.rs", "size != std::mem::size_of::<State>()", "false"),
    ("stale_read", "lib.rs", "revision != e.text_revision", "false"),
    ("close", "lib.rs", "r.entries.remove(&handle).is_some()", "r.entries.contains_key(&handle)"),
    ("normalization", "editor.rs", "if !self.multiline {", "if self.multiline {"),
    ("empty_insert", "editor.rs", "if s.is_empty() {\n            return false;", "if s.is_empty() {\n            self.delete_selection();\n            return false;"),
    ("shift_anchor", "editor.rs", "if self.anchor.is_none() {", "if true {"),
    ("collapse", "editor.rs", "if delta != 0 {", "if false {"),
    ("layout_epoch", "layout_contract.rs", "self.values.clear();", "// retain stale metrics"),
    ("layout_failure", "layout_contract.rs", "if self.failed {", "if false {"),
    ("layout_shaping", "layout_contract.rs", "Ok(kerned(s))", "Ok(s.len() as f64 * 10.0)"),
]
PYTHON = [
    ("ownership", "self._native = Editor(lib, text, multiline, max_chars)", "super().__init__(text, multiline, max_chars)"),
    ("handover_text", "state = dict(text=n.text(),", "state = dict(text='',"),
    ("projection_cache", "if self.text_revision != s.text_revision:", "if self.text_cache is None:"),
    ("delta_count", "start, end, count = self.patch if self.patch is not None else (0, 0, s.length)", "start, end, count = (0, 0, s.length)"),
    ("size_check", "out.length > LIMIT", "False"),
    ("clone_owner", "other._native = self._native.clone()", "other._native = self._native"),
    ("no_replay", "if self.failed:\n            raise RuntimeError(\"editor unavailable after a native failure\")", "if False:\n            raise RuntimeError(\"editor unavailable after a native failure\")"),
]


def main():
    evidence = Path(tempfile.mkdtemp(prefix="ui-mut-", dir="/tmp"))
    crate = evidence / "ui_core"
    shutil.copytree(ROOT / "native/ui_core", crate)
    source = evidence / "narwhal3d"
    source.mkdir()
    for name in ("textfield.py", "native_textfield.py"):
        shutil.copyfile(ROOT / "narwhal3d" / name, source / name)
    env = dict(os.environ, UI_CORE_SOURCE_DIR=str(evidence))
    control_lib = env["NARWHAL_UI_CORE_LIBRARY"]
    target = evidence / "target"
    receipts = []

    def run(name, command):
        with (evidence / (name + ".log")).open("w") as log:
            return subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120).returncode

    test = [sys.executable, "-B", str(ROOT / "spike/test_ui_core_editor.py")]
    assert run("initial-control", test) == 0
    for name, file, old, new in NATIVE:
        path = crate / "src" / file
        original = path.read_text()
        assert old in original, (name, old)
        path.write_text(original.replace(old, new, 1))
        common = ["--release", "--manifest-path", str(crate / "Cargo.toml"), "--target-dir", str(target), "-j", "1"]
        compiler = ["flock", "-w", "60", "/tmp/narwhal3d-heavy-build.lock", "cargo"]
        try:
            assert run(name + "-build", compiler + ["build"] + common) == 0, name + " compile failure is NOT a kill"
            env["NARWHAL_UI_CORE_LIBRARY"] = str(target / "release/libnarwhal_ui_core.so")
            rust_rc = run(name + "-rust", compiler + ["test"] + common)
            py_rc = run(name + "-python", test)
            assert rust_rc or py_rc, name + " SURVIVED"
            receipts.append((name, "RED", rust_rc, py_rc))
        finally:
            path.write_text(original)
        print(receipts[-1], flush=True)
    env["NARWHAL_UI_CORE_LIBRARY"] = control_lib
    path = source / "native_textfield.py"
    for name, old, new in PYTHON:
        original = path.read_text()
        assert old in original, (name, old)
        path.write_text(original.replace(old, new, 1))
        try:
            rc = run(name + "-python", test)
            assert rc, name + " SURVIVED"
            receipts.append((name, "RED", rc))
        finally:
            path.write_text(original)
        print(receipts[-1], flush=True)
    assert run("restored-control", test) == 0
    path = source / "textfield.py"
    original = path.read_text()
    old = 'if measure(text[start:p1].rstrip(" ")) <= width:'
    new = 'if (measure(text[start:p1].rstrip(" ")), measure(text[start:p1].rstrip(" ")))[1] <= width:'
    assert old in original
    layout_test = [sys.executable, "-B", str(ROOT / "spike/test_ui_core_layout.py")]
    assert run("layout-control", layout_test) == 0
    try:
        path.write_text(original.replace(old, new, 1))
        assert run("layout-duplicate-callback", layout_test) != 0
        receipts.append(("layout-duplicate-callback", "RED"))
    finally:
        path.write_text(original)
    assert run("layout-restored-control", layout_test) == 0
    print(f"{len(receipts)} mutants RED; restored control GREEN; logs: {evidence}")


if __name__ == "__main__":
    main()
