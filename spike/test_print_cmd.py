"""Headless-Blender tests for Print options, file selection, and PDF geometry.

A scripted viewport and file-dialog backend make inputs deterministic; the PDF is
read back to verify physical placement in points.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import filedialog, print2d, print_commands  # noqa: E402
from narwhal3d.model import RhinoModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


class Dlg:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def save(self, title, start, filters):
        self.calls += 1
        return self.answers.pop(0)

    def open(self, *a):
        raise AssertionError("open dialog")

    def ask(self, *a):
        raise AssertionError("ask dialog")


TOP = print2d.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 200.0, 150.0)
PERSP = print2d.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 200.0, 150.0, perspective=True)
TMP = tempfile.mkdtemp(prefix="narwhal_print_")
MM_PT = 72.0 / 25.4


def last():
    return app_state.history[-1] if app_state.history else ""


def pdfinfo(path):
    if not shutil.which("pdfinfo"):
        return None
    r = subprocess.run(["pdfinfo", path], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def run():
    app_state.set_model(RhinoModel())
    app_state.active_command = None
    print_commands.view_provider = lambda: TOP
    for t in ("Circle", "w0,0,0", "20"):
        app_state.feed_line(t)

    # 1. Print, Scale=1:1, Enter -> dialog -> PDF
    out = os.path.join(TMP, "one.pdf")
    dlg = Dlg([(filedialog.OK, out)])
    filedialog.set_backend(dlg)
    for t in ("Print", "Scale", "1:1", ""):
        app_state.feed_line(t)
    info = pdfinfo(out)
    check(os.path.exists(out) and dlg.calls == 1 and "Printed 1 curve" in last(),
          "Print: Enter opens the file dialog and writes the PDF", f"{last()!r}")
    check(info is None or (re.search(r"^Pages:\s+1$", info, re.M)
                           and "792 x 612 pts" in info),
          "a real reader accepts it: one Letter-landscape page", f"{info!r}")
    data = open(out, "rb").read().decode("latin-1") if os.path.exists(out) else ""
    m = re.search(r"([-\d.]+) ([-\d.]+) m\b", data)
    want = ((279.4 / 2 + 20.0) * MM_PT, (215.9 / 2) * MM_PT)
    got = (float(m.group(1)), float(m.group(2))) if m else None
    check(got is not None and abs(got[0] - want[0]) < 1e-3
          and abs(got[1] - want[1]) < 1e-3,
          "at 1:1 the circle starts EXACTLY 20 mm right of the page centre",
          f"got {got} want {want}")

    # 1b. 1:2 -- an ASYMMETRIC scale, so an inverted ratio cannot pass (1:1 could
    # not tell a/b from b/a: that mutation survived the first version of this suite)
    half = os.path.join(TMP, "half.pdf")
    for t in ("Print", "Scale", "1:2", half):
        app_state.feed_line(t)
    data = open(half, "rb").read().decode("latin-1") if os.path.exists(half) else ""
    m = re.search(r"([-\d.]+) ([-\d.]+) m\b", data)
    got = float(m.group(1)) if m else None
    want = (279.4 / 2 + 10.0) * MM_PT
    check(got is not None and abs(got - want) < 1e-3,
          "at 1:2 the r=20 circle prints with a 10 mm radius (paper:model)",
          f"got x={got} want {want}")

    # 2. a typed name without extension -> .pdf, no dialog
    dlg = Dlg([])
    filedialog.set_backend(dlg)
    typed = os.path.join(TMP, "typed")
    for t in ("Print", typed):
        app_state.feed_line(t)
    check(os.path.exists(typed + ".pdf") and dlg.calls == 0,
          "a typed file name prints without a dialog, adding .pdf", f"{last()!r}")

    # 3. .ai and .svg by extension
    for ext in ("ai", "svg"):
        p = os.path.join(TMP, f"x.{ext}")
        app_state.feed_line("Print")
        app_state.feed_line(p)
        ok = os.path.exists(p)
        if ext == "ai":
            info = pdfinfo(p)
            ok = ok and (info is None or "Pages:" in info)
        check(ok, f"Print to .{ext} writes a .{ext} file", f"{last()!r}")


    # 4. a format Print does not write: refused by name
    p = os.path.join(TMP, "x.dwg")
    app_state.feed_line("Print")
    app_state.feed_line(p)
    check(not os.path.exists(p) and "not implemented" in last(),
          "Print to .dwg is refused by name", f"{last()!r}")

    # 5. options reach the page: A4 Portrait
    p = os.path.join(TMP, "a4.pdf")
    for t in ("Print", "Size", "A4", "Orientation", "Portrait", p):
        app_state.feed_line(t)
    info = pdfinfo(p)
    check(os.path.exists(p) and (info is None or "595.276 x 841.89 pts" in info),
          "Size=A4 Orientation=Portrait gives an A4 portrait page", f"{info!r}")

    # 6. perspective refused, nothing written
    print_commands.view_provider = lambda: PERSP
    p = os.path.join(TMP, "persp.pdf")
    app_state.feed_line("Print")
    app_state.feed_line(p)
    check(not os.path.exists(p) and "PERSPECTIVE" in last(),
          "printing a perspective view is refused by name", f"{last()!r}")
    print_commands.view_provider = lambda: TOP

    # 7. cancel the dialog -> nothing
    filedialog.set_backend(Dlg([(filedialog.CANCEL, None)]))
    n_before = len(os.listdir(TMP))
    app_state.feed_line("Print")
    app_state.feed_line("")
    check("cancelled" in last() and len(os.listdir(TMP)) == n_before,
          "cancelling the dialog prints nothing", f"{last()!r}")

    # 8. no dialog -> asks for the name on the command line
    filedialog.set_backend(type("U", (), {
        "save": lambda *a: (filedialog.UNAVAILABLE, None)})())
    app_state.feed_line("Print")
    app_state.feed_line("")
    waiting = app_state.active_command is not None
    p = os.path.join(TMP, "fallback.pdf")
    app_state.feed_line(p)
    check(waiting and os.path.exists(p),
          "with no dialog, Print asks for the file name instead", f"{last()!r}")

    # 9. a solid prints its visible outline; the command no longer claims
    #    silhouettes are missing (this used to assert that they were)
    for t in ("Box", "w30,0,0", "w40,10,0", "10"):
        app_state.feed_line(t)
    p = os.path.join(TMP, "solid.pdf")
    app_state.feed_line("Print")
    app_state.feed_line(p)
    check(os.path.exists(p) and "silhouette" not in last(),
          "printing a solid prints its outline and no 'not implemented' note", f"{last()!r}")

    from narwhal3d import commands
    check(commands.COMMANDS.get("print").__class__.__module__.endswith(
        "print_commands"), "the registry's 'print' uses print_commands")


try:
    run()
except Exception:  # noqa: BLE001
    results.append((False, "suite raised", traceback.format_exc()))
finally:
    filedialog.set_backend(None)
    print_commands.view_provider = None
    shutil.rmtree(TMP, ignore_errors=True)

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} print-command checks passed")
if failed:
    raise SystemExit(1)
