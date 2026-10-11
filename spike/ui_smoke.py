"""UI smoke test: verifies the add-on's Blender API surface is actually valid.

The geometry e2e test (addon_e2e.py) drives the state machine directly and so
cannot catch registration/operator errors. This test exercises the parts that
only Blender can validate:

  * every class registers cleanly (bad PropertyGroup/EnumProperty callbacks)
  * operator poll/execute paths don't reference non-existent bpy attributes
  * the modal command-line operator returns only valid Blender return values
  * the theme/layout setup runs without raising

"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


spec = importlib.util.spec_from_file_location(
    "narwhal3d", os.path.join(ROOT, "narwhal3d", "__init__.py"),
    submodule_search_locations=[os.path.join(ROOT, "narwhal3d")])
narwhal3d = importlib.util.module_from_spec(spec)
sys.modules["narwhal3d"] = narwhal3d
spec.loader.exec_module(narwhal3d)

# --- registration must be clean -------------------------------------------
try:
    narwhal3d.register()
except Exception as exc:  # noqa: BLE001
    failures.append(f"register() raised: {exc!r}")

from narwhal3d import ui  # noqa: E402
from narwhal3d.commands import COMMANDS  # noqa: E402

# --- every registered class is actually present in bpy.types ---------------
for cls in ui.CLASSES:
    name = cls.__name__
    check(hasattr(bpy.types, name), f"{name} did not register into bpy.types")

# --- operators must be callable through bpy.ops ---------------------------
for idname in ("narwhal.command_input", "narwhal.begin_typing",
               "narwhal.start_command", "narwhal.open_input", "narwhal.toggle_layer",
               "narwhal.run_command", "narwhal.cancel", "narwhal.repeat_last"):
    module, _, func = idname.partition(".")
    check(hasattr(getattr(bpy.ops, module), func),
          f"operator {idname} is not exposed on bpy.ops")

# --- keyboard shortcuts ----------------------------------------------------
# Count typeable-letter and Shift bindings from the real lists, not a hard-coded total.
check(len(ui._keymap_entries) > 0, "no keymap entries registered")
# Each letter is bound plain and shifted in both "3D View" and "Info".
# Navigation has one RIGHTMOUSE binding per modifier combination. A Blender
# keymap item matches an EXACT modifier set, so a bare RIGHTMOUSE item does NOT
# fire for Ctrl+RIGHTMOUSE -- each combination needs its own item.
# The MIDDLE button uses the same modifier combinations, and Ctrl+wheel has one
# binding per wheel direction.
expected_kmi_count = (len(ui.SHORTCUTS) + 4 * len(ui.TYPEABLE)
                      + 2 * len(ui.NAV_MODIFIERS) + len(ui.CTRL_WHEEL_ZOOM_KEYS))
check(len(ui._keymap_entries) == expected_kmi_count,
      f"registered {len(ui._keymap_entries)} keymap entries, "
      f"expected {expected_kmi_count}")

# Navigation bindings must use RIGHTMOUSE on narwhal.navigate,
# and there must be one per modifier combination -- a count alone would still
# pass if they were all bound to the same (wrong) modifiers.
_nav_items = [kmi for _km, kmi in ui._keymap_entries
              if kmi.idname == "narwhal.navigate"]
check(len(_nav_items) == len(ui.NAV_MODIFIERS),
      f"expected {len(ui.NAV_MODIFIERS)} narwhal.navigate bindings, "
      f"got {len(_nav_items)}")
check(all(k.type == "RIGHTMOUSE" for k in _nav_items),
      "narwhal.navigate must be bound to RIGHTMOUSE (Rhino navigates with RMB)")
_nav_mods = {(k.ctrl, k.shift, k.alt) for k in _nav_items}
check(len(_nav_mods) == len(ui.NAV_MODIFIERS),
      f"the narwhal.navigate bindings share modifier sets {_nav_mods}; each "
      f"combination needs its own item or it will never fire")

# every shortcut must land in a keymap the active keyconfig actually consults,
# otherwise it registers fine and silently never fires
active_km_names = {k.name for k in bpy.context.window_manager.keyconfigs.active.keymaps}
for km, kmi in ui._keymap_entries:
    check(km.name in active_km_names,
          f"keymap {km.name!r} is not in the active keyconfig; "
          f"its shortcuts will never fire")

# command properties must have been written to kmi.properties
#
# ``SHORTCUTS`` contains runnable commands and explicit unavailable placeholders.
# Validate each category independently so dead and miscategorized bindings are caught.
RUN_CMD, TODO_CMD = "narwhal.run_command", "narwhal.menu_todo"
expected_run = {c for _k, _s, _key, _m, idn, c in ui.SHORTCUTS
                if c is not None and idn == RUN_CMD}
expected_todo = {c for _k, _s, _key, _m, idn, c in ui.SHORTCUTS
                 if c is not None and idn == TODO_CMD}
found_run, found_todo = set(), set()
for km, kmi in ui._keymap_entries:
    if kmi.idname == RUN_CMD:
        found_run.add(kmi.properties.command)
    elif kmi.idname == TODO_CMD:
        found_todo.add(kmi.properties.command)
check(found_run == expected_run,
      f"run_command properties {found_run} != expected {expected_run}")
check(found_todo == expected_todo,
      f"menu_todo properties {found_todo} != expected {expected_todo}")

# every run_command shortcut must resolve against the command table...
for _k, _s, _key, _m, idn, command in ui.SHORTCUTS:
    if command is None:
        continue
    if idn == RUN_CMD:
        # A shortcut may carry its command argument.
        # ("SetDisplayMode Wireframe") -- the COMMAND is the first word
        check(command.split()[0].lower() in COMMANDS,
              f"shortcut bound to unknown command {command!r}")
    elif idn == TODO_CMD:
        # ...and every menu_todo shortcut must NOT, or it is miscategorised.
        check(command.lower() not in COMMANDS,
              f"{command!r} is bound to menu_todo but IS implemented -- it should be "
              f"a run_command shortcut now")

# --- shortcut operators must run and be non-greedy when idle --------------
app_state = ui.app_state
app_state.last_command = None
app_state.active_command = None
app_state.input_buffer = ""

# nothing to cancel / nothing to repeat -> the event must pass through so we
# don't swallow Esc and Enter from the rest of the app
try:
    check(bpy.ops.narwhal.cancel() == {"PASS_THROUGH"},
          "narwhal.cancel should PASS_THROUGH when no command is active")
    check(bpy.ops.narwhal.repeat_last() == {"PASS_THROUGH"},
          "narwhal.repeat_last should PASS_THROUGH with no command history")
except Exception as exc:  # noqa: BLE001
    failures.append(f"idle shortcut operators raised: {exc!r}")

# run a zero-step command by name, then repeat it
try:
    bpy.ops.narwhal.run_command(command="Help")
    check(app_state.last_command == "help",
          f"last_command is {app_state.last_command!r}, expected 'help'")
    before = len(app_state.history)
    check(bpy.ops.narwhal.repeat_last() == {"FINISHED"},
          "narwhal.repeat_last should FINISH once a command has been run")
    check(len(app_state.history) > before,
          "repeat_last did not re-run the last command")
except Exception as exc:  # noqa: BLE001
    failures.append(f"narwhal.run_command/repeat_last raised: {exc!r}")

# cancel must clear an in-progress command
try:
    app_state.start_command("line")  # multi-step, so it stays active
    check(app_state.active_command is not None, "Line command did not stay active")
    check(bpy.ops.narwhal.cancel() == {"FINISHED"},
          "narwhal.cancel should FINISH while a command is active")
    check(app_state.active_command is None, "narwhal.cancel left a command active")
except Exception as exc:  # noqa: BLE001
    failures.append(f"narwhal.cancel raised: {exc!r}")

# --- type anywhere to start a command --------------------------------------
# begin_typing must seed the character into the buffer and (in a real GUI) hand
# off to the command-line modal. Headless -b cannot open the modal, so we assert
# on the observable effect the user sees: the character is in the buffer.
try:
    app_state.input_buffer = ""
    app_state.active_command = None
    bpy.ops.narwhal.begin_typing(char="b")
    check(app_state.input_buffer == "b",
          f"begin_typing(char='b') left buffer {app_state.input_buffer!r}, expected 'b'")
    # a second character appends (typing is cumulative, like Rhino)
    bpy.ops.narwhal.begin_typing(char="o")
    check(app_state.input_buffer == "bo",
          f"second begin_typing should append: {app_state.input_buffer!r}, expected 'bo'")
    # empty char is a no-op that CANCELS, not a crash
    app_state.input_buffer = ""
    check(bpy.ops.narwhal.begin_typing(char="") == {"CANCELLED"},
          "begin_typing(char='') should CANCEL")
except Exception as exc:  # noqa: BLE001
    failures.append(f"begin_typing raised: {exc!r}")

# every typeable letter must be bound to begin_typing with the right char
# property (assert a representative sample). Each
# letter is bound TWICE (plain + Shift), so key the dict by (type, shift) —
# keying by type alone makes the two collide and the count check silently
# stops counting what it claims to.
try:
    # Each letter is bound in both the viewport ("3D View") and the
    # command-bar ("Info") keymaps, each twice (plain + Shift). Key the dict
    # by (keymap, type, shift) and assert the keymap NAMES, not just the count.
    letter_props = {}
    for km, kmi in ui._keymap_entries:
        if kmi.idname == "narwhal.begin_typing":
            letter_props[(km.name, kmi.type, kmi.shift)] = kmi.properties.char
    for key, expected in (("B", "b"), ("G", "g"), ("X", "x"), ("S", "s"), ("Z", "z")):
        for km_name in ("3D View", "Info"):
            for shifted in (False, True):
                tag = ("Shift+" if shifted else "") + key + f" in {km_name!r}"
                k = (km_name, key, shifted)
                check(k in letter_props, f"letter key {tag} not bound to begin_typing")
                if k in letter_props:
                    check(letter_props[k] == expected,
                          f"{tag} bound char {letter_props[k]!r}, expected {expected!r}")
    check(len(letter_props) == 4 * len(ui.TYPEABLE),
          f"{len(letter_props)} letter bindings, expected {4 * len(ui.TYPEABLE)}")
    km_names = {km.name for km, _kmi in ui._keymap_entries
                if _kmi.idname == "narwhal.begin_typing"}
    check(km_names == {"3D View", "Info"},
          f"begin_typing bound in keymaps {sorted(km_names)}, "
          f"expected ['3D View', 'Info']")
except Exception as exc:  # noqa: BLE001
    failures.append(f"begin_typing keymap check raised: {exc!r}")

# --- the EnumProperty items callback must accept (self, context) ----------
try:
    items = ui._command_items(None, bpy.context)
    check(len(items) == len(COMMANDS),
          f"_command_items returned {len(items)} items, expected {len(COMMANDS)}")
except Exception as exc:  # noqa: BLE001
    failures.append(f"_command_items raised: {exc!r}")

# --- modal() must only ever return valid Blender return values -----------
VALID_RETURNS = {"RUNNING_MODAL", "CANCELLED", "FINISHED",
                 "PASS_THROUGH", "INTERFACE"}

# Every event-type identifier the modal compares against MUST be a real member
# of bpy.types.Event's `type` enum. This guard exists because it was not here:
# ui.py compared against "BACKSPACE", "UP", "DOWN", "LEFT" and "RIGHT" -- none
# of which Blender defines (they are BACK_SPACE and *_ARROW) -- so all five
# branches were dead and line editing never worked. The old test used the same
# wrong strings in FakeEvent and only asserted the RETURN value was valid;
# PASS_THROUGH is valid, so it passed while the feature was broken. Validating
# the identifiers against the live enum is what makes that class of typo
# impossible to ship again.
EVENT_TYPES = set(bpy.types.Event.bl_rna.properties["type"].enum_items.keys())


def valid_event_type(type_):
    return type_ in EVENT_TYPES


import re as _re  # noqa: E402

_ui_src = open(os.path.join(ROOT, "narwhal3d", "ui.py")).read()
_compared = set()
for _m in _re.finditer(r"event\.type\s*(?:==|in)\s*\(?((?:\s*\"[A-Z_]+\"\s*,?)+)\)?",
                       _ui_src):
    _compared.update(_re.findall(r"\"([A-Z_]+)\"", _m.group(1)))
_bogus = sorted(t for t in _compared if not valid_event_type(t))
check(not _bogus,
      f"ui.py compares event.type against non-existent identifier(s) {_bogus} "
      f"-- those branches can never fire")
check(len(_compared) >= 8,
      f"only found {len(_compared)} event.type comparisons in ui.py; the "
      f"scraper above probably stopped matching (it must keep guarding them)")


class FakeEvent:
    def __init__(self, type_, value="PRESS", ascii_="",
                 mouse_region_x=0.0, mouse_region_y=0.0,
                 mouse_x=0.0, mouse_y=0.0):
        # a FakeEvent with a type Blender does not define tests nothing
        assert valid_event_type(type_), (
            f"FakeEvent({type_!r}) is not a real bpy.types.Event type")
        self.type = type_
        self.value = value
        self.ascii = ascii_
        self.ctrl = False
        self.alt = False
        self.shift = False
        self.mouse_region_x = mouse_region_x
        self.mouse_region_y = mouse_region_y
        # WINDOW-space mouse coordinates; `mouse_region_*` is region-relative.
        # and cannot identify *which* pane was clicked, so the modal now finds
        # the pane from these window coords and derives the region coords itself.
        self.mouse_x = mouse_x
        self.mouse_y = mouse_y


op = ui.NARWHAL_OT_command_input


class ModalShim:
    """Stand-in for the operator instance.

    Blender refuses ``bpy_struct.__new__``, but ``modal`` is a plain Python
    function, so we can bind it to a lightweight object to exercise its logic.
    """

    modal = op.modal

    def _redraw(self):
        pass

    def _finish(self):
        pass

    # Esc no longer ends the modal (Rhino's command line keeps focus), so it
    # calls _clear_transient instead of _finish. The shim needs both or driving
    # modal(ESC) raises AttributeError.
    def _clear_transient(self):
        pass


inst = ModalShim()
cases = [
    FakeEvent("RET"), FakeEvent("BACK_SPACE"), FakeEvent("LEFT_ARROW"),
    FakeEvent("UP_ARROW"), FakeEvent("DOWN_ARROW"), FakeEvent("A", ascii_="A"),
    FakeEvent("MIDDLEMOUSE"), FakeEvent("ESC"),
]
for ev in cases:
    try:
        result = inst.modal(bpy.context, ev)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"modal({ev.type}) raised: {exc!r}")
        continue
    bad = set(result) - VALID_RETURNS
    check(not bad, f"modal({ev.type}) returned invalid value(s): {bad}")

# --- line editing must actually EDIT, not merely return a legal value -----
# The bug this covers: the return value was legal (PASS_THROUGH, because the
# branch never matched) while the buffer was untouched -- so "returned a valid
# value" proved nothing. These assert the observable EFFECT instead.
_st = ui.app_state

_st.input_buffer = "BOX"
inst.modal(bpy.context, FakeEvent("BACK_SPACE"))
check(_st.input_buffer == "BO",
      f"BACK_SPACE must delete the last char; buffer is {_st.input_buffer!r}")

_st.input_buffer = ""
inst.modal(bpy.context, FakeEvent("BACK_SPACE"))
check(_st.input_buffer == "",
      f"BACK_SPACE on an empty buffer must stay empty; got {_st.input_buffer!r}")

_st.input_buffer = "xyz"
inst.modal(bpy.context, FakeEvent("DOWN_ARROW"))
check(_st.input_buffer == "",
      f"DOWN_ARROW must clear the buffer; got {_st.input_buffer!r}")

_st.history.append("> Circle")
_st.input_buffer = ""
inst.modal(bpy.context, FakeEvent("UP_ARROW"))
check(_st.input_buffer == "Circle",
      f"UP_ARROW must recall the last typed command; got {_st.input_buffer!r}")
_st.input_buffer = ""

# --- interactive point picking --------------------------------------------
import copy as _copy  # noqa: E402

# _wants_point must reflect whether a viewport CLICK can answer the current
# prompt.
#
# Circle's radius step accepts a picked radius location.
#
# Both directions are asserted so the predicate cannot degenerate into "always
# True": a DISTANCE step is clickable, a genuine NUMBER step (an item COUNT,
# which is never a location) is not.
try:
    _lc = _copy.deepcopy(COMMANDS["line"])
    check(ui._wants_point(_lc) is True,
          "_wants_point(Line at step 0) should be True")
    _cc = _copy.deepcopy(COMMANDS["circle"])   # center=point, radius=DISTANCE
    check(ui._wants_point(_cc) is True,
          "_wants_point(Circle at center step) should be True")
    _cc.args.append((0.0, 0.0, 0.0))           # consume the point step
    _cc.step_index = 1                          # now on the radius (DISTANCE) step
    check(ui._wants_point(_cc) is True,
          "_wants_point(Circle at radius step) should be True "
          "(Rhino: 'Pick a radius location')")
    # a COUNT is a number and nothing else -- a click must not answer it
    _ac = _copy.deepcopy(COMMANDS["array"])
    check(ui._wants_point(_ac) is False,
          "_wants_point(Array at Count step) should be False "
          "(a count is not a location)")
except Exception as exc:  # noqa: BLE001
    failures.append(f"_wants_point raised: {exc!r}")

# The pane-aware pick API exists. Headless areas have no geometry, so a real pick
# is not meaningful in this suite.
try:
    check(callable(ui._view3d_under_mouse), "ui._view3d_under_mouse is missing")
    check(callable(ui.pick_point), "ui.pick_point is missing")
except Exception as exc:  # noqa: BLE001
    failures.append(f"pick API check raised: {exc!r}")

# Full "Line + two clicks draws a line" flow (deterministic via monkeypatched
# pane-finding and pick, the two functions the modal calls).
# The modal derives the region coords as (event.mouse_* - region.x/y), so the
# fake region must expose real region geometry (x, y, width, height).
class FakeRegion:
    def __init__(self, x=0.0, y=0.0, width=100.0, height=100.0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


try:
    real_under = ui._view3d_under_mouse
    real_pick = ui.pick_point
    # A deterministic (region, x, y) -> world-point map so the test is
    # viewport-independent: the pane is identified by (id(region), x, y).
    ui._view3d_under_mouse = lambda wx, wy: (None, None, FakeRegion())
    # `pick_point` accepts an optional constraint axis for height and distance clicks.
    #
    # projected onto). The stub must accept it or the click flow raises -- which is how this
    # suite caught the signature change, correctly.
    ui.pick_point = (lambda area, sp, reg, rx, ry, constraint=None:
                     (rx / 100.0, ry / 100.0, 0.0))
    try:
        names_before = {o.name for o in bpy.data.objects}
        app_state.active_command = None
        app_state.input_buffer = ""
        app_state.start_command("line")
        check(app_state.active_command is not None, "Line did not start")
        inst2 = ModalShim()
        # window coords (200,300): region (200,300) has x=y=0 so rx=ry=(200,300)
        r1 = inst2.modal(bpy.context, FakeEvent("LEFTMOUSE", mouse_x=200.0, mouse_y=300.0))
        check(r1 == {"RUNNING_MODAL"}, f"first click returned {r1}")
        # window coords (400,500)
        r2 = inst2.modal(bpy.context, FakeEvent("LEFTMOUSE", mouse_x=400.0, mouse_y=500.0))
        check(r2 == {"RUNNING_MODAL"}, f"second click returned {r2}")
        new_names = {o.name for o in bpy.data.objects} - names_before
        check(len(new_names) == 1,
              f"expected exactly one object from two clicks, got {new_names}")
        if len(new_names) == 1:
            ob = bpy.data.objects[new_names.pop()]
            geom = app_state.ctx.geom_by_name(ob.name)
            bb = geom.GetBoundingBox()
            mn = (bb.Min.X, bb.Min.Y, bb.Min.Z)
            mx_ = (bb.Max.X, bb.Max.Y, bb.Max.Z)
            # clicks (200,300)->(2,3,0) and (400,500)->(4,5,0)
            check(abs(mn[0] - 2.0) < 1e-6 and abs(mn[1] - 3.0) < 1e-6,
                  f"line start wrong: {mn}")
            check(abs(mx_[0] - 4.0) < 1e-6 and abs(mx_[1] - 5.0) < 1e-6,
                  f"line end wrong: {mx_}")
    finally:
        ui._view3d_under_mouse = real_under
        ui.pick_point = real_pick
except Exception as exc:  # noqa: BLE001
    failures.append(f"click flow raised: {exc!r}")

# A LEFTMOUSE when NOT on a point prompt is a SELECTION click, and selection is
# OURS -- so the modal consumes it.
#
# This assertion used to require PASS_THROUGH, on the reasoning that Blender's
# own view3d.select should make the selection. That reasoning was the bug: Rhino's
# The modal handles selection clicks so Narwhal's Shift=ADD and Ctrl=REMOVE rules
# take precedence over Blender's default selection modifiers.
try:
    app_state.active_command = None
    # The coordinates have to be INSIDE a viewport, and that is not fussiness: a press
    # in no viewport at all now passes through on purpose, so the status bar and the
    # other editors can receive their own clicks. (10, 10) is below every pane, so it
    # exercises the pass-through
    # path rather than the selection path -- which is what this check found when the
    # guard landed.
    _pane = next((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), None)
    if _pane is None:
        failures.append("no VIEW_3D area to click in")
    else:
        _in_x = _pane.x + _pane.width // 2
        _in_y = _pane.y + _pane.height // 2
        r3 = inst2.modal(bpy.context,
                         FakeEvent("LEFTMOUSE", mouse_x=float(_in_x),
                                   mouse_y=float(_in_y)))
        check(r3 == {"RUNNING_MODAL"},
              f"a click with no command is consumed for SELECTION, got {r3}")

        # ...and the other half of that contract: a press OUTSIDE every pane must NOT
        # be consumed, or the status-bar panes are dead. A modal operator receives
        # events before region UI handlers, so consuming here swallows the button.
        _below = max(0, min(a.y for a in bpy.context.screen.areas
                            if a.type == "VIEW_3D") - 4)
        r3b = inst2.modal(bpy.context,
                          FakeEvent("LEFTMOUSE", mouse_x=float(_in_x),
                                    mouse_y=float(_below)))
        check(r3b == {"PASS_THROUGH"},
              f"a click below every pane passes through to the status bar, got {r3b}")
except Exception as exc:  # noqa: BLE001
    failures.append(f"pass-through raised: {exc!r}")

# --- theme + layout setup must not raise ---------------------------------
for fn_name in ("setup_layout", "apply_rhino_theme"):
    try:
        getattr(ui, fn_name)()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{fn_name}() raised: {exc!r}")

# --- no Blender chrome above the command bar -------------------------------
# The Info header is either hidden or displays Narwhal's toolbar, never Blender's
# default header content.
try:
    info = next((a for a in bpy.context.screen.areas if a.type == "INFO"), None)
    if info is not None:
        sp = next((s for s in info.spaces if s.type == "INFO"), None)
        if sp is not None and hasattr(sp, "show_region_header"):
            ours = ui.toolbar_installed()
            check(ours or sp.show_region_header is False,
                  f"INFO header shown={sp.show_region_header} but Narwhal's toolbar "
                  f"installed={ours}: the header may only be visible with app content")
    check(ui.toolbar_installed(),
          "Rhino's toolbar is not installed in the command area's header")
except Exception as exc:  # noqa: BLE001
    failures.append(f"header check raised: {exc!r}")

# --- application theme colors ---------------------------------------------
def _close(a, b, tol=2e-3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))

try:
    v3d = bpy.context.preferences.themes[0].view_3d
    check(_close(tuple(v3d.grid), tuple(ui.GRID_COLOR)),
          f"grid theme {tuple(v3d.grid)} != expected {ui.GRID_COLOR}")
    check(_close(tuple(v3d.grid_major), tuple(ui.GRID_MAJOR)),
          f"grid_major theme {tuple(v3d.grid_major)} != expected {ui.GRID_MAJOR}")
    check(_close(tuple(v3d.object_selected)[:3], tuple(ui.SELECT_COLOR)[:3]),
          f"object_selected {tuple(v3d.object_selected)} not Rhino yellow "
          f"{ui.SELECT_COLOR}")
    check(_close(tuple(v3d.wire), (0.0, 0.0, 0.0)),
          f"wire theme {tuple(v3d.wire)} != black")

    # Constants are linear-space equivalents of the intended sRGB colors. Blender stores
    # LINEAR space and applies sRGB EOTF when rendering, so the stored value is
    # NOT the raw measurement:  linear = ((srgb/255 + 0.055) / 1.055) ** 2.4
    # (verified against our own rendered pixels: stored 0.98 -> rendered 252,
    # stored 0.616 -> rendered 205). Pinning each constant to the linear-of-
    # measured value is what catches a mutation -- comparing theme readback to
    # the constant alone would be self-referential and still pass if wrong.
    def _srgb_to_linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    _measured_srgb = {
        "VIEWPORT_BG": (157, 163, 170),
        "GRID_COLOR": (147, 153, 160),
        "GRID_MAJOR": (129, 134, 140),
        "CMD_BG": (255, 255, 255),
    }
    for _name, _srgb in _measured_srgb.items():
        _expected = tuple(round(_srgb_to_linear(c / 255.0), 3) for c in _srgb)
        _const = tuple(round(c, 3) for c in getattr(ui, _name)[:3])
        check(_close(_const, _expected, 2e-3),
              f"{_name} {_const} != linear-of-measured {_expected} "
              f"(measured sRGB {_srgb})")
except Exception as exc:  # noqa: BLE001
    failures.append(f"theme color readback raised: {exc!r}")

# --- four viewports with the application default orientations -------------
# setup_layout() runs synchronously in headless mode. The panes must carry Top,
# Front, Right, and a perspective three-quarter view. The
# perspective pane has its OWN quaternion (a 3/4 view looking down at the scene),
# distinct from Right -- we compare the MULTISET of quaternions (not a 1:1
# pairing) plus the perspective modes.
try:
    ui.setup_layout()  # idempotent; ensure the four viewports exist
    v3d_areas = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    check(len(v3d_areas) == 4,
          f"expected 4 VIEW_3D viewports after setup_layout(), got {len(v3d_areas)}")

    # quaternion helper: q and -q describe the same rotation, so accept either sign
    def _quat_close(a, b, tol=1e-3):
        a = [c for c in a]
        b = [c for c in b]
        return (all(abs(x - y) <= tol for x, y in zip(a, b))
                or all(abs(x + y) <= tol for x, y in zip(a, b)))

    actual_quats = []
    actual_persps = []
    for a in v3d_areas:
        sp = next((s for s in a.spaces if s.type == "VIEW_3D"), None)
        if sp is None or sp.region_3d is None:
            continue
        actual_quats.append(tuple(sp.region_3d.view_rotation))
        actual_persps.append(sp.region_3d.view_perspective)

    expected_quats = [ui.VIEW_TOP, ui.VIEW_FRONT, ui.VIEW_RIGHT, ui.VIEW_PERSP]
    # match each expected quaternion to a distinct actual one (multiset equality)
    matched = []
    for eq in expected_quats:
        hit = None
        for i, aq in enumerate(actual_quats):
            if i not in matched and _quat_close(aq, eq):
                hit = i
                break
        if hit is None:
            failures.append(f"no viewport with expected quaternion {tuple(eq)}; "
                            f"actuals={actual_quats}")
        else:
            matched.append(hit)

    # one pane must be a true perspective, the rest orthographic
    check(actual_persps.count("PERSP") == 1,
          f"expected exactly 1 PERSP pane, got {actual_persps.count('PERSP')} "
          f"({actual_persps})")
    check(actual_persps.count("ORTHO") == 3,
          f"expected exactly 3 ORTHO panes, got {actual_persps.count('ORTHO')} "
          f"({actual_persps})")
except Exception as exc:  # noqa: BLE001
    failures.append(f"four-viewport check raised: {exc!r}")

# --- each pane must be in its default corner -------------------------------
# The multiset check above passes with the panes in the wrong corners (it only
# asks "is a Top view somewhere?"), so it cannot catch a vertical flip. This
# quadrant assertion is what catches it: map each headless pane to a screen
# quadrant (Blender's y grows UPWARD, so larger y = top row) and require the
# exact Rhino default view at each corner.
try:
    v3d_areas = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    if len(v3d_areas) == 4:
        def _label(a):
            sp = next((s for s in a.spaces if s.type == "VIEW_3D"), None)
            if sp is None or sp.region_3d is None:
                return None
            if sp.region_3d.view_perspective == "PERSP":
                return "Perspective"

            def close(a_, b_):
                return (all(abs(x - y) <= 1e-3 for x, y in zip(a_, b_))
                        or all(abs(x + y) <= 1e-3 for x, y in zip(a_, b_)))

            q = tuple(sp.region_3d.view_rotation)
            for name, ref in (("Top", ui.VIEW_TOP), ("Front", ui.VIEW_FRONT),
                              ("Right", ui.VIEW_RIGHT)):
                if close(q, tuple(ref)):
                    return name
            return "custom"

        labels = [(a, _label(a)) for a in v3d_areas]
        if all(lbl is not None for _a, lbl in labels):
            mid_x = sum(a.x + a.width / 2 for a in v3d_areas) / 4
            mid_y = sum(a.y + a.height / 2 for a in v3d_areas) / 4
            actual = {}
            for a, lbl in labels:
                cx, cy = a.x + a.width / 2, a.y + a.height / 2
                quad = ("T" if cy > mid_y else "B") + ("L" if cx < mid_x else "R")
                actual[quad] = lbl
            expected = {"TL": "Top", "TR": "Perspective",
                        "BL": "Front", "BR": "Right"}
            check(actual == expected,
                  f"panes in wrong corners: got {actual}, "
                  f"expected {expected}")
        else:
            check(False, "could not classify a headless pane for the "
                         "quadrant check")
except Exception as exc:  # noqa: BLE001
    failures.append(f"quadrant check raised: {exc!r}")

# --- Blender's competing bare-letter shortcuts are switched off -----------
# Headless has only ~108 keymaps (per-mode keymaps like "Object Mode" are
# created lazily on region init), so the real zero-competitor assertion lives
# in the GUI probe spike/suppress_gui_check.py. Here we assert the parts that
# ARE observable under -b: "3D View" exists headless, its bare-letter
# competitors are off, our 26 letters stay on, and unregister() restores.
try:
    wm = bpy.context.window_manager
    # The merged user keymap is only materialised after keyconfigs.update();
    # without it the keymap exists but keymap_items is empty (probe-verified).
    wm.keyconfigs.update()
    # NOTE: `keyconfigs.user` is a bpy_struct; only its `.keymaps` collection
    # supports `.get(name)`. `user.get(name)` raises TypeError (IDProperties).
    km3d = wm.keyconfigs.user.keymaps.get("3D View")
    check(km3d is not None, "'3D View' keymap missing in user keyconfig")

    def _competitor(kmi):
        return (kmi.map_type == "KEYBOARD"
                and kmi.type in ui.TYPEABLE_UPPER
                and not kmi.ctrl and not kmi.alt and not kmi.oskey
                and not kmi.idname.startswith("narwhal."))

    if km3d is not None:
        on = [kmi for kmi in km3d.keymap_items
              if _competitor(kmi) and kmi.active]
        on_rows = [(k.type, k.idname) for k in on]
        check(not on,
              f"{len(on)} bare-letter Blender shortcut(s) still active in "
              f"'3D View': {on_rows}")
        # Each letter has plain and Shift items.
        ours = [kmi for kmi in km3d.keymap_items
                if kmi.idname == "narwhal.begin_typing"]
        check(len(ours) == 2 * len(ui.TYPEABLE),
              f"expected {2 * len(ui.TYPEABLE)} begin_typing items in '3D View', "
              f"got {len(ours)}")
        check(all(kmi.active for kmi in ours),
              "some narwhal.begin_typing items are inactive in '3D View'")
except Exception as exc:  # noqa: BLE001
    failures.append(f"shortcut suppression check raised: {exc!r}")

# --- unregister must be clean (so reload/disable works) ------------------
try:
    narwhal3d.unregister()
except Exception as exc:  # noqa: BLE001
    failures.append(f"unregister() raised: {exc!r}")

# leaked keymap entries break add-on reload
check(len(ui._keymap_entries) == 0,
      f"unregister() leaked {len(ui._keymap_entries)} keymap entries")

# unregister() must restore what suppress_blender_shortcuts() switched
# off (an add-on reload must not leave the user's Blender crippled).
try:
    check(len(ui._suppressed) == 0,
          f"unregister() leaked {len(ui._suppressed)} suppressed items")
    km3d = bpy.context.window_manager.keyconfigs.user.keymaps.get("3D View")
    if km3d is not None:
        restored = [kmi for kmi in km3d.keymap_items
                    if _competitor(kmi) and not kmi.active]
        off_rows = [(k.type, k.idname) for k in restored]
        check(not restored,
              f"{len(restored)} suppressed item(s) still inactive after "
              f"unregister(): {off_rows}")
except Exception as exc:  # noqa: BLE001
    failures.append(f"shortcut restore check raised: {exc!r}")

print("\n" + "=" * 50)
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("UI SMOKE PASSED")
