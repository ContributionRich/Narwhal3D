"""Length measures exact selected solid edges, not sampled display polylines."""
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import bridge, commands, curve_inputs  # noqa: E402


class Context:
    def __init__(self, geometry, parts):
        self.geometry, self.parts = geometry, parts
        self.model = types.SimpleNamespace(unit_system=lambda: "Millimeters")
        self.changes = []

    def geom_by_name(self, name):
        return self.geometry.get(name)

    def selected_subobjects(self):
        return self.parts

    def selected_objects(self):
        return [types.SimpleNamespace(name=name) for name in self.geometry]

    def note_change(self, *args):
        self.changes.append(args)


def measured_number(message):
    return float(message.split(" = ", 1)[1].split()[0])


box = bridge.make_box((0, 0, 0), (3, 4, 0), 5)
box_parts = bridge.subobject_parts(box)
edge_id = box_parts["edges"][0][0]
exact = curve_inputs.resolve(Context({"Box": box}, []), ("edge", "Box", edge_id))
expected = commands.LengthCmd._curve_length(exact)
before = box.Encode()
ctx = Context({"Box": box}, [("Box", "Edge", edge_id)])
message, done = commands.LengthCmd().begin(ctx)
assert done and abs(measured_number(message) - expected) < 1e-9, message
assert "edge" in message and not ctx.changes and box.Encode() == before

cylinder = bridge.make_cylinder((0, 0, 0), 2, 5)
ctx = Context({"Cylinder": cylinder}, [])
circular_id = None
for identifier, _points in bridge.subobject_parts(cylinder)["edges"]:
    edge = curve_inputs.resolve(ctx, ("edge", "Cylinder", identifier))
    if edge.TryGetCircle() is not None:
        circular_id = identifier
        break
assert circular_id is not None, "fixture must expose an exact circular edge"
ctx.parts = [("Cylinder", "Edge", circular_id)]
message, done = commands.LengthCmd().begin(ctx)
assert done and abs(measured_number(message) - 4 * math.pi) < 1e-8, message
ctx.geometry["Box"] = box
ctx.parts.append(("Box", "Edge", edge_id))
message, done = commands.LengthCmd().begin(ctx)
assert done and abs(measured_number(message) - (4 * math.pi + expected)) < 1e-8, message
assert message.startswith("Cumulative length"), message
ctx.parts = [("Box", "Edge", 999999)]
message, done = commands.LengthCmd().begin(ctx)
assert done and "unavailable" in message and "Length =" not in message, message
assert not ctx.changes and box.Encode() == before
print("PASS: straight/circular solid edges, cumulative lengths, stale edge refusal and read-only measurement")
