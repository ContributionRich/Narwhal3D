"""Command-aware display previews for Helix and Spiral."""
import importlib.util
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Preview imports no package modules; load the real CPlane module under a neutral name.
spec = importlib.util.spec_from_file_location("coil_cplane", os.path.join(ROOT, "narwhal3d", "cplane.py"))
cplane = importlib.util.module_from_spec(spec)
sys.modules["coil_cplane"] = cplane
spec.loader.exec_module(cplane)
spec = importlib.util.spec_from_file_location("coil_preview", os.path.join(ROOT, "narwhal3d", "preview.py"))
preview = importlib.util.module_from_spec(spec)
sys.modules["coil_preview"] = preview
spec.loader.exec_module(preview)
checks = []


def check(condition, label):
    checks.append((bool(condition), label))


def command(**values):
    base = dict(_vertical=False, _flat=False, _reverse_twist=False,
                _coil_mode="Turns", size_mode="radius",
                opt={"Turns": 2.0, "Pitch": 1.0})
    base.update(values)
    return SimpleNamespace(**base)


check(preview.has_preview("Helix") and preview.has_preview("Spiral"),
      "both coil commands advertise live previews")
axis = preview.segments("Helix", [(0, 0, 0)], (0, 0, 5),
                        cplane.WORLD_TOP, cmd=command())
check(axis == [[(0, 0, 0), (0, 0, 5)]],
      "Helix axis start rubber-bands to the cursor")
helix = preview.segments("Helix", [(0, 0, 0), (0, 0, 5)], (2, 0, 0),
                         cplane.WORLD_TOP, cmd=command())
check(len(helix) == 2 and len(helix[1]) == 49
      and abs(helix[1][0][0] - 2.0) < 1e-12
      and abs(helix[1][-1][2] - 5.0) < 1e-12,
      "Helix preview shows axis plus the complete option-driven coil")
reverse = preview.segments("Helix", [(0, 0, 0), (0, 0, 5)], (2, 0, 0),
                           cplane.WORLD_TOP, cmd=command(_reverse_twist=True))
check(helix[1][1][1] > 0.0 and reverse[1][1][1] < 0.0,
      "ReverseTwist immediately flips preview winding")
pitched = preview.segments("Helix", [(0, 0, 0), (0, 0, 5)], (2, 0, 0),
                           cplane.WORLD_TOP,
                           cmd=command(_coil_mode="Pitch", opt={"Turns": 2, "Pitch": 2.5}))
check(len(pitched[1]) == 49,
      "Pitch precedence updates the preview turn count")

spiral = preview.segments("Spiral", [(0, 0, 0), (0, 0, 5), 1.0], (3, 0, 5),
                          cplane.WORLD_TOP, cmd=command())
check(len(spiral) == 2 and abs(spiral[1][0][0] - 1.0) < 1e-12
      and abs(spiral[1][-1][0] - 3.0) < 1e-9,
      "Spiral preview grows from the fixed first radius to the live second radius")
flat = preview.segments("Spiral", [(0, 0, 0), (0, 0, 5), 1.0], (3, 0, 5),
                        cplane.WORLD_TOP, cmd=command(_flat=True))
check({round(point[2], 12) for point in flat[1]} == {0.0},
      "Flat preview has exactly zero axial rise")
front = preview.segments("Helix", [(0, 0, 0), (0, -5, 0)], (2, 0, 0),
                         cplane.WORLD_FRONT, cmd=command())
check(abs(front[1][-1][1] + 5.0) < 1e-12,
      "coil previews follow a Front CPlane axis")
vertical = preview.segments("Helix", [(0, 0, 0), 5.0], (2, 0, 0),
                            cplane.WORLD_RIGHT, cmd=command(_vertical=True))
check(vertical and abs(vertical[0][-1][0] - 5.0) < 1e-12,
      "Vertical preview resolves numeric axis length along the active CPlane normal")
check(preview.segments("Helix", [(0, 0, 0), (0, 0, 5)], (2, 0, 0),
                       cplane.WORLD_TOP, cmd=None) == [],
      "missing command state draws nothing instead of guessing options")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
