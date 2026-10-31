"""Run unchanged caller suites with explicit backend-route receipts, plus layer AST.

The layer probe executes unchanged function bodies with only redraw stubbed; this
is an injected unit trace, not Blender/real keyboard or clipboard acceptance.
"""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("test_textfield.py", "test_shell_input.py", "test_bugreport.py",
          "test_printdialog.py", "test_ai_assistant.py", "test_ai_settings_popup.py")
WRAPPER = '''
import runpy, sys
counts = {"native": 0, "fallback": 0}
def profile(frame, event, value):
    if event == "return" and frame.f_code.co_name == "__init__" and frame.f_code.co_filename.endswith("native_textfield.py"):
        obj = frame.f_locals.get("self")
        if obj is not None and type(obj).__name__ == "TextField":
            counts["native" if obj._native is not None else "fallback"] += 1
sys.setprofile(profile)
path = sys.argv[1]
sys.argv = [path]
try:
    runpy.run_path(path, run_name="__main__")
except SystemExit as e:
    if e.code: raise
finally:
    sys.setprofile(None)
print("ROUTE", counts)
assert counts["native"] > 0, "suite never constructed a native field"
assert counts["fallback"] == 0, "ordinary caller lacks the required native owner"
'''


def main():
    logs = Path(tempfile.mkdtemp(prefix="ui-callers-"))
    for suite in SUITES:
        with (logs / (suite + ".log")).open("w") as stream:
            result = subprocess.run([sys.executable, "-B", "-c", WRAPPER, str(ROOT / "spike" / suite)],
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=60)
        assert result.returncode == 0, f"{suite}: see {logs}"
        print(suite, (logs / (suite + ".log")).read_text().splitlines()[-1])
    spec = importlib.util.spec_from_file_location("layer_trace_textfield", ROOT / "narwhal3d/textfield.py")
    tf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tf)
    tree = ast.parse((ROOT / "narwhal3d/ui.py").read_text())
    names = {"layer_rename_begin", "layer_rename_editing", "layer_rename_cancel", "layer_rename_key"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    ns = dict(textfield=tf, _layer_edit=None, _tag_redraw_bands=lambda: None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "ui.py:layer-functions", "exec"), ns)
    assert ns["layer_rename_begin"]("Original")
    f = ns["_layer_edit"]["field"]
    assert f._native is not None and f.selection() == (0, 8)
    ns["layer_rename_key"]("paste", "New\r\nlayer😀")
    assert f.text == "New layer😀"
    ns["layer_rename_key"]("left", shift=True)
    assert f.selected_text() == "😀"
    ns["layer_rename_key"]("back")
    assert f.text == "New layer"
    ns["layer_rename_key"]("esc")
    assert not ns["layer_rename_editing"]()
    print("layer unchanged AST injected trace: native PASS")
    print("caller logs:", logs)


if __name__ == "__main__":
    main()
