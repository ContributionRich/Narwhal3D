"""Sketch preview colours match completed layer-coloured curves, without needing a GPU.

Load the actual UI colour selector by AST so this check also runs in a bootstrap-only
checkout. The draw-path assertion ensures the tested selector drives the live preview.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
source = (root / "narwhal3d/ui.py").read_text()
tree = ast.parse(source)
nodes = [n for n in tree.body if
         isinstance(n, ast.FunctionDef) and n.name == "command_preview_color"
         or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and
             t.id == "_LAYER_COLOR_PREVIEW_COMMANDS" for t in n.targets)]
layer = {"index": 3, "color": (255, 32, 64, 255)}
model = SimpleNamespace(current_layer_index=3, layers=lambda: [layer])
ns = {"app_state": SimpleNamespace(model=model), "WIRE_COLOR": (0.2, 0.3, 0.4),
      "PREVIEW_COLOR": (0.9, 0.8, 0.7, 1.0)}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / "narwhal3d/ui.py"), "exec"), ns)
color = ns["command_preview_color"]
for name in ("Curve", "InterpCrv", "HandleCurve", "Line", "Polyline", "Circle", "Arc"):
    assert color(SimpleNamespace(name=name)) == (1.0, 32 / 255, 64 / 255, 1.0), name
layer["color"] = (0, 180, 255, 255)
assert color(SimpleNamespace(name="Curve")) == (0.0, 180 / 255, 1.0, 1.0), "live recolour"
layer["color"] = (0, 0, 0, 255)
assert color(SimpleNamespace(name="Curve")) == ns["WIRE_COLOR"] + (1.0,), "black layer fallback"
assert color(SimpleNamespace(name="FilletEdge")) == ns["PREVIEW_COLOR"], "operation highlight"
model.current_layer_index = 8
assert color(SimpleNamespace(name="Curve")) == ns["WIRE_COLOR"] + (1.0,), "missing layer"
ns["app_state"].model = None
assert color(SimpleNamespace(name="Curve")) == ns["WIRE_COLOR"] + (1.0,), "no document"
draw = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "draw_command_preview")
assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
           n.func.attr == "uniform_float" and len(n.args) == 2 and
           isinstance(n.args[0], ast.Constant) and n.args[0].value == "color" and
           isinstance(n.args[1], ast.Call) and isinstance(n.args[1].func, ast.Name) and
           n.args[1].func.id == "command_preview_color" for n in ast.walk(draw)), "live draw uses layer colour"
print("PASS: sketch previews use the live destination-layer colour; operation highlights unchanged")
