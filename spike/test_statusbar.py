#!/usr/bin/env python3
"""The status bar -- the pure MODEL (pane list, kinds, rendering).

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_statusbar.py

The status bar is drawn in ui.py; this tests only the model in
narwhal3d/statusbar.py, which is pure -- no bpy, no rhino3dm -- loaded by
file path exactly like cmdbar.py and gumball.py.

Every assertion traces to a source:
  * the 14 panes, their ORDER, and the two alignment panes
    -> docs/SOURCE_DATA.md section 4 (the reference screenshot)
  * each pane's kind and whether it is clickable
    -> the reference "Status bar" documentation (per-pane docs)
  * the "Gumball (CPlane)" / "Auto CPlane (Object)" renderings
    -> the screenshot, which shows the alignment in the text
The final section renders all 14 panes with screenshot_state() and checks the
whole bar against the measured screenshot strings, one by one.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "narwhal_statusbar", os.path.join(ROOT, "narwhal3d", "statusbar.py"))
sb = importlib.util.module_from_spec(spec)
sys.modules["narwhal_statusbar"] = sb
spec.loader.exec_module(sb)

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


check("bpy" not in sys.modules, "statusbar.py imports without bpy")

# ===========================================================================
print()
print("the pane list: 14 panes, in the screenshot's left-to-right order")
# ===========================================================================
# docs/SOURCE_DATA.md section 4, MEASURED from the reference screenshot:
#   CPlane | x y z | Millimeters | Default (layer) | Grid Snap | Ortho |
#   Planar | Osnap | SmartTrack | Gumball (CPlane) | Auto CPlane (Object) |
#   Record History | Filter | Memory use
# The screenshot strings are RUNTIME values ("Millimeters" is the units value,
# "Default" the layer value, "Memory use" the Info category); the pane's BASE
# label is its identity. Both are checked: identity/order here, and that
# rendering the screenshot state puts each measured value at its measured
# position (final section).
EXPECTED_BASE_LABELS = [
    "CPlane", "x y z", "Units", "Layer", "Grid Snap", "Ortho", "Planar",
    "Osnap", "SmartTrack", "Gumball", "Auto CPlane", "Record History",
    "Filter", "Info",
]
labels = [p.label for p in sb.panes()]
check(len(sb.panes()) == 15, f"14 measured panes + Absolute Tolerance  ({len(sb.panes())})")
check(labels[:14] == EXPECTED_BASE_LABELS,
      f"the 14 measured panes are present, in the screenshot's order  ({labels})")
check(labels[14] == "Absolute Tolerance" and sb.pane("tolerance").clickable
      and sb.pane("tolerance").kind == sb.KIND_READOUT,
      "the UI design's Absolute Tolerance readout comes last and opens the Units window")
check(sb.pane_ids() == [p.id for p in sb.PANES],
      "pane_ids() is the bar's canonical left-to-right order")
# the ids are stable, unique, and addressable
ids = sb.pane_ids()
check(len(ids) == len(set(ids)), f"pane ids are unique  ({ids})")
check(sb.pane("grid_snap").label == "Grid Snap",
      "pane(id) resolves to the right pane")
try:
    sb.pane("nosuchpane")
    check(False, "pane(unknown id) raises")
except KeyError as e:
    check("nosuchpane" in str(e) and "grid_snap" in str(e),
          f"pane(unknown id) raises naming the valid ids  ({e})")

# ===========================================================================
print()
print("kinds: toggle vs alignment vs readout")
# ===========================================================================
# the reference documentation documents each pane's click action; the seven
# "Click the <Pane> pane to toggle ... The pane lights up" panes are boolean
# toggles, Gumball/Auto CPlane carry an alignment, the rest are readouts.
kind = {p.id: p.kind for p in sb.panes()}
check(kind["grid_snap"] == sb.KIND_TOGGLE, "Grid Snap is a toggle")
check(kind["ortho"] == sb.KIND_TOGGLE, "Ortho is a toggle")
check(kind["planar"] == sb.KIND_TOGGLE, "Planar is a toggle")
check(kind["osnap"] == sb.KIND_TOGGLE, "Osnap is a toggle")
check(kind["smarttrack"] == sb.KIND_TOGGLE, "SmartTrack is a toggle")
check(kind["record_history"] == sb.KIND_TOGGLE, "Record History is a toggle")
check(kind["filter"] == sb.KIND_TOGGLE, "Filter is a toggle")
check(kind["gumball"] == sb.KIND_ALIGNMENT, "Gumball is an alignment pane")
check(kind["auto_cplane"] == sb.KIND_ALIGNMENT, "Auto CPlane is an alignment pane")
check(kind["cplane"] == sb.KIND_READOUT, "CPlane is a readout (the CPlane/World toggle)")
check(kind["coordinates"] == sb.KIND_READOUT, "x y z is a readout")
check(kind["units"] == sb.KIND_READOUT, "Units is a readout")
check(kind["layer"] == sb.KIND_READOUT, "Layer is a readout")
check(kind["info"] == sb.KIND_READOUT, "Info (Memory use) is a readout")

# ===========================================================================
print()
print("clickability: cursor coordinates are inert; v4 units opens a selector")
# ===========================================================================
# the reference documentation: every pane but x/y/z and Units is documented with a click
# action (toggle, open panel, or cycle Info category).
clickable = {p.id: p.clickable for p in sb.panes()}
for tid in ("cplane", "layer", "grid_snap", "ortho", "planar", "osnap",
            "smarttrack", "gumball", "auto_cplane", "record_history",
            "filter", "info"):
    check(clickable[tid] is True, f"{tid} is clickable")
check(clickable["coordinates"] is False, "x y z is NOT clickable (plain display)")
check(clickable["units"] is True, "v4 Units is a selector bound to document settings")
check(sb.is_clickable("ortho") is True and sb.is_clickable("units") is True,
      "is_clickable() agrees with the pane data")

# ===========================================================================
print()
print("rendering: a pane's text given its state")
# ===========================================================================
# the task's examples: "Gumball (CPlane)" (alignment shown in the text) and
# "x 25.075" (a coordinate readout).
check(sb.render("gumball", {"gumball": {"on": True, "alignment": "CPlane"}})
      == "Gumball (CPlane)",
      "Gumball renders its alignment: 'Gumball (CPlane)'")
check(sb.render("gumball", {"gumball": "World"}) == "Gumball (World)",
      "Gumball takes a bare alignment string: 'Gumball (World)'")
check(sb.render("auto_cplane", {"auto_cplane": "Object"})
      == "Auto CPlane (Object)",
      "Auto CPlane renders its alignment: 'Auto CPlane (Object)'")
check(sb.render("gumball", {}) == "Gumball (CPlane)",
      "Gumball with no state defaults to CPlane (the documented default)")
# a toggle's TEXT is its label -- on/off only changes whether it lights up
check(sb.render("ortho", {"ortho": True}) == "Ortho",
      "an on toggle still reads 'Ortho'")
check(sb.render("ortho", {"ortho": False}) == "Ortho",
      "an off toggle still reads 'Ortho'")
check(sb.is_active("ortho", {"ortho": True}) is True,
      "an on toggle lights up")
check(sb.is_active("ortho", {"ortho": False}) is False,
      "an off toggle does not light up")
check(sb.is_active("gumball", {"gumball": {"on": True, "alignment": "CPlane"}})
      is True, "an on alignment pane lights up")
check(sb.is_active("gumball", {"gumball": {"on": False, "alignment": "CPlane"}})
      is False, "an off alignment pane does not light up")
check(sb.is_active("layer", {"layer": "Default"}) is False,
      "a readout never lights up")
# --- the coordinate readout -------------------------------------------------
#
# These three checks USED to assert exact strings -- "x 25.075 y 0.000 z 0.000",
# "x 1.000 y 2.000 z -3.000" and the bare label "x y z". Those were SNAPSHOTS of
# an unpadded format, and the format has deliberately changed: the numbers are now
# right-aligned in fixed-width fields.
#
# WHY, user-reported: "the cororidinates at the botom move the entire row
# around". The readout is the only pane whose text width changes constantly, and
# in a left-to-right row every pane after it moves with it, so the whole bar
# jittered as the mouse moved. The reference panes are fixed cells.
#
# So the snapshots are re-expressed as the INVARIANTS they were really about --
# labelled x/y/z, three decimals, the right numbers -- plus the new width rule.
# The content assertions are not weakened: each number is still checked exactly.
def _coord(value):
    return sb.render("coordinates", {"coordinates": value})


check(_coord((25.075, 0.0, 0.0)).split()
      == ["x", "25.075", "y", "0.000", "z", "0.000"],
      "a cursor location renders labelled x/y/z with 3 decimals")
check(_coord({"x": 1, "y": 2, "z": -3}).split()
      == ["x", "1.000", "y", "2.000", "z", "-3.000"],
      "a dict coordinate renders the same values")

# Each NUMBER must follow its own label immediately. Right-aligning inside the
# fixed field put all the padding between the label and the value, so the bar read
# "x        14.667" and the label looked detached -- user-reported as "spacing
# issues with the xyz coordinates". Constant width alone does NOT catch that, which
# is why this check exists separately.
for _v, _want in (((14.667, 0.0, 1.943), ("x 14.667", "y 0.000", "z 1.943")),
                  ((-12345.678, 1.0, 2.0), ("x -12345.678", "y 1.000", "z 2.000"))):
    _t = _coord(_v)
    check(all(w in _t for w in _want),
          f"each number sits directly after its label  ({_t!r})")

# THE NEW INVARIANT: the pane's width never changes, whatever the cursor reads.
_widths = {len(_coord(v)) for v in ((0.0, 0.0, 0.0),
                                    (25.075, 0.0, 0.0),
                                    (12.5, -3.25, 0.0),
                                    (1234.5, -9876.543, 0.001),
                                    (-12345.678, 1.0, 2.0))}
check(len(_widths) == 1,
      f"every coordinate reading is the SAME WIDTH (got {sorted(_widths)}) -- "
      "otherwise the pane, and every pane after it, shifts as the mouse moves")
check(len(sb.render("coordinates", {})) == _widths.pop(),
      "and the IDLE pane is that width too, so it does not jump the moment the "
      "cursor enters a viewport")
check(sb.render("coordinates", {}) != sb.PANES[1].label,
      "which means the idle text is NOT the bare 'x y z' label any more")
check("x" in sb.render("coordinates", {})
      and "y" in sb.render("coordinates", {})
      and "z" in sb.render("coordinates", {}),
      "the idle pane still reads as x/y/z, just padded")
# value readouts: the value when present, the base label when not
check(sb.render("units", {"units": "Millimeters"}) == "Millimeters",
      "the units pane shows the current unit system")
check(sb.render("units", {}) == "Units",
      "an idle units pane shows its base label")
check(sb.render("layer", {"layer": "Default"}) == "Default",
      "the layer pane shows the current/selected layer")
check(sb.render("info", {"info": "Memory use"}) == "Memory use",
      "the Info pane shows its current category ('Memory use')")
check(sb.render("info", {}) == "Info",
      "an idle Info pane shows its base label")

# ===========================================================================
print()
print("the whole bar: rendering every pane reproduces the screenshot")
# ===========================================================================
# docs/SOURCE_DATA.md section 4, the 14 measured strings, one by one. This is
# the acceptance test: the renderer consumes exactly this.
state = sb.screenshot_state()
rendered = [sb.render(pid, state) for pid in sb.pane_ids()]
expected = [
    "CPlane", None, "Millimeters", "Default", "Grid Snap",
    "Ortho", "Planar", "Osnap", "SmartTrack", "Gumball (CPlane)",
    "Auto CPlane (Object)", "Record History", "Filter", "Memory use",
]
# The coordinate pane is `None` above, not "x y z": its idle text is now PADDED to
# the width of a real reading, so the row does not shift as the cursor moves
# (user-reported). The screenshot's own "x y z" is what an idle reference bar reads,
# and the padded form still reads as x/y/z -- checked separately just below, as an
# invariant rather than as a snapshot of the spacing.
for i, (got, want) in enumerate(zip(rendered, expected)):
    if want is None:
        check(got.split() == ["x", "-", "y", "-", "z", "-"],
              f"pane {i}: the idle coordinate pane reads as x/y/z ({got!r})")
        continue
    check(got == want,
          f"pane {i}: {got!r} == {want!r}")
check([r for r, w in zip(rendered, expected) if w is not None]
      == [w for w in expected if w is not None],
      "every other pane matches the measured screenshot left to right")
check(len(rendered) == 15, f"and all 15 panes render ({len(rendered)})")
check(sb.render("tolerance", {}) == "Absolute Tolerance"
      and sb.render("tolerance", {"tolerance": "Absolute Tolerance: 0.001"})
      == "Absolute Tolerance: 0.001", "the tolerance readout shows its live value")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"STATUSBAR TESTS PASSED ({checks} checks)")
