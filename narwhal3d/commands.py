"""the reference CAD application commands: prompt-driven state machines + geometry execution.

Each Command walks a sequence of steps (prompt -> parsed arg). Enter with an
empty input finishes variable-point commands or repeats the command, matching
the reference CAD application's command-line behavior.
"""
from __future__ import annotations

import math
import os

import rhino3dm as r3

from . import bridge
from . import curve_inputs
from . import coil
from . import conics
from . import catenary
from . import tween
from . import handlecurve
# the Gumball's pure geometry/mode rules. bpy-free and rhino3dm-free, so
# importing it here is safe everywhere; `GumballNumericCmd` needs its mode table.
from . import gumball
# construction planes (a real orthonormal frame) and the reference CAD application's
# coordinate-entry syntax. Both are pure and bpy-free, so the rules can be
# exercised without Blender.
from . import cplane
# the object-snap mode names + settings. Pure (no bpy, no rhino3dm), so the
# snap commands can be unit-tested without a viewport.
from . import osnap
from . import coords
# The command-line option framework is pure and bpy-free, so it can be exercised
# without Blender. Imported as `opts` because `Command`
# has its own `options` attribute (the per-command tuple) and the two names would
# otherwise read as the same thing.
from . import options as opts
from . import undo
# the pure decision logic for SelAll / SelNone / Invert (a name -> function
# table). bpy-free and rhino3dm-free; this module supplies the live document.
from . import sel_commands
# the pure arithmetic for Divide (by_count / by_length / by_chord).
# bpy-free and rhino3dm-free.
from . import divide
# Exact projection onto a plane is pure and rhino3dm-only, with no bpy dependency.
# The command uses
# its `project_curve` (curves) and `project_point` (points); both raise
# ValueError with a message written to be surfaced verbatim.
from . import project
# Exact extension of a curve end is pure and rhino3dm-only, with no bpy dependency.
# The command
# uses `extend_curve` (typed-length form, Line/Arc/Natural); the kernel raises
# ValueError with a message written to be surfaced verbatim for the cases it
# refuses (Smooth, Natural resolving to Smooth, Arc on a non-arc, closed,
# non-positive length).
from . import extend
# The pure Named CPlane model stores `cplane.CPlane` values and has no bpy or
# rhino3dm dependency.
from . import named_cplanes
# the set of names `Isolate` hid, so `Unisolate` can show exactly that
# set again. Pure, with no bpy or rhino3dm dependency.
from . import isolate_state
# SelLast/SelPrev state -- the set of objects the app created and the
# previous selection set, keyed by model identity. Pure, with no bpy or rhino3dm
# dependency.
from . import selection_state
# pure exact-name/property selection rules shared by SelGroup and later
# property-selection commands (no bpy and no document mutation).
from . import property_select
# Attribute User Text's pure row rules are used by the Properties
# model; command data itself remains in the document's ObjectAttributes.
from . import user_text
# the pure Grid settings model/store.  This command updates settings only;
# viewport redraw and UI application remain separate responsibilities.
from . import grid_settings
# the non-dialog Layer State Manager command-line backend.  The panel and
#.3dm import remain explicit deferred capabilities.
from . import layer_states
# OptionsImport/OptionsExport expose only the Aliases text
# interchange.  The table itself is pure and atomic; these commands add
# file I/O and deliberately do not pretend to cover all reference application options.
from . import alias_table
# Portable document-fragment encoding. Pure (rhino3dm-only): clipboard transport and
# document/display mutation remain in the three commands below.
from . import document_clipboard


# ---------------------------------------------------------------------------
# argument parsing helpers
# ---------------------------------------------------------------------------

def parse_point(text: str, plane=None, last=None):
    """Typed text -> a WORLD point, read in `plane`'s coordinates.

    the reference CAD application reads a typed point in the ACTIVE VIEWPORT'S construction plane, not in
    world coordinates (the command behavior), so in a Front
    viewport ``1,2`` is world ``(1, 0, 2)``. The syntax (``w`` for world, ``r``/
    ``@`` for relative, the ``0``/``w0`` shortcuts, and which forms are refused)
    lives in the pure `coords` module.

    `plane` defaults to World Top, where CPlane and world coordinates coincide --
    so a caller with no viewport (the headless suites) behaves exactly as before.
    Raises ValueError with the reference CAD application's own wording when the text is not a coordinate.
    """
    if plane is None:
        plane = cplane.WORLD_TOP
    point, err = coords.resolve(text, plane, last)
    if err:
        raise ValueError(err)
    return point


def parse_number(text: str) -> float:
    """A typed number: plain, fraction, unit suffix or formula (- the lower
    tier's calculator via `coords.parse_scalar`). Raises ValueError, as before."""
    value, err = coords.parse_scalar(text)
    if err:
        raise ValueError(err)
    return value


#: Step kinds. A step's kind decides how its typed text is read AND whether a
#: viewport click can answer it (`ui._wants_point`).
#:
#: ``DISTANCE`` accepts either a typed number or a viewport location. Declaring
#: radius and height steps as ``NUMBER`` would make them keyboard-only and would
#: also disable their point-driven preview after the first click.
POINT = "point"
NUMBER = "number"
DISTANCE = "distance"
TEXT = "text"
NAME = "name"
# a step answered by the SELECTION (or a typed name). Satisfied
# immediately if something is selected, else it prompts and waits for the user
# to select and press Enter (see `_feed_objects_step`). Unlike the other kinds,
# an empty Enter here CONFIRMS the selection (or reports "Nothing selected")
# rather than cancelling -- hence its own branch in `_feed`. `ui._wants_point`
# does NOT list this: a viewport click answers it by SELECTING, which the
# existing click-to-select path (ui.py) already does.
OBJECTS = "objects"

# a step answered by a WHOLE SET of objects -- "select the objects and press
# Enter". The difference from OBJECTS is only the cardinality, and it matters:
# OBJECTS takes `selected[0].name` (ONE name), so a command built on it silently
# ignores everything else you selected.
#
# Boolean union consumes one complete selection set; difference and intersection
# consume two sets in order. The previous two-name representation could not express
# this interaction and treated Enter as cancellation after partial selection.
#
# The argument this step appends is a LIST of names, not a name -- a command reading
# it must expect a list. Enter CONFIRMS (like OBJECTS) and the selection is CLEARED
# afterwards, so a second set-step starts empty instead of re-taking the first set.
OBJECTS_MULTI = "objects_multi"

#: The two step kinds answered by a SELECTION rather than typed geometry. Both are
#: routed before the "empty Enter cancels" rule in `_feed`, and both are excluded
#: from `CLICKABLE_KINDS` because a click answers them by selecting.
SELECTION_KINDS = (OBJECTS, OBJECTS_MULTI)

#: kinds a viewport click can answer. `ui._wants_point` imports this so the
#: modal and the commands cannot disagree about what is clickable.
CLICKABLE_KINDS = (POINT, DISTANCE)


# ---------------------------------------------------------------------------
# command base
# ---------------------------------------------------------------------------


def _document_tolerance(ctx=None):
    """the DOCUMENT's absolute tolerance, in model units -- what every tolerance
    consumer a command calls (autoclose, Join, Project, Extend, the nearest-point
    solvers) must use. `ctx.model`'s when there is one, else `units.document_tolerance`
    (the open document, else the 0.01 default)."""
    from . import units

    return units.document_tolerance(getattr(ctx, "model", None))


class Command:
    name = ""
    description = "no help"
    # each step: (prompt, kind)  kind in point|number|name|text
    steps: list[tuple[str, str]] = []
    variable_points = False  # keep asking points until empty input
    # the reference CAD application command-line options, as a tuple of options.Option. Empty by
    # default, so a command that declares none behaves exactly as before.
    options: tuple = ()
    # set True for one feed (by `ui`) when the user holds Alt while
    # placing a point, to SUSPEND autoclose. (user_interface/keyboard
    # the command behavior, Alt): "Suspends `Polyline`, `Curve`, and `InterpCrv`
    # autoclose." Default False; a command that does not autoclose never reads it.
    suspend_autoclose = False
    # How many LEADING `name` steps this command will take from the existing
    # selection instead of prompting for. the reference CAD application's normal shape is "select the
    # objects, then run the command": the command behavior's steps are
    # "Select a curve. Specify a distance." -- there is no curve PROMPT at all.
    #
    # Selection-driven commands use this before prompting. A fixed name prompt cannot
    # use empty input as a selection fallback because empty input cancels that step.
    preselect_names = 0
    # A selection-acting command can opt into treating confirmation of an empty first
    # set as cancellation. Two-set Boolean commands leave this disabled so they can
    # continue waiting for their required operands.
    cancel_on_empty_set = False
    # OURS: explicit non-destructive curve-input protocol, never a global permission
    # to operate on (or delete) a component's owner. Python only routes UI/API calls.
    accepts_curve_subobjects = False
    accepts_subobjects = False
    # does this command's work belong in an undo record? True for anything
    # that can change the document. `Undo` and `Redo` set it False -- they NAVIGATE
    # history rather than adding to it, and running them inside an open record
    # would be refused by the stack's own safety rule (you cannot undo halfway
    # through recording a change).
    records_undo = True

    def __init__(self):
        self.args: list = []
        self.step_index = 0
        # set True by `on_point` when the picked start point is snapped
        # onto args[0] and the command is finished by AUTOclose (as opposed to
        # an explicit Enter or an explicit Close). `Curve`/`InterpCrv` then
        # build the SMOOTH periodic closed curve built from the unique
        # points; `Polyline` ignores the flag -- a polyline's corners are real.
        self._autoclosed = False
        # the steps list is copied to the INSTANCE so the size step's
        # label ("Radius:" -> "Diameter:" etc.) can be updated per-run without
        # mutating the shared class attribute that other instances and tests
        # read.
        self.steps = list(type(self).steps)
        # current option values (MODE options hold none), and the option we are
        # waiting on a value for after the user typed its bare name.
        self.opt = opts.defaults(self.options)
        self.pending_option = None
        self.mode = None  # set by a MODE option via on_mode
        # radius-equivalent size mode -- "radius" (default) or one of
        # "diameter", "circumference", "area" (set by the matching option).
        self.size_mode = "radius"
        # Circle's Circumference / Area are VALUE options. The user can
        # type `Circumference=31.4` BEFORE the centre is picked, in which case
        # we cannot build yet. The value is parked here and consumed by `_feed`
        # as soon as the centre (step 0) arrives. Always reset per instance.
        self._pending_size_value = None
        # names typed at an `objects_multi` step, accumulated until Enter.
        # Per-instance, like every other piece of step state here -- a class-level
        # list would leak one command's operands into the next run.
        self._pending_multi = []

    @property
    def prompt(self) -> str:
        # waiting on a value for a bare ENUM/VALUE option: ask for just that
        if self.pending_option is not None:
            return opts.value_prompt(self.pending_option)
        if self.variable_points:
            base = self.variable_prompt(len(self.args) + 1)
        elif self.step_index < len(self.steps):
            base = self.steps[self.step_index][0]
        else:
            return ""
        # the reference CAD application shows the options in brackets before the colon. With no
        # options this returns `base` untouched.
        return opts.format_prompt(base, self.options, self.opt)

    def _active_cplane(self, ctx):
        """The construction plane typed points are read in.

        Asks the context for the ACTIVE viewport's CPlane. Falls back to World
        Top, where CPlane and world coordinates coincide, so a context without
        viewports (the headless suites) behaves exactly as it did before.
        """
        getter = getattr(ctx, "active_cplane", None)
        if callable(getter):
            try:
                plane = getter()
            except Exception:  # noqa: BLE001
                return cplane.WORLD_TOP
            if plane is not None:
                return plane
        return cplane.WORLD_TOP

    def variable_prompt(self, n):
        """The prompt for the `n`-th point of a variable-point command."""
        return f"{self.name} Point {n}:"

    def _last_point(self):
        """The previous picked point, for the reference CAD application's relative (``r``/``@``) forms."""
        for value in reversed(self.args):
            if isinstance(value, tuple) and len(value) == 3:
                return value
        return None

    def axis_constraint(self, plane, resolve=None):
        """``(origin, direction)`` to constrain a click to, or None for the CPlane.

        A DISTANCE step measures along a direction, so a click answering it must be projected
        onto that axis, not flattened onto the pane's construction plane. In a Perspective
        pane the CPlane is world XY, so every click
        lands at z = 0 and a height along Z came out as 0 every time -- `ExtrudeCrv`
        did nothing in Perspective, and `Cylinder`/`Cone` had the same defect.

        This is the reference CAD application's elevator/direction constraint. None means "no constraint", which is
        the right default: only a command that knows it is measuring along a direction can say
        what that direction is.

        `resolve` is an optional ``name -> geometry`` lookup, for a command whose axis depends
        on an object it has been given rather than on a point it has been picked.
        """
        return None

    def _distance_base(self):
        """The point a picked DISTANCE is from.

        For `Circle` that is the centre, for `Cylinder`'s height the base centre:
        in both cases the most recent picked point, which is what `_last_point`
        already means. A command needing a different reference (an axis endpoint,
        say) overrides this.
        """
        return self._last_point()

    # the radius-equivalent size options (Diameter, Circumference, Area)
    # all change what the NUMBER the user types means, and nothing else -- the
    # geometry is still built from a radius, so this is pure arithmetic on a
    # radius. See `radius_from` for the conversions.

    @classmethod
    def radius_from(cls, value, mode):
        """Convert a typed `value` under `mode` to the radius the reference CAD application would build.

        The documented size modes are:

          * ``radius``        -- the radius itself (the default step).
          * ``diameter``      -- "Toggles between accepting a radius value and a
                                 diameter value" -> r = d / 2.
          * ``circumference`` -- "Specifies the circle's circumference" ->
                                 r = c / (2*pi).
          * ``area``          -- "Specifies the circle's area" / "Enter the
                                 area of the circle" (the SHARED page the
                                 Sphere/Cylinder/Cone pages link to). The
                                 wording is "the circle", so this is the
                                 EQUATOR CIRCLE's area -> r = sqrt(a / pi),
                                 NOT the sphere's surface area (4*pi*r^2).
                                 Flagged `` where the per-command page is
                                 silent: the shared page says "the circle",
                                 and that is the only reading consistent
                                 across all four commands.
        """
        v = float(value)
        if mode == "diameter":
            return v / 2.0
        if mode == "circumference":
            return v / (2.0 * math.pi)
        if mode == "area":
            return math.sqrt(v / math.pi)
        return v

    def _parse_distance(self, text, ctx):
        """A DISTANCE step: a typed number, or a picked location.

        the reference CAD application accepts both (see:data:`DISTANCE`). A bare number is the distance
        itself; anything else must be a coordinate, and the distance is then how
        far that point lies from:meth:`_distance_base` -- which is exactly what
        "pick a radius location" means.
        """
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass                       # not a number -> it should be a location
        base = self._distance_base()
        if base is None:
            raise ValueError(
                "there is no reference point to measure that distance from")
        point = parse_point(stripped, self._active_cplane(ctx), base)
        return math.dist(base, point)

    def _numeric_input_wins(self, text: str) -> bool:
        """True when `text` is a number the CURRENT step wants, so it beats options.

        the reference CAD application has real options whose names start with a digit -- `2Point` and
        `3Point` -- and our unique-prefix matching made the bare token `2` a
        prefix of `2Point`. At Circle's `Radius:` prompt that meant a radius of 2
        or 3 silently branched into an unimplemented option instead of being the
        radius. `2.5` parsed fine, which is why the bug hid.

        The rule: a prompt asking for a NUMBER or a DISTANCE is asking for a
        value, so a token that IS a value is that value. Options remain reachable
        by their letters (`2P`, `2Point`). Point steps are deliberately excluded --
        there a bare number is a distance constraint, not a step value, and the
        existing behaviour is already documented and tested.
        """
        if self.pending_option is not None:
            return False                # the sub-prompt owns this line
        steps = getattr(self, "steps", [])
        idx = getattr(self, "step_index", 0)
        if idx >= len(steps):
            return False
        if steps[idx][1] not in (NUMBER, DISTANCE):
            return False
        try:
            parse_number(text)
        except ValueError:
            return False
        return True

    def _parse(self, kind: str, text: str, ctx=None):
        if kind == POINT:
            return parse_point(text, self._active_cplane(ctx), self._last_point())
        if kind == NUMBER:
            return parse_number(text)
        if kind == DISTANCE:
            return self._parse_distance(text, ctx)
        return text.strip()

    def on_mode(self, name: str, ctx):
        """React to a MODE option (Box's Diagonal/Cube/3Point/...).

        MODE options branch the command, so only the command itself knows what
        to do. The default says so plainly rather than silently ignoring the
        option and appearing to work -- an unimplemented option must never look
        implemented. Returns (message|None, done:bool).
        """
        return f"{self.name}: the {name} option is not implemented yet", False

    def unknown_option_message(self, text: str):
        """The refusal line for text that is not one of this command's options.

        Used by a zero-step command (no argument to feed, so the line was meant
        to be an option and was not one). The default names only the offending
        text; a command whose options are a small, fixed, well-known set -- the
        `Gumball` command's On / Off / Toggle -- overrides this to also list the
        valid ones, so the user sees what they should have typed rather than
        having to re-consult the help. Returns a message string.
        """
        return f"{self.name}: unknown option {text!r}"

    def on_option_set(self, option, value, ctx):
        """A TOGGLE/ENUM/VALUE option was just stored -- inline (`Name=Value`)
        or as the answer to a bare `Name`. Default: do nothing beyond storing,
        which is right for most commands. A command whose job is to APPLY option
        values (CPlane) overrides this to act, returning (message|None,
        done:bool). Returning None means "no special action" and the caller
        falls back to the standard `Name=Value` acknowledgement.
        """
        return None

    def on_point(self, point, ctx):
        """A variable-point command parsed a new point (already in `self.args`).

        If `autoclose` is set (True on `Polyline` / `Curve` / `InterpCrv`, False
        by default) and the point is the START point within tolerance with
        enough points placed, picking it finishes the command as a CLOSED curve,
        with no duplicate control point. A command whose point is not an
        argument (CPlane) or that draws discrete objects (Points) overrides this
        and keeps `autoclose` False.
        Returns (message|None, done:bool) or None to keep collecting.
        """
        if self.autoclose and self._should_autoclose(point, ctx):
            # The just-picked start point is the LAST in args (the base appended
            # it). first==last is what makes `IsClosed` True, so snap it onto the
            # EXACT start -- no sliver, no zero-length segment. (Popping it would
            # remove the closing point and leave the curve OPEN.)
            self.args[-1] = self.args[0]
            self._autoclosed = True   # this finish is an AUTOclose
            return self.execute(ctx)
        return None

    # autoclose for the variable-point curve commands.
    #
    # (user_interface/keyboard the command behavior, the Alt key):
    #   "Suspends `Polyline`, `Curve`, and `InterpCrv` autoclose."
    # A modifier that SUSPENDS a behaviour is proof the behaviour exists, and it
    # names exactly the three commands that have it. So: when a picked point
    # coincides with the FIRST point (within the document tolerance), the command
    # closes and finishes instead of adding a duplicate point.
    #
    # and already in the code: `Close` "appears only after three points
    # have been placed" (the command behavior). Autoclose respects the same rule
    # -- two coincident points are a degenerate curve, not a closed one, so the
    # gate is >= 3 placed points.
    autoclose = False

    def _autoclose_tolerance(self, ctx=None):
        """The gap still counted as "the start point".

        the reference CAD application judges coincidence against the document's **absolute tolerance** --
        `ctx.model`'s (DocumentProperties, model units), see
        `_document_tolerance`. It used to be the 0.01 default whatever the document.
        """
        return _document_tolerance(ctx)

    def _should_autoclose(self, point, ctx):
        """True if `point` is the start point (within tolerance) and enough points
        have been placed to close. Alt suspends it (`suspend_autoclose`)."""
        if self.suspend_autoclose:
            return False
        if len(self.args) < 4:  # the start point is args[-1]; need >=3 others
            return False
        first = self.args[0]
        return math.dist(point, first) <= self._autoclose_tolerance(ctx)

    def _apply_option(self, m, ctx):
        """Handle a matched option. Returns (message|None, done:bool)."""
        if m.status == opts.SET:
            self.opt[m.option.name] = m.value
            hook = self.on_option_set(m.option, m.value, ctx)
            if hook is not None:
                return hook
            return f"{opts.format_option(m.option, self.opt)}", False
        if m.status == opts.NEEDS_VALUE:
            self.pending_option = m.option
            return None, False
        if m.status == opts.BRANCH:
            self.mode = m.option.name
            out = self.on_mode(m.option.name, ctx)
            self._note_mode(m.option.name, out)
            return out
        # AMBIGUOUS / INVALID: report and stay put, as the reference CAD application does
        return m.message, False

    #: MODE options that are one-shot ACTIONS, not states -- never shown as "in use"
    ACTION_MODES = frozenset({"Undo", "Redo", "SetBasePoint", "Toggle", "On", "Off",
                              "Enable", "Disable", "UseLastCenter", "UseLastAxis",
                              "List", "SelectCurve", "Label", "MarkEnds"})

    def _note_mode(self, name, out):
        """Remember a MODE option that took effect, for `active_modes`. A refused one
        ("... not implemented yet", or one the command answered with an error and
        no change) and a one-shot action are not states, so they are not recorded."""
        msg = (out[0] if isinstance(out, tuple) and out else "") or ""
        if name in self.ACTION_MODES or "not implemented" in msg.lower():
            return
        if "first" in msg.lower() and "choose" in msg.lower():
            return                          # "choose the curve first": refused
        self._active_mode = name

    def active_modes(self):
        """Return the MODE options currently in effect for command-line highlighting.

        The last successful mode is active by default. Commands with independent modes,
        such as a rectangle that is both centred and rounded, extend this set. Toggle,
        enum, and value options already expose their state as ``Name=Value``.
        """
        name = getattr(self, "_active_mode", None)
        return {name} if name else set()

    def begin(self, ctx):
        """Called right after the command is named.

        Zero-step commands (Help/Zoom) run immediately on the naming Enter,
        like the reference CAD application. A command whose first step is a set-step (Delete,
        Hide, Lock, PointsOn, PointsOff) likewise runs immediately when a
        pre-selection is present. Returns (message|None, done:bool).
        """
        # SelLast's "last created" batch starts with this command (lazily: a command that
        # creates nothing keeps the previous batch -- MEASURED research_all_13 #142).
        model = getattr(ctx, "model", None)
        if model is not None:
            selection_state.begin_command(model)
        if not self.steps and not self.variable_points:
            # one command is one undo step. The record is only kept if the
            # command actually changed something (`note_change` captures; `end`
            # discards an empty record), so Zoom and Help leave no history.
            return self._recorded(ctx, lambda: self.execute(ctx))
        if (self.accepts_curve_subobjects and self.preselect_names and not self.args
                and self.steps and self.steps[0][1] in SELECTION_KINDS):
            try:
                components = self._selected_curve_subobjects(ctx)
            except curve_inputs.CurveInputError as exc:
                return f"{self.name}: {exc}", False
            if components:
                # Do not let preselection substitute stale whole names for parts.
                return self._recorded(ctx, lambda: self._feed("", ctx))
        self._take_preselection(ctx)
        self._take_objects_selection(ctx)
        if self.step_index >= len(self.steps) and not self.variable_points:
            # the selection supplied everything; run now, as the reference CAD application does
            return self._recorded(ctx, lambda: self.execute(ctx))
        return None, False

    def _take_preselection(self, ctx):
        """Fill leading name steps from the current selection. Returns how many.

        the reference CAD application's normal flow is to select first and then run the command, so a
        command that opts in (`preselect_names`) must not prompt for what is
        already selected. Only LEADING `name` steps are consumed, and only as many
        as `preselect_names` allows, so a command that also needs points still asks
        for them.
        """
        wanted = int(getattr(self, "preselect_names", 0) or 0)
        if wanted <= 0 or self.args:
            return 0
        getter = getattr(ctx, "selected_objects", None)
        if not callable(getter):
            return 0
        try:
            selected = list(getter())
        except Exception:  # noqa: BLE001
            return 0
        if not selected:
            return 0
        taken = 0
        while (taken < wanted and self.step_index < len(self.steps)
               and self.steps[self.step_index][1] == NAME
               and taken < len(selected)):
            self.args.append(selected[taken].name)
            self.step_index += 1
            taken += 1
        return taken

    def _take_objects_selection(self, ctx):
        """satisfy leading `objects` steps from the current selection.

        An `objects` step is answered by the selection (or a typed name -- see
        ``_feed_objects_step``). If something is selected, the step is satisfied
        immediately and the command proceeds to its next step (or runs). If
        nothing is selected, the step stays put and prompts -- the user selects
        and presses Enter, which ``_feed_objects_step`` then satisfies. The first
        selected object's name is taken (the shape ``ExtrudeCrv`` expects in
        ``args[0]``).

        Like ``_take_preselection``, this is opt-in via ``preselect_names`` (the
        selection-fill count) and gated on the ``objects`` step kind; a command
        that did not ask for it does not consume the selection.
        Returns how many steps were satisfied.
        """
        wanted = int(getattr(self, "preselect_names", 0) or 0)
        if wanted <= 0 or self.args:
            return 0
        getter = getattr(ctx, "selected_objects", None)
        if not callable(getter):
            return 0
        try:
            selected = list(getter())
        except Exception:  # noqa: BLE001
            return 0
        if not selected:
            return 0
        taken = 0
        while (self.step_index < len(self.steps)
               and self.steps[self.step_index][1] in SELECTION_KINDS
               and taken < wanted and taken < len(selected)):
            kind = self.steps[self.step_index][1]
            if kind == OBJECTS_MULTI:
                # the WHOLE selection, as a list -- and only ONE such step is
                # filled. A second set-step must be answered by a fresh selection,
                # so the loop stops here and the selection is cleared for it. Taking
                # the same set for both would make `BooleanDifference` subtract an
                # object from itself.
                self.args.append([o.name for o in selected])
                self.step_index += 1
                taken += 1
                # Clear the selection only before ANOTHER selection step, so a
                # two-set command's second set starts empty. A following
                # point/number step (Rotate's centre, Mirror's points)
                # must NOT clear -- the command acts on this very selection.
                if (self.step_index < len(self.steps)
                        and self.steps[self.step_index][1] in SELECTION_KINDS):
                    selector = getattr(ctx, "select_by_names", None)
                    if callable(selector):
                        try:
                            selector([])
                        except Exception:  # noqa: BLE001
                            pass
                break
            self.args.append(selected[taken].name)
            self.step_index += 1
            taken += 1
        if (taken and self.step_index < len(self.steps)
                and self.steps[self.step_index][1] in SELECTION_KINDS
                and taken >= len(selected)):
            selector = getattr(ctx, "select_by_names", None)
            if callable(selector):
                try:
                    selector([])
                except Exception:  # noqa: BLE001
                    pass
        return taken

    @staticmethod
    def _selected_curve_subobjects(ctx):
        getter = getattr(ctx, "selected_subobjects", None)
        if not callable(getter):
            try:
                from . import ui
                getter = ui.ordered_selected_subobjects
            except (ImportError, AttributeError):
                getter = None
        try:
            return list(getter() or []) if callable(getter) else []
        except Exception as exc:  # never fall back to whole owners on a failed pick read
            raise curve_inputs.CurveInputError(f"cannot read curve selection: {exc}") from exc

    def _curve_component_refs(self, ctx):
        # The UI supplies its current ordered list (currently sorted); do not sort
        # again or collapse different parts of one owner into a single name.
        refs = [curve_inputs.reference_from_selection(item)
                for item in self._selected_curve_subobjects(ctx)]
        for ref in refs:
            curve_inputs.resolve(ctx, ref)  # validate detached EXACT geometry before accepting
        return refs

    @staticmethod
    def _clear_curve_input_selection(ctx):
        selector = getattr(ctx, "select_by_names", None)
        if callable(selector):
            selector([])
        clearer = getattr(ctx, "clear_subobject_selection", None)
        if not callable(clearer):
            try:
                from . import ui
                clearer = ui.clear_subobject_selection
            except (ImportError, AttributeError):
                clearer = None
        if callable(clearer):
            clearer()

    def _feed_objects_step(self, text, ctx):
        """answer the current `objects` step.

        The step is satisfied by the SELECTION or by a typed name (the reference CAD application's object
        prompt accepts both), so it gets its own branch (see ``_feed``) instead of
        falling into the load-bearing "empty Enter on a fixed prompt cancels" rule
        that the other step kinds rely on:

        * Enter (empty text) on a non-empty selection -> the step is done (and the
          command runs if this was its last step);
        * Enter with nothing selected -> report that, and STAY: do not proceed
          with zero objects and do not cancel -- the user can still select and
          press Enter (Esc is the cancel, handled by ``ui`` / ``state.cancel``);
        * non-empty text -> a typed object name. The selection is a shortcut, not
          a replacement: explicitly naming a curve must keep working, so the name is
          taken here and
          validated when the command runs (`execute` reports a missing curve).

        Returns (message|None, done:bool).
        """
        if self.accepts_curve_subobjects and not text:
            try:
                refs = self._curve_component_refs(ctx)
            except curve_inputs.CurveInputError as exc:
                return f"{self.name}: {exc}", False
            if len(refs) > 1:
                return f"{self.name}: select one curve, segment or surface edge", False
            if refs:
                text = refs[0]
        getter = getattr(ctx, "selected_objects", None)
        try:
            selected = list(getter()) if callable(getter) else []
        except Exception:  # noqa: BLE001
            selected = []
        if not text:
            if selected:
                self.args.append(selected[0].name)
                self.step_index += 1
                if self.accepts_curve_subobjects:
                    self._clear_curve_input_selection(ctx)
                if self.step_index >= len(self.steps):
                    return self.execute(ctx)
                return None, False
            return "Nothing selected", False
        # non-empty text: a typed name (the selection path is a shortcut, not a
        # replacement). Take it; the command's `execute` validates it.
        self.args.append(text)
        self.step_index += 1
        if self.accepts_curve_subobjects:
            self._clear_curve_input_selection(ctx)
        if self.step_index >= len(self.steps):
            return self.execute(ctx)
        return None, False

    def _feed_objects_multi_step(self, text, ctx):
        """answer the current `objects_multi` step -- a whole SET of objects.

        shape (the command behavior): *"Select the objects and press
        Enter."* So:

        * **non-empty text** -> a typed object name. It is ACCUMULATED and the step
          stays open, because the user may name several before confirming. (The
          single-object `OBJECTS` step advances on the first name; this one cannot,
          or you could never name a second.)
        * **Enter** -> confirm. The set is whatever was typed, or else the current
          selection. The argument appended is a **list of names**.
        * **Enter with nothing typed and nothing selected** -> report it and STAY.
          Do not proceed with an empty set and do not cancel -- Esc is the cancel
          (``ui`` / ``state.cancel``). The previous path incorrectly fell through to
          the fixed-prompt rule where empty Enter cancels.

        The selection is CLEARED after a confirmed set, which is load-bearing for
        the two-set commands: `BooleanDifference` asks for "subtract from" and then
        "subtract with", and without the clear the still-selected first set would be
        taken again as the second set the moment the user pressed Enter.

        Returns (message|None, done:bool).
        """
        if text:
            self._pending_multi.append(text)
            return (f"{text} added ({len(self._pending_multi)} so far) -- "
                    f"press Enter when done"), False

        names = list(self._pending_multi)
        if self.accepts_curve_subobjects:
            try:
                # Typed names are deliberate inputs; components supersede only
                # stale whole-object selection, not names explicitly entered.
                names.extend(self._curve_component_refs(ctx))
            except curve_inputs.CurveInputError as exc:
                return f"{self.name}: {exc}", False
        if not names:
            getter = getattr(ctx, "selected_objects", None)
            try:
                names = [o.name for o in (getter() or [])] if callable(getter) else []
            except Exception:  # noqa: BLE001
                names = []
        if not names:
            # A selection-acting command can opt into cancelling on an empty confirm.
            # Two-set Boolean commands do not opt in, so they continue waiting.
            if getattr(self, "cancel_on_empty_set", False):
                if getattr(self, "silent_empty_cancel", False):
                    return None, True
                return f"{self.name} cancelled", True
            return "Nothing selected", False

        self._pending_multi = []
        self.args.append(names)
        self.step_index += 1
        if self.accepts_curve_subobjects:
            self._clear_curve_input_selection(ctx)
        if self.step_index < len(self.steps):
            # Clear the selection only before ANOTHER selection step (a two-set
            # command's second set must start empty -- see the docstring). A
            # following point/number step (a transform's centre/points)
            # must NOT clear it: the command acts on this very selection, which
            # stays live all the way to `execute`.
            if self.steps[self.step_index][1] in SELECTION_KINDS:
                selector = getattr(ctx, "select_by_names", None)
                if callable(selector):
                    try:
                        selector([])
                    except Exception:  # noqa: BLE001
                        pass
            return None, False
        return self.execute(ctx)

    def _recorded(self, ctx, work):
        """Run `work` inside one undo record named after this command.

        Nesting is safe -- `UndoStack.begin` joins an open record -- so a command
        that reaches the document through several layers still produces a single
        undo step. A raising command abandons its record rather than leaving a
        half-described change in the history.
        """
        stack = getattr(ctx, "begin_undo", None)
        if stack is None or not self.records_undo:
            return work()          # no history, or a command that navigates it
        ctx.begin_undo(self.name)
        try:
            result = work()
        except Exception:
            ctx.undo.abandon()
            raise
        ctx.end_undo()
        return result

    def feed(self, text: str, ctx):
        """Feed one line of input. Returns (message|None, done:bool).

        the whole line is handled inside ONE undo record, so a command that
        commits on its final input (`Circle` on its radius) yields a single undo
        step. Lines that change nothing -- an option toggle, a re-prompt after a
        typo -- capture no snapshot and so leave no record behind.
        """
        return self._recorded(ctx, lambda: self._feed(text, ctx))

    def _feed(self, text: str, ctx):
        text = text.strip()

        # a bare ENUM/VALUE option asked for a value; this line is it.
        if self.pending_option is not None:
            option = self.pending_option
            if not text:
                self.pending_option = None
                return f"{option.name} unchanged", False
            value, err = opts.coerce(option, text)
            if err:
                return err, False          # stay on the sub-prompt
            self.opt[option.name] = value
            self.pending_option = None
            hook = self.on_option_set(option, value, ctx)
            if hook is not None:
                return hook
            return f"{opts.format_option(option, self.opt)}", False

        # is this an option rather than a step argument? `match` returns
        # NONE for ordinary input (points, decimals), so steps still work.
        #
        # except for BARE INTEGERS. the reference CAD application names two real options `2Point`
        # and `3Point`, and unique-prefix matching made `2` a prefix of `2Point` --
        # so at Circle's `Radius:` prompt, typing a radius of 2 or 3 silently ran
        # the 2Point/3Point option instead. You could not draw a circle of radius
        # 2. (`2.5` was fine, which is why this hid for so long.)
        #
        # A value prompt takes a VALUE: when the current step wants a number or a
        # distance and the text parses as one, it is that number -- never an
        # option name. An option is still reachable there by its letters
        # (`2P`, `2Po`, `2Point`), which is how the reference CAD application invokes it.
        if self.options and text and not self._numeric_input_wins(text):
            m = opts.match(text, self.options, self.opt)
            if m.status != opts.NONE:
                return self._apply_option(m, ctx)

        if self.variable_points:
            # keep collecting points until an empty Enter finishes the command
            if not text:
                return self.execute(ctx)
            # a bad coordinate re-prompts (the reference CAD application does not cancel the
            # command over a typo), so report it and stay on this step.
            try:
                point = self._parse("point", text, ctx)
            except ValueError as e:
                return str(e), False
            self.args.append(point)
            hook = self.on_point(point, ctx)
            if hook is not None:
                return hook
            return None, False

        # a zero-step command (Zoom/Delete/Help) has no argument to take,
        # so anything left here was meant to be an option and was not one. Say
        # so instead of indexing past the end of `steps` -- and instead of
        # silently running the default, which would make `Zoom Selected` look
        # like it worked while actually zooming to extents.
        if not self.steps:
            if not text:
                return self.execute(ctx)
            return self.unknown_option_message(text), True

        # an `objects` step is answered by the selection (or a typed
        # name), and its Enter means CONFIRM, not cancel -- so it has its own
        # branch placed BEFORE the "empty Enter cancels" rule below. That rule
        # stays load-bearing for every other step kind (point/number/...), and
        # relaxing it globally is exactly what forbids.
        if self.step_index < len(self.steps) \
                and self.steps[self.step_index][1] in SELECTION_KINDS:
            if self.steps[self.step_index][1] == OBJECTS_MULTI:
                return self._feed_objects_multi_step(text, ctx)
            return self._feed_objects_step(text, ctx)

        if not text:
            # a command may give Enter at a fixed point/number step a
            # meaning of its own (Move/Copy use the selection's bounding-box
            # centre as the "from" point -- the command behavior). The
            # default returns None, so Enter still cancels for every other
            # command and step, exactly as before.
            replacement = self._enter_default(ctx)
            if replacement is not None:
                self.args.append(replacement)
                self.step_index += 1
                if self.step_index >= len(self.steps):
                    return self.execute(ctx)
                return None, False
            # empty Enter on a fixed prompt cancels the command
            return f"{self.name} cancelled", True

        kind = self.steps[self.step_index][1]
        # invalid input re-prompts instead of cancelling, as the reference CAD application does.
        try:
            value = self._parse(kind, text, ctx)
        except ValueError as e:
            return str(e), False
        self.args.append(value)
        self.step_index += 1
        # if a VALUE size option (Circle's Circumference / Area) was typed
        # BEFORE the centre, the centre is now in self.args and the parked value
        # can be consumed: append it and build. The command is otherwise at
        # step 0 (centre), so this only fires on the centre step.
        if (getattr(self, "_pending_size_value", None) is not None
                and self.step_index == 1
                and len(self.args) == 1):
            self.args.append(self._pending_size_value)
            self._pending_size_value = None
            return self.execute(ctx)
        if self.step_index >= len(self.steps):
            return self.execute(ctx)
        return None, False

    def escape_commits(self):
        """True when Esc at the current prompt should FINISH the command (like Enter)
        instead of cancelling it. The default is False: Esc cancels. The UI's Esc
        handler asks this first."""
        return False

    def _enter_default(self, ctx):
        """Value to substitute when the user presses Enter at the current fixed
        step, or None to keep the default behaviour (cancel the command).

        Move/Copy override this so Enter at the "from" prompt uses the
        selection's bounding-box centre (the command behavior). The base
        returns None, so no other command's Enter behaviour changes.
        """
        return None

    def execute(self, ctx):
        raise NotImplementedError


def _tag_representation(geom):
    """Record whether ``geom`` is analytic or mesh-derived at the document boundary.

    Every geometry object enters through ``add_geometry``, making this the common place
    to enforce representation tagging. Existing producer tags are retained. An untagged
    solid that looks tessellated is tagged ``MESH_DERIVED`` and logged; other untagged
    solids are tagged ``ANALYTIC``.

    Representation bookkeeping never raises during object creation.
    """
    try:
        from . import kernel
    except Exception:  # noqa: BLE001
        return None
    try:
        if not getattr(geom, "IsSolid", False):
            return None                     # curves/surfaces have nothing to declare
        if kernel.representation_of(geom) != kernel.UNKNOWN:
            return kernel.representation_of(geom)
        if kernel.looks_tessellated(geom):
            total, planar = kernel.face_summary(geom)
            kernel.tag(geom, kernel.MESH_DERIVED)
            app_state.log(
                f"note: that solid is mesh-derived ({total} faces, {planar} planar) "
                f"-- curved surfaces are faceted, so it is not exportable as surfaces")
            return kernel.MESH_DERIVED
        kernel.tag(geom, kernel.ANALYTIC)
        return kernel.ANALYTIC
    except Exception:  # noqa: BLE001
        return None


def _assign_picture_material(model, obj_id, geom):
    """a Picture object entering the document gets its own Picture material.

    Called by `NarwhalContext.add_geometry`, so EVERY route a picture surface takes into the
    document -- `Picture` itself, Copy, Mirror, a Split piece -- carries one. (the command behavior): "When a Picture plane is split and shrunk, a new material will
    be added for each new Picture plane." Returns the material index, or None.
    """
    frame = bridge.picture_frame(geom)
    if frame is None:
        return None
    try:
        index = model.add_picture_material(frame["image"])
        model.set_object_material(obj_id, index)
        return index
    except Exception:  # noqa: BLE001
        return None


def _picture_display_material(image_path):
    """The Blender material that SHOWS a picture: an Image Texture node on the base colour,
    read through the mesh's UV map. One per image file. A missing file still gets a
    material (untextured) rather than failing the object."""
    import os

    import bpy

    path = os.path.abspath(image_path)
    name = f"narwhal_picture:{path}"
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    tex = nodes.new("ShaderNodeTexImage")
    try:
        tex.image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        app_state.log(f"Picture: the image file {path} cannot be read; shown untextured")
    if bsdf is not None:
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def selection_plan(records, names):
    """Plan the minimal selection delta for ordered ``(name, selected)`` rows.

    This is deliberately independent of Blender: names are matched
    case-insensitively, document order is retained, missing names are ignored,
    and the result contains no operation for an object already in its desired
    state. The caller performs the small number of state-changing writes.
    """
    rows = list(records or [])
    wanted = {str(name).lower() for name in (names or [])}
    found = [name for name, _selected in rows if str(name).lower() in wanted]
    return {
        "found": found,
        "select": [name for name, selected in rows
                   if str(name).lower() in wanted and not selected],
        "deselect": [name for name, selected in rows
                     if str(name).lower() not in wanted and selected],
        "active": found[0] if found else None,
    }


class NarwhalContext:
    """Everything a command can touch: model, Blender scene, selection."""

    def __init__(self, model):
        self.model = model
        self.counters: dict[str, int] = {}
        # the document history. Lives on the context because that is what
        # every command already holds, and the context owns the model the
        # snapshots are of.
        self.undo = undo.UndoStack()

    # -- undo / redo  ------------------------------------------------
    #
    # The snapshot is taken at the moment of MUTATION, not when a command
    # starts. Two reasons, both learned from this codebase:
    #   * A command that changes nothing must leave no undo step -- a cancelled
    #     `Line`, a `Zoom`, an osnap toggle. the reference CAD application behaves that way, and lazy
    #     capture gets it for free.
    #   * `Boolean` overrides `begin` and can mutate from there, so anything
    #     keyed only on the normal step flow would miss it. Hooking the mutation
    #     itself cannot be bypassed by an override.

    def begin_undo(self, name):
        """Open an undo record for `name`. Nesting joins the record already open."""
        return self.undo.begin(name)

    def end_undo(self):
        """Close the record; it is kept only if something was actually captured."""
        return self.undo.end()

    def note_change(self, name=None):
        """Call IMMEDIATELY BEFORE mutating the document.

        Captures the "before" snapshot on the open record, once. If no record is
        open this opens (and leaves open) an implicit one -- better a correctly
        named step we close a moment later than a silently unundoable change.
        """
        if not self.undo.enabled:
            return False
        if self.undo.recording and self.undo.captured:
            return False
        implicit = not self.undo.recording
        if implicit:
            self.undo.begin(name or "Change")
        captured = self.undo.capture(self.model.snapshot())
        if implicit:
            # nobody is bracketing this change, so close it right away and let it
            # stand on its own as one undo step
            self.undo.end()
        return captured

    def undo_last(self):
        """Step back one record. Returns its name, or None if there is nothing."""
        return self._navigate_history('undo')

    def redo_last(self):
        """Step forward one record. Returns its name, or None."""
        return self._navigate_history('redo')

    def _navigate_history(self, direction):
        started = False

        def apply(snapshot):
            nonlocal started
            self.model.validate_restore(snapshot)
            started = True
            self._restore_document(snapshot)

        # A failed pure admission is known not to have changed the document.
        # Once restore/display work starts, an exception is not rollback proof.
        record = getattr(self.undo, direction)(self.model.snapshot(), restore=apply,
            failure_state=lambda _exc: 'unknown' if started else 'unchanged')
        return record.name if record is not None else None

    def _restore_document(self, snap):
        """Put `snap` back, rebuild the display, and keep the selection.

        `rebuild_display` deletes every Blender object and recreates it, so the
        selection -- and any reference held to a selected object -- does not
        survive it. Undoing a Gumball drag therefore used to make the widget
        vanish, because nothing was selected any more. the reference CAD application leaves an object
        selected after undoing a change to it, so the names are captured and
        re-selected here.

        Selection is matched by NAME because that is what survives the rebuild;
        object ids survive too (`model.restore` preserves them) but the Blender
        objects are new instances either way.
        """
        try:
            selected = [ob.name for ob in self.selected_objects()]
        except Exception:  # noqa: BLE001
            selected = []            # headless: no selection to preserve
        self.model.restore(snap)
        # only the objects the restore changed (bridge.refresh_display_objects)
        if not bridge.refresh_display_objects(
                self, getattr(self.model, "last_restore", None)):
            self.rebuild_display()
        if selected:
            self.select_by_names(selected)
        return True

    def expand_group_names(self, names):
        """Expand object names to every transitively connected document-group member.

        (the command behavior): grouping "allows all grouped objects to be selected as one"
        and "commands apply to the entire group." Overlapping groups therefore form one
        selectable unit: if B belongs to A's group and C's group, selecting A reaches C.
        """
        return _expanded_group_names(self.model, names)

    def select_by_names(self, names, already_expanded=False):
        """Select exactly ``names`` (case-insensitive), clearing anything else.

        ``already_expanded`` is for hit-test callers that expanded groups before
        applying modifier semantics. Other callers keep the normal group behavior.
        Only objects whose state changes receive ``select_set``; this matters for
        large imported documents where selection is otherwise a redraw-sized stall.
        """
        try:
            import bpy
        except Exception:  # noqa: BLE001
            # Headless / no bpy: nothing gets selected, but the selection DID
            # change (to empty) -- record it so SelPrev still tracks it.
            selection_state.selection_changed(self.model, [])
            return []
        if not already_expanded:
            names = self.expand_group_names(names)
        objects = list(bpy.data.objects)
        records = []
        for ob in objects:
            try:
                selected = bool(ob.select_get())
            except Exception:  # noqa: BLE001
                selected = False
            records.append((ob.name, selected))
        plan = selection_plan(records, names)
        by_name = {ob.name.lower(): ob for ob in objects}
        found = []
        for name in plan["deselect"]:
            ob = by_name.get(name.lower())
            if ob is not None:
                try:
                    ob.select_set(False)
                except Exception:  # noqa: BLE001
                    pass
        for name in plan["select"]:
            ob = by_name.get(name.lower())
            if ob is None:
                continue
            try:
                ob.select_set(True)
            except Exception:  # noqa: BLE001
                continue
        for name in plan["found"]:
            ob = by_name.get(name.lower())
            if ob is None:
                continue
            try:
                if bool(ob.select_get()):
                    found.append(ob.name)
            except Exception:  # noqa: BLE001
                continue
        active = by_name.get(plan["active"].lower()) if plan["active"] else None
        try:
            if active is not None:
                bpy.context.view_layer.objects.active = active
        except Exception:  # noqa: BLE001
            pass
        # the selection changed -- the OLD current set is now the
        # "previous" selection that `SelPrev` re-selects. `select_by_names`
        # is the single choke point that also records it.
        selection_state.selection_changed(self.model, found)
        return found

    # -- naming
    def next_name(self, base: str) -> str:
        n = self.counters.get(base, 0) + 1
        self.counters[base] = n
        return f"{base}{n}"

    # -- construction plane
    def active_cplane(self):
        """The ACTIVE viewport's construction plane, or None if there is none.

        the reference CAD application reads a typed point in this plane's coordinates. `ui` is imported
        lazily because `ui` imports this module, so a top-level import would be
        circular; and it imports `bpy`, which does not exist outside Blender.
        Returning None lets `Command._active_cplane` fall back to World Top,
        which is what the headless suites (and the reference CAD application's default Perspective
        viewport) use.
        """
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return None
        try:
            return ui.active_cplane()
        except Exception:  # noqa: BLE001
            return None

    def set_active_cplane(self, plane):
        """Give the ACTIVE viewport its own construction plane.

        Returns True if it was applied. False means there was no viewport to
        apply it to (headless), which a command should report rather than treat
        as success.
        """
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return False
        try:
            return bool(ui.set_cplane(ui.active_pane_area(), plane))
        except Exception:  # noqa: BLE001
            return False

    # -- selection (Blender objects)
    def selected_objects(self):
        # not `bpy.context.selected_objects`: it omits every curve, because Blender no
        # longer draws them (bridge.CURVE_COLLECTION)
        return bridge.selected_display_objects()

    def object_by_name(self, name: str):
        import bpy
        # the reference CAD application names are case-insensitive
        ob = bpy.data.objects.get(name)
        if ob is not None:
            return ob
        for o in bpy.data.objects:
            if o.name.lower() == name.lower():
                return o
        return None

    # -- find rhino3dm geometry stored in the model by display name
    def geom_by_name(self, name: str):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    # -- create a Blender object from rhino3dm geometry
    def add_geometry(self, geom, base_name: str,
                     layer_index: int | None = None, *, publication_check=None) -> tuple[str, str]:
        if publication_check is not None:
            return self._guarded_geometry_add(geom, base_name, layer_index, publication_check)
        return self._add_geometry(geom, base_name, layer_index)

    @staticmethod
    def _same_publication_document(before, after):
        """Compare existing Undo snapshots, not meshes or a new document store.

        Shared immutable entries need no serialization; changed API wrappers are
        compared by their persisted contents. Only used to settle a failed addition.
        """
        for field in ("settings", "layers", "groups", "strings"):
            if getattr(before, field) != getattr(after, field):
                return False
        if before.by_id.keys() != after.by_id.keys():
            return False
        for oid, entry in before.by_id.items():
            other = after.by_id[oid]
            if entry is other:
                continue
            if entry.get('_attributes') is not None and other.get('_attributes') is not None:
                if entry['_attributes'].Encode() != other['_attributes'].Encode():
                    return False
            for field in ("attrs", "user_strings", "groups"):
                if entry[field] != other[field]:
                    return False
            if entry["geometry"].Encode() != other["geometry"].Encode():
                return False
        return True

    def _settle_failed_publication(self, record, previous_before, file, baseline):
        """Keep surviving external edits undoable/dirty and invalidate stale Redo.

        UndoStack.end handles the serial and Redo chain normally. A failed Sweep1
        must not erase an external edit's capture just to leave no Sweep1 record.
        Never attach a replacement file to the old document's history.
        """
        if record is None or self.undo._open is not record:
            return
        if previous_before is not None:
            if record.name != "Sweep1":
                record.before = previous_before  # Preserve a legitimate enclosing record.
                return
            # Selection-clear callbacks can edit sources in this feed's Sweep1
            # record BEFORE execute starts. That capture is external too.
            baseline, previous_before = previous_before, None
        if self.model.file is file and not self._same_publication_document(baseline, self.model.snapshot()):
            record.before = baseline
            record.name = "External change during Sweep1"
        else:
            record.before = previous_before

    @staticmethod
    def _remove_publication_allocations(allocations):
        """Delete only allocation-time RNA handles, never names or pointer deltas."""
        import bpy

        objects = []
        for obj in allocations["objects"]:
            try:
                if obj.as_pointer():
                    objects.append(obj)
            except ReferenceError:
                pass  # An external document replacement already deleted it.
        bridge.note_display_changed([o.name for o in objects if bridge.is_app_drawn(o)], removed=True)
        if objects:
            # Do not use remove_display_objects: a callback may have assigned an
            # unrelated mesh to our object, and that helper also deletes its data.
            bpy.data.batch_remove(objects)
        for mesh in allocations["meshes"]:
            try:
                if mesh.as_pointer() and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass

    def _guarded_geometry_add(self, geom, base_name, layer_index, check):
        """Opt-in single-result rollback; never restore an entire document/registry.

        Sweep1 needs this because a display failure occurs AFTER document insertion.
        The ordinary command/Undo path is retained; only this operation's allocation
        and lazy undo capture are undone. Python owns Blender/API transactions only.
        """
        import bpy
        from . import kernel

        model, file, stack = self.model, self.model.file, self.undo
        record = stack._open
        if stack.enabled and record is None:
            raise RuntimeError("guarded geometry publication requires an open undo record")
        before = record.before if record is not None else None
        baseline = model.snapshot()
        counter = self.counters.get(base_name, 0)
        name = f"{base_name}{counter + 1}"
        # Refuse a collision rather than let Blender silently rename only the display.
        if any((o.Attributes.Name or "").lower() == name.lower() for o in file.Objects) or bpy.data.objects.get(name):
            raise RuntimeError("publication display name already exists")
        created_before = selection_state.last_created(model)
        master = kernel.master_of(geom)
        key = kernel.master_key_of(geom)
        publication = {"check": check, "allocations": {"objects": [], "meshes": []}}
        previous_hook = model.before_change

        def before_insert():
            check()
            if previous_hook is not None:
                previous_hook()
            # A callback may switch files. Stop BEFORE Objects.Add can target it;
            # rollback must never scan/delete objects in a replacement document.
            check()

        try:
            check()
            model.before_change = before_insert
            result = self._add_geometry(geom, base_name, layer_index, publication)
            check()
            return result
        except Exception:
            # An injected writer may insert then raise before returning its UUID.
            # Match our unique binding, not all IDs added since a global snapshot.
            for obj in list(file.Objects):
                if key and kernel.master_key_of(obj.Geometry) == key:
                    oid = str(obj.Attributes.Id)
                    file.Objects.Delete(oid)
                    # The native object index removes this UUID's display association
                    # when it observes the deletion; no separate Python map to clean.
            self._remove_publication_allocations(publication["allocations"])
            if self.counters.get(base_name) == counter + 1:
                if counter:
                    self.counters[base_name] = counter
                else:
                    self.counters.pop(base_name, None)
            entry = selection_state._state.get(model)
            if entry is not None and name not in created_before:
                entry["created"][:] = [n for n in entry["created"] if n != name]
                if name in entry.get("batch", []):
                    entry["batch"].remove(name)
            self._settle_failed_publication(record, before, file, baseline)
            kernel.discard_master_binding(key, master)
            raise
        finally:
            if model.before_change is before_insert:
                model.before_change = previous_hook

    def _add_geometry(self, geom, base_name, layer_index, publication=None):
        import bpy

        def check():
            if publication is not None:
                publication["check"]()

        # snapshot BEFORE the document changes, so this addition is undoable
        self.note_change(base_name)
        check()
        _tag_representation(geom)
        display_name = self.next_name(base_name)
        obj_id, _attr = self.model.add_object(geom, display_name, layer_index)
        check()
        # wiring: `layer_index=None` means "the current layer" --
        # `model.add_object` resolves it to `model.current_layer_index`. Resolve
        # the SAME index here so the display object is coloured by the layer the
        # object actually landed on, not always layer 0. Passing an explicit 0 here
        # would incorrectly ignore the current layer. An explicit
        # index from a caller that inherits a layer -- Boolean, Join -- is used
        # as given.
        resolved = (self.model.current_layer_index if layer_index is None
                    else layer_index)
        _assign_picture_material(self.model, obj_id, geom)
        if publication is None:
            self._make_display_object(geom, display_name, resolved)
        else:
            display = self._make_display_object(
                geom, display_name, resolved, allocations=publication["allocations"])
            if display is None:
                raise RuntimeError("publication did not create a display object")
        check()
        self.model.register_blender_object(obj_id, display_name)
        # this object was just CREATED by a drawing command -- record it
        # so `SelLast` can select the "last created objects". `add_geometry` is
        # the single choke point for creation, so it is recorded here.
        selection_state.record_created(self.model, display_name)
        # the document changed, so the cached snap targets are stale -- the
        # object just drawn must be snappable to immediately (that is the whole
        # point of snapping while you draw).
        try:
            from . import ui

            ui.invalidate_snap_cache()
        except Exception:  # noqa: BLE001
            pass            # headless / no bpy: nothing is cached anyway
        return obj_id, display_name

    @staticmethod
    def _matrix_from_xform(xform):
        from mathutils import Matrix

        return Matrix(((xform.M00, xform.M01, xform.M02, xform.M03),
                       (xform.M10, xform.M11, xform.M12, xform.M13),
                       (xform.M20, xform.M21, xform.M22, xform.M23),
                       (xform.M30, xform.M31, xform.M32, xform.M33)))

    @staticmethod
    def _append_bmesh(target, source, matrix=None):
        """Append ``source`` to ``target``, optionally transformed; consume ``source``."""
        vertices = {}
        try:
            for vertex in source.verts:
                co = matrix @ vertex.co if matrix is not None else vertex.co
                vertices[vertex] = target.verts.new(co)
            for face in source.faces:
                try:
                    made = target.faces.new(tuple(vertices[v] for v in face.verts))
                    made.smooth = face.smooth
                except ValueError:
                    pass
            # Faces create their own edges. Preserve wires/points in a mixed definition too.
            for edge in source.edges:
                if edge.link_faces:
                    continue
                try:
                    target.edges.new((vertices[edge.verts[0]], vertices[edge.verts[1]]))
                except ValueError:
                    pass
        finally:
            source.free()

    def _definition_bmesh(self, definition_id, definitions, objects, stack=()):
        """One block definition in its local coordinates, recursively and non-destructively."""
        import bmesh

        key = str(definition_id)
        if key in stack:
            raise ValueError(f"cyclic block definition {key}")
        definition = definitions.get(key)
        if definition is None:
            raise ValueError(f"missing block definition {key}")
        result = bmesh.new()
        try:
            for object_id in definition.GetObjectIds():
                fobj = objects.get(str(object_id))
                if fobj is None:
                    continue
                geom = fobj.Geometry
                if isinstance(geom, r3.InstanceReference):
                    part = self._definition_bmesh(
                        geom.ParentIdefId, definitions, objects, stack + (key,))
                    self._append_bmesh(result, part, self._matrix_from_xform(geom.Xform))
                else:
                    self._append_bmesh(result, bridge.geometry_to_bmesh(geom))
            if not result.verts:
                raise ValueError(f"block definition {definition.Name!r} has no drawable geometry")
            return result
        except Exception:
            result.free()
            raise

    def _apply_layer_material(self, ob, layer_index, layers):
        import bpy

        if 0 <= layer_index < len(layers):
            r, g, b = (c / 255.0 for c in layers[layer_index]["color"][:3])
            mat = bpy.data.materials.get(f"narwhal_layer_{layer_index}")
            if mat is None:
                mat = bpy.data.materials.new(f"narwhal_layer_{layer_index}")
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf:
                    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
            ob.data.materials.append(mat)

    def _make_instance_display(self, fobj, display_name, layer_index, layers,
                               definitions, definition_objects, mesh_cache):
        """Draw one the reference CAD application block reference as one selectable, shared display object."""
        import bpy

        ref = fobj.Geometry
        definition_key = str(ref.ParentIdefId)
        cache_key = (definition_key, int(layer_index))
        mesh = mesh_cache.get(cache_key)
        if mesh is None:
            bm = self._definition_bmesh(definition_key, definitions, definition_objects)
            ob = bridge.bmesh_to_object(display_name, bm)
            self._apply_layer_material(ob, layer_index, layers)
            mesh_cache[cache_key] = ob.data
        else:
            ob = bpy.data.objects.new(display_name, mesh)
            bpy.context.scene.collection.objects.link(ob)
        ob.matrix_world = self._matrix_from_xform(ref.Xform)
        ob["narwhal_instance_definition"] = definition_key
        ob["narwhal_instance_layer"] = int(layer_index)
        return ob

    def _make_file_object_display(self, fobj, layers, definitions=None,
                                  definition_objects=None, mesh_cache=None):
        """Display one document object, using the same dispatch for Open and Undo/Redo."""
        import bpy

        if fobj.Attributes.Mode == r3.ObjectMode.InstanceDefinitionObject:
            return None
        geom = fobj.Geometry
        name = fobj.Attributes.Name or ""
        layer_index = int(fobj.Attributes.LayerIndex)
        if isinstance(geom, r3.InstanceReference):
            definitions = definitions or {str(d.Id): d
                                          for d in self.model.file.InstanceDefinitions}
            definition_objects = definition_objects or {
                str(o.Attributes.Id): o for o in self.model.file.Objects}
            if mesh_cache is None:
                mesh_cache = {}
                for ob in bpy.data.objects:
                    definition = ob.get("narwhal_instance_definition")
                    layer = ob.get("narwhal_instance_layer")
                    if definition is not None and layer is not None and ob.data is not None:
                        mesh_cache[(str(definition), int(layer))] = ob.data
            return self._make_instance_display(
                fobj, name, layer_index, layers, definitions, definition_objects, mesh_cache)
        return self._make_display_object(geom, name, layer_index, fobj=fobj, layers=layers)

    def _make_display_object(self, geom, display_name: str, layer_index: int = 0,
                             fobj=None, layers=None, *, allocations=None):
        """Tessellate `geom` into a Blender object named `display_name`.

        Shared by add_geometry (new geometry) and rebuild_display (after Open). `fobj` (the
        document object, when known) lets a REAL the reference CAD application picture show its image.

        `layers`: the layer TABLE, pre-read by a caller that makes many objects in a
        loop (rebuild_display reads it ONCE, then passes it in). When None (add_geometry, or
        a direct call), it is read here as before -- other callers are unchanged.
        """
        import bpy

        bm = bridge.geometry_to_bmesh(geom)
        frame = bridge.picture_frame(geom)
        if frame is None and fobj is not None:
            frame = self._texture_frame(geom, fobj)
            if frame is not None:
                bridge._apply_picture_uvs(bm, frame)
        ob = (bridge.bmesh_to_object(display_name, bm) if allocations is None else
              bridge.bmesh_to_object(display_name, bm, allocations=allocations))
        if (frame is None and isinstance(geom, (r3.Brep, r3.Extrusion, r3.Surface))
                and hasattr(ob.data, "narwhal_boundary_wires")):
            # Patched Blender: this mesh is only the shaded proxy of analytic CAD geometry.
            # Draw split Brep boundaries, never manifold triangulation edges.
            ob.data.narwhal_boundary_wires = True
        elif isinstance(geom, r3.Mesh):
            # A mesh command's edges ARE its geometry; unlike a CAD solid, show all of them.
            ob.show_all_edges = True
        if frame is not None:
            # a Picture shows its IMAGE (UVs come from the frame, see
            # bridge.geometry_to_bmesh), and "Isocurves and edges are turned off on picture
            # objects in all display modes"  -- the object is flagged so the
            # viewport layer can skip its edges, and Blender's own wire is off.
            ob.data.materials.append(_picture_display_material(frame["image"]))
            ob.show_wire = False
            ob["narwhal_picture"] = True
            return ob

        # color by layer (the reference CAD application colors objects by layer by default)
        if layers is None:  # caller did not pre-read the table
            layers = self.model.layers()
        self._apply_layer_material(ob, layer_index, layers)
        return ob

    def _texture_frame(self, geom, fobj):
        """A picture frame for a REAL the reference CAD application picture: an all-planar brep whose render
        material has a bitmap texture we can find (`model.object_texture_path`). the reference CAD application maps
        a picture's image by the face's SURFACE PARAMETERS (its default mapping), so the
        frame is the first face's underlying surface: origin at (u0, v0), u and v along its
        domain. Returns None for anything else (it then draws in its layer colour)."""
        if not isinstance(geom, r3.Brep) or len(geom.Faces) == 0:
            return None
        finder = getattr(self.model, "object_texture_path", None)
        path = finder(fobj) if callable(finder) else None
        if not path:
            return None
        try:
            srf = geom.Faces[0].UnderlyingSurface()
            du, dv = srf.Domain(0), srf.Domain(1)
            o = srf.PointAt(du.T0, dv.T0)
            pu = srf.PointAt(du.T1, dv.T0)
            pv = srf.PointAt(du.T0, dv.T1)
            # every face planar AND in face 0's plane (a picture, maybe split into
            # pieces) -- a textured box would need a per-face mapping, which this is not
            ex = [pu.X - o.X, pu.Y - o.Y, pu.Z - o.Z]
            ey = [pv.X - o.X, pv.Y - o.Y, pv.Z - o.Z]
            n = [ex[1] * ey[2] - ex[2] * ey[1], ex[2] * ey[0] - ex[0] * ey[2],
                 ex[0] * ey[1] - ex[1] * ey[0]]
            ln = math.sqrt(sum(c * c for c in n))
            if ln <= 1e-12:
                return None
            n = [c / ln for c in n]
            size = max(math.sqrt(sum(c * c for c in ex)), math.sqrt(sum(c * c for c in ey)))
            for i in range(len(geom.Faces)):
                f = geom.Faces[i]
                if not f.IsPlanar():
                    return None
                s = f.UnderlyingSurface()
                a, b = s.Domain(0), s.Domain(1)
                for uu, vv in ((a.T0, b.T0), (a.T1, b.T0), (a.T0, b.T1), (a.T1, b.T1)):
                    p = s.PointAt(uu, vv)
                    if abs((p.X - o.X) * n[0] + (p.Y - o.Y) * n[1]
                           + (p.Z - o.Z) * n[2]) > 1e-6 * max(size, 1.0):
                        return None
        except Exception:  # noqa: BLE001
            return None
        from . import picture
        return picture.make_frame(path, (o.X, o.Y, o.Z),
                                  (pu.X - o.X, pu.Y - o.Y, pu.Z - o.Z),
                                  (pv.X - o.X, pv.Y - o.Y, pv.Z - o.Z))

    # -- rebuild the whole viewport from the document (used by Open)
    def rebuild_display(self):
        """Drop every display object and re-tessellate the document from scratch.

        `Open` swaps in a new File3dm but the viewport still shows the previous
        session's meshes, so without this the user opens a.3dm and sees nothing
        from it. Also restores the name counters, otherwise the next
        `Box` would try to reuse a name already present in the opened file.
        """
        import bpy
        import re

        for ob in list(bpy.data.objects):
            data = ob.data
            bridge.note_display_changed([ob.name], removed=bridge.is_app_drawn(ob))
            bpy.data.objects.remove(ob, do_unlink=True)
            if data is not None and getattr(data, "users", 1) == 0:
                try:
                    bpy.data.meshes.remove(data)
                except (ReferenceError, TypeError):
                    pass

        self.counters = {}
        # read the layer table ONCE for the whole file, not once per object.
        # (A 5,702-object file was rebuilding this table 5,702 times -- ~0.9 s.)
        layers = self.model.layers()
        definitions = {str(d.Id): d for d in self.model.file.InstanceDefinitions}
        definition_objects = {str(o.Attributes.Id): o for o in self.model.file.Objects}
        instance_meshes = {}
        rebuilt = 0
        for fobj in self.model.file.Objects:
            name = fobj.Attributes.Name or ""
            if not name:
                continue
            # Definition objects are library geometry, not objects placed in model space.
            # Their InstanceReferences below draw them at the reference transforms.
            if fobj.Attributes.Mode == r3.ObjectMode.InstanceDefinitionObject:
                continue
            try:
                self._make_file_object_display(
                    fobj, layers, definitions, definition_objects, instance_meshes)
            except Exception as exc:  # noqa: BLE001
                # One unsupported type must not abort the file, but it must not disappear
                # silently either (project the explicit-refusal policy).
                try:
                    from .state import app_state
                    app_state.log(f"Open: skipped {name}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                continue
            self.model.register_blender_object(str(fobj.Attributes.Id), name)
            rebuilt += 1
            # keep next_name past anything already in the file: "Box7" -> Box:7
            m = re.match(r"^(.*?)(\d+)$", name)
            if m:
                base, num = m.group(1), int(m.group(2))
                if num > self.counters.get(base, 0):
                    self.counters[base] = num
        return rebuilt


# ---------------------------------------------------------------------------
# concrete commands
# ---------------------------------------------------------------------------


class HeightAxisMixin:
    """Constrain a HEIGHT step's click to the CPlane normal through the base point.

    Shared by `Cylinder`, `Cone` and `Box`, whose height steps all measure along the CPlane
    normal from a point already picked -- (the command behavior): *"Pick a location
    or type a number to set the height"*, with *"The CPlane +Z direction defines the positive
    direction"*.

    `_height_step_index` is which step that is; the constraint applies only while THAT step is
    the one being answered, because a click answering the radius must still land on the CPlane.
    """

    _height_step_index = 2

    def axis_constraint(self, plane, resolve=None):
        if len(self.args) != self._height_step_index:
            return None                     # not the height step
        base = self._last_point()
        if base is None or plane is None:
            return None
        return (base, tuple(float(c) for c in plane.zaxis))


class _StickyDistanceMixin:
    """The sticky `<value>` of an "Extrusion distance" prompt.

    MEASURED (research_all_13 ExtrudeCrv/ExtrudeCrvTapered/ExtrudeSrf, research_preview_14
    P36-P38): "Extrusion distance <0>" on first use (<1> for ExtrudeCrvTapered); after a
    picked 2.9171 the next run shows <2.9171>, after a typed 3 <3>; Enter takes it."""

    STICKY_LABEL = "Extrusion distance:"
    _sticky_first = 0.0
    _sticky_values = {}

    def _sticky_value(self):
        return _StickyDistanceMixin._sticky_values.get(self.name, self._sticky_first)

    def _at_sticky_step(self):
        return (self.pending_option is None and self.step_index < len(self.steps)
                and self.steps[self.step_index][0] == self.STICKY_LABEL)

    @property
    def prompt(self):
        base = super().prompt
        label = self.STICKY_LABEL[:-1]
        if self._at_sticky_step() and base.startswith(label):
            return f"{label} <{_num(self._sticky_value())}>" + base[len(label):]
        return base

    def _enter_default(self, ctx):
        if self._at_sticky_step() and len(self.args) == self.step_index:
            value = float(self._sticky_value())
            return value if value != 0.0 else None
        return super()._enter_default(ctx)

    def _feed(self, text, ctx):
        index = self.step_index if self._at_sticky_step() else None
        msg, done = super()._feed(text, ctx)
        if (index is not None and len(self.args) > index
                and isinstance(self.args[index], (int, float))
                and not isinstance(self.args[index], bool)
                and str(msg or "").startswith("Created")):
            _StickyDistanceMixin._sticky_values[self.name] = float(self.args[index])
        return msg, done


class _StickySizeMixin:
    """The `<value>` default of a radius / height prompt, sticky per command.

    MEASURED (research_all_13 #021 #023 #035 #165, research_preview_14 P03 P04 P05 P12):
    "Radius <1.000> ( ... ):" -- the default is the value last USED by that command (a
    picked radius of 4.028 shows `<4.028>` on the next Sphere; Escape does not change it),
    and Enter accepts it. Cone's "End of cone <10.000>" and Cylinder's "End of cylinder
    <5.000>" were the first-use height defaults seen. A step only shows its default while
    it is the command's own step (not a Diameter/2Point/Tangent replacement).
    """

    #: {step index: first-use default}. Each command sets its own.
    _sticky_defaults = {}

    def _sticky_store(self):
        cls = type(self)
        if "_sticky_values" not in cls.__dict__:
            cls._sticky_values = dict(cls._sticky_defaults)
        return cls._sticky_values

    def _sticky_on(self):
        idx = self.step_index
        return (self.pending_option is None and idx in self._sticky_defaults
                and idx < len(self.steps) and idx < len(type(self).steps)
                and self.steps[idx] == type(self).steps[idx]
                and not getattr(self, "_points_mode", None)
                and not getattr(self, "_tangent", False))

    @property
    def prompt(self):
        base = super().prompt
        if self._sticky_on():
            label = self.steps[self.step_index][0][:-1]
            if base.startswith(label):
                value = self._sticky_store()[self.step_index]
                return f"{label} <{value:.3f}>" + base[len(label):]
        return base

    def _enter_default(self, ctx):
        if self._sticky_on():
            return float(self._sticky_store()[self.step_index])
        return super()._enter_default(ctx)

    def _feed(self, text, ctx):
        # Each command defines its own `execute`, which comes BEFORE this mixin in the MRO,
        # so the value is recorded around `_feed` (which runs `execute` at the last step).
        msg, done = super()._feed(text, ctx)
        if done and str(msg or "").startswith("Created"):
            store = self._sticky_store()
            for idx in self._sticky_defaults:
                if idx < len(self.args) and isinstance(self.args[idx], (int, float)):
                    value = float(self.args[idx])
                    if idx == getattr(self, "_size_step_index", -1):
                        value = self.radius_from(value, getattr(self, "size_mode", None))
                    store[idx] = abs(value)
        return msg, done


class SizeStepMixin:
    """shared radius-equivalent size behaviour for Circle / Sphere /
    Cylinder / Cone. These four commands all have one step that asks for a SIZE
    (a radius, or one of its equivalents: diameter, circumference, area). The
    size options change what the NUMBER the user types means, and nothing else --
    the geometry is still built from a radius, so this is pure arithmetic on a
    radius (see `Command.radius_from`).

    The mixin provides three hooks:

    * `on_mode` -- for MODE options (Sphere / Cylinder / Cone). Setting the
      mode is the whole action; the command then continues to the size step and
      `execute` interprets the typed value under `size_mode`.
    * `on_option_set` -- for TOGGLE / ENUM / VALUE options (Circle's
      `Radius/Diameter` ENUM and `Circumference` / `Area` VALUE, pinned by
      ). Setting the option stores its value AND sets `size_mode`.
    * `prompt` -- the size step's label changes to the quantity being asked for
      ("Diameter:" instead of "Radius:"), so the prompt never lies about what
      the number means (the explicit-refusal policy).

    `_size_step_index` is the index into `self.steps` of the size step. All four
    commands have it at index 1 (step 0 is the centre), so the mixin defaults
    to 1; a command with a different layout overrides it.
    """

    _size_step_index = 1

    def size_step_done(self):
        """True when the centre (step 0) has already been taken, i.e. there is
        at least one arg. Used to decide whether a VALUE size option can build
        immediately or must be parked until the centre arrives."""
        return len(self.args) >= 1

    def _apply_size_mode(self):
        """update the size step's label in `self.steps` to reflect the
        current `size_mode`, so the prompt (which reads `self.steps`) says
        "Diameter:" / "Circumference:" / "Area:" instead of "Radius:" when the
        user has chosen that mode. the explicit-refusal policy: a prompt still reading "Radius:" while
        it means diameter is a quiet lie.

        Mutates `self.steps` (the instance copy made in `Command.__init__`), not
        the class attribute, so other instances and tests that read the class
        attribute are unaffected.
        """
        kind = self.steps[self._size_step_index][1]
        if self.size_mode in ("diameter", "circumference", "area"):
            label = self.size_mode.capitalize() + ":"
        else:
            #  back to radius -- restore the size step's own
            # label ("Radius:"), not a stale "Diameter:".
            # Choosing Diameter used to leave no way to get back to Radius.
            label = type(self).steps[self._size_step_index][0]
        self.steps[self._size_step_index] = (label, kind)

    def _size_mode_for(self, name):
        """Map an option name to the `size_mode` it sets, or None if `name` is
        not one of the radius-equivalent options (Radius / Diameter /
        Circumference / Area). added "Radius" so a command can be toggled
        BACK to radius. Circle's `Radius/Diameter` ENUM maps to the value the
        user chose ("Radius" -> radius, "Diameter" -> diameter)."""
        low = (name or "").lower()
        if low == "radius":
            return "radius"
        if low == "diameter":
            return "diameter"
        if low == "circumference":
            return "circumference"
        if low == "area":
            return "area"
        if low == "radius/diameter":
            val = (self.opt or {}).get("Radius/Diameter", "Radius")
            return "diameter" if (val or "").lower().startswith("d") else "radius"
        return None

    def _refresh_size_options(self):
        """ while the size step means a diameter, the options
        offer `Radius` (in place of `Diameter`) so the user can toggle back;
        otherwise they offer `Diameter`. This is ONE option that flips, exactly
        as the reference CAD application's `Radius/Diameter` ("Toggles between accepting a radius value
        and a diameter value").

        Only a `Diameter` **MODE** option is swapped (Sphere / Cylinder / Cone);
        every other option is untouched, and a command whose size option is not
        a `Diameter` MODE (Circle's `Radius/Diameter` ENUM) is left exactly as it
        was. A fresh tuple is assigned to the INSTANCE -- the shared class
        attribute is never mutated, so other instances and tests are unaffected.
        MODE options hold no value, so `self.opt` (built from the defaults) stays
        valid and is not recomputed.
        """
        base = tuple(type(self).options)
        if self.size_mode != "diameter":
            self.options = base
            return
        out = []
        for o in base:
            if o.name == "Diameter" and o.kind == opts.MODE:
                out.append(opts.Option("Radius", opts.MODE,
                                       help=self._radius_help(o)))
            else:
                out.append(o)
        self.options = tuple(out)

    @staticmethod
    def _radius_help(diam):
        """Derive the Radius option's help from the Diameter option's, per
        command ("the sphere's equator" / "the base circle"), so we do not
        hardcode one command's wording for another."""
        h = (diam.help or "").strip()
        low = h.lower()
        i = low.find("diameter")
        if i >= 0:
            return h[:i] + "radius" + h[i + len("diameter"):]
        return "Enter the radius."

    def on_mode(self, name, ctx):
        m = self._size_mode_for(name)
        if m is not None:
            self.size_mode = m
            self._apply_size_mode()
            self._refresh_size_options()
            return (f"{self.name}: size mode is now {m} -- type a {m} value "
                    f"at the next prompt.", False)
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        m = self._size_mode_for(option.name)
        if m is None:
            return super().on_option_set(option, value, ctx)
        self.size_mode = m
        if option.kind == opts.VALUE:
            # VALUE option (Circle's Circumference / Area): the value the user
            # typed IS the size. If the centre (step 0) is already taken, build
            # now; otherwise park the value and let `_feed` consume it as soon
            # as the centre arrives (the option can be typed before the centre).
            if self.size_step_done():
                self.args.append(value)
                return self.execute(ctx)
            self._pending_size_value = value
            return (f"{self.name}: {option.name}={value:g} noted -- now pick the "
                    f"centre.", False)
        # ENUM option (Circle's Radius/Diameter): set the mode, let the user
        # type the value at the size step.
        self._apply_size_mode()
        return f"{self.name}: {option.name} set -- the size step now " \
               f"accepts a {m} value.", False


def _curve_near(ctx, point):
    """The document curve nearest `point` (the one a pick on a curve means), or None."""
    from . import nearest
    best = None
    for fobj in ctx.model.file.Objects:
        g = fobj.Geometry
        if not isinstance(g, r3.Curve):
            continue
        try:
            d = nearest.distance_to_curve(g, tuple(point))
        except ValueError:
            continue
        if best is None or d < best[0]:
            best = (d, g)
    return best[1] if best is not None else None


def _solve_on_curve(curve, start_point, pick, condition):
    """Newton on curve parameter t from the point nearest `pick`, to make
    `condition(C(t) - start, C'(t))` zero. Returns C(t) or None."""
    from . import nearest
    t = nearest.closest_point_on_curve(curve, tuple(pick)).t
    dom = curve.Domain
    h = 1e-6 * (dom.T1 - dom.T0)

    def at(u):
        p = curve.PointAt(u)
        return (p.X, p.Y, p.Z)

    def f(u):
        a, b = at(min(u + h, dom.T1)), at(max(u - h, dom.T0))
        d = tuple((a[i] - b[i]) for i in range(3))
        n = math.sqrt(cplane.dot(d, d)) or 1.0
        return condition(_sub3(at(u), start_point), tuple(c / n for c in d))

    for _ in range(60):
        v = f(t)
        dv = (f(min(t + h, dom.T1)) - f(max(t - h, dom.T0))) / (
            min(t + h, dom.T1) - max(t - h, dom.T0))
        if dv == 0:
            break
        step = v / dv
        t = min(max(t - step, dom.T0), dom.T1)
        if abs(step) <= 1e-15 * max(1.0, abs(t)):
            break
    reach = math.dist(at(t), tuple(start_point))
    return at(t) if abs(f(t)) <= 1e-9 * max(1.0, reach) else None


class LineCmd(Command):
    """`Line` -- the command behavior.

    Built: `BothSides` ("Draws the object on both sides of the start point"), `Vertical`,
    `Angled`, `FourPoint` and `Bisector`. `Normal`, `Perpendicular`, `Tangent`,
    `Extension` are declared and say "not implemented yet".

    MEASURED (research_all_13 note 082), all with typed points:

    * prompts `Start of line` / `End of line`; with BothSides the first is `Middle of line`;
    * Angled -- `Start of base line`, `End of base line`, `Pivot angle`, `End of line`:
      the line leaves the base START at the pivot angle (in the CPlane, about its normal)
      and the end pick is PROJECTED onto that direction (base 0->4x, 45 deg in Front,
      end -> (1,0,1));
    * FourPoint -- base start/end, `Start of line`, `End of line`: the line starts at
      point 3 and runs along the base direction for the projection of point 4 (6 long);
    * Bisector -- `Start of bisector line`, `Start of angle to bisect`, `End of angle to
      bisect`, `End of line`: along the bisector of the angle, end projected onto it;
    * a zero-length line re-prompts `End of line` with NO message.

    The help adds "Pick the end of the line or type a length" for Bisector and Vertical,
    so a typed number at these end prompts is a length (OURS for Angled / FourPoint).
    """

    name = "Line"
    description = "Draw a line segment. Enter two points."
    steps = [("Start of line:", POINT), ("End of line:", POINT)]
    _MODE_STEPS = {
        "Vertical": [("Start of line:", POINT), ("End of line:", DISTANCE)],
        "Angled": [("Start of base line:", POINT), ("End of base line:", POINT),
                   ("Pivot angle:", NUMBER), ("End of line:", DISTANCE)],
        "FourPoint": [("Start of base line:", POINT), ("End of base line:", POINT),
                      ("Start of line:", POINT), ("End of line:", DISTANCE)],
        "Bisector": [("Start of bisector line:", POINT), ("Start of angle to bisect:", POINT),
                     ("End of angle to bisect:", POINT), ("End of line:", DISTANCE)],
    }
    options = (
        opts.Option("BothSides", opts.TOGGLE, default=False,
                    help="Draws the object on both sides of the start point, creating "
                         "the object twice as long as you indicate."),
        opts.Option("Normal", opts.MODE,
                    help="Draws a line normal to a location on a surface or a curve."),
        opts.Option("Angled", opts.MODE,
                    help="Draws the line at a specified angle from a reference line."),
        opts.Option("Vertical", opts.MODE,
                    help="Draws the line vertical to the construction plane."),
        opts.Option("FourPoint", opts.MODE,
                    help="Two points establish direction and two points length."),
        opts.Option("Bisector", opts.MODE,
                    help="Draws the line that bisects a specified angle."),
        opts.Option("Perpendicular", opts.MODE,
                    help="Draws the line perpendicular to or from a curve."),
        opts.Option("Tangent", opts.MODE, help="Draws the line tangent from a curve."),
        opts.Option("Extension", opts.MODE, help="Extends a curve with a line."),
    )

    def on_mode(self, name, ctx):
        if name in ("Perpendicular", "Tangent"):
            # MEASURED (research_preview_14 R10 Line Perpendicular / Tangent): "Start of line
            # ( BothSides  Point  PointOnCurve  2Curves )", then "End of line" -- a pick on a
            # curve ends the line at the foot of the perpendicular / the tangent point.
            # OURS: the PointOnCurve and 2Curves routes are not built.
            if self.args:
                return f"Line: {name} is chosen before the start point", False
            self._to_curve = name
            self.steps = [("Start of line:", POINT), ("End of line:", POINT)]
            keep = tuple(o for o in self.options if o.name == "BothSides")
            self.options = keep + (
                opts.Option("Point", opts.MODE, help="Start from a picked point (the default)."),
                opts.Option("PointOnCurve", opts.MODE,
                            help="Start on a curve (not implemented yet)."),
                opts.Option("2Curves", opts.MODE,
                            help="Between two curves (not implemented yet)."))
            return None, False
        if getattr(self, "_to_curve", None) is not None and name == "Point":
            return None, False
        if name in self._MODE_STEPS:
            if self.args:
                return f"Line: {name} is chosen before the start point", False
            self.steps = list(self._MODE_STEPS[name])
            self._line_mode = name
            self._vertical = name == "Vertical"
            self._sync_middle_prompt()
            if name == "Vertical":
                return "Line: Vertical mode -- pick the start, then the end or a length.", False
            return None, False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "BothSides":
            self._sync_middle_prompt()
        return super().on_option_set(option, value, ctx)

    def _sync_middle_prompt(self):
        # MEASURED: with BothSides the plain line's first prompt is "Middle of line".
        if getattr(self, "_line_mode", None) is None and not self.args:
            first = "Middle of line:" if self.opt.get("BothSides") else "Start of line:"
            self.steps[0] = (first, POINT)

    def _normal(self, ctx):
        return tuple(float(c) for c in self._active_cplane(ctx).zaxis)

    def axis_constraint(self, plane, resolve=None):
        # Vertical: a click answering the end is projected onto the CPlane normal through
        # the start ('s elevator constraint), not flattened onto the CPlane
        if not getattr(self, "_vertical", False) or len(self.args) != 1 or plane is None:
            return None
        return (self.args[0], tuple(float(c) for c in plane.zaxis))

    def _direction(self, ctx):
        """(origin, unit direction) of the line the END step measures along, or a refusal
        string. Vertical: the CPlane normal; the others from the inputs so far."""
        mode = getattr(self, "_line_mode", None)
        a = self.args
        if mode == "Vertical":
            return a[0], self._normal(ctx)
        if mode in ("Angled", "FourPoint"):
            base = cplane.normalize(_sub3(a[1], a[0]))
            if base is None:
                return "the base line has no length"
            if mode == "FourPoint":
                return a[2], base
            n = self._normal(ctx)
            t = math.radians(float(a[2]))
            c, s = math.cos(t), math.sin(t)
            k = cplane.dot(n, base)
            cross = (n[1] * base[2] - n[2] * base[1], n[2] * base[0] - n[0] * base[2],
                     n[0] * base[1] - n[1] * base[0])
            d = tuple(base[i] * c + cross[i] * s + n[i] * k * (1.0 - c) for i in range(3))
            return a[0], d
        if mode == "Bisector":
            u = cplane.normalize(_sub3(a[1], a[0]))
            v = cplane.normalize(_sub3(a[2], a[0]))
            if u is None or v is None:
                return "an arm of the angle has no length"
            d = cplane.normalize(tuple(u[i] + v[i] for i in range(3)))
            if d is None:
                return "a straight (180 degree) angle has no single bisector"  # OURS
            return a[0], d
        return None

    def _parse_distance(self, text, ctx):
        """The mode's end: a typed length, or the SIGNED projection of a picked point on the
        mode's direction (a pick below the CPlane draws Vertical downward, as
        `ExtrudeCrv`)."""
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        found = self._direction(ctx)
        if isinstance(found, str):
            raise ValueError(f"Line: {found}")
        origin, d = found
        p = parse_point(stripped, self._active_cplane(ctx), self._last_point())
        return sum((p[i] - origin[i]) * d[i] for i in range(3))

    def _reprompt_end(self):
        # MEASURED: a zero-length line re-prompts "End of line" with no message.
        self.args.pop()
        self.step_index = len(self.args)
        return None, False

    def execute(self, ctx: NarwhalContext):
        mode = getattr(self, "_line_mode", None)
        to_curve = getattr(self, "_to_curve", None)
        if to_curve is not None:
            p1, pick = self.args
            curve = _curve_near(ctx, pick)
            if curve is None:
                return "Line: pick a curve for the end of the line", True
            if to_curve == "Perpendicular":
                cond = lambda v, d: cplane.dot(v, d)            # noqa: E731
            else:
                n = tuple(self._active_cplane(ctx).zaxis)
                cond = lambda v, d: cplane.dot(cplane.cross(v, d), n)   # noqa: E731
            p2 = _solve_on_curve(curve, p1, pick, cond)
            if p2 is None:
                return f"Line: no {to_curve.lower()} point on that curve near the pick", True
        elif mode is None:
            p1, p2 = self.args
        else:
            found = self._direction(ctx)
            if isinstance(found, str):
                return f"Line: {found}", True
            p1, d = found
            length = float(self.args[-1])
            p2 = tuple(p1[i] + d[i] * length for i in range(3))
        if math.dist(p1, p2) <= 0.0:
            return self._reprompt_end()
        if self.opt.get("BothSides"):
            p1 = tuple(2 * p1[i] - p2[i] for i in range(3))
        geom = bridge.make_line(p1, p2)
        _, display_name = ctx.add_geometry(geom, "Line")
        return f"Created {display_name}", True


class PointCmd(Command):
    # the `Point` command -- one point object at a picked location
    # (the command behavior: "Pick a location for the point object."). The help
    # documents NO command-line options for Point, so none are declared -- that
    # is the faithful answer, not an oversight. The point is a location, not a
    # plane-dependent shape, so it is built directly from the resolved world
    # point (already resolved it in the active CPlane).
    name = "Point"
    description = "Draw a single point object at a location."
    # MEASURED (research_all_13 #108): the measured prompt.
    steps = [("Location of point object:", POINT)]

    def execute(self, ctx: NarwhalContext):
        (location,) = self.args
        geom = bridge.make_point(location)
        _, display_name = ctx.add_geometry(geom, "Point")
        return f"Created {display_name}", True



#: Circle / Arc Tangent prompts, in the command UI: "First tangent curve",
#: "Second tangent curve or radius", "Third tangent curve. Press Enter to draw circle
#: (arc) from first two points".
_TANGENT_STEPS = {
    kind: [("First tangent curve:", POINT), ("Second tangent curve or radius:", POINT),
           (f"Third tangent curve. Press Enter to draw {kind.lower()} from first two "
            f"points:", POINT)]
    for kind in ("Circle", "Arc")}


def _tangent_radius_input(cmd, text):
    """At "Second tangent curve or radius", a typed NUMBER is the radius: store it, drop
    the third prompt (a radius and two curves fix the circle) and ask for the second curve.
    Returns the feed result, or None when `text` is not that."""
    if not getattr(cmd, "_tangent", False) or cmd.step_index != 1 \
            or cmd._tan_radius is not None or cmd.pending_option is not None:
        return None
    raw = (text or "").strip()
    if not raw or "," in raw or "<" in raw:
        return None
    try:
        r = float(raw)
    except ValueError:
        return None
    return _set_tangent_radius(cmd, r)


def _set_tangent_radius(cmd, r):
    """Fix the Tangent flow's radius: a radius and two curves fix the circle, so the third
    prompt is dropped and the second asks for a curve only."""
    if not r > 0:
        return f"{cmd.name}: the radius must be positive", False
    cmd._tan_radius = float(r)
    cmd.steps = [cmd.steps[0], ("Second tangent curve:", POINT)]
    return f"{cmd.name}: radius {r:g}", False


def _tangent_solution(cmd, ctx, what):
    """``(TangentCircle, [curve names])`` for a Tangent flow, or a refusal string."""
    from . import tangent_circle

    picks = [p for p in cmd.args if p != ""]
    radius = cmd._tan_radius
    if len(picks) == 2 and radius is None:
        return (f"{what} Tangent: drawing the {what.lower()} from the first two tangent "
                f"points (Enter at the third prompt) is not implemented yet -- type a "
                f"radius at the second prompt, or pick a third tangent curve")
    named = []
    for p in picks:
        nm, geom = _curve_under_pick(ctx, p)
        if nm is None:
            return f"{what} Tangent: {geom}"
        if any(nm == n for n, _g in named):
            return f"{what} Tangent: pick a DIFFERENT curve each time ({nm} twice)"
        named.append((nm, geom))
    try:
        if radius is not None:
            tc = tangent_circle.circle_tangent_to_two(named[0][1], named[1][1], radius,
                                                      picks[0], picks[1])
        else:
            tc = tangent_circle.circle_tangent_to_three_lines([g for _n, g in named], picks)
    except ValueError as e:
        return f"{what} Tangent: {e}"
    return tc, [n for n, _g in named]


class CircleCmd(_StickySizeMixin, SizeStepMixin, Command):
    name = "Circle"
    description = "Draw a circle. Pick the center, then a radius location or type a radius."
    # the reference CAD application: "Pick the center location. Pick a radius location or specify an
    # option." -- so the radius step takes a CLICK as well as a typed number
    # (the command behavior). See commands.DISTANCE.
    # MEASURED (research_all_13 #021): "Center of circle", then "Radius <1.000>".
    steps = [("Center of circle:", POINT), ("Radius:", DISTANCE)]
    _sticky_defaults = {1: 1.0}
    # the top-level options the the reference CAD application 8 help documents for Circle
    # (the command behavior). Sub-options that only appear *after* a top-level
    # option is chosen (Degree/PointCount under Deformable, FromFirstPoint under
    # Tangent, etc.) are NOT declared here -- they belong to a sub-command we do
    # not implement. Declared-but-unimplemented options are answered honestly by
    # the framework's default on_mode ("the X option is not implemented yet")
    # rather than silently doing the default and appearing to work.
    options = (
        opts.Option("Deformable", opts.TOGGLE, default=False,
                    help="Draws a NURBS approximation; the degree and number of "
                         "points you specify decide whether it is deformable."),
        opts.Option("2Point", opts.MODE,
                    help="Draw the circle from the two ends of its diameter."),
        opts.Option("3Point", opts.MODE,
                    help="Draw the circle through three points on the circumference."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draw a circle perpendicular to a curve."),
        opts.Option("FitPoints", opts.MODE,
                    help="Draw a circle by fitting to selected point objects, "
                         "curve/surface control points, and mesh vertices."),
        opts.Option("Tangent", opts.MODE,
                    help="Draw a circle tangent to curves."),
        opts.Option("Vertical", opts.MODE,
                    help="Draw a circle perpendicular to the construction plane."),
        opts.Option("Radius/Diameter", opts.ENUM,
                    choices=("Radius", "Diameter"), default="Radius",
                    help="Toggle between accepting a radius value and a diameter value."),
        opts.Option("Orientation", opts.MODE,
                    help="Specify an orientation not on the construction plane."),
        opts.Option("Circumference", opts.VALUE, value_kind="number",
                    help="Specify the circle's circumference."),
        opts.Option("Area", opts.VALUE, value_kind="number",
                    help="Specify the circle's area."),
    )

    def on_mode(self, name, ctx):
        """`2Point` ("Draws the circle from the two ends of its diameter") and
        `3Point` ("Draws a circle through three points on the circumference") are built;
        the prompts are the reference CAD application's in the command UI: "Start of diameter" / "End of diameter",
        "First point" / "Second point" / "Third point"."""
        if name in ("2Point", "3Point"):
            if self.args:
                return f"Circle: {name} is chosen before the first point", False
            if name == "2Point":
                self.steps = [("Start of diameter:", POINT), ("End of diameter:", POINT)]
            else:
                self.steps = [("First point:", POINT), ("Second point:", POINT),
                              ("Third point:", POINT)]
            self._points_mode = name
            return f"Circle: {name} mode", False
        if name == "Vertical":
            # MEASURED (research_preview_14 R10 Circle Vertical): the circle stands in the
            # vertical plane through the centre that contains the CPlane normal and the
            # direction to the pick; a typed radius STAYS locked (`<r>`) and the pick then only
            # sets that direction; Enter keeps the prompt.
            if self.args:
                return "Circle: Vertical is chosen before the first point", False
            self._vertical_circle = True
            self._locked_radius = None
            return None, False
        if name == "Tangent":
            if self.args:
                return "Circle: Tangent is chosen before the first point", False
            self.steps = list(_TANGENT_STEPS["Circle"])
            self._tangent = True
            self._tan_radius = None
            return "Circle: Tangent mode", False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        """"Second tangent curve or radius" takes a typed RADIUS as well as a pick
        (prompt, the command UI). Read before the option matcher: a bare `2` would
        otherwise prefix-match the `2Point` option (the trap)."""
        hit = _tangent_radius_input(self, text)
        if hit is not None:
            return hit
        raw = (text or "").strip()
        if self.pending_option is None and raw.lower() in ("diameter", "radius"):
            option = next(o for o in self.options if o.name == "Radius/Diameter")
            value = "Diameter" if raw.lower() == "diameter" else "Radius"
            self.opt[option.name] = value
            hook = self.on_option_set(option, value, ctx)
            return hook if hook is not None else (None, False)
        if (getattr(self, "_vertical_circle", False) and self.step_index == 1
                and self.pending_option is None
                and not (raw and opts.match(raw, self.options, self.opt).status != opts.NONE)):
            if not raw:
                return None, False                      # MEASURED: Enter keeps the prompt
            try:
                self._locked_radius = parse_number(raw)
                return None, False                      # MEASURED: a typed radius STAYS
            except ValueError:
                pass
            try:
                pick = parse_point(raw, self._active_cplane(ctx), self.args[0])
            except ValueError as e:
                return str(e), False
            return self._vertical_circle_at(pick, ctx)
        return super()._feed(text, ctx)

    def _vertical_circle_at(self, pick, ctx):
        center = self.args[0]
        n = tuple(self._active_cplane(ctx).zaxis)
        d = _sub3(pick, center)
        d = tuple(d[i] - cplane.dot(d, n) * n[i] for i in range(3))
        u = cplane.normalize(d)
        if u is None:
            return "Circle: pick away from the centre to set the circle's direction", False
        r = (self._locked_radius if self._locked_radius is not None
             else math.dist(center, pick))
        r = self.radius_from(r, self.size_mode)
        if not r > 0:
            return "Radius must be positive", False
        geom = bridge.make_circle(center, r, cplane.CPlane(center, u, n))
        self.args.append(r)
        self.step_index = len(self.steps)
        _, display_name = ctx.add_geometry(geom, "Circle")
        return f"Created {display_name} (vertical, r={_num(r)})", True

    def _enter_default(self, ctx):
        # "Third tangent curve. Press Enter to draw circle from first two points"
        if getattr(self, "_tangent", False) and self.step_index == 2:
            return ""
        return super()._enter_default(ctx)

    def on_option_set(self, option, value, ctx):
        pm = getattr(self, "_points_mode", None)
        if pm and option.kind == opts.VALUE:
            return (f"Circle: {option.name} sets the size of a centre-and-radius circle; "
                    f"in {pm} mode the points fix the size"), False
        return super().on_option_set(option, value, ctx)

    def _two_or_three_point(self, ctx):
        """``(center, radius, frame)`` for 2Point / 3Point, or a refusal string."""
        import types as _t

        if self._points_mode == "2Point":
            a, b = self.args
            if math.dist(a, b) <= 0.0:
                return "Circle: the two ends of the diameter are the same point"
            center = tuple((a[i] + b[i]) / 2 for i in range(3))
            # the circle lies parallel to the active CPlane (two points fix a
            # diameter, not a plane); its seam is put at the first pick when possible
            return center, math.dist(a, b) / 2, self._active_cplane(ctx)
        p1, p2, p3 = self.args
        u = tuple(p2[i] - p1[i] for i in range(3))
        v = tuple(p3[i] - p1[i] for i in range(3))
        n = cplane.cross(u, v)
        nn = cplane.dot(n, n)
        if nn <= 1e-24 * max(cplane.dot(u, u) * cplane.dot(v, v), 1e-300):
            return "Circle: the three points are in a line, so no circle passes through them"
        # circumcentre: p1 + (|u|^2 (v x n) + |v|^2 (n x u)) / (2 |n|^2)
        a = cplane.cross(v, n)
        b = cplane.cross(n, u)
        uu, vv = cplane.dot(u, u), cplane.dot(v, v)
        center = tuple(p1[i] + (uu * a[i] + vv * b[i]) / (2 * nn) for i in range(3))
        radius = math.dist(center, p1)
        x = cplane.normalize(tuple(p1[i] - center[i] for i in range(3)))
        y = cplane.normalize(cplane.cross(cplane.normalize(n), x))
        return center, radius, _t.SimpleNamespace(xaxis=x, yaxis=y)

    def execute(self, ctx: NarwhalContext):
        if getattr(self, "_tangent", False):
            res = _tangent_solution(self, ctx, "Circle")
            if isinstance(res, str):
                return res, True
            tc, names = res
            import types as _t

            x = cplane.normalize(tuple(tc.tangents[0][i] - tc.center[i] for i in range(3)))
            frame = _t.SimpleNamespace(xaxis=x, yaxis=cplane.cross(tc.normal, x))
            geom = bridge.make_circle(tc.center, tc.radius, frame)
            _, display_name = ctx.add_geometry(geom, "Circle")
            return (f"Created {display_name} (r={tc.radius:g}) tangent to "
                    f"{', '.join(names)}"), True
        if getattr(self, "_points_mode", None):
            res = self._two_or_three_point(ctx)
            if isinstance(res, str):
                return res, True
            center, radius, frame = res
            geom = bridge.make_circle(center, radius, frame)
            _, display_name = ctx.add_geometry(geom, "Circle")
            return f"Created {display_name} (r={radius:g})", True
        center, typed = self.args
        if typed <= 0:
            return "Radius must be positive", True
        # the typed value means whatever `size_mode` says -- radius by
        # default, or diameter / circumference / area when the matching option
        # was chosen first. Convert to a radius, then build as before.
        radius = self.radius_from(typed, self.size_mode)
        # build the circle in the ACTIVE viewport's construction plane.
        # the reference CAD application: "when you draw a circle, the circle is drawn on the active
        # construction plane by default" (the command behavior). The
        # centre is already a world point (resolved it); the CPlane supplies
        # the circle's ORIENTATION, which used to be hard-coded to world XY --
        # so a circle drawn on Front came out edge-on (Reported).
        geom = bridge.make_circle(center, radius, self._active_cplane(ctx))
        _, display_name = ctx.add_geometry(geom, "Circle")
        return f"Created {display_name} (r={radius:g})", True


class PolylineCmd(Command):
    name = "Polyline"
    description = "Draw a polyline. Enter points; empty input finishes."
    variable_points = True
    # autoclose -- picking the start point after three picks closes the
    # polyline (base `on_point`; Alt suspends it). (user_interface/
    # keyboard the command behavior, Alt): "Suspends `Polyline`, `Curve`, and `InterpCrv`
    # autoclose." This REPLACES the earlier close-on-start-point hack
    # (hardcoded 1e-9 tolerance, no Alt) with the documented behaviour.
    autoclose = True
    # (the command behavior, "Command-line options"): "Close -- The Close
    # option appears only after three points have been placed. Closes the curve."
    # `Helpers` is the other documented option (dynamic tangent/ortho tracking
    # lines); it is declared so it is visible and reports honestly, since tracking
    # lines are a separate feature we have not built.
    options = (
        opts.Option("Close", opts.MODE,
                    help=("Closes the curve. Appears only after three points "
                          "have been placed.")),
        opts.Option("Helpers", opts.TOGGLE, default=False,
                    help=("Turns on dynamic tangent and ortho tracking lines "
                          "that make it easier to extend arcs with lines.")),
        # the option a user reaches for most while drawing.
        # (the command behavior, "Command-line options"): "Undo -- Reverses the
        # last action." It removes the last point PICKED without cancelling the
        # command; it is NOT the `Undo` COMMAND (nothing is in the document yet,
        # so the undo stack must stay untouched).
        opts.Option("Undo", opts.MODE,
                    help=("Removes the last point picked, without cancelling "
                          "the command.")),
    )

    def variable_prompt(self, n):
        # MEASURED (research_preview_14 P11): "Start of polyline", "Next point of polyline",
        # then from the third point on "Next point of polyline. Press Enter when done".
        if n == 1:
            return "Start of polyline:"
        return ("Next point of polyline:" if n == 2
                else "Next point of polyline. Press Enter when done:")

    @property
    def prompt(self):
        # the reference CAD application only OFFERS Close once it is possible, so the prompt must not show
        # it earlier -- an option you cannot use is worse than one that is absent.
        base = super().prompt
        if self._can_close():
            return base
        return opts.format_prompt(
            self.variable_prompt(len(self.args) + 1),
            tuple(o for o in self.options if o.name != "Close"), self.opt)

    def _can_close(self):
        """the reference CAD application: Close "appears only after three points have been placed"."""
        return len(self.args) >= 3

    def on_mode(self, name, ctx):
        if name == "Undo":
            # pop the last picked point, keep the command running (done
            # stays False). Refuse at ZERO points -- never go This is
            # the in-repo `PointsCmd` precedent: it refuses only at 0, so 1 -> 0
            # is allowed (the "one point left" case is left to the user, as in
            # Points).
            if not self.args:
                return "Polyline: there is no point to undo", False
            # Mutate self.args in place rather than keeping a second list: both
            # ui._in_progress_snap_targets and _can_close read the live
            # self.args, so the snap targets and the three-point Close rule
            # follow the undo for free.
            self.args.pop()
            return f"Polyline: removed the last point ({len(self.args)} left)", False
        if name == "Close":
            if not self._can_close():
                return ("Polyline: Close needs at least three points", False)
            return self._close_and_build(ctx)
        return f"Polyline: the {name} option is not implemented yet", False

    def _close_and_build(self, ctx):
        """Repeat the first point so the curve closes, then build it.

        A PolylineCurve whose last point equals its first reports IsClosed == True, which is what "closes the curve" means for this geometry -- no
        separate closed flag exists to set.
        """
        first = self.args[0]
        if math.dist(self.args[-1], first) > 1e-9:
            self.args.append(first)
        return self.execute(ctx)

    def execute(self, ctx: NarwhalContext):
        if len(self.args) < 2:
            return "Need at least 2 points", True
        geom = bridge.make_polyline(self.args, degree=1)
        _, display_name = ctx.add_geometry(geom, "Polyline")
        closed = ", closed" if getattr(geom, "IsClosed", False) else ""
        return f"Created {display_name} ({len(self.args)} pts{closed})", True


class LinesCmd(Command):
    # the `Lines` command (the command behavior) -- like `Polyline` it
    # is a variable-point command with `Close` and `Undo` MODE options, but it
    # builds ONE SEPARATE LINE OBJECT per consecutive pair instead of a single
    # polyline. N points -> N-1 `bridge.make_line` objects (N if closed). This
    # is the whole point of the command: the segments stay independent so each
    # can be edited, deleted or moved on its own.
    #
    # Documented command behavior:
    #   steps:  "Pick the start of the first line segment. Pick the ends of the
    #            segments, and press Enter."
    #   Close:  MODE -- "Ends the line segment string at the beginning of the
    #            series"  (the last segment connects back to the first point).
    #   Undo:   MODE -- "Reverses the last action" (removes the last picked
    #            point; nothing is in the document yet, so the undo stack must
    #            stay untouched -- same rule as Polyline/Points).
    # `Lines` is NOT one of the three auto-close commands, so `autoclose`
    # stays at its base default of False.
    name = "Lines"
    description = "Draw separate line objects. Enter points; empty input finishes."
    variable_points = True

    def variable_prompt(self, n):
        # MEASURED (research_all_13 #083).
        if n == 1:
            return "Start of line:"
        return "End of line:" if n == 2 else "End of line. Press Enter when done:"
    options = (
        # "Close -- Ends the line segment string at the beginning of
        # the series." the reference CAD application only offers Close once it is meaningful, so the
        # prompt hides it until three points are placed (same as Polyline).
        opts.Option("Close", opts.MODE,
                    help=("Ends the line segment string at the beginning "
                          "of the series.")),
        # "Undo -- Reverses the last action." Removes the last point
        # PICKED without cancelling the command; it is NOT the `Undo` command.
        opts.Option("Undo", opts.MODE,
                    help=("Removes the last point picked, without cancelling "
                          "the command.")),
    )

    @property
    def prompt(self):
        # the reference CAD application only OFFERS Close once it is possible, so the prompt must not show
        # it earlier -- an option you cannot use is worse than one that is absent.
        base = super().prompt
        if self._can_close():
            return base
        return opts.format_prompt(
            self.variable_prompt(len(self.args) + 1),
            tuple(o for o in self.options if o.name != "Close"), self.opt)

    def _can_close(self):
        """Close is only meaningful once three points exist (a triangle can
        close on itself). Mirrors Polyline's three-point rule."""
        return len(self.args) >= 3

    def on_mode(self, name, ctx):
        if name == "Undo":
            # Pop the last picked point, keep the command running (done stays
            # False). Refuse at ZERO points -- never go Same rule as
            # Polyline/Points: 1 -> 0 is allowed, 0 -> -1 is refused.
            if not self.args:
                return "Lines: there is no point to undo", False
            # Mutate self.args in place so _can_close and any snap-target
            # readers see the undo for free.
            self.args.pop()
            return f"Lines: removed the last point ({len(self.args)} left)", False
        if name == "Close":
            if not self._can_close():
                return ("Lines: Close needs at least three points", False)
            # Append the first point so the final segment connects back to it,
            # then build. If the user already picked the start point as the last
            # one, do not duplicate it (the closing segment would be zero-length).
            first = self.args[0]
            if math.dist(self.args[-1], first) > 1e-9:
                self.args.append(first)
            return self.execute(ctx)
        return f"Lines: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        # N points -> N-1 line objects. Each is its OWN object (not a polyline),
        # so they can be selected, moved or deleted independently.
        if len(self.args) < 2:
            return "Lines: need at least 2 points", True
        ctx.note_change(self.name)
        count = 0
        for i in range(len(self.args) - 1):
            geom = bridge.make_line(self.args[i], self.args[i + 1])
            ctx.add_geometry(geom, "Line")
            count += 1
        closed = ", closed" if len(self.args) >= 3 and math.dist(
            self.args[-1], self.args[0]) < 1e-9 else ""
        return f"Created {count} line object(s){closed}", True


class PointsCmd(Command):
    # the `Points` command -- multiple point objects, finished on an empty
    # Enter (the command behavior, "Points" section: "Pick locations for the point
    # objects, and press Enter."). Same single-vertex geometry as `Point` (the
    # help page documents both); the only difference is the variable-point flow.
    name = "Points"
    description = "Draw multiple point objects; an empty Enter finishes."
    variable_points = True

    def variable_prompt(self, n):
        # MEASURED (research_all_13 #109).
        return ("Location of point object:" if n == 1
                else "Location of point object. Press Enter when done:")
    # The one documented command-line option (the command behavior, "Points"
    # section): "Undo -- Removes points in reverse order in the current command
    # session." It is a MODE -- it acts on the points collected so far rather
    # than changing what gets built.
    options = (
        opts.Option("Undo", opts.MODE,
                    help=("Removes points in reverse order in the current "
                          "command session.")),
    )

    def on_mode(self, name, ctx):
        if name == "Undo":
            if not self.args:
                return "Points: there is no point to undo", False
            self.args.pop()
            return f"Points: removed the last point ({len(self.args)} left)", False
        return f"Points: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        if not self.args:
            return "Points: pick at least one location first", True
        for location in self.args:
            geom = bridge.make_point(location)
            ctx.add_geometry(geom, "Point")
        n = len(self.args)
        return f"Created {n} point object{'s' if n != 1 else ''}", True


def _curve_point_prompt(n):
    """MEASURED (research_preview_14 P17 Curve, P18 InterpCrv): "Start of curve", "Next
    point", then from the third point on "Next point. Press Enter when done"."""
    if n == 1:
        return "Start of curve:"
    return "Next point:" if n == 2 else "Next point. Press Enter when done:"


def _close_gated_prompt(cmd, base):
    """`Close` is offered only once three points are placed (MEASURED P17/P18)."""
    if len(cmd.args) >= 3 or cmd.pending_option is not None:
        return base
    return opts.format_prompt(cmd.variable_prompt(len(cmd.args) + 1),
                              tuple(o for o in cmd.options if o.name != "Close"), cmd.opt)


def _close_variable_curve(cmd, ctx):
    """`Close` at three or more points: the same closed build as autoclose."""
    if len(cmd.args) < 3:
        return f"{cmd.name}: Close needs at least three points", False
    cmd.args.append(cmd.args[0])
    cmd._autoclosed = True
    return cmd.execute(ctx)


class CurveCmd(Command):
    name = "Curve"
    description = "Draw a control-point curve (Degree 2 or 3). Empty input finishes."
    variable_points = True
    # autoclose -- picking the start point after three picks closes the
    # curve (base `on_point`; Alt suspends it). (user_interface/keyboard
    # the command behavior, Alt): "Suspends `Polyline`, `Curve`, and `InterpCrv` autoclose."
    autoclose = True
    # the option a user reaches for most while drawing.
    # (the command behavior, "Command-line options"): "Undo -- Reverses the last
    # action." It removes the last point PICKED without cancelling the command;
    # it is NOT the `Undo` COMMAND (nothing is in the document yet, so the undo
    # stack must stay untouched).
    options = (
        opts.Option("Degree", opts.ENUM, default="3", choices=("2", "3"),
                    help="Requested NURBS degree; 2 and 3 are supported. Fewer points reduce the degree."),
        opts.Option("Close", opts.MODE,
                    help="Closes the curve smoothly. Appears only after three points."),
        opts.Option("Undo", opts.MODE,
                    help=("Removes the last point picked, without cancelling "
                          "the command.")),
    )

    def variable_prompt(self, n):
        return _curve_point_prompt(n)

    @property
    def prompt(self):
        return _close_gated_prompt(self, super().prompt)

    def begin(self, ctx):
        degree = getattr(ctx, "_curve_degree", "3")
        self.opt["Degree"] = degree if degree in ("2", "3") else "3"
        return super().begin(ctx)

    def _recorded(self, ctx, work):
        self._published_degree = None
        result = super()._recorded(ctx, work)
        # MEASURED: degree persists after commit. OURS: keep the previous value
        # after cancellation/failure until the live cancellation case is measured.
        if self._published_degree is not None and result[1]:
            ctx._curve_degree = self._published_degree
        self._published_degree = None
        return result

    def on_mode(self, name, ctx):
        if name == "Undo":
            # pop the last picked point, keep the command running (done
            # stays False). Refuse at ZERO points -- never go Mirrors
            # the in-repo `PointsCmd` precedent (refuse only at 0, so 1 -> 0 is
            # allowed).
            if not self.args:
                return "Curve: there is no point to undo", False
            # Mutate self.args in place rather than keeping a second list: the
            # live self.args is what ui._in_progress_snap_targets reads, so the
            # in-progress snap targets follow the undo for free.
            self.args.pop()
            return f"Curve: removed the last point ({len(self.args)} left)", False
        if name == "Close":
            return _close_variable_curve(self, ctx)
        return f"Curve: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        if len(self.args) < 2:
            return "Need at least 2 points", True
        if self._autoclosed:
            # this finish is an AUTOclose -- the start point was just
            # picked again, so args ends on a repeat of args[0]. Build the
            # periodic curve (C1 quadratic or C2 cubic seam) from the
            # UNIQUE points, instead of
            # a clamped curve through the repeated seam point, which leaves
            # a visible C0 corner at the seam.
            # `PolylineCmd` must NOT take this path: its corners are real.
            try:
                geom = bridge.make_polyline(self.args[:-1], degree=int(self.opt.get("Degree", "3")), closed=True)
            except ValueError as e:
                # refuses a closed curve below three points by name;
                # surface ITS message rather than inventing a second answer.
                return str(e), True
        else:
            geom = bridge.make_polyline(self.args, degree=int(self.opt.get("Degree", "3")))
        requested = int(self.opt.get("Degree", "3"))
        expected = min(requested, len(self.args) - 1)
        if geom is None or not geom.IsValid or geom.Degree != expected:
            return "Curve: requested NURBS degree could not be constructed", True
        _, display_name = ctx.add_geometry(geom, "Curve")
        self._published_degree = str(requested)
        return f"Created {display_name}", True


class HandleCurveCmd(Command):
    """Draw chained illustration-style cubic Bézier handles as one C1 NURBS."""
    name="HandleCurve";description="Draw a chained Bézier handle curve.";variable_points=True
    options=(opts.Option("AutoClose",opts.TOGGLE,default=False,help="Close near the first point."),
             opts.Option("PersistentClose",opts.MODE,help="Remain closed while adding points."),
             opts.Option("Close",opts.MODE,help="Close the curve smoothly."),
             opts.Option("Sharp",opts.MODE,help="Close with a kink."),
             opts.Option("Undo",opts.MODE,help="Reverse the last pick."))
    @property
    def prompt(self):
        # MEASURED (research_preview_14 P39): "Curve point", "Handle location", "Next curve point".
        base=("Curve point:" if not self.args else
              "Handle location:" if len(self.args)%2 else "Next curve point:")
        return opts.format_prompt(base,self.options,self.opt)
    def _feed(self,text,ctx):
        # OURS (#42) AutoClose: a new CURVE POINT within the document tolerance of the
        # first one closes the curve exactly as Close does; that pick is not kept as an
        # anchor, so the seam reuses the first anchor's handle and stays C1.
        raw=(text or "").strip()
        if (self.opt.get("AutoClose") and raw and self.pending_option is None
                and len(self.args)>=4 and len(self.args)%2==0
                and opts.match(raw,self.options,self.opt).status==opts.NONE):
            try:pick=parse_point(raw,self._active_cplane(ctx),self._last_point())
            except ValueError:pick=None
            if pick is not None and math.dist(pick,self.args[0])<=_document_tolerance(ctx):
                self._close=True;return self.execute(ctx)
        return super()._feed(text,ctx)
    def on_mode(self,name,ctx):
        if name=="Undo":
            if self.args:self.args.pop();return "HandleCurve: reversed last pick",False
            return "HandleCurve: nothing to undo",False
        if name=="Close":self._close=True;return self.execute(ctx)
        if name in ("PersistentClose","Sharp"):return f"HandleCurve: {name} is not implemented yet",False
        return super().on_mode(name,ctx)
    def execute(self,ctx):
        try:curve=handlecurve.make_handle_curve(self.args,getattr(self,"_close",False))
        except ValueError as exc:return f"HandleCurve: {exc}",True
        _,display=ctx.add_geometry(curve,"HandleCurve");return f"Created {display} (C1 cubic NURBS)",True


class InterpCrvCmd(Command):
    # `InterpCrv` -- draw a curve THROUGH a set of points (an interpolation
    # curve). the reference CAD application's `Curve` and `InterpCrv` are the two point-through commands;
    # we already had `Curve`, so `InterpCrv` is the named one the autoclose rule
    # also covers.
    #
    # (user_interface/keyboard the command behavior, the Alt key):
    #   "Suspends `Polyline`, `Curve`, and `InterpCrv` autoclose."
    # The Alt-key help NAMES `InterpCrv` as a command that HAS autoclose, so this
    # command exists and closes on the start point. Open or closed, the curve is
    # an exact cubic interpolation THROUGH the picks (`bridge.make_interpolated_curve`),
    # where `Curve` treats them as control points.
    name = "InterpCrv"
    description = "Draw a curve through points. Empty input finishes."
    variable_points = True
    # autoclose -- picking the start point after three picks closes the
    # curve (base `on_point`; Alt suspends it). (user_interface/keyboard
    # the command behavior, Alt): "Suspends `Polyline`, `Curve`, and `InterpCrv` autoclose."
    autoclose = True
    # style undo (same in-command-session semantics as `Curve`): the last
    # point picked is removed without cancelling the command.
    options = (
        opts.Option("Close", opts.MODE,
                    help="Closes the curve. Appears only after three points."),
        opts.Option("Undo", opts.MODE,
                    help=("Removes the last point picked, without cancelling "
                          "the command.")),
    )

    def variable_prompt(self, n):
        return _curve_point_prompt(n)

    @property
    def prompt(self):
        return _close_gated_prompt(self, super().prompt)

    def on_mode(self, name, ctx):
        if name == "Undo":
            if not self.args:
                return "InterpCrv: there is no point to undo", False
            self.args.pop()
            return (f"InterpCrv: removed the last point ({len(self.args)} left)",
                    False)
        if name == "Close":
            return _close_variable_curve(self, ctx)
        return f"InterpCrv: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        if len(self.args) < 2:
            return "Need at least 2 points", True
        if self._autoclosed:
            # Close / autoclose: the closed periodic cubic THROUGH every unique pick
            # (the repeated start point is dropped), C2 across the seam.
            try:
                geom = bridge.make_interpolated_curve(self.args[:-1], degree=3, closed=True)
            except ValueError as e:
                return str(e), True
            _, display_name = ctx.add_geometry(geom, "InterpCrv")
            return f"Created {display_name}", True
        # TRUE INTERPOLATION. (the command behavior): InterpCrv "draws a
        # curve THROUGH selected locations in space... Curve matches input." This used to
        # call `make_polyline`, i.e. CreateControlPointCurve, which treats the picks as
        # CONTROL points -- a smooth curve, but the wrong one: it missed the
        # picked points by up to 1.5865 units on a 6-point sample.
        try:
            geom = bridge.make_interpolated_curve(self.args, degree=3)
        except ValueError as e:
            return str(e), True
        _, display_name = ctx.add_geometry(geom, "InterpCrv")
        return f"Created {display_name}", True


class _LengthWidthMixin:
    """A typed LENGTH at the other-corner prompt, then "Width. Press Enter to use length:".

    MEASURED (research_preview_14 P01 Box, P53 Plane): typing a number at "Other corner
    ... or length" fixes the length along the CPlane x axis and asks for the width; the
    width follows the cursor's offset along y (the cursor rides the far side), and Enter
    there uses the length. The same route as `RectangleCmd._parse_other_corner`.
    Classes using it define OTHER_PROMPT and WIDTH_PROMPT, with OTHER_PROMPT a DISTANCE step.
    """

    def _current_label(self):
        return self.steps[self.step_index][0] if self.step_index < len(self.steps) else None

    def _typed_length(self):
        """The SIGNED length typed at the other-corner prompt, or None (a picked corner)."""
        if len(self.args) < 2:
            return None
        other = self.args[1]
        return None if isinstance(other, tuple) else float(other)

    def _cursor_sign(self, ctx, axis):
        return RectangleCmd._cursor_sign(self, ctx, axis)    # toward the live cursor

    def _parse_distance(self, text, ctx):
        label = self._current_label()
        if label == self.OTHER_PROMPT:
            stripped = (text or "").strip()
            try:
                value = parse_number(stripped)
            except ValueError:
                return parse_point(stripped, self._active_cplane(ctx), self._last_point())
            if abs(value) < 1e-12:
                raise ValueError(f"{self.name}: the length must not be zero")
            self.steps.insert(self.step_index + 1, (self.WIDTH_PROMPT, DISTANCE))
            return float(value * self._cursor_sign(ctx, self._active_cplane(ctx).xaxis))
        if label == self.WIDTH_PROMPT:
            p = self._active_cplane(ctx)
            stripped = (text or "").strip()
            try:
                value = parse_number(stripped) * self._cursor_sign(ctx, p.yaxis)
            except ValueError:
                P = parse_point(stripped, p, self._last_point())
                value = sum((P[k] - self.args[0][k]) * float(p.yaxis[k]) for k in range(3))
            if abs(value) < 1e-12:
                raise ValueError(f"{self.name}: the width must not be zero")
            return value
        return super()._parse_distance(text, ctx)

    def _width_default(self, ctx):
        """Enter at the Width step: the length (a square), toward the cursor's side."""
        if self._current_label() == self.WIDTH_PROMPT and self._typed_length() is not None:
            plane = self._active_cplane(ctx)
            return abs(self._typed_length()) * self._cursor_sign(ctx, plane.yaxis)
        return None

    def axis_constraint(self, plane, resolve=None):
        length = self._typed_length()
        if self._current_label() == self.WIDTH_PROMPT and length is not None and plane:
            c1 = self.args[0]
            origin = tuple(c1[k] + length * float(plane.xaxis[k]) for k in range(3))
            return origin, tuple(float(c) for c in plane.yaxis)
        return super().axis_constraint(plane, resolve)

    def _corner2(self, plane):
        length = self._typed_length()
        if length is None:
            return self.args[1]
        c1, width = self.args[0], float(self.args[2])
        return tuple(c1[k] + length * float(plane.xaxis[k]) + width * float(plane.yaxis[k])
                     for k in range(3))


class BoxCmd(_LengthWidthMixin, HeightAxisMixin, Command):
    """Draw a box whose interactive height follows the construction-plane normal.

    The third step constrains the cursor through the second base corner, allowing the
    height and preview to work correctly in perspective views.
    """
    name = "Box"
    description = "Draw a box. Two base corners then height."
    # the height step takes a picked location as well as a typed number.
    #   the command behavior: "Pick the height." (and "Press Enter to use the width
    #   value for the height" -- the typed-value path).
    # the height now runs along the active CPlane's NORMAL and the base is
    # in that plane, so a box drawn on Front stands up correctly. The
    # note that used to live here ("make_box builds the height straight up
    # world-Z, so a picked height is most meaningful on the Top plane") described
    # the bug the user hit and no longer applies.
    # MEASURED (research_all_13 #013): the prompts, and Enter at the height prompt builds
    # the box with the height equal to its WIDTH (corners (0,0)-(3,5) gave height 5).
    # DERIVED: "width" is the extent along the CPlane y axis (the help's length/width
    # naming, consistent with that measurement).
    # MEASURED (research_preview_14 P01): a number typed at "Other corner of base or
    # length" is the LENGTH and Box asks "Width. Press Enter to use length:" before the
    # height -- the Rectangle route (`RectangleCmd._parse_other_corner`), reused here.
    OTHER_PROMPT = "Other corner of base or length:"
    WIDTH_PROMPT = "Width. Press Enter to use length:"
    HEIGHT_PROMPT = "Height. Press Enter to use width:"
    steps = [("First corner of base:", POINT), (OTHER_PROMPT, DISTANCE),
             (HEIGHT_PROMPT, DISTANCE)]
    @property
    def _height_step_index(self):
        return len(self.steps) - 1

    def _enter_default(self, ctx):
        plane = self._active_cplane(ctx)
        label = self._current_label()
        width = self._width_default(ctx)
        if width is not None:
            return width
        if label != self.HEIGHT_PROMPT or len(self.args) != self.step_index:
            return None
        y = tuple(float(c) for c in plane.yaxis)
        c2 = self._corner2(plane)
        width = abs(sum((c2[i] - self.args[0][i]) * y[i] for i in range(3)))
        return width if width > 0 else None

    def execute(self, ctx: NarwhalContext):
        c1, c2, h = self.args[0], self._corner2(self._active_cplane(ctx)), self.args[-1]
        if h <= 0:
            return "Height must be positive", True
        geom = bridge.make_box(c1, c2, h, self._active_cplane(ctx))
        _, display_name = ctx.add_geometry(geom, "Box")
        return f"Created {display_name}", True


class SphereCmd(_StickySizeMixin, SizeStepMixin, Command):
    name = "Sphere"
    description = "Draw a sphere. Center then radius."
    # the radius step takes a picked location as well as a typed number.
    # the command behavior: "Radius -- Creates a sphere by picking the center
    # point and a radius point." A picked radius location is from the
    # centre (the last picked point), exactly as Circle's radius is.
    # MEASURED (research_all_13 #165): the centre prompt is "Center of sphere".
    steps = [("Center of sphere:", POINT), ("Radius:", DISTANCE)]
    _sticky_defaults = {1: 1.0}
    # the radius-equivalent options. (the command behavior):
    # Sphere's `Diameter`, `Circumference`, `Area` are listed as top-level
    # command-line options and all LINK to the shared page
    # the command behavior, which states
    # `Area` = "Enter the area of the circle." So Sphere's Area is the
    # EQUATOR CIRCLE's area (r = sqrt(A/pi)), not the surface area
    # (r = sqrt(A/(4*pi))). Flagged the per-command page is silent,
    # the shared page says "the circle", and that is the only reading
    # consistent across Circle/Sphere/Cylinder/Cone.
    # Declared as MODE options (the pattern the implementation specifies); the
    # `SizeStepMixin` hooks set `size_mode` and `execute` interprets the
    # typed value under that mode.
    options = (
        opts.Option("Diameter", opts.MODE,
                    help="Enter the diameter of the sphere's equator."),
        opts.Option("Circumference", opts.MODE,
                    help="Enter the circumference of the sphere's equator."),
        opts.Option("Area", opts.MODE,
                    help="Enter the area of the sphere's equator circle "
                          "(r = sqrt(A / pi))."),
        # the other documented top-level options, declared so that typing
        # them reports "not implemented yet" (the explicit-refusal policy: an unimplemented thing must
        # never look implemented) rather than a confusing parse error. This
        # copies CircleCmd's pattern. None are implemented here -- they are on
        # the implementation's Do-NOT list (geometry / picking we do not have).
        opts.Option("Orientation", opts.MODE,
                    help="Pick the perpendicular direction of the sphere."),
        opts.Option("ProjectOsnap", opts.MODE,
                    help="Projects the snapped point to the elevation of the center."),
        opts.Option("2Point", opts.MODE,
                    help="Creates a sphere from two opposite points on the base circle."),
        opts.Option("3Point", opts.MODE,
                    help="Creates a sphere from three points on the base circle."),
        opts.Option("Tangent", opts.MODE,
                    help="Creates a sphere with the base circle tangent to curves."),
        opts.Option("AroundCurve", opts.MODE,
                    help=("Creates a sphere from its center on a curve and a "
                          "point on the base circle perpendicular to the curve.")),
        opts.Option("FitPoints", opts.MODE,
                    help=("Creates a sphere by fitting to selected point "
                          "objects, curve/surface control points, and mesh vertices.")),
    )

    def execute(self, ctx: NarwhalContext):
        center, typed = self.args
        if typed <= 0:
            return "Radius must be positive", True
        # the typed value means whatever `size_mode` says -- radius by
        # default, or diameter / circumference / area when the matching option
        # was chosen first.
        radius = self.radius_from(typed, self.size_mode)
        geom = bridge.make_sphere(center, radius)
        _, display_name = ctx.add_geometry(geom, "Sphere")
        return f"Created {display_name} (r={radius:g})", True


class CylinderCmd(_StickySizeMixin, HeightAxisMixin, SizeStepMixin, Command):
    name = "Cylinder"
    description = "Draw a cylinder. Center then radius then height."
    # the radius and the height both take a picked location as well as a
    # typed number.
    #   the command behavior: "Radius -- Draws the base circle by picking the
    #     center point and a radius point."
    #   the command behavior: "Pick a location or type a number to set the
    #     height." (the base circle is the reference for the height)
    # MEASURED (research_all_13 #035, research_preview_14 P03): "Base of cylinder",
    # "Radius <r>", "End of cylinder <5.000>".
    steps = [("Base of cylinder:", POINT), ("Radius:", DISTANCE), ("End of cylinder:", DISTANCE)]
    _sticky_defaults = {1: 1.0, 2: 5.0}
    # the radius-equivalent options. (the command behavior):
    # `Diameter`, `Circumference`, `Area` are listed as top-level command-line
    # options and all LINK to the shared page
    # the command behavior, which states
    # `Area` = "Enter the area of the circle." So Cylinder's Area is the BASE
    # CIRCLE's area (r = sqrt(A/pi)), not the cylinder's total surface area.
    # Declared as MODE options; the `SizeStepMixin` hooks set `size_mode` and
    # `execute` interprets the typed value under that mode.
    options = (
        opts.Option("Diameter", opts.MODE,
                    help="Enter the diameter of the base circle."),
        opts.Option("Circumference", opts.MODE,
                    help="Enter the circumference of the base circle."),
        opts.Option("Area", opts.MODE,
                    help="Enter the area of the base circle (r = sqrt(A / pi))."),
        # the other documented top-level options, declared so that typing
        # them reports "not implemented yet" (the explicit-refusal policy) rather than a confusing
        # parse error. This copies CircleCmd's pattern. None are implemented
        # here -- they are on the implementation's Do-NOT list (geometry / picking we do
        # not have).
        opts.Option("Vertical", opts.MODE,
                    help="Creates a cylinder perpendicular to the construction plane."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws the base circle perpendicular to the picked point on a curve."),
        opts.Option("ProjectOsnap", opts.MODE,
                    help="Projects the snapped point to the elevation of the center."),
        opts.Option("2Point", opts.MODE,
                    help="Draws the base circle from two opposite points."),
        opts.Option("3Point", opts.MODE,
                    help="Draws the base circle through three points."),
        opts.Option("Tangent", opts.MODE,
                    help="Draws the base circle tangent to one, two, or three curves."),
        opts.Option("FitPoints", opts.MODE,
                    help=("Draws the base circle by fitting to selected points, "
                          "control points, or mesh vertices.")),
    )

    def execute(self, ctx: NarwhalContext):
        center, typed, height = self.args
        if typed <= 0 or height <= 0:
            return "Radius and height must be positive", True
        # the typed radius value means whatever `size_mode` says.
        radius = self.radius_from(typed, self.size_mode)
        # build in the ACTIVE CPlane -- base disc in the plane, height along
        # its normal. The centre is already a world point; the CPlane
        # supplies the orientation, which used to be hard-coded to world XY and
        # the base to z=0 (the picked centre's z was discarded).
        geom = bridge.make_cylinder(center, radius, height,
                                    self._active_cplane(ctx))
        _, display_name = ctx.add_geometry(geom, "Cylinder")
        return f"Created {display_name}", True


class ConeCmd(_StickySizeMixin, HeightAxisMixin, SizeStepMixin, Command):
    name = "Cone"
    description = "Draw a cone. Center then base radius then height."
    # the base radius and the height both take a picked location as well
    # as a typed number.
    #   the command behavior: "Radius -- Draws the base circle by picking the
    #     center point and a radius point."
    #   the command behavior: "Pick or type a number to set the height."
    # MEASURED (research_all_13 #023, research_preview_14 P04): "Base of cone", "Radius
    # <1.000>", "End of cone <10.000>"; a typed radius advances, a typed height completes.
    steps = [("Base of cone:", POINT), ("Radius:", DISTANCE), ("End of cone:", DISTANCE)]
    _sticky_defaults = {1: 1.0, 2: 10.0}
    # the radius-equivalent options. (the command behavior):
    # `Diameter`, `Circumference`, `Area` are listed as top-level command-line
    # options and all LINK to the shared page
    # the command behavior, which states
    # `Area` = "Enter the area of the circle." So Cone's Area is the BASE
    # CIRCLE's area (r = sqrt(A/pi)), not the cone's total surface area.
    # Declared as MODE options; the `SizeStepMixin` hooks set `size_mode` and
    # `execute` interprets the typed value under that mode.
    options = (
        opts.Option("Diameter", opts.MODE,
                    help="Enter the diameter of the base circle."),
        opts.Option("Circumference", opts.MODE,
                    help="Enter the circumference of the base circle."),
        opts.Option("Area", opts.MODE,
                    help="Enter the area of the base circle (r = sqrt(A / pi))."),
        # the other documented top-level options, declared so that typing
        # them reports "not implemented yet" (the explicit-refusal policy) rather than a confusing
        # parse error. This copies CircleCmd's pattern. None are implemented
        # here -- they are on the implementation's Do-NOT list (geometry / picking we do
        # not have).
        opts.Option("Vertical", opts.MODE,
                    help="Creates a cone perpendicular to the construction plane."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws the base circle perpendicular to the picked point on a curve."),
        opts.Option("ProjectOsnap", opts.MODE,
                    help="Projects the snapped point to the elevation of the center."),
        opts.Option("2Point", opts.MODE,
                    help="Draws the base circle from two opposite points."),
        opts.Option("3Point", opts.MODE,
                    help="Draws the base circle through three points."),
        opts.Option("Tangent", opts.MODE,
                    help="Draws the base circle tangent to one, two, or three curves."),
        opts.Option("FitPoints", opts.MODE,
                    help=("Draws the base circle by fitting to selected points, "
                          "control points, or mesh vertices.")),
    )

    def execute(self, ctx: NarwhalContext):
        center, typed, h = self.args
        if typed <= 0 or h <= 0:
            return "Radius and height must be positive", True
        # the typed base-radius value means whatever `size_mode` says.
        r0 = self.radius_from(typed, self.size_mode)
        # build in the ACTIVE CPlane -- base disc in the plane, height (to
        # the apex) along its normal. The old code translated in x/y only, so a
        # cone drawn on Front/Right was in the wrong place and the wrong way.
        geom = bridge.make_cone(center, r0, h, self._active_cplane(ctx))
        _, display_name = ctx.add_geometry(geom, "Cone")
        return f"Created {display_name}", True


class _VerticalRectMixin:
    """`Rectangle Vertical` / `Plane Vertical` -- MEASURED (research_preview_14 R10): "Start of
    edge", "End of edge", "Height. Press Enter to use width" (a typed height locks it), then
    "Choose rectangle" -- the click picks the side along the CPlane normal (above or below
    the edge). Exact: `bridge.make_rectangle_3point` with the edge and the height."""

    VERT_STEPS = [("Start of edge:", POINT), ("End of edge:", POINT),
                  ("Height. Press Enter to use width:", DISTANCE), ("Choose rectangle:", POINT)]

    def _start_vertical(self):
        if self.args:
            return f"{self.name}: Vertical is chosen before the first point", False
        self._vertical_rect = True
        self.steps = list(self.VERT_STEPS)
        return None, False

    def _vertical_enter(self, ctx):
        if getattr(self, "_vertical_rect", False) and self.step_index == 2 and len(self.args) == 2:
            return math.dist(self.args[0], self.args[1])          # Enter uses the width
        return None

    def _vertical_boundary(self, ctx):
        a, b, h, pick = self.args[:4]
        n = tuple(self._active_cplane(ctx).zaxis)
        sign = -1.0 if cplane.dot(_sub3(pick, a), n) < 0 else 1.0
        h = abs(float(h))
        if math.dist(a, b) <= 1e-12 or h <= 1e-12:
            return f"{self.name}: the edge and the height must not be zero"
        side = tuple(a[i] + sign * h * n[i] for i in range(3))
        e = _sub3(b, a)
        e = cplane.normalize(tuple(e[i] - cplane.dot(e, n) * n[i] for i in range(3)))
        if e is None:
            return f"{self.name}: the edge must not run along the construction plane normal"
        # the vertical plane through the edge: x along the edge, y along the CPlane normal
        return bridge.make_rectangle_3point(a, b, side, cplane.CPlane(a, e, n))


class RectangleCmd(_VerticalRectMixin, Command):
    # `Rectangle` -- two OPPOSITE corners (the command behavior,
    # "(Default) Draws the rectangle using two opposite corners."). Built in the
    # active CPlane as a closed polyline, so a rectangle drawn on Front stands up
    # in the Front plane instead of lying flat (the same class of bug as the
    # circle, ).
    name = "Rectangle"
    description = ("Draw a rectangle. Pick two opposite corners in the "
                   "construction plane, or a corner, a length and a width.")
    # The second step accepts either the opposite corner or a typed length. It is a
    # DISTANCE step so typed numbers become side lengths rather than point constraints;
    # a typed length inserts the Width step (see ``_parse_other_corner``).
    FIRST_PROMPT = "First corner of rectangle:"
    OTHER_PROMPT = "Other corner or length:"
    WIDTH_PROMPT = "Width. Press Enter to use length:"
    steps = [(FIRST_PROMPT, POINT), (OTHER_PROMPT, DISTANCE)]
    # the top-level options the the reference CAD application 8 help documents for Rectangle
    # (the command behavior). They are MODE options -- each branches the
    # command into a different drawing mode. Only the default (two opposite
    # corners) is implemented; the rest report "not implemented yet" through the
    # framework's default on_mode rather than silently doing the default.
    #
    # NOTE: these six options are NOT in the command-coverage COMMANDS table --
    # Rectangle's help page lists them under an <Options> tag, not "command-line
    # options", so the general command-option inventory does not include them.
    # Rectangle's options are therefore checked directly.
    options = (
        opts.Option("3Point", opts.MODE,
                    help=("Draws the rectangle using two adjacent corner "
                          "locations and a location on the opposite side.")),
        opts.Option("EdgeMidpoint", opts.MODE,
                    help=("Draws the rectangle from the midpoint of the first "
                          "edge, an end of the edge, and a location on the "
                          "opposite side.")),
        opts.Option("Vertical", opts.MODE,
                    help="Draws the rectangle perpendicular to the construction plane."),
        opts.Option("Center", opts.MODE,
                    help="Draws the rectangle from the center point and a corner."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws a rectangle perpendicular to a curve."),
        opts.Option("Rounded", opts.MODE,
                    help=("Rounds the corners of the rectangle with arcs or "
                          "conic sections.")),
    )

    def on_mode(self, name, ctx):
        if name == "Vertical":
            return self._start_vertical()
        if name == "Center":
            # (the command behavior): "Center -- Draws the
            # rectangle from the center point and a corner." The result is an
            # AXIS-ALIGNED rectangle (sides along the CPlane axes) centred on the
            # picked centre: the opposite corner is the picked corner mirrored
            # through the centre, so bridge.make_rectangle(corner, opposite,
            # plane) expresses it exactly -- no new geometry needed.
            # Update the step labels so the prompts say "Center:"/"Corner:"
            # (the explicit-refusal policy: a prompt still reading "Corner 1:" is a quiet lie).
            self.steps = [("Center:", POINT), ("Corner:", POINT)]
            return ("Rectangle: Center mode -- pick the centre, then a corner.",
                    False)
        if name == "3Point":
            # (the command behavior): "3Point -- Draws the
            # rectangle using two adjacent corner locations and a location on the
            # opposite side." Two ADJACENT corners define one whole EDGE at any
            # angle to the CPlane axes, so the result is generally NOT
            # axis-aligned -- exactly what the refusal said
            # bridge.make_rectangle could not express. That builder now exists
            # (bridge.make_rectangle_3point), which takes the three points
            # in exactly this order, so 3Point is now a real mode, not a refusal
            # (the stale refusal is gone).
            # Update the step labels so the prompts say "Corner 1:"/"Corner 2:"/
            # "Opposite side:" (the explicit-refusal policy: a prompt that mislabels its step is a
            # quiet lie).
            self.steps = [("Corner 1:", POINT), ("Corner 2:", POINT),
                          ("Opposite side:", POINT)]
            return ("Rectangle: 3Point mode -- pick two adjacent corners, then "
                    "a point on the opposite side.", False)
        if name == "Rounded":
            # (the command behavior): "Rounded -- Rounds the corners of
            # the rectangle with arcs... Pick the radius of the rounded corner." A
            # radius step follows the corners; the reference CAD application's prompt in the command UI is "Radius
            # or point for rounded corner to pass through". Combines with Center/3Point.
            if not self._rounded:
                self._rounded = True
                self.steps.append((self.ROUNDED_PROMPT, DISTANCE))
            return ("Rectangle: Rounded -- after the corners, type the corner radius or "
                    "pick a point the corner passes through.", False)
        # EdgeMidpoint / Vertical / AroundCurve: keep reporting as they did before
        # (the explicit-refusal policy -- an unimplemented option must never look implemented).
        return f"Rectangle: the {name} option is not implemented yet", False

    ROUNDED_PROMPT = "Radius or point for rounded corner to pass through:"

    def __init__(self):
        super().__init__()
        self._rounded = False


    def _apply_option(self, m, ctx):
        # a later mode (Center / 3Point) REPLACES the steps; keep the radius step last.
        # Only Center / 3Point choose the SHAPE: `Rounded` (or a refused option) must not
        # overwrite `self.mode`, which the base sets for every MODE option.
        prev = self.mode
        out = super()._apply_option(m, ctx)
        if m.status == opts.BRANCH and m.option.name not in ("Center", "3Point"):
            self.mode = prev
        if self._rounded and (not self.steps or self.steps[-1][0] != self.ROUNDED_PROMPT):
            self.steps.append((self.ROUNDED_PROMPT, DISTANCE))
        return out

    def active_modes(self):
        """The shape (Center / 3Point -- `self.mode`, which `_apply_option` keeps for
        those two only) and, independently, Rounded."""
        out = {self.mode} if self.mode in ("Center", "3Point") else set()
        if self._rounded:
            out.add("Rounded")
        return out

    def _corner_count(self):
        """How many args the shape takes before the Rounded radius."""
        if self.mode == "3Point" or self._typed_length() is not None:
            return 3                    # 3Point's points, or corner + length + width
        return 2

    def _current_label(self):
        if self.step_index < len(self.steps):
            return self.steps[self.step_index][0]
        return None

    def _typed_length(self):
        """The SIGNED length typed at "Other corner or length", or None (a click)."""
        if self.mode in ("Center", "3Point") or len(self.args) < 2:
            return None
        other = self.args[1]
        return None if isinstance(other, tuple) else float(other)

    def _cursor_sign(self, ctx, axis):
        """+1 / -1: which side of corner 1 the live cursor is on along `axis`.

        (the help gives no rule): a typed length or width grows toward the
        cursor, else +x / +y. The cursor is the viewport preview's (`ui`); headless
        there is none, so the sign is +1.
        """
        cursor = None
        getter = getattr(ctx, "cursor_point", None)
        try:
            if callable(getter):
                cursor = getter()
            else:
                from . import ui
                cursor = getattr(ui, "_preview_cursor", None)
        except Exception:  # noqa: BLE001 - no viewport: headless
            cursor = None
        if cursor is None or not self.args:
            return 1.0
        c1 = self.args[0]
        along = sum((float(cursor[k]) - c1[k]) * float(axis[k]) for k in range(3))
        return -1.0 if along < -1e-9 else 1.0

    def _parse_other_corner(self, text, ctx):
        """"Other corner or length": a point is the opposite corner (unchanged); a
        number is the LENGTH along the CPlane x axis, and the Width step follows."""
        stripped = (text or "").strip()
        try:
            value = parse_number(stripped)
        except ValueError:
            return parse_point(stripped, self._active_cplane(ctx), self._last_point())
        if abs(value) < 1e-12:
            raise ValueError("Rectangle: the length must not be zero")
        length = value * self._cursor_sign(ctx, self._active_cplane(ctx).xaxis)
        # Inserted here, not after the feed: the base `_feed` runs `execute` the moment
        # the last step is answered, so the Width step must exist before it advances.
        # Nothing can fail between this parse and that advance.
        self.steps.insert(self.step_index + 1, (self.WIDTH_PROMPT, DISTANCE))
        return float(length)

    def _parse_width(self, text, ctx):
        """"Width": a typed number (toward the cursor's side), or a picked point --
        its signed offset from corner 1 along the CPlane y axis."""
        p = self._active_cplane(ctx)
        stripped = (text or "").strip()
        try:
            value = parse_number(stripped)
        except ValueError:
            P = parse_point(stripped, p, self._last_point())
            c1 = self.args[0]
            value = sum((P[k] - c1[k]) * float(p.yaxis[k]) for k in range(3))
            if abs(value) < 1e-12:
                raise ValueError("Rectangle: the width must not be zero")
            return value
        if abs(value) < 1e-12:
            raise ValueError("Rectangle: the width must not be zero")
        return value * self._cursor_sign(ctx, p.yaxis)

    def _enter_default(self, ctx):
        """"Width. Press Enter to use length" -- Enter makes a square."""
        vertical = self._vertical_enter(ctx)
        if vertical is not None:
            return vertical
        length = self._typed_length()
        if self._current_label() == self.WIDTH_PROMPT and length is not None:
            return abs(length) * self._cursor_sign(ctx, self._active_cplane(ctx).yaxis)
        return None

    def axis_constraint(self, plane, resolve=None):
        """At the Width step the cursor rides the far side of the typed length (along
        the CPlane y axis), so the rubber band IS length x width while you choose."""
        length = self._typed_length()
        if self._current_label() != self.WIDTH_PROMPT or length is None:
            return None
        c1 = self.args[0]
        origin = tuple(c1[k] + length * float(plane.xaxis[k]) for k in range(3))
        return origin, tuple(float(c) for c in plane.yaxis)

    def _opposite_corner(self, p):
        """Corner 2: the clicked one, or corner 1 + length * x + width * y."""
        length = self._typed_length()
        if length is None:
            return self.args[1]
        c1, width = self.args[0], float(self.args[2])
        return tuple(c1[k] + length * float(p.xaxis[k]) + width * float(p.yaxis[k])
                     for k in range(3))

    def _parse_distance(self, text, ctx):
        """The rounded-corner step: a typed radius, or a point the corner arc passes
        through. For a corner C with unit edges a, b and the point at inward offsets
        u = (P-C).a, v = (P-C).b, the arc centred C + r(a+b) passes through P when
        r^2 - 2(u+v) r + (u^2+v^2) = 0, i.e. r = u + v +- sqrt(2uv). The corner arc spans
        only the quadrant facing C, where u, v <= r -- the SMALLER root has r < max(u, v)
        (P on the far side of a small circle), so the LARGER root is the one. The corner
        nearest P is used. (The other two DISTANCE steps -- "Other corner or length"
        and "Width" -- are dispatched first.)"""
        label = self._current_label()
        if label == self.OTHER_PROMPT:
            return self._parse_other_corner(text, ctx)
        if label == self.WIDTH_PROMPT:
            return self._parse_width(text, ctx)
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        geom = self._plain_rectangle(ctx)
        if isinstance(geom, str):
            raise ValueError(geom)
        corners = bridge._polyline_vertices(geom)[:4]
        P = parse_point(stripped, self._active_cplane(ctx), self._last_point())
        i = min(range(4), key=lambda k: math.dist(P, corners[k]))
        C, prev, nxt = corners[i], corners[i - 1], corners[(i + 1) % 4]
        a = cplane.normalize(tuple(prev[k] - C[k] for k in range(3)))
        b = cplane.normalize(tuple(nxt[k] - C[k] for k in range(3)))
        d = tuple(P[k] - C[k] for k in range(3))
        u, v = cplane.dot(d, a), cplane.dot(d, b)
        if u <= 0 or v <= 0:
            raise ValueError("that point is outside the rectangle's corner")
        return u + v + math.sqrt(2 * u * v)

    def _plain_rectangle(self, ctx):
        """The sharp-cornered rectangle the corner steps define (geometry or refusal)."""
        p = self._active_cplane(ctx)
        if self.mode == "Center":
            centre, corner = self.args[:2]
            cu, cv, _wc = p.from_world(centre)
            ku, kv, _wk = p.from_world(corner)
            if abs(ku - cu) < 1e-9 and abs(kv - cv) < 1e-9:
                return "Rectangle: the centre and the corner must differ in the plane"
            opposite = tuple(2 * centre[i] - corner[i] for i in range(3))
            return bridge.make_rectangle(corner, opposite, p)
        if self.mode == "3Point":
            try:
                return bridge.make_rectangle_3point(*self.args[:3], p)
            except ValueError as e:
                return str(e)
        c1, c2 = self.args[0], self._opposite_corner(p)
        u1, v1, _w1 = p.from_world(c1)
        u2, v2, _w2 = p.from_world(c2)
        if abs(u1 - u2) < 1e-9 and abs(v1 - v2) < 1e-9:
            return "Rectangle: the two corners must differ"
        return bridge.make_rectangle(c1, c2, p)

    def execute(self, ctx: NarwhalContext):
        if getattr(self, "_vertical_rect", False):
            geom = self._vertical_boundary(ctx)
            if isinstance(geom, str):
                return geom, True
            _, display_name = ctx.add_geometry(geom, "Rectangle")
            return f"Created {display_name}", True
        if self._rounded:
            geom = self._plain_rectangle(ctx)
            if isinstance(geom, str):
                return geom, True
            radius = float(self.args[self._corner_count()])
            try:
                geom = bridge.make_rounded_rectangle(
                    bridge._polyline_vertices(geom)[:4], radius)
            except ValueError as e:
                return str(e), True
            _, display_name = ctx.add_geometry(geom, "Rectangle")
            return f"Created {display_name} (corners R{radius:g})", True
        p = self._active_cplane(ctx)
        if self.mode == "Center":
            # args = [centre, corner]. The rectangle is axis-aligned in the
            # CPlane and centred on the centre, so the opposite corner is the
            # corner mirrored through the centre (a pure world-space reflection --
            # no CPlane round-trip needed). make_rectangle(corner, opposite, p)
            # then builds the exact axis-aligned rectangle.
            centre, corner = self.args
            cu, cv, _wc = p.from_world(centre)
            ku, kv, _wk = p.from_world(corner)
            if abs(ku - cu) < 1e-9 and abs(kv - cv) < 1e-9:
                return ("Rectangle: the centre and the corner must differ in "
                        "the plane", True)
            opposite = (2 * centre[0] - corner[0],
                        2 * centre[1] - corner[1],
                        2 * centre[2] - corner[2])
            geom = bridge.make_rectangle(corner, opposite, p)
        elif self.mode == "3Point":
            # args = [corner_a, corner_b, side_point] -- two ADJACENT
            # corners then a point on the opposite side, in exactly that order
            # (the builder's signature, ). The builder raises ValueError for
            # the two degenerate cases (a == b; side_point on the line through
            # a,b) with a message written to be surfaced verbatim, so a refusal
            # is `return str(e)` rather than a crash (the explicit-refusal policy).
            corner_a, corner_b, side_point = self.args
            try:
                geom = bridge.make_rectangle_3point(
                    corner_a, corner_b, side_point, p)
            except ValueError as e:
                return str(e), True
        else:
            # Default: two OPPOSITE corners (the behaviour), or corner 1 +
            # a typed length and width.
            c1, c2 = self.args[0], self._opposite_corner(p)
            # The two corners must differ in at least one CPlane axis, otherwise
            # the rectangle has zero area and there is nothing to build.
            u1, v1, _w1 = p.from_world(c1)
            u2, v2, _w2 = p.from_world(c2)
            if abs(u1 - u2) < 1e-9 and abs(v1 - v2) < 1e-9:
                return "Rectangle: the two corners must differ", True
            geom = bridge.make_rectangle(c1, c2, p)
        _, display_name = ctx.add_geometry(geom, "Rectangle")
        return f"Created {display_name}", True


class PlaneCmd(_VerticalRectMixin, _LengthWidthMixin, Command):
    # `Plane` (the command behavior) -- draw a rectangle in the active CPlane
    # and cap it into a PLANAR SURFACE. This is the surface twin of `Rectangle`:
    # same two-opposite-corners pick, same `bridge.make_rectangle` boundary, but the
    # result is a surface (`bridge.make_planar_srf`) not a curve, so it has area and
    # can be extruded / booleans'd /
    #
    # Documented command behavior:
    #   steps:  "Pick two points for the opposite corners of a rectangle. Or, select
    #            one of the options to draw the rectangle in a different way."
    #   options: 3Point, EdgeMidpoint, Vertical, Center, AroundCurve -- all MODE.
    # Only the DEFAULT (two opposite corners) is built; the other four report "not
    # implemented yet" (the explicit-refusal policy -- an unimplemented option must never look
    # implemented), the same refusal `Rectangle` uses for its non-default modes.
    #
    # Gate (the implementation's bar): the result is ONE `PLANE` face and its area is EXACTLY
    # w*h -- not a tessellation. `make_planar_srf` hands us a recipe (not a master),
    # so the check is the kernel's `face_summary == (1, 1)` (one face, planar),
    # `looks_tessellated` False, `honesty_problem` None, and `analysis.surface_area`
    # matching w*h to machine precision (a flat quad's triangulated area is exact).
    name = "Plane"
    description = ("Draw a planar surface: pick two opposite corners of a "
                   "rectangle in the construction plane.")
    FIRST_PROMPT = "First corner of plane:"
    # MEASURED (research_all_13 #107, research_preview_14 P53): "Other corner or length";
    # a typed length asks "Width. Press Enter to use length:" (`_LengthWidthMixin`).
    OTHER_PROMPT = "Other corner or length:"
    WIDTH_PROMPT = "Width. Press Enter to use length:"
    steps = [(FIRST_PROMPT, POINT), (OTHER_PROMPT, DISTANCE)]

    def _enter_default(self, ctx):
        vertical = self._vertical_enter(ctx)
        if vertical is not None:
            return vertical
        return self._width_default(ctx)
    # (the command behavior, "Command-line options"). MODE options -- each
    # branches the drawing. None is in the command-coverage COMMANDS table (Plane's
    # help lists them, but only the default is built), so they are declared for
    # fidelity and refused by name rather than silently drawing the default.
    options = (
        opts.Option("3Point", opts.MODE,
                    help=("Draws the rectangle using two adjacent corner "
                          "locations and a location on the opposite side.")),
        opts.Option("EdgeMidpoint", opts.MODE,
                    help=("Draws the rectangle from the midpoint of the first "
                          "edge, an end of the edge, and a location on the "
                          "opposite side.")),
        opts.Option("Vertical", opts.MODE,
                    help="Draws the rectangle perpendicular to the construction plane."),
        opts.Option("Center", opts.MODE,
                    help="Draws the rectangle from the center point and a corner."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws a rectangle perpendicular to a curve."),
    )

    def on_mode(self, name, ctx):
        # Every documented non-default mode is a different way to define the
        # rectangle, none of which `make_planar_srf` + `make_rectangle` express in
        # this build. Refuse by name (the explicit-refusal policy) rather than draw the default and
        # call it the picked option.
        if name == "Vertical":
            return self._start_vertical()
        if name == "3Point":
            # OURS: the same three picks as Rectangle 3Point (two adjacent corners, then the
            # opposite side); the result is the planar surface of that rectangle.
            if self.args:
                return "Plane: 3Point is chosen before the first point", False
            self._three_point = True
            self.steps = [("Corner 1:", POINT), ("Corner 2:", POINT),
                          ("Opposite side:", POINT)]
            return None, False
        return f"Plane: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        from . import analysis

        if getattr(self, "_three_point", False):
            try:
                boundary = bridge.make_rectangle_3point(*self.args[:3],
                                                        self._active_cplane(ctx))
            except ValueError as e:
                return f"Plane: {e}", True
            try:
                geom = bridge.make_planar_srf([boundary])[0]
            except ValueError as e:
                return f"Plane: {e}", True
            _, display_name = ctx.add_geometry(geom, "Plane")
            return (f"Created {display_name} "
                    f"(planar surface, area {analysis.surface_area(geom):g})"), True
        if getattr(self, "_vertical_rect", False):
            boundary = self._vertical_boundary(ctx)
            if isinstance(boundary, str):
                return boundary, True
            try:
                geom = bridge.make_planar_srf([boundary])[0]
            except ValueError as e:
                return f"Plane: {e}", True
            _, display_name = ctx.add_geometry(geom, "Plane")
            return (f"Created {display_name} "
                    f"(planar surface, area {analysis.surface_area(geom):g})"), True

        # args = [corner1, corner2] (two OPPOSITE corners, the default mode).
        p = self._active_cplane(ctx)
        c1, c2 = self.args[0], self._corner2(p)
        u1, v1, _w1 = p.from_world(c1)
        u2, v2, _w2 = p.from_world(c2)
        if abs(u1 - u2) < 1e-9 and abs(v1 - v2) < 1e-9:
            return "Plane: the two corners must differ", True
        boundary = bridge.make_rectangle(c1, c2, p)
        try:
            pieces = bridge.make_planar_srf([boundary])
        except ValueError as e:
            return f"Plane: {e}", True
        geom = pieces[0]
        _, display_name = ctx.add_geometry(geom, "Plane")
        area = analysis.surface_area(geom)
        return (f"Created {display_name} "
                f"(planar surface, area {area:g})"), True


class EllipseCmd(Command):
    # `Ellipse` -- center, end of first axis, end of second axis
    # (the command behavior, "Default" steps). Built in the active CPlane: the
    # centre is a world point and the two axes are the world vectors from the
    # centre to each picked end, so the ellipse is fully 3D and correct in every
    # viewport. rhino3dm has no Ellipse constructor, so the bridge
    # maps a unit circle affinely -- see bridge.make_ellipse.
    name = "Ellipse"
    description = ("Draw an ellipse. Pick the center, then the end of the first "
                   "axis, then the end of the second axis.")
    # The two axis ends are LOCATIONS, not distances: the reference CAD application's "end of the first
    # axis" is a picked point (the command behavior: "Pick the end of the first
    # axis."), and the ellipse's size is the distance from the centre to that
    # location. They are therefore POINT steps, exactly as Line's two ends --
    # the centre and the two ends are three world points that fully define the
    # ellipse.
    # MEASURED (research_preview_14 P14): "Ellipse center", "End of first axis", "End of
    # second axis"; the live ellipse's axes are perpendicular. DERIVED: the second semi-axis
    # is the cursor's offset from the first axis (every probe cursor was on the
    # perpendicular, where that equals the centre distance). The old build used the raw
    # centre->pick vector, which skews the curve so its picked "axes" are not its axes.
    steps = [("Ellipse center:", POINT), ("End of first axis:", POINT),
             ("End of second axis:", POINT)]

    @staticmethod
    def second_axis_end(center, a1, a2):
        """The second axis end: `a2`'s offset from the first axis, perpendicular to it."""
        u = tuple(a1[i] - center[i] for i in range(3))
        w = tuple(a2[i] - center[i] for i in range(3))
        uu = cplane.dot(u, u)
        k = cplane.dot(w, u) / uu if uu > 1e-24 else 0.0
        return tuple(center[i] + w[i] - k * u[i] for i in range(3))
    # the top-level options the the reference CAD application 8 help documents for Ellipse
    # (the command behavior). Sub-options that appear only after a top-level
    # option is chosen (PointCount under Deformable, etc.) are NOT declared --
    # they belong to a sub-command we do not implement. Declared-but-unimplemented
    # options are answered honestly by the framework's default on_mode.
    options = (
        opts.Option("Deformable", opts.TOGGLE, default=False,
                    help=("NURBS approximation of the curve with the degree and "
                          "number of points you specify.")),
        opts.Option("Vertical", opts.MODE,
                    help=("Draws the ellipse center point and axes perpendicular "
                          "to the construction plane.")),
        opts.Option("Corner", opts.MODE,
                    help="Draws the ellipse from the corners of an enclosing rectangle."),
        opts.Option("Diameter", opts.MODE,
                    help="Draws the ellipse from points on its axes."),
        opts.Option("FromFoci", opts.MODE,
                    help="Draws the ellipse from focus points and a point on the curve."),
        opts.Option("MarkFoci", opts.TOGGLE, default=False,
                    help="Places a point object at the focus locations."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws the ellipse around a curve."),
    )

    def on_mode(self, name, ctx):
        """`Corner` ("Draws the ellipse from the corners of an enclosing
        rectangle") is built; prompts "First corner" / "Other corner" in the command UI.
        MEASURED (research_preview_14 R10): `Diameter` -- "Start of first axis", "End of first
        axis", "End of second axis" (the first axis is a whole diameter); `FromFoci` -- "First
        focus", "Second focus", "Point on ellipse"."""
        if name in ("Diameter", "FromFoci"):
            if self.args:
                return f"Ellipse: {name} is chosen before the first point", False
            self._flow = name
            self.steps = ([("Start of first axis:", POINT), ("End of first axis:", POINT),
                           ("End of second axis:", POINT)] if name == "Diameter" else
                          [("First focus:", POINT), ("Second focus:", POINT),
                           ("Point on ellipse:", POINT)])
            return None, False
        if name == "Vertical":
            if self.args:
                return "Ellipse: Vertical is chosen before the first point", False
            self._flow = name
            return "Ellipse: Vertical mode -- the ellipse stands on the construction plane", False
        if name == "Corner":
            if self.args:
                return "Ellipse: Corner is chosen before the first point", False
            self.steps = [("First corner:", POINT), ("Other corner:", POINT)]
            self._corner = True
            return "Ellipse: Corner mode -- pick two opposite corners", False
        return super().on_mode(name, ctx)

    def execute(self, ctx: NarwhalContext):
        if getattr(self, "_corner", False):
            # the rectangle's sides run along the CPlane axes (as Rectangle's do); the
            # ellipse is inscribed in it: centre the middle, semi-axes half the sides
            c1, c2 = self.args
            p = self._active_cplane(ctx)
            u1, v1, w1 = p.from_world(c1)
            u2, v2, _w2 = p.from_world(c2)
            if abs(u2 - u1) <= 1e-12 or abs(v2 - v1) <= 1e-12:
                return "Ellipse: the two corners must differ in both directions", True
            cu, cv = (u1 + u2) / 2, (v1 + v2) / 2
            self.args = [p.to_world(cu, cv, w1), p.to_world(max(u1, u2), cv, w1),
                         p.to_world(cu, max(v1, v2), w1)]
        flow = getattr(self, "_flow", None)
        if flow == "Diameter":
            s0, s1, a2 = self.args
            center = tuple((s0[i] + s1[i]) / 2 for i in range(3))
            self.args = [center, tuple(s1), a2]
        elif flow == "Vertical":
            # OURS (#37), after Circle Vertical: the ellipse stands in the vertical plane
            # through the centre that contains the CPlane normal and the direction to the
            # first pick. The first semi-axis is the distance to that pick, along its
            # direction in the CPlane; the second runs along the normal, as long as the
            # second pick's offset from the first axis (the default flow's rule).
            center, p1, p2 = self.args
            n = tuple(self._active_cplane(ctx).zaxis)
            d = _sub3(p1, center)
            u = cplane.normalize(tuple(d[i] - cplane.dot(d, n) * n[i] for i in range(3)))
            if u is None:
                return ("Ellipse: pick the first axis away from the centre along the "
                        "construction plane"), True
            w = _sub3(p2, center)
            off = tuple(w[i] - cplane.dot(w, u) * u[i] for i in range(3))
            a, b = math.dist(center, p1), math.sqrt(cplane.dot(off, off))
            self.args = [center, tuple(center[i] + a * u[i] for i in range(3)),
                         tuple(center[i] + b * n[i] for i in range(3))]
        elif flow == "FromFoci":
            f1, f2, on = self.args
            center = tuple((f1[i] + f2[i]) / 2 for i in range(3))
            c = math.dist(f1, f2) / 2
            a = (math.dist(on, f1) + math.dist(on, f2)) / 2
            u = cplane.normalize(_sub3(f2, f1))
            if u is None or not a > c:
                return "Ellipse: the foci must differ and the point lie off the focal segment", True
            n = cplane.normalize(cplane.cross(_sub3(f2, f1), _sub3(on, f1))) or \
                tuple(self._active_cplane(ctx).zaxis)
            v = cplane.cross(n, u)
            b = math.sqrt(a * a - c * c)
            self.args = [center, tuple(center[i] + a * u[i] for i in range(3)),
                         tuple(center[i] + b * v[i] for i in range(3))]
            if self.opt.get("MarkFoci"):
                for f in (f1, f2):
                    ctx.add_geometry(bridge.make_point(f), "Point")
        center, a1, a2 = self.args
        a2 = self.second_axis_end(center, a1, a2)
        la = math.dist(center, a1)
        lb = math.dist(center, a2)
        if la < 1e-9 or lb < 1e-9:
            return "Ellipse: both axes must have non-zero length", True
        geom = bridge.make_ellipse(center, a1, a2)
        _, display_name = ctx.add_geometry(geom, "Ellipse")
        return f"Created {display_name} (a={la:g}, b={lb:g})", True


class PolygonCmd(Command):
    # MEASURED: count changes at the initial point prompt; only a completed
    # polygon updates remembered NumSides. Geometry remains in existing producers.
    name = "Polygon"
    description = "Draw a regular polygon. Set NumSides, then pick its center and corner."
    # MEASURED live reference (probe_f5d19b426b8d48419d5c642720df42d9): a submitted
    # radius keeps the corner pick active. POINT lets the UI's existing numeric
    # distance constraint preserve that radius while the cursor chooses direction.
    steps = [("Center of inscribed polygon:", POINT), ("Corner of polygon:", POINT)]
    options = (
        opts.Option("NumSides", opts.VALUE, default=5, value_kind="number",
                    help="Specifies the number of sides for the polygon."),
        opts.Option("Vertical", opts.MODE,
                    help="Draws a polygon perpendicular to the construction plane."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws a polygon perpendicular to a curve."),
        opts.Option("Inscribed", opts.MODE,
                    help=("Corners of the sides touch an imaginary circle of a "
                          "specified radius.")),
        opts.Option("Circumscribed", opts.MODE,
                    help=("Midpoints of the sides touch an imaginary circle of "
                          "a specified radius.")),
        opts.Option("Edge", opts.MODE,
                    help="Draws a polygon by defining one edge."),
        opts.Option("Flip", opts.TOGGLE, default=False,
                    help="Flips the polygon about the edge you are drawing."),
        opts.Option("Star", opts.MODE,
                    help="Draws a star-shaped polygon."),
        opts.Option("Automatic", opts.TOGGLE, default=False,
                    help=("For 5 or more sides, creates a star where the size "
                          "of the outer and inner points are equal.")),
        # MEASURED (research_preview_14 P16): the prompt shows "Mode=Inscribed"; the
        # workspace buttons answer "Mode=Circumscribed". Inscribed / Circumscribed above stay
        # typeable as the same choice.
        opts.Option("Mode", opts.ENUM, choices=("Inscribed", "Circumscribed"),
                    default="Inscribed", help="Corners or side midpoints on the radius."),
    )
    #: MEASURED (P16): "( NumSides=5  Mode=Inscribed  Edge  Star  Vertical  AroundCurve )"
    SHOWN_OPTIONS = ("NumSides", "Mode", "Edge", "Star", "Vertical", "AroundCurve")

    @property
    def prompt(self):
        if self.pending_option is not None or self.step_index >= len(self.steps):
            return super().prompt
        shown = tuple(o for name in self.SHOWN_OPTIONS for o in self.options if o.name == name)
        return opts.format_prompt(self.steps[self.step_index][0], shown, self.opt)

    # OURS: bound interactive allocation rather than accept an unbounded count.
    MAX_SIDES = 10_000

    def __init__(self):
        super().__init__()
        self._inscribed = True
        self._edge = False
        self._num_sides = 5
        self._published_num_sides = None

    def begin(self, ctx):
        # Load at begin, not construction: the registry supplies deep-copied prototypes.
        self._num_sides = self._validated_num_sides(getattr(ctx, '_polygon_num_sides', 5))
        self.opt['NumSides'] = self._num_sides
        return super().begin(ctx)

    @classmethod
    def _validated_num_sides(cls, value):
        count = float(value)
        if not math.isfinite(count) or not count.is_integer() or count < 3:
            raise ValueError('Polygon: needs an integer count of at least 3 sides')
        if count > cls.MAX_SIDES:
            raise ValueError(f'Polygon: interactive side-count limit is {cls.MAX_SIDES}')
        return int(count)

    def on_option_set(self, option, value, ctx):
        if option.name == 'Mode':
            return self.on_mode(str(value), ctx)
        if option.name != 'NumSides':
            return super().on_option_set(option, value, ctx)
        try:
            count = self._validated_num_sides(value)
        except (TypeError, ValueError, OverflowError) as exc:
            self.opt['NumSides'] = self._num_sides
            return str(exc), False
        self._num_sides = count
        self.opt['NumSides'] = count
        return f'NumSides={count}', False

    def _feed(self, text, ctx):
        text = text.strip()
        if self.pending_option is not None and self.pending_option.name == 'NumSides' and text:
            value, error = opts.coerce(self.pending_option, text)
            if error:
                return error, False
            try:
                self._validated_num_sides(value)
            except (TypeError, ValueError, OverflowError) as exc:
                return str(exc), False
        elif self.pending_option is None and self.step_index == 0 and text:
            try:
                count = parse_number(text)
            except ValueError:
                pass
            else:
                return self._apply_option(opts.Match(opts.SET, self.options[0], count), ctx)
        return super()._feed(text, ctx)

    def _recorded(self, ctx, work):
        self._published_num_sides = None
        result = super()._recorded(ctx, work)
        # A builder, publisher or Undo finalization exception must not commit the draft.
        if self._published_num_sides is not None and result[1]:
            ctx._polygon_num_sides = self._published_num_sides
        self._published_num_sides = None
        return result

    def on_mode(self, name, ctx):
        """`Inscribed` / `Circumscribed` ("Midpoints of the sides touch an imaginary
        circle of a specified radius") and `Edge` ("Draws a polygon by defining one
        edge") are built. Prompts are the reference CAD application's in the command UI: "Center of inscribed
        polygon", "Center of circumscribed polygon", "Start of edge" / "End of edge"."""
        if name in ("Inscribed", "Circumscribed"):
            self._inscribed = name == "Inscribed"
            self.opt["Mode"] = name
            if not self._edge:
                self.steps[1] = ("Corner of polygon:" if self._inscribed else
                                 "Midpoint of polygon edge:", POINT)
                if self.step_index == 0:
                    self.steps[0] = (f"Center of {name.lower()} polygon:", POINT)
            return f"Polygon: {name} -- the radius reaches the " + (
                "corners" if self._inscribed else "midpoints of the sides"), False
        if name == "Star":
            # MEASURED (research_preview_14 R10 Polygon Star): "Center of star", "Corner of
            # star", "Second star radius. Press Enter for automatic"; a typed inner radius
            # finishes. Result: one closed polyline alternating outer corners and inner points.
            if self.args:
                return "Polygon: Star is chosen before the first point", False
            self._star = True
            self.steps = [("Center of star:", POINT), ("Corner of star:", POINT),
                          ("Second star radius. Press Enter for automatic:", DISTANCE)]
            return None, False
        if name == "Edge":
            if self.args:
                return "Polygon: Edge is chosen before the first point", False
            self._edge = True
            self.steps = [("Start of edge:", POINT), ("End of edge:", POINT)]
            return "Polygon: Edge mode -- pick the two ends of one side", False
        return super().on_mode(name, ctx)

    def _edge_polygon(self, ctx):
        """Edge mode: a regular polygon with the picked side. it lies on the LEFT
        of start -> end in the active CPlane (the reference CAD application uses the cursor's side); `Flip`
        puts it on the right ("Flips the polygon about the edge you are drawing")."""
        a, b = self.args
        n = self._num_sides
        if n < 3:
            return "Polygon: needs at least 3 sides"
        p = self._active_cplane(ctx)
        au, av, aw = p.from_world(a)
        bu, bv, _bw = p.from_world(b)
        s = math.hypot(bu - au, bv - av)
        if s <= 1e-12:
            return "Polygon: the two ends of the edge are the same point"
        ang = math.atan2(bv - av, bu - au)
        turn = 2 * math.pi / n * (-1.0 if self.opt.get("Flip") else 1.0)
        pts, cu, cv = [], au, av
        for k in range(n):
            pts.append(p.to_world(cu, cv, aw))
            cu += s * math.cos(ang + k * turn)
            cv += s * math.sin(ang + k * turn)
        return bridge.make_polyline(pts + [pts[0]], degree=1)

    def _distance_base(self):
        if getattr(self, "_star", False) and self.args:
            return self.args[0]                       # the inner radius is from the centre
        return super()._distance_base()

    def _enter_default(self, ctx):
        if getattr(self, "_star", False) and self.step_index == 2 and len(self.args) == 2:
            # DERIVED (help: Automatic "creates a star where the size of the outer and inner
            # points are equal"): the regular star {n/2}, r2 = r1 cos(2 pi/n) / cos(pi/n).
            # OURS: Enter's result was not measured.
            n = int(self._num_sides)
            r1 = math.dist(self.args[0], self.args[1])
            return r1 * math.cos(2 * math.pi / n) / math.cos(math.pi / n) if n >= 5 else None
        return super()._enter_default(ctx)

    def _star_polygon(self, ctx):
        center, corner, inner = self.args[:3]
        n = int(self._num_sides)
        plane = self._active_cplane(ctx)
        cu, cv, _ = plane.from_world(center)
        pu, pv, _ = plane.from_world(corner)
        r1 = math.hypot(pu - cu, pv - cv)
        r2 = abs(float(inner))
        if n < 3 or r1 <= 0 or r2 <= 0:
            return "Polygon: a star needs 3 or more sides and two non-zero radii"
        a0 = math.atan2(pv - cv, pu - cu)
        pts = []
        for k in range(2 * n):
            r = r1 if k % 2 == 0 else r2
            a = a0 + math.pi * k / n
            pts.append(plane.to_world(cu + r * math.cos(a), cv + r * math.sin(a), 0.0))
        pts.append(pts[0])
        return r3.PolylineCurve([r3.Point3d(*q) for q in pts])

    def execute(self, ctx: NarwhalContext):
        if getattr(self, "_star", False):
            geom = self._star_polygon(ctx)
            if isinstance(geom, str):
                return geom, True
            _, display_name = ctx.add_geometry(geom, "Polygon")
            self._published_num_sides = self._num_sides
            return f"Created {display_name} ({self._num_sides}-point star)", True
        if self._edge:
            geom = self._edge_polygon(ctx)
            if isinstance(geom, str):
                return geom, True
            _, display_name = ctx.add_geometry(geom, "Polygon")
            if not display_name:
                raise RuntimeError('Polygon publication did not return a display object')
            self._published_num_sides = self._num_sides
            return f"Created {display_name} ({self._num_sides} sides, by edge)", True
        center, corner = self.args
        sides = self._num_sides
        plane = self._active_cplane(ctx)
        cu, cv, _ = plane.from_world(center)
        pu, pv, _ = plane.from_world(corner)
        du, dv = pu - cu, pv - cv
        radius = math.hypot(du, dv)
        if not math.isfinite(radius) or radius <= 0:
            return "Polygon: radius must be positive in the construction plane", True
        sides = int(sides)
        if sides < 3:
            return "Polygon: needs at least 3 sides", True
        # Frame adaptation only: reuse the exact producer, with +x pointing at
        # the picked corner (or Circumscribed edge midpoint). No new geometry math.
        oriented = cplane.CPlane(center, plane.vector_to_world(du / radius, dv / radius),
                                 plane.vector_to_world(-dv / radius, du / radius))
        geom = bridge.make_polygon(center, radius, sides, inscribed=self._inscribed,
                                   plane=oriented)
        if geom is None or not isinstance(geom, r3.Curve) or not geom.IsValid:
            raise ValueError('Polygon construction did not return a valid curve')
        _, display_name = ctx.add_geometry(geom, "Polygon")
        if not display_name:
            raise RuntimeError('Polygon publication did not return a display object')
        self._published_num_sides = sides
        return f"Created {display_name} ({sides} sides, r={radius:g})", True


class ArcCmd(Command):
    # `Arc` -- MEASURED (research_all_13 #003, research_preview_14 P13): the default is
    # CENTRE-first: "Center of arc", "Start of arc", then "End point or angle", where a
    # typed 90 completes a counter-clockwise quarter arc. `StartPoint` is the three-point
    # route ("Start of arc", "End of arc", "Point on arc"); its picked points define the
    # arc's plane, so the bridge builds a fully 3D arc from them (bridge.make_arc).
    name = "Arc"
    description = ("Draw an arc. Pick the center, the start point, then the end point "
                   "or type an angle.")
    CENTER_STEPS = [("Center of arc:", POINT), ("Start of arc:", POINT),
                    ("End point or angle:", DISTANCE)]
    steps = CENTER_STEPS
    _flow = "Center"
    # the top-level options the the reference CAD application 8 help documents for Arc
    # (the command behavior). Sub-options that appear only after a top-level option
    # is chosen (Length/Tilted under Center, Degree/PointCount under Deformable,
    # Extension, etc.) are NOT declared -- they belong to sub-commands we do not
    # implement. The rest report "not implemented yet" via the framework's
    # default ``on_mode``. The command-coverage table includes every declared option
    # name so prompt registration stays complete.
    options = (
        opts.Option("Center", opts.MODE,
                    help="Draws the arc from its center, a start point and an end point."),
        opts.Option("Midpoint", opts.MODE,
                    help="Draws an arc from its midpoint."),
        opts.Option("Deformable", opts.TOGGLE, default=False,
                    help=("NURBS approximation of the arc with a specified "
                          "degree and number of control points.")),
        opts.Option("StartPoint", opts.MODE,
                    help="Draws the arc starting from the first end of the arc."),
        opts.Option("Direction", opts.MODE,
                    help="Specifies the direction tangent to the arc's start point."),
        opts.Option("ThroughPoint", opts.MODE,
                    help="Specifies a location the arc curve must pass through."),
        opts.Option("Tangent", opts.MODE,
                    help="Draws an arc tangent to selected curves."),
        opts.Option("Radius", opts.VALUE, value_kind="number",
                    help="Specifies a radius for the arc."),
        opts.Option("Counterclockwise", opts.TOGGLE, default=False,
                    help="Draws the arc in a counterclockwise direction."),
        opts.Option("Clockwise", opts.TOGGLE, default=False,
                    help="Draws the arc in a clockwise direction."),
    )

    def on_mode(self, name, ctx):
        """`Center`, `StartPoint`, `Direction`, `Midpoint` and `ThroughPoint` are built, with the reference CAD application's prompts
        in the command UI: "Center of arc" / "Start of arc" / "End point", "Start of arc"
        / "End of arc" / a through point, "Start of arc" / "Direction at start" / "End of
        arc"."""
        flows = {
            "Center": list(self.CENTER_STEPS),
            # StartPoint steps: "Pick the arc's start point. Pick the arc's end
            # point. Click a location the arc passes through." -- our default flow
            "StartPoint": [("Start of arc:", POINT), ("End of arc:", POINT),
                           ("Point on arc:", POINT)],
            "Direction": [("Start of arc:", POINT), ("Direction at start:", POINT),
                          ("End of arc:", POINT)],
            # OURS (#36): the pick sequence is not documented. Like Line's BothSides
            # ("Middle of line"), the first pick is the middle: the point halfway ALONG
            # the arc. The end and a point on the arc then fix the circle, and the start
            # is the end mirrored across the diameter through the midpoint.
            "Midpoint": [("Midpoint of arc:", POINT), ("End of arc:", POINT),
                         ("Point on arc:", POINT)],
        }
        if name in flows:
            if self.args:
                return f"Arc: {name} is chosen before the first point", False
            self.steps = list(flows[name])
            self._flow = name
            return f"Arc: {name} mode", False
        if name == "ThroughPoint":
            # OURS (#36): ThroughPoint continues StartPoint ("Start of arc", "End of arc",
            # "Point on arc"), which is the flow the "start, end, point on arc" macro
            # `_StartPoint _Pause _ThroughPoint` expects; on its own it is refused.
            if self._flow != "StartPoint":
                return "Arc: ThroughPoint follows StartPoint; choose StartPoint first", False
            if self.step_index > 1:
                return "Arc: ThroughPoint is chosen before the end of the arc", False
            return "Arc: ThroughPoint -- pick the end, then a point the arc passes through", False
        if name == "Tangent":
            if self.args:
                return "Arc: Tangent is chosen before the first point", False
            self.steps = list(_TANGENT_STEPS["Arc"])
            self._flow = "Tangent"
            self._tangent = True
            self._tan_radius = None
            if self.opt.get("Radius"):               # Arc's own Radius, set beforehand
                _set_tangent_radius(self, float(self.opt["Radius"]))
            return "Arc: Tangent mode", False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        # Arc's `Radius` ("Specifies a radius for the arc") answers Tangent's radius
        if option.name == "Radius" and getattr(self, "_tangent", False) \
                and self.step_index <= 1 and value:
            return _set_tangent_radius(self, float(value))
        return super().on_option_set(option, value, ctx)

    def _feed(self, text, ctx):
        """as Circle Tangent -- the second prompt takes a typed radius."""
        hit = _tangent_radius_input(self, text)
        if hit is not None:
            return hit
        return super()._feed(text, ctx)

    def _distance_base(self):
        return None             # "End point or angle": a typed number is an ANGLE

    def _parse_distance(self, text, ctx):
        """"End point or angle": a typed number is the sweep angle in degrees (MEASURED:
        90 completes a counter-clockwise quarter arc); anything else is the end point."""
        stripped = (text or "").strip()
        try:
            angle = parse_number(stripped)
        except ValueError:
            return parse_point(stripped, self._active_cplane(ctx), self._last_point())
        if not 0.0 < abs(angle) < 360.0:
            raise ValueError("Arc: the angle must be between -360 and 360 degrees, not 0")
        return float(angle)

    def _enter_default(self, ctx):
        # "Third tangent curve. Press Enter to draw arc from first two points"
        if getattr(self, "_tangent", False) and self.step_index == 2:
            return ""
        return super()._enter_default(ctx)

    def _tangent_arc(self, ctx):
        """``(start, on_arc, end, names)`` for Arc Tangent, or a refusal string.

        Two curves + radius: the arc between the two tangent points on the tangent circle
        -- the SHORTER one, which is the fillet arc (the reference CAD application then lets the cursor
        "choose from the suggested possibilities", a pick a typed point cannot make).
        Three lines: from the first tangent point THROUGH the second to the third."""
        res = _tangent_solution(self, ctx, "Arc")
        if isinstance(res, str):
            return res
        tc, names = res
        if len(tc.tangents) == 3:
            return tc.tangents[0], tc.tangents[1], tc.tangents[2], names
        a, b = tc.tangents
        c = tc.center
        bis = tuple((a[i] - c[i]) + (b[i] - c[i]) for i in range(3))
        u = cplane.normalize(bis)
        if u is None or math.sqrt(cplane.dot(bis, bis)) < 1e-12 * tc.radius:
            u = cplane.normalize(cplane.cross(tc.normal,
                                              tuple(a[i] - c[i] for i in range(3))))
        mid = tuple(c[i] + tc.radius * u[i] for i in range(3))
        return a, mid, b, names

    @staticmethod
    def _sweep(center, u, v, r, phi):
        """The point at angle `phi` on the circle `center` + r (cos u + sin v)."""
        return tuple(center[i] + r * (math.cos(phi) * u[i] + math.sin(phi) * v[i])
                     for i in range(3))

    def _center_arc(self, ctx):
        """Center mode -> ``(start, mid, end)``. "Pick the center... Pick the
        arc's first endpoint. This point is also the arc's start point. Pick the arc's
        second end point". The end point fixes the END ANGLE (it need not lie on the
        circle). the arc runs COUNTERCLOCKWISE in the active CPlane from start to
        end -- the reference CAD application follows the direction the cursor swept, which a typed point lacks."""
        center, start, end = self.args
        z = tuple(float(c) for c in self._active_cplane(ctx).zaxis)
        d0 = tuple(start[i] - center[i] for i in range(3))
        d0 = tuple(d0[i] - cplane.dot(d0, z) * z[i] for i in range(3))
        r = math.sqrt(cplane.dot(d0, d0))
        if r <= 1e-12:
            return "Arc: the start point is at the center"
        u = tuple(c / r for c in d0)
        v = cplane.cross(z, u)
        if isinstance(end, (int, float)):
            # a typed angle: counter-clockwise for positive (MEASURED), clockwise for negative
            phi = math.radians(float(end))
            start_p = self._sweep(center, u, v, r, 0.0)
            return (start_p, self._sweep(center, u, v, r, phi / 2),
                    self._sweep(center, u, v, r, phi))
        de = tuple(end[i] - center[i] for i in range(3))
        phi = math.atan2(cplane.dot(de, v), cplane.dot(de, u)) % (2 * math.pi)
        if phi <= 1e-12 or math.hypot(cplane.dot(de, u), cplane.dot(de, v)) <= 1e-12:
            return "Arc: the end point gives no angle from the start"
        start_p = self._sweep(center, u, v, r, 0.0)
        return (start_p, self._sweep(center, u, v, r, phi / 2),
                self._sweep(center, u, v, r, phi))

    def _direction_arc(self):
        """Direction mode -> ``(start, mid, end)``: the arc leaving `start` TANGENT to the
        picked direction and ending at `end` (exact: the centre is where the normal to the
        tangent meets the chord's perpendicular bisector)."""
        start, direction, end = self.args
        t = cplane.normalize(tuple(direction[i] - start[i] for i in range(3)))
        c = tuple(end[i] - start[i] for i in range(3))
        if t is None or cplane.dot(c, c) <= 1e-24:
            return "Arc: the direction and the end must differ from the start"
        n = tuple(c[i] - cplane.dot(c, t) * t[i] for i in range(3))
        nl = math.sqrt(cplane.dot(n, n))
        if nl <= 1e-12 * math.sqrt(cplane.dot(c, c)):
            return "Arc: the end lies on the start direction, so the arc would be a line"
        n = tuple(x / nl for x in n)
        r = cplane.dot(c, c) / (2.0 * cplane.dot(c, n))
        center = tuple(start[i] + r * n[i] for i in range(3))
        u = tuple(-x for x in n)                 # centre -> start
        theta = math.atan2(cplane.dot(c, n), cplane.dot(c, t))   # half the sweep
        return (tuple(start), self._sweep(center, u, t, r, theta),
                self._sweep(center, u, t, r, 2 * theta))

    def _midpoint_arc(self):
        """Midpoint mode -> ``(start, mid, end)``: the symmetric arc whose midpoint and end
        were picked, on the circle through the midpoint, the end and the point on the arc."""
        mid, end, on_arc = self.args
        try:
            circle = r3.Arc(r3.Point3d(*mid), r3.Point3d(*on_arc), r3.Point3d(*end))
            center = (circle.Center.X, circle.Center.Y, circle.Center.Z)
        except Exception:  # noqa: BLE001 -- rhino3dm reports a degenerate arc by raising
            center = None
        if center is None or not circle.IsValid:
            return "Arc: the midpoint, end and point on arc must not be collinear"
        u = cplane.normalize(tuple(mid[i] - center[i] for i in range(3)))
        e = tuple(end[i] - center[i] for i in range(3))
        r = math.sqrt(cplane.dot(e, e))
        along = cplane.dot(e, u)
        if along <= -r * (1.0 - 1e-12):
            return "Arc: the end is opposite the midpoint, so the arc would be a whole circle"
        start = tuple(center[i] + 2.0 * along * u[i] - e[i] for i in range(3))
        p = tuple(on_arc[i] - center[i] for i in range(3))
        if cplane.dot(p, u) < along - 1e-9 * r:
            return "Arc: the point on arc must lie between the start and the end of the arc"
        return start, tuple(mid), tuple(end)

    def execute(self, ctx: NarwhalContext):
        flow = getattr(self, "_flow", None)
        if flow == "Tangent":
            res = self._tangent_arc(ctx)
            if isinstance(res, str):
                return res, True
            start, on_arc, end, names = res
            try:
                geom = bridge.make_arc(start, on_arc, end)
            except ValueError as e:
                return f"Arc: {e}", True
            _, display_name = ctx.add_geometry(geom, "Arc")
            return f"Created {display_name} tangent to {', '.join(names)}", True
        if flow in ("Center", "Direction", "Midpoint"):
            res = (self._center_arc(ctx) if flow == "Center" else
                   self._direction_arc() if flow == "Direction" else self._midpoint_arc())
            if isinstance(res, str):
                return res, True
            start, on_arc, end = res
        else:
            start, end, on_arc = self.args
        # Three collinear points cannot define an arc; the bridge raises, so
        # catch it here and report rather than ship a degenerate curve.
        try:
            geom = bridge.make_arc(start, on_arc, end)
        except ValueError as e:
            return f"Arc: {e}", True
        _, display_name = ctx.add_geometry(geom, "Arc")
        return f"Created {display_name}", True


class ConicCmd(Command):
    """Draw an exact rational quadratic conic from start, end, apex and rho."""

    name = "Conic"
    description = "Draw a conic from start, end, apex and a through point or rho."
    # MEASURED (research_preview_14 P31): "Start of conic", "End of conic ( Apex ... )",
    # "Apex", then "Curvature point or rho <0.500>" -- a typed 0.5 completes; Enter takes the
    # default rho (DERIVED). `Apex` is offered at the END prompt, after the start.
    RHO_PROMPT = "Curvature point or rho <0.500>:"
    steps = [("Start of conic:", POINT), ("End of conic:", POINT),
             ("Apex:", POINT), (RHO_PROMPT, POINT)]

    def _enter_default(self, ctx):
        return 0.5 if self.step_index == 3 else None
    options = (
        opts.Option("Apex", opts.MODE, help="Pick the apex before the endpoint."),
        opts.Option("Tangent", opts.MODE, help="Start or end tangent to a selected curve."),
        opts.Option("Perpendicular", opts.MODE,
                    help="Start or end perpendicular to a selected curve."),
        opts.Option("SubCrv", opts.MODE, help="Use part of a curve as input."),
    )

    def __init__(self):
        super().__init__()
        self._apex_first = False

    def on_mode(self, name, ctx):
        if name == "Apex":
            if len(self.args) > 1:
                return "Conic: Apex must be chosen before the end point", False
            self._apex_first = True
            self.steps = [("Start of conic:", POINT), ("Apex:", POINT),
                          ("End of conic:", POINT), (self.RHO_PROMPT, POINT)]
            return "Conic: pick start, apex, then end", False
        if name in ("Tangent", "Perpendicular", "SubCrv"):
            return f"Conic: {name} is not implemented yet", False
        return super().on_mode(name, ctx)

    def _parse(self, kind, text, ctx=None):
        if self.step_index == 3:
            try:
                return parse_number((text or "").strip())
            except ValueError:
                pass
        return super()._parse(kind, text, ctx)

    def execute(self, ctx):
        if self._apex_first:
            start, apex, end, shape = self.args
        else:
            start, end, apex, shape = self.args
        try:
            rho = (float(shape) if isinstance(shape, (int, float))
                   else conics.rho_from_point(start, end, apex, shape))
            curve = conics.make_conic(start, end, apex, rho)
        except ValueError as exc:
            return f"Conic: {exc}", True
        _, display = ctx.add_geometry(curve, "Conic")
        return f"Created {display} (rho {rho:g}, exact rational quadratic)", True


class ParabolaCmd(Command):
    """Draw exact quadratic parabola segments by focus or vertex."""

    name = "Parabola"
    description = "Draw a parabola by focus/direction/end or vertex/focus/end."
    # OURS: the retained help does not state the startup mode; the authored
    # toolbar's primary action is "Parabola by focus", so Focus is the default.
    # MEASURED (research_all_13 #101): the Focus construction's prompts.
    FOCUS_STEPS = [("Parabola focus:", POINT), ("Parabola direction:", POINT),
                   ("Parabola end:", POINT)]
    steps = FOCUS_STEPS
    options = (
        opts.Option("MarkFocus", opts.TOGGLE, default=False,
                    help="Place a point object at the focus."),
        opts.Option("Half", opts.TOGGLE, default=False,
                    help="Draw only half of the parabola."),
        opts.Option("Focus", opts.MODE,
                    help="Pick a focus, open direction and end."),
        opts.Option("Vertex", opts.MODE,
                    help="Pick a vertex, focus and end."),
    )

    def __init__(self):
        super().__init__()
        self._construction = "Focus"

    def on_mode(self, name, ctx):
        if name in ("Focus", "Vertex"):
            if self.args:
                return f"Parabola: {name} must be chosen before picking points", False
            self._construction = name
            self.steps = (list(self.FOCUS_STEPS)
                          if name == "Focus" else
                          [("Vertex:", POINT), ("Focus:", POINT), ("End:", POINT)])
            return f"Parabola: {name} construction", False
        return super().on_mode(name, ctx)

    def execute(self, ctx):
        try:
            if self._construction == "Vertex":
                curve, vertex, focus = conics.parabola_from_vertex(
                    self.args[0], self.args[1], self.args[2],
                    half=bool(self.opt.get("Half")))
            else:
                curve, vertex, focus = conics.parabola_from_focus(
                    self.args[0], self.args[1], self.args[2],
                    half=bool(self.opt.get("Half")))
        except ValueError as exc:
            return f"Parabola: {exc}", True
        _, display = ctx.add_geometry(curve, "Parabola")
        if self.opt.get("MarkFocus"):
            ctx.add_geometry(bridge.make_point(focus), "Point")
        from . import arclength
        # MEASURED (research_preview_14 P49): "Focus to vertex distance = 2.18744, length =
        # 24.7567" -- six significant figures.
        return (f"Focus to vertex distance = {math.dist(focus, vertex):.6g}, "
                f"length = {arclength.length_of(curve):.6g}"), True


class Parabola3PtCmd(Command):
    """Draw an exact parabola: start, a point on it, end, then the axis direction.

    MEASURED (research_preview_14 P50 and R5 Parabola3Pt): "Parabola start", "Point on
    parabola", "Parabola end", then "Parabola direction ( MarkFocus=No )"; the axis is parallel
    to end -> direction (`conics.parabola_through_direction`, checked against six exact control
    points). Mode=Vertex keeps the symmetric start / vertex / end construction (OURS: its
    prompts were not measured). Mode=Focus is the Parabola command's focus / direction /
    end construction; PickOrder does not apply to it and is refused by name."""

    name = "Parabola3Pt"
    description = "Draw an exact parabola through start, a point and end, aimed by a direction."
    steps = [("Parabola start:", POINT), ("Point on parabola:", POINT),
             ("Parabola end:", POINT), ("Parabola direction:", POINT)]
    options = (
        opts.Option("Mode", opts.ENUM, choices=("Focus", "ThroughPoint", "Vertex"),
                    default="ThroughPoint", help="Set the function of the special point."),
        opts.Option("PickOrder", opts.ENUM,
                    choices=("Default", "EndsFirst", "EndsLast"), default="Default",
                    help="Choose when endpoints and the special point are picked."),
        opts.Option("MarkFocus", opts.TOGGLE, default=False,
                    help="Place a point object at the focus."),
    )

    def _refresh_steps(self):
        special = self.opt.get("Mode", "ThroughPoint")
        if special == "Focus":
            # The Parabola command's Focus construction; no start point is asked.
            self.steps = list(ParabolaCmd.FOCUS_STEPS)
            return
        label = {"ThroughPoint": "Point on parabola:", "Vertex": "Vertex point:"}.get(
            special, "Focus point:")
        start, end = ("Parabola start:", POINT), ("Parabola end:", POINT)
        order = self.opt.get("PickOrder", "Default")
        if order == "EndsFirst":
            self.steps = [start, end, (label, POINT)]
        elif order == "EndsLast":
            self.steps = [(label, POINT), start, end]
        else:
            self.steps = [start, (label, POINT), end]
        if special == "ThroughPoint":
            self.steps.append(("Parabola direction:", POINT))

    def on_option_set(self, option, value, ctx):
        if option.name in ("Mode", "PickOrder"):
            if self.args:
                return f"Parabola3Pt: {option.name} must be set before picking points", False
            if option.name == "PickOrder" and self.opt.get("Mode") == "Focus":
                # OURS (#39): Focus picks focus, direction, end; there is no order to choose.
                self.opt["PickOrder"] = "Default"
                return "Parabola3Pt: PickOrder is not available with Mode=Focus", False
            note = ""
            if option.name == "Mode" and value == "Focus" \
                    and self.opt.get("PickOrder", "Default") != "Default":
                self.opt["PickOrder"] = "Default"
                note = " (PickOrder reset to Default; it does not apply to Focus)"
            self._refresh_steps()
            return f"Parabola3Pt: {option.name}={value}{note}", False
        return super().on_option_set(option, value, ctx)

    def execute(self, ctx):
        mode = self.opt.get("Mode", "ThroughPoint")
        if mode == "Focus":
            try:
                curve, _vertex, focus = conics.parabola_from_focus(*self.args[:3])
            except ValueError as exc:
                return f"Parabola3Pt: {exc}", True
            _, display = ctx.add_geometry(curve, "Parabola")
            if self.opt.get("MarkFocus"):
                ctx.add_geometry(bridge.make_point(focus), "Point")
            return f"Created {display} (exact quadratic)", True
        order = self.opt.get("PickOrder", "Default")
        picks = list(self.args[:3])
        if order == "EndsFirst":
            start, end, special = picks
        elif order == "EndsLast":
            special, start, end = picks
        else:
            start, special, end = picks
        try:
            if mode == "ThroughPoint":
                curve, focus = conics.parabola_through_direction(start, special, end,
                                                                 self.args[3])
            else:
                curve, focus = conics.parabola_from_three(start, end, special)
        except ValueError as exc:
            return f"Parabola3Pt: {exc}", True
        _, display = ctx.add_geometry(curve, "Parabola")
        if self.opt.get("MarkFocus"):
            ctx.add_geometry(bridge.make_point(focus), "Point")
        return f"Created {display} (exact quadratic)", True


class HyperbolaCmd(Command):
    """Draw an exact finite hyperbola branch from center, focus and end."""

    name = "Hyperbola"
    description = "Draw a hyperbola from its center, focus and branch end."
    # MEASURED (research_all_13 note 071): the reference CAD application's prompt wording.
    steps = [("Hyperbola center:", POINT), ("Hyperbola focus:", POINT),
             ("Hyperbola end:", POINT)]
    options = (
        opts.Option("Default", opts.MODE, help="Use center, focus and end."),
        opts.Option("FromCoefficient", opts.MODE, help="Use A and B coefficients."),
        opts.Option("FromFoci", opts.MODE, help="Start from both focus points."),
        opts.Option("FromVertex", opts.MODE, help="Start from a vertex and focus."),
        opts.Option("MarkFoci", opts.TOGGLE, default=False,
                    help="Place point objects at both foci."),
        opts.Option("BothBranches", opts.TOGGLE, default=False,
                    help="Draw both branches of the hyperbola."),
        opts.Option("ShowAsymptotes", opts.TOGGLE, default=False,
                    help="Display the asymptotes while drawing."),
    )

    def on_mode(self, name, ctx):
        if name == "Default":
            return "Hyperbola: Center construction", False
        if name in ("FromCoefficient", "FromFoci", "FromVertex"):
            return f"Hyperbola: {name} is not implemented yet", False
        return super().on_mode(name, ctx)

    def execute(self, ctx):
        center, focus, end = self.args
        try:
            curve, a, b = conics.hyperbola_from_center(center, focus, end)
        except conics.HyperbolaEndOnAxis:
            return None, True      # MEASURED (#71 item 6): a silent return, nothing made
        except ValueError as exc:
            return f"Hyperbola: {exc}", True
        displays = [ctx.add_geometry(curve, "Hyperbola")[1]]
        if self.opt.get("BothBranches"):
            other_focus = tuple(2.0 * center[i] - focus[i] for i in range(3))
            other_end = tuple(2.0 * center[i] - end[i] for i in range(3))
            other, _a, _b = conics.hyperbola_from_center(center, other_focus, other_end)
            displays.append(ctx.add_geometry(other, "Hyperbola")[1])
        if self.opt.get("MarkFoci"):
            ctx.add_geometry(bridge.make_point(focus), "Point")
            other_focus = tuple(2.0 * center[i] - focus[i] for i in range(3))
            ctx.add_geometry(bridge.make_point(other_focus), "Point")
        return (f"Created {len(displays)} hyperbola branch"
                f"{'es' if len(displays) != 1 else ''} (A={a:g}, B={b:g}, exact rational)"), True


class CatenaryCmd(Command):
    """Draw a tolerance-qualified hanging-chain curve through a midpoint sag."""

    name = "Catenary"
    description = "Draw a hanging-chain curve from endpoints, axis and through point."
    # MEASURED (research_preview_14 P30): "Catenary axis direction. Press Enter for default"
    # -- Enter advances; with the default in Top a through point above the chord (+y) made
    # a live catenary and one below drew none. PointCount defaults to 20.
    steps = [("Start of catenary:", POINT), ("End of catenary:", POINT),
             ("Catenary axis direction. Press Enter for default:", POINT),
             ("Point on catenary:", POINT)]
    options = (
        opts.Option("Mode", opts.ENUM,
                    choices=("ThroughPoint", "Length", "Parameter", "Apex"),
                    default="ThroughPoint", help="Select the catenary definition mode."),
        opts.Option("Output", opts.ENUM, choices=("Smooth", "Polyline"),
                    default="Smooth", help="Create a smooth curve or polyline."),
        opts.Option("PointCount", opts.VALUE, default=20, value_kind="number",
                    help="Control the requested polyline point count."),
        opts.Option("MarkApex", opts.TOGGLE, default=False,
                    help="Place a point object at the analytic apex."),
    )

    def _enter_default(self, ctx):
        """DERIVED from P30: the default axis lies in the CPlane, perpendicular to the chord,
        on the CPlane +y side (the side the measured live catenary bulged to)."""
        if self.step_index != 2 or len(self.args) != 2:
            return None
        plane = self._active_cplane(ctx)
        start, end = self.args
        perp = cplane.normalize(cplane.cross(tuple(plane.zaxis), _sub3(end, start)))
        if perp is None:
            return None
        if cplane.dot(perp, tuple(plane.yaxis)) < -1e-12 or (
                abs(cplane.dot(perp, tuple(plane.yaxis))) <= 1e-12
                and cplane.dot(perp, tuple(plane.xaxis)) < 0):
            perp = tuple(-c for c in perp)
        mid = tuple((start[i] + end[i]) / 2 for i in range(3))
        return tuple(mid[i] + perp[i] for i in range(3))

    def execute(self, ctx):
        mode = self.opt.get("Mode", "ThroughPoint")
        if mode != "ThroughPoint":
            return (f"Catenary: Mode={mode} is not implemented yet -- "
                    "ThroughPoint midpoint sag is supported"), True
        try:
            curve, deviation = catenary.make_curve(
                self.args[0], self.args[1], self.args[2], self.args[3],
                _document_tolerance(ctx))
        except ValueError as exc:
            return f"Catenary: {exc}", True
        output = self.opt.get("Output", "Smooth")
        if output == "Polyline":
            count_value = float(self.opt.get("PointCount", 20))
            count = int(count_value)
            if count != count_value or count < 2:
                return "Catenary: PointCount must be a whole number >= 2", True
            domain = curve.Domain
            points = [curve.PointAt(domain.T0 + (domain.T1-domain.T0) * i/(count-1))
                      for i in range(count)]
            result = r3.PolylineCurve(points)
        else:
            result = curve
        _, display = ctx.add_geometry(result, "Catenary")
        if self.opt.get("MarkApex"):
            ctx.add_geometry(bridge.make_point(self.args[3]), "Point")
        return (f"Created {display} ({output}; approximate NURBS deviation "
                f"{deviation:.3g})"), True


class _CoilCmd(Command):
    """Shared straight-axis option behavior for Helix and Spiral."""

    # OURS: the help does not publish initial values. Ten turns matches the
    # command's conventional first-use shape; every value remains editable.
    DEFAULT_TURNS = 10.0
    options = (
        opts.Option("Vertical", opts.MODE,
                    help="Draw the axis perpendicular to the construction plane."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draw around a selected curve with history."),
        opts.Option("Diameter/Radius", opts.ENUM, choices=("Radius", "Diameter"),
                    default="Radius", help="Toggle radius or diameter input."),
        opts.Option("Mode", opts.MODE,
                    help="Display whether Turns or Pitch currently takes precedence."),
        opts.Option("Turns", opts.VALUE, default=DEFAULT_TURNS, value_kind="number",
                    help="Set the number of turns; pitch adjusts automatically."),
        opts.Option("Pitch", opts.VALUE, default=1.0, value_kind="number",
                    help="Set distance between turns; turn count adjusts automatically."),
        opts.Option("ReverseTwist", opts.MODE,
                    help="Reverse the direction of twist."),
    )

    def __init__(self):
        super().__init__()
        self._coil_mode = "Turns"
        self._reverse_twist = False
        self._vertical = False
        self.size_mode = "radius"

    def _normal(self, ctx):
        return tuple(float(value) for value in self._active_cplane(ctx).zaxis)

    def _radial_direction(self, ctx, start, end):
        axis = tuple(end[i] - start[i] for i in range(3))
        plane = self._active_cplane(ctx)
        choices = (tuple(float(value) for value in plane.xaxis),
                   tuple(float(value) for value in plane.yaxis))
        axis_sq = cplane.dot(axis, axis)
        for direction in choices:
            projected = tuple(direction[i] - axis[i] * cplane.dot(direction, axis) / axis_sq
                              for i in range(3))
            if cplane.dot(projected, projected) > 1e-20:
                return projected
        raise ValueError("the axis has no usable radial direction")

    def _axis_end(self, ctx):
        start, second = self.args[:2]
        if not self._vertical:
            return start, second
        normal = self._normal(ctx)
        end = tuple(start[i] + float(second) * normal[i] for i in range(3))
        return start, end

    def _turn_count(self, axis_length):
        if self._coil_mode == "Pitch":
            pitch = float(self.opt.get("Pitch", 0.0))
            if pitch <= 0.0:
                raise ValueError("Pitch must be greater than zero")
            return axis_length / pitch
        turns = float(self.opt.get("Turns", self.DEFAULT_TURNS))
        if turns <= 0.0:
            raise ValueError("Turns must be greater than zero")
        return turns

    def _radius(self, value):
        radius = float(value) / 2.0 if self.size_mode == "diameter" else float(value)
        if radius <= 0.0:
            raise ValueError("Radius must be greater than zero")
        return radius

    def _parse_distance(self, text, ctx):
        if self._vertical and self.step_index == 1:
            stripped = (text or "").strip()
            try:
                return parse_number(stripped)
            except ValueError:
                start = self.args[0]
                point = parse_point(stripped, self._active_cplane(ctx), start)
                normal = self._normal(ctx)
                return sum((point[i] - start[i]) * normal[i] for i in range(3))
        return super()._parse_distance(text, ctx)

    def _distance_base(self):
        if self.step_index >= 2 and self.args:
            return self.args[0]
        return super()._distance_base()

    def axis_constraint(self, plane, resolve=None):
        if self._vertical and self.step_index == 1 and self.args:
            return self.args[0], tuple(float(value) for value in plane.zaxis)
        return None

    def on_mode(self, name, ctx):
        if name == "AroundCurve":
            return (f"{self.name}: AroundCurve is not implemented yet -- its "
                    "moving-frame behavior has not been measured"), False
        if name == "Mode":
            return (f"{self.name}: {self._coil_mode} takes precedence; "
                    f"ReverseTwist={'Yes' if self._reverse_twist else 'No'}"), False
        if name == "ReverseTwist":
            self._reverse_twist = not self._reverse_twist
            return f"{self.name}: ReverseTwist={'Yes' if self._reverse_twist else 'No'}", False
        if name == "Vertical":
            if self.step_index > 1:
                return f"{self.name}: Vertical must be chosen before the axis end", False
            self._vertical = True
            self.steps[1] = ("Axis length:", DISTANCE)
            return f"{self.name}: axis is vertical to the construction plane", False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "Diameter/Radius":
            self.size_mode = "diameter" if str(value).lower().startswith("d") else "radius"
            indices = getattr(self, "_radius_step_indices", (self._radius_step_index,))
            for index in indices:
                _label, kind = self.steps[index]
                original = type(self).steps[index][0]
                diameter = original.replace("radius", "diameter").replace("Radius", "Diameter")
                self.steps[index] = (
                    diameter if self.size_mode == "diameter" else original, kind)
            return f"{self.name}: size input is {self.size_mode}", False
        if option.name in ("Turns", "Pitch"):
            previous = getattr(self, "_last_good", {}).get(option.name, option.default)
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = float("nan")
            if not math.isfinite(number) or number <= 0.0:
                # The refused value must not stay stored (it used to, and the build then
                # failed later). MEASURED (research_all_13 #068): Turns 0 prints this
                # and asks for the number of turns again.
                self.opt[option.name] = previous
                if option.name == "Turns":
                    self.pending_option = option
                    return "Number must be bigger than or equal to 1.49012e-08", False
                return f"{self.name}: {option.name} must be greater than zero", False
            self._last_good = dict(getattr(self, "_last_good", {}), **{option.name: number})
            self._coil_mode = option.name
            return f"{self.name}: {option.name}={number:g} takes precedence", False
        return super().on_option_set(option, value, ctx)


class HelixCmd(_StickySizeMixin, _CoilCmd):
    """Draw a tolerance-qualified cubic NURBS helix around a straight axis."""

    name = "Helix"
    description = "Draw a helical NURBS curve from an axis and radius."
    # MEASURED (research_preview_14 P40): "Radius and start point <1.000>", sticky.
    steps = [("Start of axis:", POINT), ("End of axis:", POINT),
             ("Radius and start point:", DISTANCE)]
    _radius_step_index = 2
    _radius_prompt = "Radius and start point:"
    _size_step_index = 2
    _sticky_defaults = {2: 1.0}

    def on_mode(self, name, ctx):
        result = super().on_mode(name, ctx)
        if name == "Vertical" and self._vertical:
            # MEASURED (research_all_13 #068 item 7): Vertical asks "End of axis:".
            self.steps[1] = ("End of axis:", DISTANCE)
        return result

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        # MEASURED (#068 item 7): with Vertical, a number typed at "End of axis" stays on
        # that prompt; the next bare Enter takes it and moves on to the radius.
        if (self._vertical and self.step_index == 1 and self.pending_option is None
                and len(self.args) == 1):
            if raw:
                try:
                    self._locked_axis = parse_number(raw)
                    return None, False
                except ValueError:
                    pass
            elif getattr(self, "_locked_axis", None) is not None:
                text = repr(self._locked_axis)
        # MEASURED (research_all_13 #068 item 2): a number typed at the radius prompt
        # stays on that prompt (a preview); a second, bare Enter commits it -- `2`,
        # Enter, Enter gave a helix starting at (2,0,0). A pick after it also commits.
        raw = (text or "").strip()
        if (self.step_index == self._radius_step_index and self.pending_option is None
                and len(self.args) == self._radius_step_index):
            if raw and not (self.options and opts.match(raw, self.options, self.opt).status
                            != opts.NONE):
                try:
                    value = parse_number(raw)
                except ValueError:
                    value = None
                if value is not None:
                    self._locked_radius = value
                    return None, False
            if getattr(self, "_locked_radius", None) is not None and (
                    not raw or "," in raw or "<" in raw):
                text = repr(self._locked_radius)
        result = super()._feed(text, ctx)
        # MEASURED (research_all_13 #068 item 8): an axis end ON the start returns to
        # Command: silently -- no radius prompt, no object, no message.
        if (not self._vertical and len(self.args) == 2 and _is_point(self.args[1])
                and math.dist(self.args[0], self.args[1]) <= 1e-12):
            return None, True
        return result

    def execute(self, ctx):
        try:
            start, end = self._axis_end(ctx)
            axis_length = math.dist(start, end)
            if axis_length <= 1e-12:
                raise ValueError("axis start and end must be different")
            radius = self._radius(self.args[2])
            turns = self._turn_count(axis_length)
            radial = self._radial_direction(ctx, start, end)
            curve, deviation = coil.make_curve(
                start, end, radial, radius, radius, turns,
                self._reverse_twist, _document_tolerance(ctx))
        except ValueError as exc:
            return f"Helix: {exc}", True
        _, display_name = ctx.add_geometry(curve, "Helix")
        pitch = axis_length / turns
        return (f"Created {display_name} ({turns:g} turns, pitch {pitch:g}, "
                f"NURBS deviation {deviation:.3g})"), True


class SpiralCmd(_StickySizeMixin, _CoilCmd):
    """Draw a smooth variable-radius spiral around a straight axis."""

    name = "Spiral"
    description = "Draw a variable-radius spiral NURBS curve."
    options = (opts.Option("Flat", opts.MODE,
                           help="Draw a planar spiral curve in the axis-start plane."),) \
        + _CoilCmd.options
    # MEASURED (research_preview_14 P55): "Start of axis ( Flat  Vertical  AroundCurve )",
    # "End of axis", "First radius and start point <1.000>", "Second radius <0.000>", both
    # sticky. A typed FIRST radius stays locked until Enter or a pick; a typed SECOND radius
    # completes.
    steps = [("Start of axis:", POINT), ("End of axis:", POINT),
             ("First radius and start point:", DISTANCE), ("Second radius:", DISTANCE)]
    _radius_step_index = 2
    _radius_step_indices = (2, 3)
    _radius_prompt = "First radius and start point:"
    _size_step_index = 2
    _sticky_defaults = {2: 1.0, 3: 0.0}

    def _sticky_on(self):
        # a 0 second radius is a default the user can see but Enter must not build
        return super()._sticky_on() and not (self.step_index == 3
                                             and self._sticky_store()[3] <= 0.0)

    @property
    def prompt(self):
        base = super().prompt
        if self.step_index == 3 and self.pending_option is None and base.startswith("Second radius") \
                and "<" not in base.split("(")[0]:
            value = self._sticky_store()[3]
            return f"Second radius <{value:.3f}>" + base[len("Second radius"):]
        return base

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if (self.step_index == 2 and self.pending_option is None and len(self.args) == 2):
            if raw and not (self.options and opts.match(raw, self.options, self.opt).status
                            != opts.NONE):
                try:
                    value = parse_number(raw)
                except ValueError:
                    value = None
                if value is not None:
                    self._locked_radius = value
                    return None, False
            if getattr(self, "_locked_radius", None) is not None and (
                    not raw or "," in raw or "<" in raw):
                text = repr(self._locked_radius)
                self._locked_radius = None
        return super()._feed(text, ctx)

    def __init__(self):
        super().__init__()
        self._flat = False

    def on_mode(self, name, ctx):
        if name == "Flat":
            self._flat = True
            return "Spiral: Flat=Yes -- the curve remains in the axis-start plane", False
        return super().on_mode(name, ctx)


    def _parse_distance(self, text, ctx):
        if self._vertical and self.step_index == 1:
            return super()._parse_distance(text, ctx)
        if self.step_index in self._radius_step_indices:
            stripped = (text or "").strip()
            try:
                return parse_number(stripped)
            except ValueError:
                start, end = self._axis_end(ctx)
                base = start if self.step_index == 2 else end
                point = parse_point(stripped, self._active_cplane(ctx), base)
                return math.dist(base, point)
        return super()._parse_distance(text, ctx)

    def execute(self, ctx):
        try:
            start, end = self._axis_end(ctx)
            axis_length = math.dist(start, end)
            if axis_length <= 1e-12:
                raise ValueError("axis start and end must be different")
            if self._flat and self._coil_mode == "Pitch":
                raise ValueError("Pitch cannot take precedence in Flat mode")
            radius0 = self._radius(self.args[2])
            radius1 = self._radius(self.args[3])
            turns = self._turn_count(axis_length)
            radial = self._radial_direction(ctx, start, end)
            curve, deviation = coil.make_curve(
                start, end, radial, radius0, radius1, turns,
                self._reverse_twist, _document_tolerance(ctx), self._flat)
        except ValueError as exc:
            return f"Spiral: {exc}", True
        _, display_name = ctx.add_geometry(curve, "Spiral")
        pitch = 0.0 if self._flat else axis_length / turns
        return (f"Created {display_name} ({turns:g} turns, pitch {pitch:g}, "
                f"radii {radius0:g}->{radius1:g}, NURBS deviation {deviation:.3g})"), True


def extrude_planar_profile_regions(profiles, distance, direction, both=False, solid=True):
    """Build exact prisms from two or more closed coplanar profile curves.

    This is the shared behavior backend for command and UI extrusion. ``PlanarSrf``
    validates the curves and classifies nested loops as holes while preserving disjoint
    outer loops as separate regions; each returned region is then extruded exactly.

    Raises:
        ValueError: If fewer than two profiles are supplied, the profiles do not define
            planar regions, or either exact bridge operation refuses the geometry.
    """
    profiles = list(profiles)
    if len(profiles) < 2:
        raise ValueError("multiple-profile extrusion needs at least two profile curves")
    faces = bridge.make_planar_srf(profiles)
    if not faces:
        raise ValueError("the profiles did not define a planar region")
    return [bridge.make_extrude_srf(face, distance, direction=direction,
                                    both=bool(both), solid=bool(solid))
            for face in faces]


class ExtrudeCmd(_StickyDistanceMixin, Command):
    # the reference CAD application has no `Extrude` -- the real name for extruding a *curve*
    # profile is `ExtrudeCrv` (surfaces use `ExtrudeSrf`). We extrude a closed
    # curve, so the honest name is ExtrudeCrv (confirmed: the command behavior
    # exists, the command behavior does not).
    name = "ExtrudeCrv"
    description = "Extrude a closed curve. Select it, then type a distance or pick a point."
    # The profile step accepts the whole current selection or one or more typed object
    # names. The distance accepts a typed number or a picked location. Planar profiles
    # default to their plane normal; non-planar profiles use the active CPlane Z direction.
    # Picked distances are signed projections along that extrusion direction.
    #
    # OURS (owner-required extension to the singular help-page wording): two or more
    # closed coplanar profiles are treated as planar regions. Nested loops become holes
    # and disjoint outer loops become separate exact solids.
    # MEASURED (research_preview_14 P36): "Select curves to Extrude", "Extrusion distance
    # <0> ( Output=Surface  Direction  BothSides=No  Solid=No ... )".
    steps = [("Select curves to Extrude:", OBJECTS_MULTI), ("Extrusion distance:", DISTANCE)]
    # `preselect_names` is the selection-fill count (1: the whole profile set, the
    # first step, is filled from the selection; the distance is still prompted).
    # ``Command._take_objects_selection`` fills this leading OBJECTS_MULTI step from a
    # preselection while leaving the distance step to be prompted normally.
    preselect_names = 1
    # the documented options (the command behavior). Built: Direction, BothSides,
    # Solid, DeleteInput. Declared, reported not implemented: Output=SubD, ToBoundary,
    # SplitAtTangents, SetBasePoint. Solid defaults No -- MEASURED twice (research_all_13
    # and research_preview_14 P36: "Solid=No" in the typed command's prompt); the reference
    # menu macro `!_ExtrudeCrv _Pause _Solid=_Yes` sets it on explicitly.
    options = (
        opts.Option("Output", opts.ENUM, choices=("Surface", "SubD"), default="Surface",
                    help="Create an extrusion or a surface object, or a SubD object."),
        opts.Option("Direction", opts.MODE, help="Pick two points to define the extruding "
                                                 "direction."),
        opts.Option("BothSides", opts.TOGGLE, default=False,
                    help="Extrude towards the both sides of the input curve."),
        opts.Option("Solid", opts.TOGGLE, default=False,
                    help="Cap a closed planar profile into a closed polysurface."),
        opts.Option("DeleteInput", opts.TOGGLE, default=False,
                    help="Controls whether the original geometry is deleted."),
        opts.Option("ToBoundary", opts.MODE, help="Extrude the object to a boundary surface."),
        opts.Option("SplitAtTangents", opts.TOGGLE, default=False,
                    help="Tangent points become joined edges, not kinks."),
        opts.Option("SetBasePoint", opts.MODE,
                    help="Specify the first point for a picked extrusion distance."),
    )
    #: Direction mode uses two points to define the extrusion axis.
    DIRECTION_STEPS = [("Base point for direction:", POINT),
                       ("Second point for direction:", POINT)]

    def _feed_objects_multi_step(self, text, ctx):
        """Preserve the established typed one-profile flow on the new set step.

        A selected set uses the normal ``OBJECTS_MULTI`` Enter-confirm path. An explicitly
        typed object name remains the old one-profile shortcut and advances immediately to
        Distance, so ``ExtrudeCrv`` → ``Profile`` → ``5`` does not gain an extra Enter.
        Multi-profile regions are selected together, which is the owner-required workflow.
        """
        if text and not self._pending_multi:
            self.args.append([text])
            self.step_index += 1
            return None, False
        return super()._feed_objects_multi_step(text, ctx)

    def on_mode(self, name, ctx):
        """`Direction` -- "Pick two points to define the extruding direction", asked
        at the distance prompt (the curve is chosen first). Inserts the two picks before
        the distance."""
        if name == "Direction":
            if self.step_index < 1 or getattr(self, "_dir_mode", False):
                return f"{self.name}: choose the curve first", False
            self.steps = (self.steps[:self.step_index] + list(self.DIRECTION_STEPS)
                          + self.steps[self.step_index:])
            self._dir_mode = True
            return f"{self.name}: pick the extrusion direction", False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "SplitAtTangents" and value:
            return f"{self.name}: the SplitAtTangents option is not implemented yet", False
        if option.name == "Output" and value == "SubD":
            self.opt["Output"] = "Surface"
            return f"{self.name}: Output=SubD is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def _picked_direction(self):
        if not getattr(self, "_dir_mode", False) or len(self.args) < 3:
            return None
        a, b = self.args[1], self.args[2]
        v = tuple(b[i] - a[i] for i in range(3))
        return cplane.normalize(v)

    def _uses_options(self, geom):
        """True when the plain (rhino3dm Extrusion) route cannot express the request."""
        return (bool(self.opt.get("BothSides")) or not self.opt.get("Solid", True)
                or getattr(self, "_dir_mode", False) or not geom.IsClosed)

    def _profile_names(self, ctx=None):
        """The ordered profile names from the set-step, with old one-name args tolerated.

        ``OBJECTS_MULTI`` stores a list in ``args[0]``. Accepting the former scalar shape
        here keeps direct callers and the one-profile geometry path backward-compatible;
        normal command input always uses the list shape.
        """
        raw = self.args[0] if self.args else None
        if isinstance(raw, (list, tuple)):
            names = [str(name) for name in raw if name]
        else:
            names = [str(raw)] if raw else []
        if not names and ctx is not None:
            try:
                names = [o.name for o in (ctx.selected_objects() or [])]
            except Exception:  # noqa: BLE001
                names = []
        return names

    def _extrude_curve(self, ctx):
        """The first curve, used as the common direction/distance reference."""
        names = self._profile_names(ctx)
        geom = ctx.geom_by_name(names[0]) if names else None
        return geom if isinstance(geom, r3.Curve) else None

    @staticmethod
    def default_direction(geom, plane):
        """The unit direction a curve extrudes along when none was picked -- the ONE rule
        the distance, the click constraint and the preview all use.

        Planar curves use their profile-plane normal; non-planar curves use the active
        CPlane Z direction. Because a profile normal inherits curve winding, it is oriented
        toward the positive CPlane Z side so positive distances consistently extrude away
        from the construction plane. A profile perpendicular to the CPlane keeps its own
        normal because neither orientation has a positive Z side.
        """
        z = None
        if plane is not None:
            z = getattr(plane, "normal", None) or getattr(plane, "zaxis", None)
        z = tuple(float(c) for c in z) if z is not None else (0.0, 0.0, 1.0)
        normal = bridge.profile_normal(geom) if geom is not None and geom.IsClosed else None
        if normal is None:
            return z
        if sum(normal[i] * z[i] for i in range(3)) < -1e-9:
            normal = tuple(-float(c) for c in normal)
        return tuple(float(c) for c in normal)

    def extrusion_direction(self, ctx):
        """The unit direction the extrusion runs along (`default_direction`, or the
        two picked points in Direction mode)."""
        picked = self._picked_direction()
        if picked is not None:
            return picked
        return self.default_direction(self._extrude_curve(ctx), self._active_cplane(ctx))

    def axis_constraint(self, plane, resolve=None):
        """constrain the distance click to the EXTRUSION AXIS.

        Without this the click is flattened onto the pane's CPlane, which in Perspective is
        world XY -- so an XY profile's height along Z was always 0 and nothing
        extruded. The axis runs along the profile normal through a point on the curve, which is
        exactly what `_parse_distance` then measures against, so the pick and the arithmetic
        agree by construction.
        """
        dir_mode = getattr(self, "_dir_mode", False)
        if len(self.args) != (3 if dir_mode else 1):
            return None                     # not the distance step
        geom = None
        if resolve is not None:
            names = self._profile_names()
            geom = resolve(names[0]) if names else None
        if geom is None:
            return None
        # Direction mode: the axis is the PICKED direction, which is what `_parse_distance`
        # measures along -- without this the click fell back onto the CPlane (the bug).
        normal = (self._picked_direction() if dir_mode
                  else self.default_direction(geom, plane))
        if normal is None:
            return None
        start = geom.PointAtStart
        return ((start.X, start.Y, start.Z), normal)

    def _parse_distance(self, text, ctx):
        """ExtrudeCrv's distance: a typed number, or a PICKED location.

        The reference is the CURVE, not the last picked point, so the base
        `_parse_distance` (which measures from `_distance_base`) does not apply -- there
        is no earlier POINT step here at all, which is exactly why a picked location did
        nothing before. This mirrors `OffsetCmd`, which had the same shape of problem.

        The distance is the SIGNED projection of the picked point onto the extrusion
        direction, from a point on the curve. Signed because
        the command behavior says "The extrusion preview direction in the viewport
        defines the positive direction for distance input" -- picking on the far side must
        extrude the other way, not take an absolute value.
        """
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass                       # not a number -> it should be a location
        geom = self._extrude_curve(ctx)
        if geom is None:
            raise ValueError("there is no curve to extrude to that point")
        point = parse_point(stripped, self._active_cplane(ctx), self._last_point())
        start = geom.PointAtStart
        direction = self.extrusion_direction(ctx)
        return sum((point[i] - (start.X, start.Y, start.Z)[i]) * direction[i]
                   for i in range(3))

    @staticmethod
    def _delete_profiles(ctx, names):
        """Delete every source profile after all outputs have been built successfully."""
        for name in names:
            ob = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)

    def execute(self, ctx: NarwhalContext):
        names = self._profile_names(ctx)
        height = self.args[-1]
        if not names:
            return f"{self.name}: select curves to extrude", True

        profiles = []
        invalid = []
        for name in names:
            geom = ctx.geom_by_name(name)
            if isinstance(geom, r3.Curve):
                profiles.append(geom)
            else:
                invalid.append(name)
        if invalid:
            if len(names) == 1:
                return f"No curve named {names[0]!r}", True
            return (f"{self.name}: multiple-profile regions require only closed coplanar "
                    f"curves; unsupported or missing: {', '.join(invalid)}"), True

        if len(profiles) == 1:
            # Preserve the established one-profile behavior exactly: open curves, taper,
            # BothSides / Solid=No / Direction, and the plain native Extrusion route.
            geom = profiles[0]
            if self._uses_options(geom) or self._draft() is not None:
                # BothSides / Solid=No / Direction / an open curve / a taper -- exact OCCT
                try:
                    result = bridge.extrude_curve(
                        geom, height, direction=self.extrusion_direction(ctx),
                        both=bool(self.opt.get("BothSides")),
                        solid=bool(self.opt.get("Solid", True)), draft_deg=self._draft(),
                        corners=self.opt.get("Corners", "Sharp"),
                        flip=bool(self.opt.get("FlipAngle")))
                except ValueError as e:
                    return f"{self.name}: {e}", True
            else:
                # `make_extrusion` runs along the curve's OWN normal; a positive
                # distance runs along `extrusion_direction`, so flip the height when the two
                # point opposite ways (a clockwise curve)
                own = bridge.profile_normal(geom)
                want = self.extrusion_direction(ctx)
                if own is not None and sum(own[i] * want[i] for i in range(3)) < 0:
                    height_ = -float(height)
                else:
                    height_ = float(height)
                result = bridge.make_extrusion(geom, height_)
            results = [result]
        else:
            if self._draft() is not None:
                return (f"{self.name}: tapered extrusion of multiple profiles is not "
                        f"implemented yet"), True
            try:
                results = extrude_planar_profile_regions(
                    profiles, height, self.extrusion_direction(ctx),
                    both=bool(self.opt.get("BothSides")),
                    solid=bool(self.opt.get("Solid", True)))
            except ValueError as e:
                return f"{self.name}: multiple-profile region refused: {e}", True

        if self.opt.get("DeleteInput"):
            self._delete_profiles(ctx, names)
        made = [ctx.add_geometry(result, "Extrusion")[1] for result in results]
        if len(made) == 1:
            return f"Created {made[0]} (h={height:g})", True
        return f"Created {len(made)} extrusions: {', '.join(made)} (h={height:g})", True

    def _draft(self):
        return None


class ExtrudeCrvTaperedCmd(ExtrudeCmd):
    """`ExtrudeCrvTapered` -- the command behavior. "creates a surface
    by tracing the path of a curve in a straight line tapering in or out at a specified
    draft angle"; options Direction, DraftAngle, Solid, Corners (Sharp / Round / Smooth),
    DeleteInput, FlipAngle, ToBoundary, SetBasePoint, SplitAtTangents. Shares ExtrudeCrv's
    code. Exact: OCCT MakeDraft (`occt_backend.make_extrusion_ex`). DraftAngle
    defaults 5 deg (no default is documented anywhere we hold); positive tapers INWARD;
    Corners=Smooth (G2 blends) is not implemented and says so."""
    name = "ExtrudeCrvTapered"
    description = "Extrude a curve straight, tapering at a draft angle."
    _sticky_first = 1.0          # MEASURED (P37): "Extrusion distance <1>" on first use
    options = tuple(o for o in ExtrudeCmd.options
                    if o.name not in ("BothSides", "Output")) + (
        opts.Option("DraftAngle", opts.VALUE, default=5.0, value_kind="number",
                    help="The draft angle for the taper, in degrees."),
        opts.Option("Corners", opts.ENUM, choices=("Sharp", "Round", "Smooth"),
                    default="Sharp", help="How corner continuity is handled."),
        opts.Option("FlipAngle", opts.TOGGLE, default=False,
                    help="Toggles the draft angle direction."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "Corners" and value == "Smooth":
            self.opt["Corners"] = "Sharp"
            return (f"{self.name}: Corners=Smooth (G2 blends) is not implemented yet -- "
                    f"Sharp and Round are"), False
        return super().on_option_set(option, value, ctx)

    def _draft(self):
        return float(self.opt.get("DraftAngle", 5.0))


# ---------------------------------------------------------------------------
# PlanarSrf, ExtrudeSrf, EdgeSrf -- exact surfaces from curves / a surface
# ---------------------------------------------------------------------------

def _curves_named(ctx, names):
    out, bad = [], []
    for ref in names or []:
        try:
            out.append((ref, curve_inputs.resolve(ctx, ref)))
        except curve_inputs.CurveInputError:
            if not isinstance(ref, str):
                # Components can become stale between acceptance and execution.
                # Never ignore one (PlanarSrf) or retry it as its whole parent.
                raise
            bad.append(ref)  # preserve the established whole-name diagnostics
    return out, bad


class CurveThroughPolylineCmd(Command):
    """Create control-point or interpolated curves from polyline vertices."""

    name = "CurveThroughPolyline"
    description = "Create curves from the vertex points of selected polylines."
    steps = [("Select polylines:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("Degree", opts.VALUE, default=3, value_kind="number",
                    help="Specifies the degree of the output curve."),
        opts.Option("CurveType", opts.ENUM,
                    choices=("ControlPoint", "Interpolated"), default="ControlPoint",
                    help="Choose whether controls or the curve pass through vertices."),
        opts.Option("Knots", opts.ENUM, choices=("Uniform", "Chord", "SqrtChrd"),
                    default="Chord", help="Parameterization for interpolated curves."),
        opts.Option("DeleteInput", opts.TOGGLE, default=False,
                    help="Delete or retain the source polylines."),
        opts.Option("SubCrv", opts.MODE, help="Use part of a curve as input."),
    )

    def on_mode(self, name, ctx):
        if name == "SubCrv":
            return "CurveThroughPolyline: SubCrv is not implemented yet", False
        return super().on_mode(name, ctx)

    def execute(self, ctx):
        names = list(self.args[0]) if self.args else []
        try:
            degree_value = float(self.opt.get("Degree", 3))
            degree = int(degree_value)
            if degree != degree_value or degree < 1:
                raise ValueError("Degree must be a whole number greater than zero")
        except (TypeError, ValueError) as exc:
            return f"CurveThroughPolyline: {exc}", True
        made = []
        for name in names:
            geom = ctx.geom_by_name(name)
            vertices = bridge._polyline_vertices(geom) if isinstance(geom, r3.Curve) else None
            if vertices is None or len(vertices) < 2:
                return f"CurveThroughPolyline: {name!r} is not a polyline", True
            try:
                if self.opt.get("CurveType") == "Interpolated":
                    if len(vertices) > 2 and math.dist(vertices[0], vertices[-1]) <= 1e-12:
                        raise ValueError("closed interpolated polylines are not implemented yet")
                    result = bridge.make_interpolated_curve(
                        vertices, degree=degree, style=self.opt.get("Knots", "Chord"))
                else:
                    result = bridge.make_polyline(vertices, degree=degree)
            except ValueError as exc:
                return f"CurveThroughPolyline: {exc}", True
            made.append(result)
        if not made:
            return "CurveThroughPolyline: select polylines", True
        if self.opt.get("DeleteInput"):
            ctx.note_change(self.name)
            for name in names:
                obj = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
                if obj is not None:
                    _remove_object_from_scene(obj)
                object_id = ctx.model.object_id_by_name(name)
                if object_id is not None:
                    ctx.model.remove_object(object_id)
        displays = [ctx.add_geometry(curve, "Curve")[1] for curve in made]
        return f"Created {len(displays)} curve{'s' if len(displays) != 1 else ''}", True


class PlanarSrfCmd(Command):
    """`PlanarSrf` -- the command behavior. "creates a planar surface from a set
    of planar curves that define the surface edges"; "If a curve is wholly within another
    curve then it will be treated as a hole boundary"; prompt in the command UI "Select planar
    curves to build surface". Exact: `bridge.make_planar_srf`."""
    name = "PlanarSrf"
    description = "Create planar surfaces bounded by closed planar curves (inner curves are holes)."
    steps = [("Select planar curves to build surface:", OBJECTS_MULTI)]
    accepts_curve_subobjects = True
    accepts_subobjects = True
    preselect_names = 1
    cancel_on_empty_set = True

    def execute(self, ctx: NarwhalContext):
        try:
            curves, bad = _curves_named(ctx, self.args[0] if self.args else [])
        except curve_inputs.CurveInputError as exc:
            return f"PlanarSrf: {exc}", True
        if not curves:
            return "PlanarSrf: select planar curves", True
        if not any(g.IsClosed for _n, g in curves):
            # MEASURED (research_all_13 #106): an open curve alone.
            return "No faces were made. Curve must be closed and planar.", True
        try:
            pieces = bridge.make_planar_srf([g for _n, g in curves])
        except ValueError as e:
            return f"PlanarSrf: {e}", True
        for g in pieces:
            ctx.add_geometry(g, "Surface")
        # MEASURED (#106): a successful PlanarSrf prints nothing.
        if bad:
            return f"Not curves, ignored: {', '.join(bad)}", True
        return None, True


class EdgeSrfCmd(Command):
    """`EdgeSrf` -- the command behavior. "creates a surface from two, three, or
    four selected curves. The edges of the created surface can precisely match the input
    curves"; prompt "Select 2, 3, or 4 open curves". Exact Coons patch:
    `bridge.make_edge_srf`."""
    name = "EdgeSrf"
    description = "Create a surface from 2, 3 or 4 edge curves."
    steps = [("Select 2, 3, or 4 open curves:", OBJECTS_MULTI)]
    accepts_curve_subobjects = True
    accepts_subobjects = True
    preselect_names = 1
    cancel_on_empty_set = True

    def execute(self, ctx: NarwhalContext):
        try:
            curves, bad = _curves_named(ctx, self.args[0] if self.args else [])
        except curve_inputs.CurveInputError as exc:
            return f"EdgeSrf: {exc}", True
        if bad:
            return f"EdgeSrf: only curves can be edges ({', '.join(bad)})", True
        try:
            geom = bridge.make_edge_srf([g for _n, g in curves])
        except ValueError as e:
            return f"EdgeSrf: {e}", True
        _, display = ctx.add_geometry(geom, "Surface")
        return f"Created {display} from {len(curves)} edge curves", True


class CurveThroughPtCmd(Command):
    """Fit one control-point or interpolated curve through selected point objects."""

    name = "CurveThroughPt"
    description = "Create a curve from selected point objects, ignoring duplicates."
    steps = [("Select points:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = tuple(option for option in CurveThroughPolylineCmd.options
                    if option.name in ("Degree", "CurveType", "Knots"))

    def execute(self, ctx):
        names = list(self.args[0]) if self.args else []
        points, seen = [], set()
        for name in names:
            geom = ctx.geom_by_name(name)
            if not isinstance(geom, r3.Point):
                return f"CurveThroughPt: {name!r} is not a point object", True
            point = (float(geom.Location.X), float(geom.Location.Y), float(geom.Location.Z))
            if point not in seen:
                seen.add(point)
                points.append(point)
        if len(points) < 2:
            return "CurveThroughPt: at least two distinct point objects are required", True
        degree_value = float(self.opt.get("Degree", 3))
        degree = int(degree_value)
        if degree != degree_value or degree < 1:
            return "CurveThroughPt: Degree must be a whole number greater than zero", True
        try:
            if self.opt.get("CurveType") == "Interpolated":
                curve = bridge.make_interpolated_curve(
                    points, degree=degree, style=self.opt.get("Knots", "Chord"))
            else:
                curve = bridge.make_polyline(points, degree=degree)
        except ValueError as exc:
            return f"CurveThroughPt: {exc}", True
        _, display = ctx.add_geometry(curve, "Curve")
        return f"Created {display} from {len(points)} distinct points", True


class CurveThroughSrfControlPtCmd(Command):
    """Extract U/V surface control polygons as real curves."""
    name="CurveThroughSrfControlPt";description="Fit curves through a surface control polygon."
    # MEASURED (research_preview_14 P33): "Select point to draw curve through ( CurveType=
    # Interpolated  Direction=U  Toggle )" -- Interpolated is the default.
    steps=[("Select surface:",OBJECTS),("Select point to draw curve through:",POINT)];preselect_names=1
    options=(opts.Option("Degree",opts.VALUE,default=3,value_kind="number",help="Output curve degree."),
             opts.Option("CurveType",opts.ENUM,choices=("ControlPoint","Interpolated"),default="Interpolated",help="Control or interpolated curve."),
             opts.Option("Direction",opts.ENUM,choices=("U","V","Both"),default="U",help="Surface control-net direction."),
             opts.Option("Toggle",opts.MODE,help="Toggle between U and V."))
    def on_mode(self,name,ctx):
        if name=="Toggle":
            current=self.opt.get("Direction","U");self.opt["Direction"]="V" if current=="U" else "U"
            return f"CurveThroughSrfControlPt: Direction={self.opt['Direction']}",False
        return super().on_mode(name,ctx)
    def execute(self,ctx):
        name,near=self.args;geom=ctx.geom_by_name(name)
        if isinstance(geom,r3.Brep):
            if len(geom.Faces)!=1:return "CurveThroughSrfControlPt: polysurfaces are not implemented yet",True
            surface=geom.Faces[0].ToNurbsSurface()
        elif isinstance(geom,r3.NurbsSurface):surface=geom
        else:return f"CurveThroughSrfControlPt: {name!r} is not a surface",True
        grid=surface.Points
        def point(u,v):
            cp=grid.GetControlPoint(u,v);w=cp.W or 1.0;return (cp.X/w,cp.Y/w,cp.Z/w)
        iu,iv=min(((u,v) for u in range(grid.CountU) for v in range(grid.CountV)),key=lambda q:math.dist(point(*q),near))
        direction=self.opt.get("Direction","U");rows=[]
        if direction in ("U","Both"):rows.append([point(u,iv) for u in range(grid.CountU)])
        if direction in ("V","Both"):rows.append([point(iu,v) for v in range(grid.CountV)])
        degree_value=float(self.opt.get("Degree",3));degree=int(degree_value)
        if degree!=degree_value or degree<1:return "CurveThroughSrfControlPt: Degree must be a whole number >= 1",True
        made=[]
        try:
            for row in rows:
                curve=(bridge.make_interpolated_curve(row,degree=degree)
                       if self.opt.get("CurveType")=="Interpolated" else bridge.make_polyline(row,degree=degree))
                made.append(ctx.add_geometry(curve,"Curve")[1])
        except ValueError as exc:return f"CurveThroughSrfControlPt: {exc}",True
        return f"Created {len(made)} surface-control curve{'s' if len(made)!=1 else ''}",True


class TweenCurvesCmd(Command):
    """Create compatible control-point tween curves between two inputs.

    MEASURED (research_all_13 note 175): with two curves PRESELECTED the reference CAD application skips the
    picks and asks "Press Enter to accept options ( Number=1  OutputLayer=CurrentLayer
    MatchMethod=None  Flip ):"; Enter builds the tween. `MatchMethod=None` is the
    control-point matching (the help's first MatchMethod entry), and SampleNumber is not
    in that prompt -- OURS: it is offered once MatchMethod=SamplePoints, which it governs.
    """

    name = "TweenCurves"
    description = "Create intermediate curves between two compatible curves."
    ACCEPT_PROMPT = "Press Enter to accept options:"
    steps = [("Select first curve:", OBJECTS), ("Select second curve:", OBJECTS),
             (ACCEPT_PROMPT, TEXT)]
    preselect_names = 2
    _SAMPLE_NUMBER = opts.Option("SampleNumber", opts.VALUE, default=100, value_kind="number",
                                 help="Sample count for SamplePoints matching.")
    options = (
        opts.Option("Number", opts.VALUE, default=1, value_kind="number",
                    help="Number of curves to create between the inputs."),
        opts.Option("OutputLayer", opts.ENUM,
                    choices=("CurrentLayer", "StartCrv", "EndCrv"),
                    default="CurrentLayer", help="Layer for the tween curves."),
        opts.Option("MatchMethod", opts.ENUM,
                    choices=("None", "Refit", "SamplePoints"),
                    default="None", help="Method for matching the curves."),
        opts.Option("Flip", opts.MODE, help="Reverse the second curve direction."),
    )

    def __init__(self):
        super().__init__()
        self._flip = False
        self.opt.setdefault("SampleNumber", 100)

    def on_option_set(self, option, value, ctx):
        if option.name == "MatchMethod":
            base = tuple(o for o in self.options if o.name != "SampleNumber")
            self.options = base + ((self._SAMPLE_NUMBER,) if value == "SamplePoints" else ())
        return super().on_option_set(option, value, ctx)

    def _enter_default(self, ctx):
        if self.step_index == 2:
            return ""
        return None

    def _feed(self, text, ctx):
        stripped = (text or "").strip()
        if (self.step_index == 2 and stripped and self.pending_option is None
                and opts.match(stripped, self.options, self.opt).status == opts.NONE):
            return f"{self.name}: press Enter to accept options", False
        return super()._feed(text, ctx)

    def on_mode(self, name, ctx):
        if name == "Flip":
            self._flip = not self._flip
            return f"TweenCurves: Flip={'Yes' if self._flip else 'No'}", False
        return super().on_mode(name, ctx)

    def _layer(self, ctx, source_name):
        object_id = ctx.model.object_id_by_name(source_name)
        item = ctx.model.file.Objects.FindId(object_id) if object_id is not None else None
        return int(item.Attributes.LayerIndex) if item is not None else None

    def execute(self, ctx):
        first_name, second_name = self.args[:2]
        first, second = ctx.geom_by_name(first_name), ctx.geom_by_name(second_name)
        if not isinstance(first, r3.Curve) or not isinstance(second, r3.Curve):
            return "TweenCurves: select two curves", True
        method = self.opt.get("MatchMethod", "None")
        if method == "Refit":
            return "TweenCurves: MatchMethod=Refit is not implemented yet", True
        number_value = float(self.opt.get("Number", 1))
        number = int(number_value)
        if number != number_value or number < 1:
            return "TweenCurves: Number must be a whole number >= 1", True
        try:
            if method == "SamplePoints":
                sample_value=float(self.opt.get("SampleNumber",100));sample_number=int(sample_value)
                if sample_number!=sample_value:raise ValueError("SampleNumber must be a whole number")
                curves=tween.sample_point_tweens(first,second,number,sample_number,flip=self._flip)
            else:
                curves = tween.compatible_tweens(first, second, number, flip=self._flip)
        except ValueError as exc:
            return f"TweenCurves: {exc}", True
        output_layer = self.opt.get("OutputLayer", "CurrentLayer")
        layer = (self._layer(ctx, first_name) if output_layer == "StartCrv" else
                 self._layer(ctx, second_name) if output_layer == "EndCrv" else None)
        displays = [ctx.add_geometry(curve, "Tween", layer)[1] for curve in curves]
        return f"Created {len(displays)} tween curve{'s' if len(displays) != 1 else ''}", True


class ExtrudeSrfCmd(_StickyDistanceMixin, Command):
    """`ExtrudeSrf` -- the command behavior. "creates a solid by tracing the path
    of the surface edges in a straight line"; "A planar surface extrudes vertically to the
    surface plane by default. If the surface is not planar, the default extrusion direction
    is the z direction of the active CPlane"; prompt "Extrusion distance". Built options:
    BothSides, Solid, DeleteInput. Direction / ToBoundary / SplitAtTangents are declared and
    report not implemented. Solid defaults No, DeleteInput No (MEASURED: research_all_13
    and research_preview_14 P38 both show "Solid=No  DeleteInput=No")."""
    name = "ExtrudeSrf"
    description = "Extrude a surface straight. Select it, then a distance."
    steps = [("Select surfaces to Extrude:", OBJECTS), ("Extrusion distance:", DISTANCE)]
    preselect_names = 1
    options = (
        opts.Option("Direction", opts.MODE, help="Two points establish the direction angle."),
        opts.Option("BothSides", opts.TOGGLE, default=False,
                    help="Draws the object on both sides of the start point."),
        opts.Option("Solid", opts.TOGGLE, default=False,
                    help="Caps the ends and joins them into a closed polysurface."),
        opts.Option("DeleteInput", opts.TOGGLE, default=False,
                    help="Controls whether the original geometry is deleted."),
        opts.Option("ToBoundary", opts.MODE, help="Extrude the object to a boundary surface."),
        opts.Option("SplitAtTangents", opts.TOGGLE, default=False,
                    help="Tangent boundary points become joined edges, not kinks."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "SplitAtTangents":
            return "ExtrudeSrf: the SplitAtTangents option is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def _surface(self, ctx):
        name = self.args[0] if self.args else None
        return name, ctx.geom_by_name(name) if name else None

    def _direction(self, ctx, geom):
        return self.direction_for(geom, self._active_cplane(ctx))

    @staticmethod
    def direction_for(geom, plane):
        """The extrusion direction: a planar surface's normal, else the CPlane's z."""
        occt = bridge._occt()
        face = bridge.planar_face_shape(geom) if occt is not None else None
        if face is not None:
            return tuple(occt.face_plane(face)[3])
        z = getattr(plane, "zaxis", None) if plane is not None else None
        return tuple(float(c) for c in z) if z is not None else (0.0, 0.0, 1.0)

    def axis_constraint(self, plane, resolve=None):
        """Constrain the distance click to the extrusion axis through the first vertex.

        ``_parse_distance`` measures from the same point. Without this constraint, a
        perspective click can land on the CPlane and produce zero extrusion distance.
        """
        if len(self.args) != 1 or resolve is None:
            return None                     # not the distance step
        geom = resolve(self.args[0])
        if not isinstance(geom, r3.Brep) or len(geom.Vertices) == 0:
            return None
        v = geom.Vertices[0].Location
        return ((v.X, v.Y, v.Z), self.direction_for(geom, plane))

    def _parse_distance(self, text, ctx):
        """A typed number, or a PICKED location: its signed distance along the extrusion
        direction from the surface's first vertex (as ExtrudeCrv)."""
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        _n, geom = self._surface(ctx)
        if not isinstance(geom, r3.Brep) or len(geom.Vertices) == 0:
            raise ValueError("there is no surface to extrude to that point")
        point = parse_point(stripped, self._active_cplane(ctx), self._last_point())
        v = geom.Vertices[0].Location
        d = self._direction(ctx, geom)
        return sum((point[i] - (v.X, v.Y, v.Z)[i]) * d[i] for i in range(3))

    def execute(self, ctx: NarwhalContext):
        name, geom = self._surface(ctx)
        distance = self.args[1] if len(self.args) > 1 else None
        if not isinstance(geom, (r3.Brep, r3.Extrusion)):
            return f"ExtrudeSrf: {name!r} is not a surface", True
        try:
            solid = bridge.make_extrude_srf(
                geom, distance, direction=self._direction(ctx, geom),
                both=bool(self.opt.get("BothSides")), solid=bool(self.opt.get("Solid", True)))
        except ValueError as e:
            return f"ExtrudeSrf: {e}", True
        if self.opt.get("DeleteInput"):
            ob = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)
        _, display = ctx.add_geometry(solid, "Extrusion")
        return f"Created {display} (d={distance:g})", True


# ---------------------------------------------------------------------------
# Shell and OffsetSrf -- exact (OCCT MakeThickSolid)
# ---------------------------------------------------------------------------

class ShellCmd(Command):
    """`Shell` -- the command behavior "creates a hollowed out shell from
    a solid"; "These surfaces are removed and the remainder is offset inward"; prompt
    in the command UI "Select faces to remove from closed polysurface. Leave at least one face
    unselected"; options Thickness, DeleteInput (built), EditThickness (declared, not built:
    it needs History). the faces are PICKED as points on them (no sub-object click
    here), each pick taking the face nearest it; a pick on an edge is refused. The default
    Thickness is 1 (- the help's figure uses 20, not a documented default). The input
    solid is replaced unless DeleteInput=No."""
    name = "Shell"
    description = "Hollow a solid to a wall thickness, removing the picked faces."
    steps = [("Select the solid to shell:", OBJECTS),
             ("Select faces to remove from closed polysurface. Leave at least one face "
              "unselected. Press Enter when done:", POINT)]
    preselect_names = 1
    options = (
        opts.Option("Thickness", opts.VALUE, default=1.0, value_kind="number",
                    help="Specify the thickness for the shell."),
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Specify if the input object will be deleted."),
        opts.Option("EditThickness", opts.MODE,
                    help="Change a shelled object's thickness (needs History)."),
    )

    def __init__(self):
        super().__init__()
        self._picks = []

    def _feed(self, text, ctx):
        if self.step_index == 1 and self.pending_option is None:
            raw = (text or "").strip()
            if not raw:
                if not self._picks:
                    return "Shell: pick at least one face to remove", False
                return self.execute(ctx)
            is_option = (not self._numeric_input_wins(raw)
                         and opts.match(raw, self.options, self.opt).status != opts.NONE)
            if not is_option:
                try:
                    point = self._parse(POINT, raw, ctx)
                except ValueError as e:
                    return str(e), False
                self._picks.append(point)
                return f"{len(self._picks)} face(s) picked -- press Enter when done", False
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        name = self.args[0] if self.args else None
        geom = ctx.geom_by_name(name) if name else None
        if geom is None:
            return f"Shell: no object named {name!r}", True
        try:
            out = bridge.shell_occt(geom, self._picks, float(self.opt.get("Thickness", 1.0)))
        except ValueError as e:
            return f"Shell: {e}", True
        if self.opt.get("DeleteInput", True):
            ob = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)
        _, display = ctx.add_geometry(out, "Shell")
        return (f"Created {display} (thickness {_num(self.opt.get('Thickness', 1.0))}, "
                f"{len(self._picks)} face(s) removed)"), True


class OffsetSrfCmd(Command):
    """`OffsetSrf` -- the command behavior. prompts in the command UI: "Select
    surfaces or polysurfaces to offset", "Signed offset distance". builds Solid=Yes
    ("Makes a closed solid from the input and offset surfaces by lofting a ruled surface
    between all of the matching edges") EXACTLY for a single surface -- OCCT
    MakeThickSolidBySimple; "When a plane, torus, sphere, open cylinder, or open cone surface
    is offset, the resulting surface is exact". Solid=No (the offset surface alone) uses the
    existing `offset.surface_offset`. `ThroughPoint` (at the flip prompt) asks for a point
    and offsets by its signed distance from the surface along the normal at the closest
    point, without changing the sticky Distance. Declared, not built: Corner, FlipAll,
    Loose, Tolerance, BothSides. Polysurfaces are refused by name. Solid defaults No
    (the help gives no default), DeleteInput No."""
    name = "OffsetSrf"
    description = "Offset a surface along its normal; Solid=Yes makes the solid between them."
    # MEASURED (research_all_13 #093): with the surface preselected the prompt is "Select
    # object to flip direction. Press Enter when done ( Distance=1 ... Solid=Yes ...
    # DeleteInput=Yes ... )"; Enter offsets at once by the sticky Distance (no distance
    # prompt), giving a closed solid and deleting the input surface. Corner's measured
    # default (Round) is not built, so Sharp stays the default. MEASURED (research_preview_14
    # P48): a number typed at the flip prompt SETS the sticky Distance and keeps the prompt
    # (as Offset does); nothing follows the cursor there. OURS: naming the surface at the
    # flip prompt flips its direction.
    FLIP_PROMPT = "Select object to flip direction. Press Enter when done:"
    steps = [("Select surfaces or polysurfaces to offset:", OBJECTS), (FLIP_PROMPT, TEXT)]
    preselect_names = 1
    _sticky_distance = [1.0]
    options = (
        opts.Option("Distance", opts.VALUE, value_kind="number", default=1.0,
                    help="The offset distance."),
        opts.Option("Solid", opts.TOGGLE, default=True,
                    help="Makes a closed solid from the input and offset surfaces."),
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Controls whether the original geometry is deleted."),
        opts.Option("Corner", opts.ENUM, choices=("Sharp", "Round"), default="Sharp",
                    help="How offset corner continuity is handled."),
        opts.Option("ThroughPoint", opts.MODE, help="Offsets through a picked point."),
        opts.Option("FlipAll", opts.MODE, help="Flips the offset direction of all surfaces."),
        opts.Option("Loose", opts.TOGGLE, default=False,
                    help="The offset keeps the original control point structure."),
        opts.Option("Tolerance", opts.VALUE, default=0.0, value_kind="number",
                    help="The tolerance for the offset surface (0 = default)."),
        opts.Option("BothSides", opts.TOGGLE, default=False,
                    help="Draws the offset on both sides of the original."),
    )

    def __init__(self):
        super().__init__()
        self.opt["Distance"] = OffsetSrfCmd._sticky_distance[0]
        self._flip = False
        self._through = False

    def on_mode(self, name, ctx):
        if name == "ThroughPoint":
            if self.step_index != 1:
                return "OffsetSrf: choose ThroughPoint after selecting the surface", False
            self._through = True
            self.steps[1] = ("Point to offset through:", POINT)
            return None, False
        if name == "FlipAll":
            self._flip = not self._flip
            return None, False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.step_index == 1 and self.pending_option is None and not self._through:
            if not raw:
                self.args.append(None)
                self.step_index += 1
                return self.execute(ctx)
            if self.args and raw.lower() == str(self.args[0]).lower():
                self._flip = not self._flip
                return None, False
            try:
                typed = parse_number(raw)
            except ValueError:
                typed = None
            if typed is not None:
                option = next(o for o in self.options if o.name == "Distance")
                self.opt["Distance"] = typed
                hook = self.on_option_set(option, typed, ctx)
                return hook if hook is not None else (None, False)
        return super()._feed(text, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "Distance":
            try:
                d = float(value)
            except (TypeError, ValueError):
                d = 0.0
            if not d > 0.0:
                self.opt["Distance"] = OffsetSrfCmd._sticky_distance[0]
                return "OffsetSrf: the distance must be positive", False
            OffsetSrfCmd._sticky_distance[0] = d
            return None
        if option.name in ("Corner", "Loose", "Tolerance", "BothSides") and value:
            if option.name == "Corner" and value == "Sharp":
                return super().on_option_set(option, value, ctx)
            self.opt[option.name] = option.default
            return f"OffsetSrf: the {option.name} option is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    @staticmethod
    def _through_distance(geom, point):
        """ThroughPoint: `point`'s signed distance from the surface, along the normal at
        the closest point. Both offset routes take positive along the underlying
        surface's own normal (MEASURED, also on a flipped face), so that normal signs it."""
        from . import nearest

        ns = geom.Faces[0].ToNurbsSurface()
        foot = nearest.closest_point_on_surface(ns, point)
        n = ns.NormalAt(foot.u, foot.v)
        n = cplane.normalize((n.X, n.Y, n.Z))
        return cplane.dot(_sub3(point, foot.point), n) if n is not None else 0.0

    def execute(self, ctx: NarwhalContext):
        name, typed = self.args[0], self.args[1]
        if self._through:
            geom = ctx.geom_by_name(name) if name else None
            if not isinstance(geom, r3.Brep) or not bridge.is_single_surface(geom):
                return f"OffsetSrf: ThroughPoint needs a single surface; {name!r} is not one", True
            distance = self._through_distance(geom, typed)
            if abs(distance) <= _document_tolerance(ctx):
                return "OffsetSrf: the point lies on the surface, so the offset would be zero", True
        elif typed is None:
            distance = float(self.opt.get("Distance", 1.0)) * (-1.0 if self._flip else 1.0)
        else:
            try:
                distance = parse_number(str(typed))
            except ValueError:
                return f"OffsetSrf: {typed!r} is not a distance", True
        geom = ctx.geom_by_name(name) if name else None
        if not isinstance(geom, r3.Brep):
            return f"OffsetSrf: {name!r} is not a surface", True
        if not bridge.is_single_surface(geom):
            return ("OffsetSrf: offsetting POLYSURFACES is not implemented yet -- a single "
                    "surface is"), True
        if self.opt.get("Solid"):
            try:
                out = bridge.offset_surface_solid(geom, distance)
            except ValueError as e:
                return f"OffsetSrf: {e}", True
            base = "Solid"
        else:
            from . import offset as _offset

            res = _offset.surface_offset(geom.Faces[0].ToNurbsSurface(), distance)
            if res.curve is None:
                return f"OffsetSrf: {res.warning or 'the offset failed'}", True
            out = r3.Brep.CreateFromSurface(res.curve)
            base = "Surface"
        if self.opt.get("DeleteInput"):
            ob = ctx.object_by_name(name) if hasattr(ctx, "object_by_name") else None
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)
        _, display = ctx.add_geometry(out, base)
        return f"Created {display} (offset {_num(distance)})", True


class RevolveCmd(Command):
    name = "Revolve"
    description = "Revolve a curve about an axis: axis start, axis end, start angle, angle."
    # MEASURED (research_all_13 #126): "Start of revolve axis:", then "End of revolve axis
    # (Press Enter to use CPlane z-axis direction):", "Start angle <0>" (Enter = 0) and
    # "Revolution angle <360>" (Enter = 360); the profile is kept. Output, DeleteInput,
    # FullCircle, SplitAtTangents and Deformable were listed but not exercised: not built.
    # MEASURED (research_preview_14 P54): the angles take a typed number (30 ADVANCES) or a
    # click -- a click at "Revolution angle" finishes the command; with start 30 the readout
    # at the profile's own direction was -30, so a picked revolution angle is measured from
    # the start angle. Both angle prompts draw the axis and a circle about it through the
    # cursor (seen edge-on in Top as a line symmetric about the axis).
    steps = [("Curve:", "name"), ("Start of revolve axis:", "point"),
             ("End of revolve axis (Press Enter to use CPlane z-axis direction):", "point"),
             ("Start angle <0>:", DISTANCE), ("Revolution angle <360>:", DISTANCE)]
    # the reference CAD application selects first, then runs the command; the selection
    # fallback these commands already tried was unreachable, because an
    # empty answer at a fixed prompt CANCELS (see Command.feed).
    preselect_names = 1

    def _distance_base(self):
        return None if self.step_index >= 3 else super()._distance_base()

    def angle_frame(self, resolve, plane):
        """``(origin, axis, ref)`` unit vectors for picking an angle about the axis, or None.

        DERIVED: the zero direction is from the axis toward the profile (the measured probe
        had the profile on +X, where the readout was 0 -- and `make_revolve`'s start angle
        turns the profile itself); a profile lying on the axis falls back to the CPlane X."""
        if len(self.args) < 3 or not _is_point(self.args[1]) or not _is_point(self.args[2]):
            return None
        origin = tuple(float(v) for v in self.args[1])
        axis = _sub3(self.args[2], origin)
        n = math.sqrt(cplane.dot(axis, axis))
        if n <= 1e-12:
            return None
        axis = tuple(v / n for v in axis)

        def flat(v):
            k = cplane.dot(v, axis)
            w = (v[0] - k * axis[0], v[1] - k * axis[1], v[2] - k * axis[2])
            m = math.sqrt(cplane.dot(w, w))
            return tuple(c / m for c in w) if m > 1e-9 else None

        candidates = []
        geom = resolve(self.args[0]) if (resolve and self.args[0]) else None
        if isinstance(geom, r3.Curve):
            dom = geom.Domain
            for t in (dom.T0, dom.T1, 0.5 * (dom.T0 + dom.T1)):
                p = geom.PointAt(t)
                candidates.append(_sub3((p.X, p.Y, p.Z), origin))
        if plane is not None:
            candidates += [tuple(plane.xaxis), tuple(plane.yaxis)]
        for v in candidates:
            ref = flat(v)
            if ref is not None:
                return origin, axis, ref
        return None

    def pick_angle(self, resolve, plane, point):
        """The angle (degrees, right-handed about the axis) of `point` from the zero
        direction, or None when it lies on the axis."""
        frame = self.angle_frame(resolve, plane)
        if frame is None:
            return None
        origin, axis, ref = frame
        v = _sub3(point, origin)
        k = cplane.dot(v, axis)
        v = (v[0] - k * axis[0], v[1] - k * axis[1], v[2] - k * axis[2])
        if cplane.dot(v, v) <= 1e-18:
            return None
        return math.degrees(math.atan2(cplane.dot(cplane.cross(ref, v), axis),
                                       cplane.dot(ref, v)))

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        plane = self._active_cplane(ctx)
        point = parse_point(stripped, plane, self._last_point())
        angle = self.pick_angle(getattr(ctx, "geom_by_name", None), plane, point)
        if angle is None:
            raise ValueError("Revolve: that point is on the axis -- pick off it")
        if self.step_index == 4:
            angle -= float(self.args[3])
            if abs(angle) < 1e-9:
                raise ValueError("Revolve: the revolution angle must not be 0")
        return angle

    def _enter_default(self, ctx):
        if self.step_index == 2 and len(self.args) == 2:
            z = self._active_cplane(ctx).zaxis
            return tuple(self.args[1][i] + float(z[i]) for i in range(3))
        if self.step_index == 3:
            return 0.0
        if self.step_index == 4:
            return 360.0
        return None

    def execute(self, ctx: NarwhalContext):
        name, a1, a2, start, angle = self.args
        if not name and ctx.selected_objects():
            name = ctx.selected_objects()[0].name
        geom = ctx.geom_by_name(name)
        if geom is None or not isinstance(geom, r3.Curve):
            return f"No curve named {name!r}", True
        try:
            solid = bridge.make_revolve(geom, a1, a2, float(start),
                                        float(start) + float(angle))
        except ValueError as e:
            return str(e), True
        _, display_name = ctx.add_geometry(solid, "Revolved")
        return f"Created {display_name} ({float(angle):g} deg)", True


class LoftCmd(Command):
    """`Loft` -- the command behavior: "Select the curves in the order in which the
    surface should pass through them." MEASURED: this used to loft exactly two curves
    with a ruled surface and no options. It now takes any number of sections in pick
    order, aligns their directions and seams automatically, and builds every
    `LoftType` the reference API defines except the obsolete Developable -- all exact
    NURBS with a recipe (`bridge.loft_brep`)."""
    name = "Loft"
    description = "Loft a surface through two or more curves, in the order picked."
    steps = [("Select curves to loft:", OBJECTS_MULTI)]
    accepts_curve_subobjects = True
    accepts_subobjects = True
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("Style", opts.ENUM, choices=bridge.LOFT_STYLES, default="Normal",
                    help="Normal: chord-length skin. Loose: control points of the curves. "
                         "Tight: square-root chord skin. Straight: ruled. Uniform: uniform knots."),
        opts.Option("Closed", opts.TOGGLE, default=False,
                    help="Continue the surface past the last curve back to the first "
                         "(not implemented yet)."),
        opts.Option("Point", opts.MODE, help="Taper to a point (not implemented yet)."),
    )

    def execute(self, ctx: NarwhalContext):
        names = list(self.args[0]) if self.args else []
        try:
            curves, bad = _curves_named(ctx, names)
        except curve_inputs.CurveInputError as exc:
            return f"Loft: {exc}", True
        if bad:
            return f"Loft: only curves can be lofted ({', '.join(bad)})", True
        if len(curves) < 2:
            return "Loft: select at least two curves", True
        if self.opt.get("Closed"):
            return "Loft: Closed=Yes is not implemented yet. Nothing was created.", True
        closed = {bool(g.IsClosed) for _n, g in curves}
        if len(closed) > 1:
            return "Loft: the curves must be all open or all closed", True
        style = self.opt.get("Style", "Normal")
        try:
            brep = bridge.loft_brep([g for _n, g in curves], style=style)
        except ValueError as e:
            return f"Loft failed: {e}", True
        _, display_name = ctx.add_geometry(brep, "Loft")
        return f"Created {display_name} ({style}, {len(curves)} curves)", True


class OffsetCmd(Command):
    name = "Offset"
    description = "Offset a curve by a distance."
    # the distance takes a typed number OR a picked location (the reference CAD application's
    # ThroughPoint). the command behavior: "Move the mouse cursor to decide the
    # offset direction, and click." and "ThroughPoint -- Offsets through a
    # picked point instead of at a distance."
    # MEASURED (research_all_13 #092): a preselected curve goes straight to "Side to offset
    # ( Distance=1 ... )"; a point there picks the SIDE and the curve is offset by Distance
    # (line (0,0,0)-(4,0,0), side w2,1,0 -> (0,1,0)-(4,1,0)); `Distance` asks "Offset
    # distance <1.000>" and is sticky. MEASURED (research_preview_14 P47): a number typed
    # at the side prompt SETS that Distance and keeps the prompt; the live preview is the
    # offset on the cursor's side.
    steps = [("Select curve:", OBJECTS), ("Side to offset:", DISTANCE)]
    # the reference CAD application selects first, then runs the command; the selection
    # fallback these commands already tried was unreachable, because an
    # empty answer at a fixed prompt CANCELS (see Command.feed).
    preselect_names = 1
    #: the sticky Distance (MEASURED: it stays at the last value until changed).
    _sticky_distance = [1.0]

    def __init__(self):
        super().__init__()
        self.opt["Distance"] = OffsetCmd._sticky_distance[0]
        self._side_point = None
    # the top-level options the the reference CAD application 8 help documents for Offset
    # (the command behavior).  Sub-options (Yes/No under Loose, Sharp/Round/
    # Smooth/Chamfer under Corner, Flat/Round under Cap, Current/Input under
    # OutputLayer) are NOT declared separately -- the framework answers them
    # honestly through the ENUM choices or the refuse-by-name default.
    #
    # against `bridge.offset_curve`: it already accepts
    # `corner` (Sharp/Chamfer built; Round/Smooth refuse), `tolerance`, and
    # `plane_normal` (InCPlane).  BothSides runs the route with +d and -d.
    # Loose, Trim, Cap and OutputLayer/SubCrv need geometry or machinery we do
    # not have and are refused by name, never silently ignored.
    options = (
        opts.Option("Distance", opts.VALUE, value_kind="number", default=1.0,
                    help="The offset distance used with a picked side."),
        opts.Option("Loose", opts.TOGGLE, default=False,
                    help="Yes: offset by moving edit points (segments break at "
                         "corners).  No: offset the curve shape within Tolerance."),
        opts.Option("Corner", opts.ENUM,
                    choices=("Sharp", "Round", "Smooth", "Chamfer"),
                    default="Sharp",
                    help="How convex corners are filled when the offset is "
                         "convex.  Sharp: extend adjacent segments.  Chamfer: "
                         "straight segment.  Round/Smooth: not implemented."),
        opts.Option("Trim", opts.TOGGLE, default=False,
                    help="Trim the offset segments to the corners."),
        opts.Option("Tolerance", opts.VALUE, value_kind="number", default=1e-3,
                    help="Tolerance for the offset curve.  0 uses the system "
                         "default."),
        opts.Option("BothSides", opts.TOGGLE, default=False,
                    help="Offset the curve on both sides of the input."),
        opts.Option("InCPlane", opts.TOGGLE, default=False,
                    help="Offset in the current construction plane instead of "
                         "the curve's own plane."),
        opts.Option("Cap", opts.TOGGLE, default=False,
                    help="Close the gap at open curve ends (Flat or Round)."),
        opts.Option("OutputLayer", opts.MODE,
                    help="Place the result on the Current or Input layer."),
        opts.Option("SubCrv", opts.MODE,
                    help="Select part of a curve as input."),
    )

    def on_option_set(self, option, value, ctx):
        """Refuse the options that need geometry we do not have, and let the
        built ones through.  Returning (msg, done) keeps the command alive;
        returning None falls through to the standard acknowledgement.

        The framework has already stored `value` in `self.opt` before calling
        this hook (see `_apply_option`), so a refused option would show as
        in-use in the prompt (``Loose=Yes``) -- exactly the dishonesty the
        framework exists to prevent.  Reset to the default on refusal so the
        prompt reflects what is actually in effect."""
        if option.name == "Distance":
            try:
                d = float(value)
            except (TypeError, ValueError):
                d = 0.0
            if not d > 0.0:
                self.opt["Distance"] = OffsetCmd._sticky_distance[0]
                return "Offset: the distance must be positive", False
            OffsetCmd._sticky_distance[0] = d
            return None
        if option.name == "Loose":
            self.opt[option.name] = option.default
            return ("Offset: the Loose option (offset by moving edit points) is "
                    "not implemented yet", False)
        if option.name == "Trim":
            self.opt[option.name] = option.default
            return ("Offset: the Trim option (trim offset segments to corners) "
                    "is not implemented yet", False)
        if option.name == "Cap":
            self.opt[option.name] = option.default
            return ("Offset: the Cap option (close the gap at open ends) is "
                    "not implemented yet", False)
        if option.name == "Corner" and value in ("Round", "Smooth"):
            self.opt[option.name] = option.default
            return (f"Offset: the {value} corner style is not implemented yet "
                    f"(Sharp and Chamfer are)", False)
        return None

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.step_index == 1 and raw and self.pending_option is None:
            try:
                value = parse_number(raw)
            except ValueError:
                value = None
            if value is not None:
                option = next(o for o in self.options if o.name == "Distance")
                self.opt["Distance"] = value
                hook = self.on_option_set(option, value, ctx)
                return hook if hook is not None else (None, False)
        return super()._feed(text, ctx)

    def preview_curves(self, ctx_or_resolve, cursor):
        """The live offset toward `cursor` (display only), as point lists."""
        name = self.args[0] if self.args else None
        resolve = ctx_or_resolve if callable(ctx_or_resolve) else None
        geom = resolve(name) if (resolve and name) else None
        if not isinstance(geom, r3.Curve):
            return []
        dist = float(self.opt.get("Distance", 1.0))
        best = None
        for d in (dist, -dist):
            try:
                res = bridge.offset_curve(geom, d, corner=self.opt.get("Corner", "Sharp"),
                                          tolerance=self.opt.get("Tolerance", 1e-3) or 1e-3)
            except ValueError:
                continue
            if res.curve is None:
                continue
            gap = bridge.distance_to_curve(res.curve, tuple(cursor))
            if best is None or gap < best[0]:
                best = (gap, res.curve)
        if best is None:
            return []
        curve = best[1]
        dom = curve.Domain
        n = 2 if curve.IsLinear() else 96
        pts = [curve.PointAt(dom.T0 + (dom.T1 - dom.T0) * i / n) for i in range(n + 1)]
        return [[(p.X, p.Y, p.Z) for p in pts]]

    def on_mode(self, name, ctx):
        """OutputLayer and SubCrv need machinery (layer management, sub-curve
        selection) that is not built.  Refuse by name."""
        if name in ("OutputLayer", "SubCrv"):
            return (f"Offset: the {name} option is not implemented yet", False)
        return super().on_mode(name, ctx)

    def _offset_plane_normal(self, ctx):
        """InCPlane: the active CPlane's normal, or None to use the curve's own
        plane (the bridge default)."""
        if not self.opt.get("InCPlane"):
            return None
        return list(self._active_cplane(ctx).normal)

    def execute(self, ctx: NarwhalContext):
        name, dist = self.args
        if not name and ctx.selected_objects():
            name = ctx.selected_objects()[0].name
        geom = ctx.geom_by_name(name)
        if geom is None or not isinstance(geom, r3.Curve):
            return f"No curve named {name!r}", True
        # the exact route (bridge.offset_curve).  Exact for
        # lines/arcs/circles/polylines, approximate (and says so) only for a
        # free-form curve.
        corner = self.opt.get("Corner", "Sharp")
        tol = self.opt.get("Tolerance", 1e-3) or self._document_tolerance(ctx)
        plane_normal = self._offset_plane_normal(ctx)
        if self._side_point is not None and not self.opt.get("BothSides"):
            # The side is whichever signed offset lands nearer the picked point.
            best = None
            for d in (dist, -dist):
                try:
                    res = bridge.offset_curve(geom, d, plane_normal=plane_normal,
                                              corner=corner, tolerance=tol)
                except ValueError:
                    continue
                if res.curve is None:
                    continue
                gap = bridge.distance_to_curve(res.curve, self._side_point)
                if best is None or gap < best[0]:
                    best = (gap, d)
            if best is None:
                return "Offset failed (degenerate)", True
            dist = best[1]
        results = []
        for d in ([dist, -dist] if self.opt.get("BothSides") else [dist]):
            try:
                res = bridge.offset_curve(geom, d,
                                          plane_normal=plane_normal,
                                          corner=corner, tolerance=tol)
            except ValueError as e:
                return f"Offset: {e}", True
            if res.curve is None:
                return "Offset failed (degenerate)", True
            results.append(res)
        if len(results) == 2:
            # BothSides: add both curves, report both
            first_name = None
            for res in results:
                _, dn = ctx.add_geometry(res.curve, "Offset")
                if first_name is None:
                    first_name = dn
            return f"Created {first_name} and 1 more (both sides)", True
        out = results[0].curve
        _, display_name = ctx.add_geometry(out, "Offset")
        # For a circle, report the radius (expects "r=7" for r=5 offset by 2).
        try:
            c = out.TryGetCircle(1e-9)
            if c is not None:
                return f"Created {display_name} (r={c.Radius:g})", True
        except Exception:  # noqa: BLE001
            pass
        if results[0].exact:
            return f"Created {display_name} (d={dist:g}, exact)", True
        return f"Created {display_name} (d={dist:g}, {results[0].warning})", True

    def _parse_distance(self, text, ctx):
        """Offset's distance: a typed number, or the distance from the offset
        curve to a picked location (ThroughPoint).

        Unlike Circle/Cylinder, the reference is not the last picked point but
        the curve being offset, so the base `_parse_distance` (which measures
        from `_distance_base`) does not apply here.
        """
        stripped = (text or "").strip()
        name = self.args[0] if self.args else None
        if not name and ctx.selected_objects():
            name = ctx.selected_objects()[0].name
        geom = ctx.geom_by_name(name)
        if geom is None or not isinstance(geom, r3.Curve):
            raise ValueError("there is no curve to offset to that side")
        self._side_point = parse_point(stripped, self._active_cplane(ctx), self._last_point())
        return float(self.opt.get("Distance", 1.0))


# ---------------------------------------------------------------------------
# transform / edit commands (operate on Blender selection)
# ---------------------------------------------------------------------------


def _document_name_index(file_objects):
    """Build one ordered exact/casefold index of a document object table.

    The transform path historically used the first case-insensitive table row for
    each selected display name.  Values are therefore lists, not single objects:
    retaining table order preserves duplicate-name behavior and, importantly,
    retains the original file objects/IDs rather than reconstructing them.
    """
    exact = {}
    folded = {}
    for fobj in file_objects:
        name = fobj.Attributes.Name or ""
        exact.setdefault(name, []).append(fobj)
        folded.setdefault(name.casefold(), []).append(fobj)
    return exact, folded


def _first_document_object(index, name):
    """Return the first table object matching ``name``, or ``None``."""
    exact, folded = index
    rows = exact.get(name) or folded.get((name or "").casefold())
    return rows[0] if rows else None


def _apply_transform_to_selection(ctx: NarwhalContext, xform: r3.Transform,
                                  per_object=None):
    """Apply a rhino3dm Transform to selected objects, in the document and the view.

    `per_object`, when given, maps each object's document geometry to the transform
    used for that object instead of `xform` (Scale's `Rigid` moves each object by its
    own offset).

    The transform is **baked into the stored File3dm geometry**. ``fobj.Geometry``
    is a live reference, so
    ``fobj.Geometry.Transform(x)`` mutates the document in place.

    We deliberately do NOT use ``ObjectAttributes.Transform(x)``: that setter has
    no getter in rhino3dm 8.35.0, so anything recorded there is unreadable on
    reload and the placement is silently lost (see ).

    the DOCUMENT half no longer needs Blender. Without `mathutils` (the headless
    suites) the geometry is still transformed and only the display matrix is skipped, so
    the transform commands are testable for real rather than through a patched helper.
    """
    # the document is about to change, so snapshot it first. This is the
    # path the Gumball's drags take, which is exactly what had no undo.
    ctx.note_change("Transform")

    # Both rhino3dm.Transform.Mij and mathutils.Matrix are row-major with the
    # translation in column 3, so map row-for-row (transposing here would put
    # the translation in the bottom row and silently do nothing).
    def display_matrix(x):
        try:
            from mathutils import Matrix
        except ImportError:
            return None
        return Matrix([
            (x.M00, x.M01, x.M02, x.M03),
            (x.M10, x.M11, x.M12, x.M13),
            (x.M20, x.M21, x.M22, x.M23),
            (x.M30, x.M31, x.M32, x.M33),
        ])

    mm = display_matrix(xform)
    moved = []
    document_index = _document_name_index(ctx.model.file.Objects)
    for ob in ctx.selected_objects():
        # Resolve against one ordered index per transform.  This keeps the old
        # first-match/case-insensitive behavior (including duplicate and missing
        # names) without scanning the complete document for every display object.
        fobj = _first_document_object(document_index, ob.name)
        refused = False
        if fobj is not None and per_object is not None:
            xform = per_object(fobj.Geometry)
            mm = display_matrix(xform)
        if fobj is not None:
            # bake into the document first, so a kernel failure can't leave the
            # viewport showing a move the saved file doesn't have
            # transform_geometry, NOT Geometry.Transform. A bare.Transform
            # moves only the display proxy and leaves the analytic MASTER and the
            # construction RECIPE behind -- that made a moved solid export
            # from the wrong position and made `box MINUS a moved-away cylinder` drill
            # a hole that was never asked for. See bridge.transform_geometry.
            old = fobj.Geometry
            try:
                new = bridge.transform_geometry(old, xform)
            except bridge.NonUniformTransformError as e:
                # never a wrong solid -- the object stays as it was, and says so
                from .state import app_state

                app_state.log(f"{ob.name}: {e}")
                refused = True
            else:
                if new is not None and new is not old:
                    # a non-uniform scale of a surface/solid is a NEW exact object
                    ctx.model.replace_geometry(fobj.Attributes.Id, new)
        if refused:
            continue
        if mm is not None and hasattr(ob, "matrix_world"):
            ob.matrix_world = mm @ ob.matrix_world
            bridge.note_display_changed(ob.name)
        moved.append(ob.name)
    return moved


def apply_transform(ctx: NarwhalContext, xform):
    """Public entry point for applying a Transform to the selection.

    The Gumball  transforms objects without running a command, so it needs
    this path too. Exposed as a named function rather than letting `ui` reach for
    the private helper, so there is exactly one place that knows how a transform
    reaches the document.
    """
    return _apply_transform_to_selection(ctx, xform)


def _selection_bbox_center(ctx):
    """World bounding-box centre of the current selection, or None if empty.

    the default for the Move/Copy "from" point when the user
    presses Enter (the command behavior: "the cumulative bounding box center of the
    selected object(s)"). Read from the DOCUMENT geometry (rhino3dm
    `GetBoundingBox`), not from Blender objects, so it is the document's own
    truth and is testable without `bpy`.
    """
    from . import analysis

    sel = ctx.selected_objects()
    if not sel:
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found = False
    for ob in sel:
        geom = ctx.geom_by_name(ob.name)
        if geom is None:
            continue
        # the TIGHT box ('s `analysis.bounding_box`). `GetBoundingBox` on a
        # NURBS curve is its CONTROL HULL, so an S-curve's "centre" was
        # off the curve's own box -- Move/Copy/Scale's Enter used that point.
        try:
            bmin, bmax = analysis.bounding_box(geom)
        except Exception:  # noqa: BLE001
            bb = geom.GetBoundingBox()
            bmin, bmax = bb.Min, bb.Max
        found = True
        for i, (a, b) in enumerate(((bmin.X, bmax.X),
                                    (bmin.Y, bmax.Y),
                                    (bmin.Z, bmax.Z))):
            lo[i] = min(lo[i], a)
            hi[i] = max(hi[i], b)
    if not found:
        return None
    return ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, (lo[2] + hi[2]) / 2.0)


# ---------------------------------------------------------------------------
# the transform commands, the reference CAD application's steps, and `live_xform` for the ghost.
#
# The live-preview contract mirrors CAD SDK's
# GetTransform.CalculateTransform): `live_xform(cursor) -> r3.Transform | None` is the
# transform the command WOULD apply if the current step were answered with `cursor`, and
# None while the step does not move anything yet. It is computed by the SAME `_compute`
# that `execute` uses, so the ghost and the result cannot disagree. `ui` draws the
# selected objects through it; nothing here touches the display.
#
# Every transform reaches the document through `bridge.transform_geometry` (via
# `_apply_transform_to_selection` or `_duplicate_with_transform`) -- audit gate 5.
# ---------------------------------------------------------------------------

def _affine(m3, origin):
    """The r3.Transform x -> m3 (x - origin) + origin, for a 3x3 row-major `m3`."""
    t = r3.Transform(1.0)
    o = origin
    for r in range(3):
        for c in range(3):
            setattr(t, f"M{r}{c}", float(m3[r][c]))
        setattr(t, f"M{r}3", float(o[r] - sum(m3[r][c] * o[c] for c in range(3))))
    return t


def _translation(d):
    return r3.Transform.Translation(float(d[0]), float(d[1]), float(d[2]))


def _rotation_m3(axis, angle):
    """Rodrigues: the 3x3 rotation by `angle` (rad) about unit `axis`, right-handed."""
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = 1.0 - c
    return [[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
            [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
            [z * x * k - y * s, z * y * k + x * s, c + z * z * k]]


def _matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def _directional_scale_m3(d, f):
    """Scale by `f` along unit `d` only: I + (f - 1) d d^T."""
    return [[(1.0 if r == c else 0.0) + (f - 1.0) * d[r] * d[c] for c in range(3)]
            for r in range(3)]


def _align_m3(a, b):
    """The rotation taking unit `a` onto unit `b` (about a x b; any perpendicular when
    they are opposite)."""
    axis = cplane.cross(a, b)
    s = math.sqrt(cplane.dot(axis, axis))
    c = max(-1.0, min(1.0, cplane.dot(a, b)))
    if s <= 1e-15:
        if c > 0:
            return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        seed = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
        perp = cplane.normalize(cplane.cross(a, seed))
        return _rotation_m3(perp, math.pi)
    return _rotation_m3(tuple(v / s for v in axis), math.atan2(s, c))


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _is_point(v):
    return isinstance(v, tuple) and len(v) == 3


class _TransformCmd(Command):
    """Shared by the transforms: the selection set first (shape), a `Copy`
    toggle, the CPlane captured for `live_xform` (which gets no ctx), and a `_compute`
    that turns the answers so far into a transform -- or None while incomplete."""

    preselect_names = 1
    cancel_on_empty_set = True
    verb = "Transformed"

    def __init__(self):
        super().__init__()
        self._plane = cplane.WORLD_TOP

    def begin(self, ctx):
        self._plane = self._active_cplane(ctx)
        return super().begin(ctx)

    def feed(self, text, ctx):
        self._plane = self._active_cplane(ctx)
        return super().feed(text, ctx)

    @property
    def _z(self):
        return tuple(float(c) for c in self._plane.zaxis)

    def _compute(self, args, live=False):
        raise NotImplementedError

    def _next_step(self, args):
        """The step to ask for when the answers so far are complete but undetermined
        (a reference point instead of a typed value), or None."""
        return None

    def live_xform(self, cursor):
        """The contract -- see the section comment above."""
        if self.step_index < 1 or self.step_index >= len(self.steps) or cursor is None:
            return None
        try:
            res = self._compute(self.args + [tuple(float(c) for c in cursor)], live=True)
        except Exception:  # noqa: BLE001
            return None
        return res if isinstance(res, r3.Transform) else None

    def execute(self, ctx: NarwhalContext):
        res = self._compute(self.args)
        if res is None:
            step = self._next_step(self.args)
            if step is None:
                return f"{self.name}: not enough input", True
            self.steps.append(step)
            return None, False
        if isinstance(res, str):
            return f"{self.name}: {res}", True
        return self._commit(ctx, res)

    def _commit(self, ctx, xform):
        names = list(self.args[0]) if self.args and isinstance(self.args[0], list) else \
            [o.name for o in ctx.selected_objects()]
        if not names:
            return "Nothing selected", True
        copy = bool(self.opt.get("Copy", False))
        if copy:
            made = [n for n in (_duplicate_with_transform(ctx, nm, xform, self.name)
                                for nm in names) if n]
            return f"{self.verb} {len(made)} copy(ies): {', '.join(made)}", True
        moved = _apply_transform_to_selection(ctx, xform)
        return f"{self.verb} {len(moved)} object(s)", True


def _copy_option(default):
    return opts.Option("Copy", opts.TOGGLE, default=default,
                       help="Specifies whether or not the objects are copied.")


class _FromToTransformCmd(Command):
    """Shared selection-and-two-point interaction for ``Move`` and ``Copy``.

    The first point is the source and the second is the destination; their difference is
    the translation. Enter at the source step uses the cumulative selection bounding-box
    centre. A preselection fills the leading object step and remains active through the
    point steps.

    Typed movement remains available by entering an explicit source point followed by the
    destination or delta endpoint. This avoids ambiguity between a one-step typed vector
    and the first point of an interactive two-point move.
    """

    preselect_names = 1
    cancel_on_empty_set = True

    def _from_step_index(self):
        """The index of the FROM point step (the first POINT step)."""
        for i, (_p, k) in enumerate(self.steps):
            if k == POINT:
                return i
        return None

    def _enter_default(self, ctx):
        # Only the FROM step has the Enter -> bbox-centre shortcut; at the TO
        # step Enter keeps its ordinary "cancel" meaning.
        if self.step_index != self._from_step_index():
            return None
        return _selection_bbox_center(ctx)

    def _delta(self):
        """The translation vector (to - from). The two trailing args are the
        from/to points; a leading object-set arg  sits before them."""
        p_from, p_to = self.args[-2], self.args[-1]
        return self._delta_of(p_from, p_to)

    # Move's `Vertical` ("Moves objects perpendicular to the current construction
    # plane") answers the TO step with a signed distance along the CPlane normal.
    _vertical = False

    def _normal(self):
        plane = getattr(self, "_plane", None) or cplane.WORLD_TOP
        return tuple(float(c) for c in plane.zaxis)

    def _delta_of(self, p_from, p_to):
        if self._vertical:
            n = self._normal()
            d = float(p_to) if not _is_point(p_to) else \
                cplane.dot(_sub3(p_to, p_from), n)
            return (n[0] * d, n[1] * d, n[2] * d)
        return _sub3(p_to, p_from)

    def begin(self, ctx):
        self._plane = self._active_cplane(ctx)
        return super().begin(ctx)

    def feed(self, text, ctx):
        self._plane = self._active_cplane(ctx)
        return super().feed(text, ctx)

    def live_xform(self, cursor):
        """the translation the TO step would make for `cursor`; None before it."""
        to_index = len(self.steps) - 1
        if self.step_index != to_index or cursor is None or len(self.args) != to_index:
            return None
        p_from = self.args[-1]
        if not _is_point(p_from):
            return None
        return _translation(self._delta_of(p_from, tuple(float(c) for c in cursor)))


class MoveCmd(_FromToTransformCmd):
    """`Move` -- the command behavior. `Vertical` built ("Moves objects perpendicular
    to the current construction plane"; the TO point is projected onto the normal through
    the FROM point, or a distance is typed); `Normal` and `SubCrv` are declared."""

    name = "Move"
    description = "Move selected objects: a point to move from, then a point to move to."
    # MEASURED (research_preview_14 P44): "Point to move from. Press Enter to use bounding box
    # center", "Point to move to" (a typed number locks the distance -- the UI's pick constraint).
    steps = [("Select objects to move:", OBJECTS_MULTI),
             ("Point to move from. Press Enter to use bounding box center:", POINT),
             ("Point to move to:", POINT)]
    options = (
        opts.Option("Vertical", opts.MODE,
                    help="Moves objects perpendicular to the current construction plane."),
        opts.Option("Normal", opts.MODE,
                    help="Moves objects in the normal direction of a picked location."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )

    def on_mode(self, name, ctx):
        if name == "Vertical":
            if self.step_index >= len(self.steps) - 1 and len(self.args) >= len(self.steps):
                return "Move: Vertical is chosen before the point to move to", False
            self._vertical = True
            self.steps[-1] = ("Point to move to:", DISTANCE)
            return "Move: Vertical -- the move is along the CPlane normal", False
        return super().on_mode(name, ctx)

    def axis_constraint(self, plane, resolve=None):
        if not self._vertical or self.step_index != len(self.steps) - 1 or plane is None:
            return None
        p_from = self.args[-1] if self.args else None
        if not _is_point(p_from):
            return None
        return (p_from, tuple(float(c) for c in plane.zaxis))

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        return parse_point(stripped, self._active_cplane(ctx), self._last_point())

    def execute(self, ctx: NarwhalContext):
        if not ctx.selected_objects():
            return "Nothing selected", True
        dx, dy, dz = self._delta()
        xform = r3.Transform.Translation(dx, dy, dz)
        moved = _apply_transform_to_selection(ctx, xform)
        return f"Moved {len(moved)} object(s)", True


class _AngleCmd(_TransformCmd):
    """Shared by `Rotate` / `Rotate3D`: "Type the angle, or pick two reference points"
    (both help pages). The angle step is a DISTANCE-kind step so it takes a
    typed number OR a click; a click is the FIRST reference point and adds the reference CAD application's
    "Second reference point" step (prompts from the command UI)."""

    verb = "Rotated"
    ANGLE_PROMPT = "Angle or first reference point:"
    #: The axis-defining points of the last committed rotation, per command name, for
    #: `UseLastCenter` / `UseLastAxis` (this session only).
    _last_axis_points = {}
    #: The option that reuses them, and what it is called in a refusal.
    _reuse_option, _reuse_noun = None, None

    def on_mode(self, name, ctx):
        if name != self._reuse_option:
            return super().on_mode(name, ctx)
        if self.step_index != 1:
            return f"{self.name}: {name} answers the {self._reuse_noun} prompt", False
        last = _AngleCmd._last_axis_points.get(self.name)
        if last is None:
            return f"{self.name}: there is no previous {self._reuse_noun} yet", False
        self.args.extend(last)
        self.step_index += len(last)
        return None, False

    def _commit(self, ctx, xform):
        _AngleCmd._last_axis_points[self.name] = [
            tuple(p) for p in self.args[1:1 + self._n_axis_args]]
        return super()._commit(ctx, xform)

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        return parse_point(stripped, self._active_cplane(ctx), self._last_point())

    def _next_step(self, args):
        return ("Second reference point:", POINT)

    def _axis_and_origin(self, args):
        raise NotImplementedError

    def _compute(self, args, live=False):
        n_pre = self._n_axis_args + 1           # the set + the axis-defining points
        if len(args) <= n_pre:
            return None
        axis, origin = self._axis_and_origin(args)
        if axis is None:
            return origin                       # a refusal string
        a = args[n_pre]
        if not _is_point(a):
            return _affine(_rotation_m3(axis, math.radians(float(a))), origin)
        if len(args) < n_pre + 2:
            return None                         # first reference point only
        b = args[n_pre + 1]

        def flat(p):
            v = _sub3(p, origin)
            k = cplane.dot(v, axis)
            return (v[0] - k * axis[0], v[1] - k * axis[1], v[2] - k * axis[2])

        u, w = flat(a), flat(b)
        if cplane.dot(u, u) <= 1e-24 or cplane.dot(w, w) <= 1e-24:
            return None if live else "a reference point lies on the rotation axis"
        ang = math.atan2(cplane.dot(cplane.cross(u, w), axis), cplane.dot(u, w))
        return _affine(_rotation_m3(axis, ang), origin)


class RotateCmd(_AngleCmd):
    """`Rotate` -- the command behavior (replacing the old typed x/y/z axis).

    steps: "Select the objects. Pick the center of rotation. Type the angle, or
    pick two reference points." The rotation is about the ACTIVE CPlane's normal through
    the centre; a positive angle is counterclockwise ("Positive angular measurement is
    counterclockwise", the command behavior). Prompts: "Center of rotation",
    "Angle or first reference point", "Second reference point" in the command UI.
    Options: `Copy` and `UseLastCenter` built (the centre of the last Rotate this
    session answers the centre prompt).
    """

    name = "Rotate"
    description = "Rotate selected objects about the CPlane normal: center, then an angle " \
                  "or two reference points."
    steps = [("Select objects to rotate:", OBJECTS_MULTI),
             ("Center of rotation:", POINT),
             (_AngleCmd.ANGLE_PROMPT, DISTANCE)]
    options = (
        _copy_option(False),
        opts.Option("UseLastCenter", opts.MODE,
                    help="Uses the previously selected center point for the rotation."),
    )
    _n_axis_args = 1
    _reuse_option, _reuse_noun = "UseLastCenter", "center of rotation"

    def _axis_and_origin(self, args):
        return self._z, args[1]


class Rotate3DCmd(_AngleCmd):
    """`Rotate3D` -- the command behavior. steps: "Select the objects. Pick the
    start of the rotation axis. Pick the end of the rotation axis. Type the angle, or pick
    two reference points." Positive is right-handed about start -> end (the help
    does not say). Options: `Copy` and `UseLastAxis` built (the start and end of the
    last Rotate3D axis this session answer both axis prompts); `SurfaceNormal`, `SubCrv`
    declared."""

    name = "Rotate3D"
    description = "Rotate selected objects about an axis given by two points."
    steps = [("Select objects to rotate:", OBJECTS_MULTI),
             ("Start of rotation axis:", POINT), ("End of rotation axis:", POINT),
             (_AngleCmd.ANGLE_PROMPT, DISTANCE)]
    options = (
        _copy_option(False),
        opts.Option("SurfaceNormal", opts.MODE,
                    help="The rotation axis is a surface's normal at a picked point."),
        opts.Option("UseLastAxis", opts.MODE,
                    help="Uses the previously selected axis direction for the rotation."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )
    _n_axis_args = 2
    _reuse_option, _reuse_noun = "UseLastAxis", "rotation axis"

    def _axis_and_origin(self, args):
        axis = cplane.normalize(_sub3(args[2], args[1]))
        if axis is None:
            return None, "the two ends of the rotation axis are the same point"
        return axis, args[1]


class _ScaleBase(_TransformCmd):
    """Shared by `Scale`, `Scale1D`, `Scale2D` -- the command behavior.

    steps: "Select objects. Pick a base point, or press Enter to use the center
    of the bounding box of all selected objects as the base point. Type the scale factor,
    or pick two reference points." and "To scale an object from an arbitrary size to a
    specific one: use the origin point and the first reference point to establish the
    original size... Type the new size for the second reference point." So: the factor
    step takes a number OR a first reference point; the second reference point takes a
    point OR a typed new size. Prompts: "Base point. Press Enter for automatic", "Scale
    factor or first reference point", "Second reference point" in the command UI.
    Options: `Copy` built; `Rigid` and `SubCrv` declared.

    said a click must not answer the factor prompt because "a scale factor is a
    RATIO, not a length". That stays true: a click there is not a factor, it is the
    first REFERENCE point, and the ratio comes from the second -- the reference CAD application's prompt says
    exactly that ("Scale factor or first reference point").
    """

    verb = "Scaled"
    FACTOR_PROMPT = "Scale factor or first reference point:"
    # MEASURED (research_preview_14 P24): "Scale factor or first reference point <1>"; after
    # a typed 2 the next Scale shows <2>. Enter takes it (DERIVED: the <> default).
    _sticky_factor = {}

    def _factor_default(self):
        return _ScaleBase._sticky_factor.get(self.name, 1.0)

    @property
    def prompt(self):
        base = super().prompt
        label = self.FACTOR_PROMPT[:-1]
        if (self.pending_option is None and self.step_index == 2
                and self.step_index < len(self.steps)
                and self.steps[2][0] == self.FACTOR_PROMPT and base.startswith(label)):
            return f"{label} <{_num(self._factor_default())}>" + base[len(label):]
        return base

    def _enter_default(self, ctx):
        if self.step_index == 1:
            return _selection_bbox_center(ctx)
        if self.step_index == 2 and len(self.args) == 2:
            return float(self._factor_default())
        return None

    def _feed(self, text, ctx):
        msg, done = super()._feed(text, ctx)
        if (len(self.args) >= 3 and isinstance(self.args[2], (int, float))
                and not isinstance(self.args[2], bool) and (done or len(self.args) == 3)):
            _ScaleBase._sticky_factor[self.name] = float(self.args[2])
        return msg, done

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        return parse_point(stripped, self._active_cplane(ctx), self._last_point())

    def _next_step(self, args):
        return ("Second reference point:", DISTANCE)

    def _distance(self, origin, p):
        return math.dist(origin, p)

    def _factor(self, args, live):
        """The ratio from args[2:] -- a typed factor, or two reference points."""
        if len(args) < 3:
            return None
        origin, a = args[1], args[2]
        if not _is_point(a):
            return float(a)
        if len(args) < 4:
            return None
        ref = self._distance(origin, a)
        if ref <= 1e-12:
            return None if live else "the first reference point is at the base point"
        b = args[3]
        new = float(b) if not _is_point(b) else self._distance(origin, b)
        return new / ref

    def _compute(self, args, live=False):
        f = self._factor(args, live)
        if f is None or isinstance(f, str):
            return f
        return self._build(args, f)

    @staticmethod
    def _rigid(xform, geom):
        """`Rigid`: the translation that carries `geom`'s bounding-box centre where
        `xform` would put it -- the object moves, its shape and size do not."""
        from . import analysis

        lo, hi = analysis.bounding_box(geom)
        c = r3.Point3d((lo.X + hi.X) / 2, (lo.Y + hi.Y) / 2, (lo.Z + hi.Z) / 2)
        p = r3.Point3d(c.X, c.Y, c.Z)
        p.Transform(xform)
        return _translation((p.X - c.X, p.Y - c.Y, p.Z - c.Z))

    def _commit(self, ctx, xform):
        if not self.opt.get("Rigid"):
            return super()._commit(ctx, xform)
        # OURS (#61): an object's position is its bounding-box centre.
        names = list(self.args[0]) if self.args and isinstance(self.args[0], list) else \
            [o.name for o in ctx.selected_objects()]
        if not names:
            return "Nothing selected", True
        if self.opt.get("Copy", False):
            made = [n for n in (_duplicate_with_transform(
                ctx, nm, self._rigid(xform, ctx.geom_by_name(nm)), self.name)
                for nm in names) if n]
            return f"Moved {len(made)} copy(ies) rigidly: {', '.join(made)}", True
        moved = _apply_transform_to_selection(ctx, xform,
                                              per_object=lambda g: self._rigid(xform, g))
        return f"Moved {len(moved)} object(s) rigidly", True


class ScaleCmd(_ScaleBase):
    name = "Scale"
    description = "Scale selected objects uniformly from a base point."
    # MEASURED (research_preview_14 P24): the base prompt ends with a full stop before the
    # options: "Base point. Press Enter for automatic. ( Copy=No  Rigid=No ):".
    steps = [("Select objects to scale:", OBJECTS_MULTI),
             ("Base point. Press Enter for automatic.:", POINT),
             (_ScaleBase.FACTOR_PROMPT, DISTANCE)]
    options = (
        _copy_option(False),
        opts.Option("Rigid", opts.TOGGLE, default=False,
                    help="Individual objects will not change, only their positions."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )

    def _build(self, args, f):
        if f == 0:
            return "a scale factor of 0 would collapse the objects to a point"
        return r3.Transform.Scale(r3.Point3d(*args[1]), float(f))


class Scale2DCmd(ScaleCmd):
    """`Scale2D`: "expanded or contracted uniformly in the two directions of the axes of
    the active viewport construction plane"  -- unchanged along its normal.
    reference distances are IN the CPlane (the scaling directions)."""

    name = "Scale2D"
    description = "Scale selected objects in the two CPlane directions."

    def _distance(self, origin, p):
        v = _sub3(p, origin)
        z = self._z
        k = cplane.dot(v, z)
        return math.sqrt(max(cplane.dot(v, v) - k * k, 0.0))

    def _build(self, args, f):
        if f == 0:
            return "a scale factor of 0 would flatten the objects to a line"
        z = self._z
        m3 = [[(f if r == c else 0.0) + (1.0 - f) * z[r] * z[c] for c in range(3)]
              for r in range(3)]
        return _affine(m3, args[1])


class Scale1DCmd(_ScaleBase):
    """`Scale1D`: "Type the scale factor and a direction, or pick two reference points.
    Typing 0 for the scale factor flattens the object in the picked direction.". With reference points the direction is base -> first reference point
    and the size is the second's distance ALONG it; a typed factor adds the reference CAD application's "Scale
    direction" step (prompt wording)."""

    name = "Scale1D"
    description = "Scale selected objects in one direction."
    steps = ScaleCmd.steps[:2] + [(_ScaleBase.FACTOR_PROMPT, DISTANCE)]
    options = ScaleCmd.options
    # MEASURED (research_all_13 note 132): after a typed factor the reference CAD application asks "Scale
    # direction ( Copy=No  Rigid=No  XAxis  YAxis  ZAxis ):", and `XAxis` finished the
    # command, scaling along X. The axes are offered only at that prompt, as there.
    # DERIVED: they are the active CPlane's axes -- measured only in the world Top CPlane,
    # where CPlane X is world X.
    _AXES = (opts.Option("XAxis", opts.MODE, help="Scale along the CPlane x-axis."),
             opts.Option("YAxis", opts.MODE, help="Scale along the CPlane y-axis."),
             opts.Option("ZAxis", opts.MODE, help="Scale along the CPlane z-axis."))

    def _next_step(self, args):
        if len(args) == 3 and not _is_point(args[2]):
            self.options = tuple(ScaleCmd.options) + self._AXES
            return ("Scale direction:", POINT)
        return ("Second reference point:", DISTANCE)

    def on_mode(self, name, ctx):
        if name in ("XAxis", "YAxis", "ZAxis") and self.step_index < len(self.steps) \
                and self.steps[self.step_index][0] == "Scale direction:":
            axis = getattr(self._plane, name[0].lower() + "axis")
            origin = self.args[1]
            self.args.append(tuple(float(origin[i]) + float(axis[i]) for i in range(3)))
            self.step_index += 1
            return self.execute(ctx)
        return super().on_mode(name, ctx)

    def _compute(self, args, live=False):
        if len(args) < 3:
            return None
        origin, a = args[1], args[2]
        if not _is_point(a):                     # typed factor, then a direction
            if len(args) < 4:
                return None
            d = cplane.normalize(_sub3(args[3], origin))
            if d is None:
                return None if live else "the direction point is at the base point"
            return _affine(_directional_scale_m3(d, float(a)), origin)
        if len(args) < 4:
            return None
        v = _sub3(a, origin)
        ref = math.sqrt(cplane.dot(v, v))
        if ref <= 1e-12:
            return None if live else "the first reference point is at the base point"
        d = tuple(c / ref for c in v)
        b = args[3]
        new = float(b) if not _is_point(b) else cplane.dot(_sub3(b, origin), d)
        return _affine(_directional_scale_m3(d, new / ref), origin)


class OrientCmd(_TransformCmd):
    """`Orient` -- the command behavior. "Pick two reference points... Pick two
    target points. The first and second reference points will match these locations.
    The objects are moved, scaled and rotated so the reference points transform to the
    target points." Option `Scale`: No ("objects remain the same size"), 1D ("only along
    the axis between the target points"), 3D ("the second target point defines... the
    scale factor"). Prompts "Reference point 1/2", "Target point 1/2" in the command UI.
    the default is `Scale=No` (the help does not state one)."""

    name = "Orient"
    verb = "Oriented"
    description = "Move, rotate (and optionally scale) objects from two reference points " \
                  "to two target points."
    steps = [("Select objects to orient:", OBJECTS_MULTI),
             ("Reference point 1:", POINT), ("Reference point 2:", POINT),
             ("Target point 1 <Reference point 1>:", POINT), ("Target point 2:", POINT)]
    options = (
        _copy_option(False),
        opts.Option("Scale", opts.ENUM, default="No", choices=("No", "1D", "3D"),
                    help="No keeps the size; 1D scales along the target axis; 3D scales "
                         "uniformly by the target/reference distance ratio."),
    )

    def _enter_default(self, ctx):
        # MEASURED (research_all_13 #097): the prompt reads "Target point 1 <Reference
        # point 1>" -- its default is reference point 1, which Enter takes (DERIVED: the
        # Enter itself was not sent in the research).
        if self.step_index == 3 and len(self.args) == 3:
            return self.args[1]
        return None

    def _compute(self, args, live=False):
        pts = args[1:]
        if len(pts) < 3:
            return None
        r1, r2, t1 = pts[0], pts[1], pts[2]
        if len(pts) == 3:
            # MEASURED (research_preview_14 P20): nothing moves at "Target point 1"; the
            # rotated copy appears only at "Target point 2".
            return None
        t2 = pts[3]
        a, b = cplane.normalize(_sub3(r2, r1)), cplane.normalize(_sub3(t2, t1))
        if a is None or b is None:
            return None if live else "two reference (or target) points coincide"
        m3 = _align_m3(a, b)
        mode = self.opt.get("Scale", "No")
        if mode != "No":
            f = math.dist(t1, t2) / math.dist(r1, r2)
            s = [[f if r == c else 0.0 for c in range(3)] for r in range(3)] \
                if mode == "3D" else _directional_scale_m3(b, f)
            m3 = _matmul(s, m3)
        # x -> t1 + M (x - r1)
        t = _affine(m3, r1)
        return _compose_translation(t, _sub3(t1, r1))


def _compose_translation(t, d):
    """`t` followed by a translation `d` (adds `d` to the translation column)."""
    t.M03 += float(d[0])
    t.M13 += float(d[1])
    t.M23 += float(d[2])
    return t


class Orient3PtCmd(_TransformCmd):
    """`Orient3Pt` -- the command behavior (confirmed). three reference
    points ("the second... defines a base direction from the first"), then three target
    points, each corresponding. The reference FRAME (origin R1, x toward R2, xy-plane
    through R3) is mapped onto the target frame: rigid. `Scale` ("Re-sizes the object as
    it is oriented... the ratio between the distance between the first two reference
    points and the first two target points. The third target point is used for
    orientation only.") -- uniform about the first target point."""

    name = "Orient3Pt"
    verb = "Oriented"
    description = "Orient objects from three reference points to three target points."
    steps = [("Select objects to orient:", OBJECTS_MULTI),
             ("Reference point 1:", POINT), ("Reference point 2:", POINT),
             ("Reference point 3:", POINT),
             ("Target point 1:", POINT), ("Target point 2:", POINT),
             ("Target point 3:", POINT)]
    options = (
        _copy_option(False),
        opts.Option("Scale", opts.TOGGLE, default=False,
                    help="Re-sizes the object by the ratio of the first-two-point "
                         "distances."),
    )

    @staticmethod
    def _frame(o, p, q):
        x = cplane.normalize(_sub3(p, o))
        if x is None:
            return None
        z = cplane.normalize(cplane.cross(x, _sub3(q, o)))
        if z is None:
            return None
        return x, cplane.cross(z, x), z

    def _compute(self, args, live=False):
        pts = args[1:]
        if len(pts) < 4:
            return None
        r1, r2, r3_, t1 = pts[0], pts[1], pts[2], pts[3]
        if len(pts) == 4:
            return _translation(_sub3(t1, r1)) if live else None
        if len(pts) == 5:
            if not live:
                return None
            a = cplane.normalize(_sub3(r2, r1))
            b = cplane.normalize(_sub3(pts[4], t1))
            if a is None or b is None:
                return None
            return _compose_translation(_affine(_align_m3(a, b), r1), _sub3(t1, r1))
        fr = self._frame(r1, r2, r3_)
        ft = self._frame(t1, pts[4], pts[5])
        if fr is None or ft is None:
            return None if live else "three reference (or target) points are in a line"
        # M = [ft columns] [fr columns]^T  -- takes each reference axis to its target axis
        m3 = [[sum(ft[k][r] * fr[k][c] for k in range(3)) for c in range(3)]
              for r in range(3)]
        if self.opt.get("Scale"):
            f = math.dist(t1, pts[4]) / math.dist(r1, r2)
            m3 = [[v * f for v in row] for row in m3]
        return _compose_translation(_affine(m3, r1), _sub3(t1, r1))


def _duplicate_with_transform(ctx: NarwhalContext, name: str, xform: r3.Transform,
                              base_name: str):
    """Duplicate the named object's geometry, apply ``xform``, add as a new object.

    Returns the display name of the copy. The original is untouched because
    rhino3dm ``Duplicate`` returns a fresh object and ``Transform`` mutates in
    place (verified).

    Since stored geometry is always in **world coordinates** (transforms are
    baked at the point of use), so there is no prior placement to compose with.
    """
    geom = ctx.geom_by_name(name)
    if geom is None:
        return None
    dup = geom.Duplicate()
    # a DUPLICATE shares its original's master id (user strings are copied), so it
    # must go through transform_geometry, which re-registers the moved master under a FRESH
    # key rather than mutating the one the original still points at.
    try:
        dup = bridge.transform_geometry(dup, xform)    # may be a NEW exact object
    except bridge.NonUniformTransformError as e:
        app_state.log(f"{name}: {e}")
        return None
    _, display_name = ctx.add_geometry(dup, base_name)
    return display_name


class CopyCmd(_FromToTransformCmd):
    name = "Copy"
    description = ("Copy selected objects: a point to copy from, then a point to "
                   "copy to -- for EACH copy. Press Enter to end the command.")
    # MEASURED (research_preview_14 P32): "Point to copy from. Press Enter to use bounding
    # box center", "Point to copy to", then after each placed copy "Point to copy to. Press
    # Enter when done"; the placed copies stay on screen and a live copy follows the cursor.
    steps = [("Select objects to copy:", OBJECTS_MULTI),
             ("Point to copy from. Press Enter to use bounding box center:", POINT),
             ("Point to copy to:", POINT)]
    REPEAT_PROMPT = "Point to copy to. Press Enter when done:"

    @property
    def prompt(self):
        base = super().prompt
        label = self.steps[-1][0][:-1]
        if (self.pending_option is None and self.step_index == len(self.steps) - 1
                and len(self.args) > self.step_index and base.startswith(label)):
            return self.REPEAT_PROMPT[:-1] + base[len(label):]
        return base
    # (the command behavior): these are the five top-level options. The
    # help gives no starting values for the toggles; per-command No is
    # and values intentionally do not persist across separate invocations.
    options = (
        opts.Option("Vertical", opts.MODE,
                    help="Duplicates the object vertical to the current construction plane."),
        opts.Option("InPlace", opts.MODE,
                    help="Duplicates the object into the same location as the original."),
        opts.Option("FromLastPoint", opts.TOGGLE, default=False,
                    help="Uses the point the last object was copied to as the base point."),
        opts.Option("UseLastDistance", opts.TOGGLE, default=False,
                    help="Places the next copy at the same distance from the base point as the last copy."),
        opts.Option("UseLastDirection", opts.TOGGLE, default=False,
                    help="Places the next copy at the same angle from the base point as the last copy."),
    )

    # the to-point is REPEATED. the reference CAD application (the command behavior) documents it as
    # "Pick a point to copy to for each copy. Press Enter to end the command.".
    # The to-points are COLLECTED (no mutation) and the copies are built in one
    # `execute`, so the whole command is a single undo step. This supersedes
    # 's single-to form; a single to-point still yields one copy set, so the
    # old behaviour is preserved as a special case.
    #
    # GROUP IDENTITY (the command behavior: "a new group is created from the copied
    # objects. The copied objects are not added to the original group.").
    # `_duplicate_with_transform` -> `ctx.add_geometry` ->
    # `model.add_object` wraps the copy in a FRESH `ObjectAttributes`, so the
    # copy never carries the original's group membership -- the copy is never
    # added to the original group.
    #
    # `complete_after_first` is an internal completion override: when True the command
    # finishes after the first destination instead of staying open for more, and
    # the repeat test goes RED. It is never True in the running app.
    complete_after_first = False

    def __init__(self):
        super().__init__()
        self._vertical = False
        self._in_place = False
        self._last_destination = None
        self._last_vector = None

    def on_mode(self, name, ctx):
        if name == "Vertical":
            self._vertical = True
            self._in_place = False
            return "Copy: Vertical -- copies are constrained along the CPlane normal", False
        if name == "InPlace":
            self._in_place = True
            # With a preselection the option is an immediate zero-delta copy;
            # no artificial base/destination points are requested.
            if self.step_index >= self._from_step_index() and self.args:
                return self.execute(ctx)
            return "Copy: InPlace -- duplicate at the original location", False
        return super().on_mode(name, ctx)

    def _normalised_destination(self, picked, ctx):
        """Turn one picked destination into its world-coordinate destination.

        This runs at collection time, not at Enter: FromLastPoint and the last
        vector therefore describe the preceding pick even though defers all
        document mutation until the command finishes.
        """
        if self._in_place:
            return (0.0, 0.0, 0.0)
        source = self.args[1]
        base = (self._last_destination
                if self.opt.get("FromLastPoint") and self._last_destination is not None
                else source)
        raw = _sub3(picked, base)
        if self._vertical:
            n = self._normal()
            scalar = cplane.dot(raw, n)
            vector = (n[0] * scalar, n[1] * scalar, n[2] * scalar)
        else:
            vector = raw
        previous = self._last_vector
        if previous is not None and self.opt.get("UseLastDistance"):
            length = math.sqrt(cplane.dot(vector, vector))
            old_length = math.sqrt(cplane.dot(previous, previous))
            if length > 1e-12 and old_length > 1e-12:
                vector = tuple(c * old_length / length for c in vector)
        if previous is not None and self.opt.get("UseLastDirection"):
            length = math.sqrt(cplane.dot(vector, vector))
            old_length = math.sqrt(cplane.dot(previous, previous))
            if length > 1e-12 and old_length > 1e-12:
                vector = tuple(c * length / old_length for c in previous)
        destination = (base[0] + vector[0], base[1] + vector[1], base[2] + vector[2])
        self._last_destination = destination
        self._last_vector = vector
        # Execute applies this displacement to the original geometry. Keeping
        # the vector (rather than only the point) is what makes FromLastPoint
        # observable: the next copy can be an incremental displacement.
        return vector

    def _feed(self, text, ctx):
        """Repeat the to-point until Enter; Enter builds every copy set at once.

        The FROM point is `args[-1]` here (the object set is `args[0]` and the
        from-point `args[1]`), so it is unambiguous. Collecting the to-points
        without mutating -- and duplicating in the single `execute` that the
        finishing Enter triggers -- is what makes the whole command one undo
        step: `feed` wraps `execute` in one `_recorded`, and every `note_change`
        captures into that one open record (nested `begin` joins it).
        """
        text = text.strip()
        # The repeated destination feeder is custom, but option matching remains
        # the Command contract (including bare-toggle values).
        if self.pending_option is not None:
            option = self.pending_option
            if not text:
                self.pending_option = None
                return f"{option.name} unchanged", False
            value, err = opts.coerce(option, text)
            if err:
                return err, False
            self.opt[option.name] = value
            self.pending_option = None
            hook = self.on_option_set(option, value, ctx)
            return (hook if hook is not None else f"{opts.format_option(option, self.opt)}", False)
        if self.options and text and not self._numeric_input_wins(text):
            match = opts.match(text, self.options, self.opt)
            if match.status != opts.NONE:
                return self._apply_option(match, ctx)
        if (self._in_place and self.step_index < len(self.steps)
                and self.steps[self.step_index][1] in SELECTION_KINDS):
            result = super()._feed(text, ctx)
            if not result[1] and self.step_index == self._from_step_index():
                return self.execute(ctx)
            return result
        if self.step_index == len(self.steps) - 1:
            if not text:
                # Enter at the to-step: build the copies now and finish.
                return self.execute(ctx)
            try:
                point = self._parse("point", text, ctx)
            except ValueError as e:
                return str(e), False          # a typo re-prompts, as the reference CAD application does
            self.args.append(self._normalised_destination(point, ctx))
            if self.complete_after_first:
                # mutation hook: complete after the first destination
                return self.execute(ctx)
            return ("pick another point to copy to, or Enter to end", False)
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        if not ctx.selected_objects():
            return "Nothing selected", True
        p_from = self.args[1] if len(self.args) > 1 else (0.0, 0.0, 0.0)
        # [selection-list, from, vector1, vector2,...]; InPlace needs no from.
        to_points = self.args[2:]
        if self._in_place:
            to_points = [(0.0, 0.0, 0.0)]
        if not to_points:
            return "Copy: no destination point given", True
        made = []
        for displacement in to_points:       # one independent copy set per to-point
            dx, dy, dz = displacement
            xform = r3.Transform.Translation(dx, dy, dz)
            for ob in ctx.selected_objects():
                name = _duplicate_with_transform(ctx, ob.name, xform, "Copy")
                if name:
                    made.append(name)
        return f"Created {len(made)} copy(ies): {', '.join(made)}", True


class ArrayLinearCmd(Command):
    """`Array` -- a rectangular X x Y x Z array in the active CPlane.

    MEASURED (research_all_13 #006), preselected object:
    "Number in X direction", "Number in Y direction", "Number in Z direction", then
    "X spacing or first reference point" (Y = 1) or "Unit cell or X spacing" (Y > 1),
    "Y spacing or first reference point" (Y > 1), "Press Enter to accept"; Enter creates.
    XNumber 3, YNumber 2, XSpacing 2, YSpacing 3 in the FRONT CPlane gave circles at
    x = 0, 2, 4 and world z = 0, 3 -- the spacings run along the CPlane axes, and the
    counts include the original. DERIVED: a Z spacing (asked only when Z > 1) runs along
    the CPlane normal; its prompt was not measured. OURS: the first counts default to
    2 x 1 x 1 and then remember the last values (MEASURED: the default is the last
    value); spacings are typed numbers -- reference-point picks are refused by name.

    MEASURED (research_preview_14 P28): a CLICK at "Unit cell or X spacing" is the first
    corner of a unit cell; "Other corner or length" draws the cell from it to the cursor, and
    the second corner gives XSpacing / YSpacing as the cell's size along the CPlane axes
    (signed: a corner to the -X side arrays toward -X). "Press Enter to accept" previews the
    copies. OURS: a typed length there, and a unit cell with Z > 1, are refused by name.
    """
    name = "Array"
    description = "Rectangular array of selected objects: X, Y, Z counts and spacings."
    ACCEPT = "Press Enter to accept:"
    UNIT_CELL = "Unit cell or X spacing:"
    OTHER_CORNER = "Other corner or length:"
    _cell_corner = None

    def _step_label(self):
        return self.steps[self.step_index][0] if self.step_index < len(self.steps) else None

    def _distance_base(self):
        return None

    def _parse_distance(self, text, ctx):
        return parse_number((text or "").strip())

    def offsets(self, plane):
        """The translation of every copy (the original excluded), once the spacings are in."""
        if len(self.args) < 4:
            return []
        nx, ny, nz = (int(v) for v in self.args[1:4])
        spacings = iter(float(v) for v in self.args[4:] if isinstance(v, (int, float)))
        try:
            sx = next(spacings) if nx > 1 else 0.0
            sy = next(spacings) if ny > 1 else 0.0
            sz = next(spacings) if nz > 1 else 0.0
        except StopIteration:
            return []
        x, y, z = (tuple(float(c) for c in a) for a in (plane.xaxis, plane.yaxis, plane.zaxis))
        return [tuple(i * sx * x[c] + j * sy * y[c] + k * sz * z[c] for c in range(3))
                for k in range(nz) for j in range(ny) for i in range(nx)
                if (i, j, k) != (0, 0, 0)]
    steps = [("Select objects to array:", OBJECTS_MULTI),
             ("Number in X direction:", NUMBER), ("Number in Y direction:", NUMBER),
             ("Number in Z direction:", NUMBER)]
    preselect_names = 1
    cancel_on_empty_set = True
    _last_counts = [2, 1, 1]

    def _count_index(self):
        return self.step_index - 1 if 1 <= self.step_index <= 3 else None

    @property
    def prompt(self):
        i = self._count_index()
        if i is not None and self.pending_option is None and self.step_index < len(self.steps):
            return f"{self.steps[self.step_index][0][:-1]} <{self._last_counts[i]}>:"
        return super().prompt

    def _enter_default(self, ctx):
        i = self._count_index()
        if i is not None:
            return float(self._last_counts[i])
        if self.step_index < len(self.steps) and self.steps[self.step_index][0] == self.ACCEPT:
            return ""
        return None

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        i = self._count_index()
        if i is not None and raw and self.pending_option is None:
            try:
                value = parse_number(raw)
            except ValueError:
                return f"{raw!r} is not a number", False
            if value != int(value) or value < 1:
                return "Number must be bigger than or equal to 1", False
        if (self.step_index > 3 and self.step_index < len(self.steps) and raw
                and self.steps[self.step_index][1] == NUMBER and self.pending_option is None):
            try:
                parse_number(raw)
            except ValueError:
                return ("Array: reference-point spacing and the unit cell are not "
                        "implemented yet -- type the spacing"), False
        label = self._step_label()
        if label == self.UNIT_CELL and raw and self.pending_option is None:
            try:
                parse_number(raw)
            except ValueError:
                if int(self.args[3]) > 1:
                    return ("Array: a unit cell with more than one Z level is not "
                            "implemented yet -- type the spacings"), False
                try:
                    self._cell_corner = parse_point(raw, self._active_cplane(ctx), None)
                except ValueError as e:
                    return str(e), False
                self.steps = self.steps[:self.step_index] + [(self.OTHER_CORNER, DISTANCE),
                                                             (self.ACCEPT, TEXT)]
                return None, False
        if label == self.OTHER_CORNER and raw and self.pending_option is None:
            try:
                parse_number(raw)
                return ("Array: a typed unit-cell length is not implemented yet -- pick "
                        "the other corner"), False
            except ValueError:
                pass
            plane = self._active_cplane(ctx)
            try:
                p = parse_point(raw, plane, self._cell_corner)
            except ValueError as e:
                return str(e), False
            d = _sub3(p, self._cell_corner)
            sx, sy = cplane.dot(d, plane.xaxis), cplane.dot(d, plane.yaxis)
            if abs(sx) < 1e-12 or abs(sy) < 1e-12:
                return "Array: the unit cell needs a width and a depth", False
            self.args += [sx, sy]
            self.step_index += 1
            return None, False
        if self.step_index == len(self.steps) - 1 and self.steps[-1][0] == self.ACCEPT and raw:
            return "Array: press Enter to accept", False
        if i == 2 and self.pending_option is None and (
                not raw or not (self.options and opts.match(raw, self.options, self.opt).status
                                != opts.NONE)):
            # the last count: the spacing steps depend on all three, so add them before
            # moving on (the base `_feed` would run `execute` at the end of `steps`).
            self.args.append(float(parse_number(raw)) if raw else float(self._last_counts[2]))
            self.step_index += 1
            self._add_spacing_steps()
            return None, False
        return super()._feed(text, ctx)

    def _add_spacing_steps(self):
        nx, ny, nz = (int(v) for v in self.args[1:4])
        ArrayLinearCmd._last_counts[:] = [nx, ny, nz]
        extra = []
        if nx > 1 and ny > 1:
            extra.append((self.UNIT_CELL, DISTANCE))       # a click starts a unit cell
        elif nx > 1:
            extra.append(("X spacing or first reference point:", NUMBER))
        if ny > 1:
            extra.append(("Y spacing or first reference point:", NUMBER))
        if nz > 1:
            extra.append(("Z spacing or first reference point:", NUMBER))
        extra.append((self.ACCEPT, TEXT))
        self.steps = self.steps[:4] + extra

    def execute(self, ctx: NarwhalContext):
        if not ctx.selected_objects():
            return "Nothing selected", True
        nx, ny, nz = (int(v) for v in self.args[1:4])
        spacings = iter(float(v) for v in self.args[4:-1])
        sx = next(spacings) if nx > 1 else 0.0
        sy = next(spacings) if ny > 1 else 0.0
        sz = next(spacings) if nz > 1 else 0.0
        if nx * ny * nz < 2:
            return "Array: 1 x 1 x 1 is only the original -- nothing was created", True
        plane = self._active_cplane(ctx)
        x, y, z = (tuple(float(c) for c in a) for a in (plane.xaxis, plane.yaxis, plane.zaxis))
        made = []
        for ob in ctx.selected_objects():
            for k in range(nz):
                for j in range(ny):
                    for i in range(nx):
                        if i == j == k == 0:
                            continue          # the count includes the original
                        d = [i * sx * x[c] + j * sy * y[c] + k * sz * z[c] for c in range(3)]
                        name = _duplicate_with_transform(ctx, ob.name,
                                                         r3.Transform.Translation(*d), "Array")
                        if name:
                            made.append(name)
        return f"Created {len(made)} array element(s)", True


class ArrayPolarCmd(Command):
    name = "ArrayPolar"
    description = "Polar array of selected objects about the CPlane normal."
    # MEASURED (research_all_13 #007): the centre prompt, Enter there = the CPlane origin;
    # at "Number of items" a count below 2 prints "Number must be bigger than or equal to
    # 2" and asks again, a non-whole count is silently asked again; then "Angle to fill or
    # first reference point <360>", Enter = 360, then "Press Enter to accept", Enter
    # creates. There is no axis question: the default axis is the CPlane normal through the
    # centre. Only the full 360 fill was measured; other fill angles are refused by name.
    steps = [("Select objects to array:", OBJECTS_MULTI),
             ("Center of polar array. Press Enter to use the CPlane origin:", POINT),
             ("Number of items:", NUMBER),
             ("Angle to fill or first reference point <360>:", NUMBER),
             ("Press Enter to accept:", TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True

    def _enter_default(self, ctx):
        if self.step_index == 1:
            return tuple(float(c) for c in self._active_cplane(ctx).origin)
        if self.step_index == 3:
            return 360.0
        if self.step_index == 4:
            return ""
        return None

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.pending_option is None and raw:
            if self.step_index == 2:
                try:
                    value = parse_number(raw)
                except ValueError:
                    value = None
                if value is not None and value != int(value):
                    return None, False
                if value is not None and value < 2:
                    return "Number must be bigger than or equal to 2", False
            if self.step_index == 4:
                return "ArrayPolar: press Enter to accept", False
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        center, count, fill = self.args[1:4]
        if not ctx.selected_objects():
            return "Nothing selected", True
        if abs(float(fill) - 360.0) > 1e-12:
            return ("ArrayPolar: a fill angle other than 360 is not implemented yet. "
                    "Nothing was created."), True
        count = int(count)
        axis = r3.Vector3d(*(float(c) for c in self._active_cplane(ctx).zaxis))
        step = math.radians(360.0 / count)
        made = []
        for ob in ctx.selected_objects():
            # the count INCLUDES the original; going to count+1 would put a copy back on
            # top of the original after a full turn.
            for i in range(1, count):
                xform = r3.Transform.Rotation(step * i, axis, r3.Point3d(*center))
                name = _duplicate_with_transform(ctx, ob.name, xform, "Array")
                if name:
                    made.append(name)
        return f"Created {len(made)} array element(s)", True


# ---------------------------------------------------------------------------
# boolean commands (BooleanUnion / BooleanDifference / BooleanIntersection)
#
# this comment used to say booleans "route through Blender's EXACT mesh solver" and
# that "the result is a mesh-brep: it loses analytic surfaces". That has been FALSE
# since the OCCT migration. `bridge.boolean_brep` tries OpenCASCADE first and a box unioned
# with a cylinder is 8 analytic faces {PLANE:7, CYLINDER:1}, airtight -- the mesh solver is
# now only an AUDIBLE fallback for operands with no recipe.
#
# The once-per-session warning flag that lived here is gone with it: the message is
# from the result (`kernel.describe`) every time, so it reports a mesh fallback WHENEVER one
# happens rather than once, and says nothing when the result is analytic.
# ---------------------------------------------------------------------------


def _remove_object_from_scene(ob):
    """Remove a Blender object and its mesh data (if now unused)."""
    import bpy

    me = ob.data
    bridge.note_display_changed([ob.name], removed=bridge.is_app_drawn(ob))
    bpy.data.objects.remove(ob, do_unlink=True)   # unlinks from every collection
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def _world_geom(ctx: NarwhalContext, name: str):
    """A private copy of the named object's geometry, in world coordinates.

    Since stored geometry is already in world coordinates, so this is just a
    defensive ``Duplicate`` — booleans mutate their operands, and we must not
    disturb the document copy before the operation is known to succeed.
    """
    geom = ctx.geom_by_name(name)
    if geom is None:
        return None
    return geom.Duplicate()


def _solid_brep_from_extrusion(ext):
    """A capped `r3.Extrusion` as a `r3.Brep`, carrying its analytic master.

    A capped extrusion is a valid solid, but it has no ``.Faces`` collection,
    which `boolean_brep`'s mesh fallback iterates (`Extrusion.Faces`
    raises `AttributeError`), so it must be handed to the kernel in Brep form.
    `ToBrep` DROPS the master and recipe (both user strings empty after
    the conversion), and losing the master would demote a correct analytic boolean
    to the faceted mesh route -- so carry both over onto the new Brep.
    """
    from . import kernel

    brep = ext.ToBrep(False)
    if brep is None or len(brep.Faces) == 0:
        return None
    master = kernel.master_of(ext)
    if master is not None:
        kernel.register_master(brep, master)
    recipe = kernel.recipe_of(ext)
    if recipe:
        kernel.record_recipe(brep, recipe)
    return brep


class _BooleanCmd(Command):
    """Apply an ordered Boolean operation and replace its operands with the result.

    ``BooleanUnion`` consumes one complete selection set. ``BooleanDifference`` and
    ``BooleanIntersection`` consume two ordered sets. Subclasses declare those steps, and
    execution folds all operands from left to right. Preselection fills the first set
    without any special case for operand count.

    ``DeleteInput`` is shared by all three commands and defaults to preserving the prior
    behavior of deleting operands. Difference also supports conditional cutter deletion.
    ``MergeCoplanarFaces`` is not implemented.
    """

    name = "Boolean"
    operation = "UNION"
    steps = [("Select objects to combine:", OBJECTS_MULTI)]
    # opt into the selection fill, so "select the solids, then run the command"
    # works with ANY count -- which is the reference CAD application's normal flow and what the removed
    # exactly-two shortcut was trying to approximate. 1 means "fill one SET step";
    # a two-set command still prompts for its second set.
    preselect_names = 1

    def _operand_names(self):
        """Flatten `self.args` into an ordered, de-duplicated list of names.

        ``args`` entries are lists, one per ``OBJECTS_MULTI`` step. A plain string is
        also accepted so direct callers and macros can pass names positionally.
        """
        out = []
        for a in self.args:
            for n in (a if isinstance(a, (list, tuple)) else [a]):
                if n not in out:
                    out.append(n)
        return out

    def execute(self, ctx: NarwhalContext):
        names = self._operand_names()
        if len(names) < 2:
            # the reference CAD application needs at least two operands. Report the count rather than
            # failing obscurely inside the solver.
            return (f"{self.name} needs at least two objects "
                    f"({len(names)} given)"), True

        geoms = []
        for name in names:
            geom = _world_geom(ctx, name)
            if geom is None:
                return f"No solid named {name!r}", True
            if isinstance(geom, r3.Extrusion):
                # A capped extrusion is a solid; the previous
                # isinstance-Brep gate rejected it before the kernel ever ran
                # ("Extrusion1 is not a solid (brep)"), even though the kernel
                # booleans it exactly. An OPEN extrusion (uncapped) is a surface,
                # not a solid, and is refused by name.
                if not (geom.IsCappedAtTop and geom.IsCappedAtBottom):
                    return f"{name} is not a solid (an uncapped extrusion)", True
                geom = _solid_brep_from_extrusion(geom)
                if geom is None:
                    return f"{name} could not be used as a solid", True
            elif not isinstance(geom, r3.Brep):
                return f"{name} is not a solid (brep)", True
            geoms.append(geom)

        # inherit the FIRST operand's layer for the result
        layer_index = 0
        first = names[0]
        for fobj in ctx.model.file.Objects:
            if (fobj.Attributes.Name or "").lower() == first.lower():
                layer_index = int(fobj.Attributes.LayerIndex)
                break

        # Fold left: (((A op B) op C) op D). For UNION and INTERSECTION this is the
        # whole story. For DIFFERENCE the first set is subtracted FROM, so every
        # later operand is subtracted from the accumulating result -- which is what
        # folding left already does, and why the set ORDER is preserved above.
        try:
            result = geoms[0]
            for other in geoms[1:]:
                result = bridge.boolean_brep(result, other, self.operation)
        except RuntimeError as e:
            return f"Boolean failed: {e}", True

        # Capture once before the first document mutation. Command.feed brackets
        # the whole operation, so all removals and the result insertion remain one
        # undo record even when there are many operands.
        note_change = getattr(ctx, "note_change", None)
        if callable(note_change):
            note_change(self.name)

        # the pre-existing command deleted every operand by default. the reference CAD application's
        # retained help does not state the default, so DeleteInput defaults to Yes
        # here to preserve that behavior. With DeleteInput=No every input remains.
        delete_input = bool(self.opt.get("DeleteInput", True))
        if self.operation == "DIFFERENCE":
            # DeleteCutters is meaningful only when DeleteInput=Yes. The option
            # hook refuses the contradictory setting, and this guard also keeps a
            # direct execute caller from silently deleting cutters.
            delete_names = names if delete_input else []
            if delete_input and not bool(self.opt.get("DeleteCutters", True)):
                delete_names = names[:1]
        else:
            delete_names = names if delete_input else []

        for name in delete_names:
            ob = ctx.object_by_name(name)
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)

        _, display_name = ctx.add_geometry(result, "Boolean", layer_index)

        # Derive the note from the actual result rather than a fixed representation claim.
        # ``kernel.describe`` reads the representation tag and verifies proxy masters, so
        # the message remains consistent with the geometry.
        note = ""
        try:
            from . import kernel

            described = kernel.describe(result)
            if "faceted" in described or "BROKEN" in described:
                why = bridge.last_occt_decline()
                note = (f" Note: this result is {described}"
                        + (f" ({why})" if why else "")
                        + " -- the analytic kernel declined, so it went through the mesh "
                          "solver.")
        except Exception:  # noqa: BLE001
            note = ""
        return f"Created {display_name}{note}", True


class BooleanUnionCmd(_BooleanCmd):
    name = "BooleanUnion"
    description = "Combine solids into one. Select the objects and press Enter."
    operation = "UNION"
    options = (
        # default: the old command deleted operands, and the reference CAD application help does not
        # retain a default value. DeleteInput=No is the keep-input path.
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Deletes (Yes) or keeps (No) the input objects."),
    )
    # (the command behavior, the whole Steps section): "Select the objects and
    # press Enter." ONE set, any number of objects.
    steps = [("Select objects to union:", OBJECTS_MULTI)]


class BooleanDifferenceCmd(_BooleanCmd):
    name = "BooleanDifference"
    description = ("Subtract solids from others. Select what to subtract FROM, "
                   "press Enter, then what to subtract WITH, press Enter.")
    operation = "DIFFERENCE"
    options = (
        # default preserves the old all-input deletion behavior; the help page
        # documents the toggle but not its initial value.
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Controls whether the original geometry is deleted."),
        opts.Option("DeleteCutters", opts.TOGGLE, default=True,
                    help="When DeleteInput=Yes, deletes the objects used to subtract."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "DeleteCutters" and value and not self.opt.get("DeleteInput", True):
            return ("BooleanDifference: DeleteCutters requires DeleteInput=Yes", False)
        return None

    # (the command behavior -> BooleanDifference): "Select surfaces or
    # polysurfaces to subtract from, and press Enter. Select surfaces or
    # polysurfaces to subtract with, and press Enter." TWO sets, and the ORDER is
    # the direction -- the first set is subtracted FROM.
    steps = [("Select surfaces or polysurfaces to subtract from:", OBJECTS_MULTI),
             ("Select surfaces or polysurfaces to subtract with:", OBJECTS_MULTI)]


class BooleanIntersectionCmd(_BooleanCmd):
    name = "BooleanIntersection"
    description = ("Keep only the overlap of solids. Select the first set, press "
                   "Enter, then the second set, press Enter.")
    operation = "INTERSECT"   # Blender's modifier enum value, not "INTERSECTION"
    options = (
        # default: preserve the pre-existing deletion behavior.
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Controls whether the original geometry is deleted."),
    )
    # (the command behavior -> BooleanIntersection): "Select the first set of
    # objects or a set of intersecting objects and press Enter. If a first set of
    # objects was selected, select the second set of objects and press Enter."
    steps = [("Select the first set of objects:", OBJECTS_MULTI),
             ("Select the second set of objects:", OBJECTS_MULTI)]


def _mirror_xform(origin, normal):
    n = cplane.normalize(normal)
    if n is None:
        return None
    m3 = [[(1.0 if r == c else 0.0) - 2.0 * n[r] * n[c] for c in range(3)] for r in range(3)]
    return _affine(m3, origin)


class MirrorCmd(_TransformCmd):
    """`Mirror` -- the command behavior.

    steps: "Select the objects. Pick the start of the mirror plane. Pick the end
    of the mirror plane... The two end points specify a mirror plane perpendicular to the
    construction plane." -- the ACTIVE CPlane (the old code hard-wired world Z). Prompts
    "Start of mirror plane" / "End of mirror plane", and for `3Point` "First/Second/
    Third point of mirror plane" in the command UI. Options built: `Copy` (default Yes --
    the mirror IMAGE, as before), `3Point`, `XAxis` ("across the CPlane x axis (xz
    plane)"), `YAxis` (yz plane), `ZAxis` ("across the CPlane (xy plane)") -- the last
    three act at once, through the CPlane origin. Declared: `Object`, `SubCrv`.
    """

    name = "Mirror"
    verb = "Mirrored"
    description = "Mirror selected objects across a plane perpendicular to the CPlane."
    steps = [("Select objects to mirror:", OBJECTS_MULTI),
             ("Start of mirror plane:", POINT), ("End of mirror plane:", POINT)]
    options = (
        opts.Option("3Point", opts.MODE, help="Pick three points to define a mirror plane."),
        _copy_option(True),
        opts.Option("XAxis", opts.MODE,
                    help="Mirrors across the CPlane x axis (xz plane)."),
        opts.Option("YAxis", opts.MODE,
                    help="Mirrors across the CPlane y axis (yz plane)."),
        opts.Option("ZAxis", opts.MODE, help="Mirrors across the CPlane (xy plane)."),
        opts.Option("Object", opts.MODE, help="Select a planar surface as the mirror plane."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )

    def __init__(self):
        super().__init__()
        self._three = False

    def on_mode(self, name, ctx):
        if name == "3Point":
            if len(self.args) > 1:
                return "Mirror: 3Point is chosen before the first plane point", False
            self._three = True
            self.steps[1:] = [("First point of mirror plane:", POINT),
                              ("Second point of mirror plane:", POINT),
                              ("Third point of mirror plane:", POINT)]
            return "Mirror: 3Point -- pick three points on the mirror plane", False
        if name in ("XAxis", "YAxis", "ZAxis"):
            if not self.args:
                return f"Mirror: select the objects before {name}", False
            p = self._plane = self._active_cplane(ctx)
            normal = {"XAxis": p.yaxis, "YAxis": p.xaxis, "ZAxis": p.zaxis}[name]
            return self._commit(ctx, _mirror_xform(tuple(p.origin), tuple(normal)))
        return super().on_mode(name, ctx)

    def _compute(self, args, live=False):
        pts = args[1:]
        if self._three:
            if len(pts) < 3:
                return None
            normal = cplane.cross(_sub3(pts[1], pts[0]), _sub3(pts[2], pts[0]))
        else:
            if len(pts) < 2:
                return None
            normal = cplane.cross(_sub3(pts[1], pts[0]), self._z)
        x = _mirror_xform(pts[0], normal)
        if x is None:
            return None if live else "those points do not define a mirror plane"
        return x


class JoinCmd(Command):
    """`Join` -- the command behavior. Keyboard shortcut **Ctrl+J**.

        "The Join command connects objects together to form a single object. Join
        turns lines into polylines, curves into polycurves"... "You can join
        curves that touch end-to-end."... "The order the objects are selected
        determines the layer of the new joined object."

    Only **curves** are joined. the reference CAD application also joins surfaces, polysurfaces, meshes
    and SubDs; those need a kernel we do not have, so they are REPORTED as not
    joined rather than quietly ignored (the explicit-refusal policy). The documented options
    (`Undo`, `JoinDisjointMeshes`, `SubDJoinedEdges`, `SelChain`, `JoinCopy`) are
    likewise not declared, because declaring an option we cannot honour would make
    it look implemented.
    """

    name = "Join"
    description = ("Join selected curves that touch end-to-end into a polyline "
                   "or polycurve.")
    # (the command behavior) "Select the objects... to join...
    # press Enter." Ask for the selection (a set-step) instead of refusing when
    # it is empty; a pre-selection fills it and runs immediately, an empty
    # confirm cancels. Same shape as Delete. `execute` still reads the
    # live selection -- a single set-step is never cleared before `execute`.
    steps = [("Select objects to join:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def _tolerance(self, ctx=None):
        """The gap still counted as touching.

        the reference CAD application judges curve ends against the document's **absolute tolerance** -- `ctx.model`'s (`_document_tolerance`), no longer the 0.01 default.
        """
        return _document_tolerance(ctx)

    def _ordered_selection(self, ctx):
        """Selected object names, the ACTIVE one first.

        the reference CAD application's rule is selection order, and Blender does not record it -- so the
        active object (the last one clicked) leads, exactly as `_BooleanCmd`
        resolves its A operand. That is an approximation of the documented rule,
        and it only affects which LAYER the result lands on.
        """
        names = [ob.name for ob in ctx.selected_objects()]
        try:
            import bpy
            active = bpy.context.view_layer.objects.active
            active = active.name if active else None
        except Exception:  # noqa: BLE001
            active = None
        if active in names:
            names.remove(active)
            names.insert(0, active)
        return names

    def execute(self, ctx: NarwhalContext):
        names = self._ordered_selection(ctx)
        if len(names) < 2:
            return "Join: select at least two objects to join", True

        items, non_curves = [], []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is not None and bridge.curve_ends(geom) is not None:
                items.append((name, geom))
            else:
                non_curves.append(name)

        if len(items) < 2:
            if non_curves:
                return ("Join: only curves can be joined yet -- joining "
                        "surfaces, polysurfaces and meshes is not implemented"), True
            return "Join: select at least two curves", True

        tol = self._tolerance(ctx)
        chains = bridge.join_curves(items, tol)
        if not chains:
            return ("Join: those curves do not touch end-to-end "
                    f"(within {tol:g})"), True

        ctx.note_change("Join")
        made = []
        for keys, geom, closed in chains:
            # The FIRST key of the chain is the earliest selected member, so its
            # layer is the one the help says the result inherits.
            layer_index = _layer_index_of(ctx, keys[0])
            for name in keys:
                ob = ctx.object_by_name(name)
                if ob is not None:
                    _remove_object_from_scene(ob)
                oid = ctx.model.object_id_by_name(name)
                if oid is not None:
                    ctx.model.remove_object(oid)
            base = "Polyline" if isinstance(geom, r3.PolylineCurve) else "Curve"
            _, display = ctx.add_geometry(geom, base, layer_index)
            made.append((display, len(keys), closed))

        left = sum(len(keys) for keys, _, _ in chains)
        parts = [f"{d} ({n} curves{', closed' if c else ''})" for d, n, c in made]
        msg = f"Joined {left} curves into {len(made)}: " + "; ".join(parts)
        if non_curves:
            msg += (f". Not joined ({len(non_curves)}): only curves can be joined "
                    f"yet -- surfaces, polysurfaces and meshes are not implemented")
        return msg, True


class CloseCrvCmd(Command):
    """`CloseCrv` -- the command behavior.

    (the command reference CloseCrv):
      STEPS:
        Select open curves and press Enter.
        The number of curves closed by adding segments and the number closed by
        moving endpoints are reported.
      COMMAND-LINE OPTIONS:
        CloseWideGapsWithLine (Yes/No) Gaps larger than the tolerance setting
          would be closed with a line (Yes), or left open (No).
        Tolerance: The degree of accuracy required. If Tolerance is set to zero,
          all selected open curves are closed by moving endpoints.

    For each selected open curve the gap between its endpoints is * gap <= tolerance (or tolerance == 0) -> close by MOVING one endpoint to
        coincide with the other via `bridge.set_control_point` (no new geometry
        is built; the curve's analytic structure is preserved).
      * gap > tolerance AND CloseWideGapsWithLine -> close by ADDING a line
        segment across the gap (`bridge.make_line` + `bridge.join_curves`, the
        same route `Join` uses for building closed chains).
      * gap > tolerance AND NOT CloseWideGapsWithLine -> LEFT OPEN (reported,
        never silently closed).

    Tolerance = 0 is the special case: every open curve is closed by
    moving its endpoint, regardless of gap.
    """

    name = "CloseCrv"
    description = ("Close open curves by moving endpoints or adding line segments.")
    steps = [("Select open curves to close:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True
    options = (
        opts.Option("CloseWideGapsWithLine", opts.TOGGLE, default=True,
                    help="Gaps larger than the tolerance setting would be closed "
                         "with a line (Yes), or left open (No)."),
        opts.Option("Tolerance", opts.VALUE, default=0.0, value_kind="number",
                    help="The degree of accuracy required. If Tolerance is set "
                         "to zero, all selected open curves are closed by moving "
                         "endpoints."),
    )

    def _effective_tolerance(self, ctx):
        """The tolerance to use. Tolerance = 0 is the special case:
        every open curve is closed by moving its endpoint regardless of gap."""
        val = self.opt.get("Tolerance")
        if val is not None and val > 0:
            return float(val)
        return 0.0

    def execute(self, ctx: NarwhalContext):
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "CloseCrv: select at least one open curve", True

        close_wide = bool(self.opt.get("CloseWideGapsWithLine", True))
        tol = self._effective_tolerance(ctx)

        moved = 0
        added = 0
        left_open = 0
        not_curves = []
        already_closed = []

        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                continue
            if bridge.curve_ends(geom) is None:
                not_curves.append(name)
                continue
            if geom.IsClosed:
                already_closed.append(name)
                continue

            start, end = bridge.curve_ends(geom)
            gap = math.dist(start, end)

            if tol == 0 or gap <= tol:
                # Close by moving the last endpoint to coincide with the first.
                n = bridge.control_point_count(geom)
                if n >= 2:
                    ctx.note_change(self.name)
                    bridge.set_control_point(geom, n - 1, start)
                    moved += 1
                else:
                    left_open += 1
            else:
                if close_wide:
                    # Close by adding a line segment from end to start.
                    line = bridge.make_line(end, start)
                    joined = bridge.join_curves(
                        [(0, geom), (1, line)], tol)
                    if joined:
                        _keys, new_geom, _closed = joined[0]
                        ctx.note_change(self.name)
                        layer_index = _layer_index_of(ctx, name)
                        oid = ctx.model.object_id_by_name(name)
                        if oid is not None:
                            ctx.model.remove_object(oid)
                        ob = ctx.object_by_name(name)
                        if ob is not None:
                            _remove_object_from_scene(ob)
                        ctx.add_geometry(new_geom, "Curve", layer_index)
                        added += 1
                    else:
                        left_open += 1
                else:
                    left_open += 1

        # Build the report in the order: "adding segments" first,
        # then "moving endpoints".
        parts = []
        if added:
            parts.append(f"{added} by adding segments")
        if moved:
            parts.append(f"{moved} by moving endpoints")
        if left_open:
            parts.append(f"{left_open} left open")
        if not_curves:
            parts.append(f"{len(not_curves)} not a curve")
        if already_closed:
            parts.append(f"{len(already_closed)} already closed")

        if not (added or moved):
            msg = "CloseCrv: no open curves closed"
            extras = []
            if left_open:
                extras.append(f"{left_open} left open")
            if not_curves:
                extras.append(f"{len(not_curves)} not a curve")
            if already_closed:
                extras.append(f"{len(already_closed)} already closed")
            if extras:
                msg += " (" + ", ".join(extras) + ")"
            return msg, True

        return "Closed " + " and ".join(parts) + ".", True


class ExplodeCmd(Command):
    """`Explode` -- the command behavior. The inverse of `Join`.

    (the command behavior, results table):

        Polycurve   -> "Single segment curves."
        Polyline    -> "Single segment lines/curves."
        Polysurface -> "Surfaces."

    Steps: *"Select objects."* -- so it is selection-driven with no prompts,
    exactly like `Delete`. The kernel halves are `bridge.explode_curve` and
    `bridge.explode_brep`, each returning None when there is nothing to split.

    **Curves and polysurfaces** are exploded. A master-backed solid explodes into its
    analytic kernel faces (each its own master, with a `face` recipe so it survives a
    reload); a plain brep explodes natively into trimmed faces. the reference CAD application's Explode also handles
    blocks, dimensions, groups, hatches, meshes, cage controls and text; we have none of
    that, so such an object is REPORTED as not exploded rather than silently dropped (house
    the explicit-refusal policy). An object already a single segment or surface is reported as such -- that is a
    different answer from success.
    """

    name = "Explode"
    description = ("Break selected objects into their parts (polycurves into "
                   "segment curves, polylines into segment lines, polysurfaces into "
                   "surfaces).")
    # (the command behavior) "Select objects." Ask for the
    # selection like Delete; `execute` still reads the live selection.
    steps = [("Select objects to explode:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def execute(self, ctx: NarwhalContext):
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "Explode: select objects to explode", True

        # Sort the selection by WHAT it is (the explicit-refusal policy: anything we cannot explode is
        # reported, never quietly ignored). adds POLYSURFACES --
        # (the command behavior) "Polysurface -- Surfaces." -- which earlier implementations recorded as
        # blocked; see `bridge.explode_brep` for why that was only true of one route.
        exploded = []          # (name, pieces, what) -- what is "segments" or "surfaces"
        already_single, unexplodable = [], []
        analytic_sources = set()
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is not None and bridge.curve_ends(geom) is not None:
                pieces = bridge.explode_curve(geom)
                if pieces is None:
                    already_single.append(name)
                else:
                    exploded.append((name, pieces, "segments"))
                continue
            if geom is not None and getattr(geom, "Faces", None) is not None:
                result = bridge.explode_brep(geom)
                if result is None:
                    already_single.append(name)      # one face: already a single surface
                else:
                    pieces, source = result
                    analytic_sources.add(source)
                    exploded.append((name, pieces, "surfaces"))
                continue
            unexplodable.append(name)

        if not exploded:
            if already_single:
                return ("Explode: nothing to explode -- "
                        f"{len(already_single)} selected object(s) are already a single "
                        "segment or surface"), True
            return ("Explode: only curves and polysurfaces can be exploded yet -- "
                    "exploding blocks, meshes, hatches and groups is not "
                    "implemented"), True

        ctx.note_change("Explode")
        counts = {"segments": 0, "surfaces": 0}
        for name, pieces, what in exploded:
            # each set of pieces inherits the layer of the object it came from
            layer_index = _layer_index_of(ctx, name)
            ob = ctx.object_by_name(name)
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)
            for piece in pieces:
                if what == "surfaces":
                    base = "Surface"
                else:
                    base = "Line" if isinstance(piece, r3.LineCurve) else "Curve"
                ctx.add_geometry(piece, base, layer_index)
                counts[what] += 1

        made = ", ".join(f"{n} {w}" for w, n in counts.items() if n)
        msg = f"Exploded {len(exploded)} object(s) into {made}"
        if "master" in analytic_sources:
            # Say so: these pieces are the kernel's ANALYTIC faces, not the display mesh's
            # triangles, and that is the thing a user exploding to verify wants to know.
            msg += " (analytic faces from the kernel)"
        extras = []
        if already_single:
            extras.append(f"{len(already_single)} already a single segment or surface")
        if unexplodable:
            extras.append(f"{len(unexplodable)} not explodable (blocks, meshes, hatches "
                          "and groups are not implemented)")
        if extras:
            msg += ". Not exploded: " + "; ".join(extras)
        return msg, True


class ProjectCmd(Command):
    """`Project` -- project selected curves and points onto the active CPlane.

    (the command behavior):

        Steps: *"Select curves and points to project. Select the target object."*
        Options:
          * ``Loose``       -- "Projects edit points toward the specified
                               direction onto the surface..." (a surface mode)
          * ``DeleteInput`` -- "Controls whether the original geometry is
                               deleted."
          * ``OutputLayer`` -- Current | Input | TargetObject
          * ``Direction``   -- CPlaneZ ("...construction plane z direction") or
                               View ("...view direction")

    **Scope this implementation: onto the ACTIVE CPlane, direction CPlaneZ.**
    The kernel half is exact (`narwhal3d/project.py`): a NURBS projects
    to the NURBS of its projected control points -- same knots, weights, domain --
    and a single point projects to a single point. `project_curve` / `project_point`
    raise ValueError with a message written to be surfaced verbatim, so a refusal
    (a curve perpendicular to the plane, collapsing to a point) is a message, not
    a crash (the explicit-refusal policy).

    **`Direction=View` and `Loose` report rather than approximate.** View needs
    the pane's camera, which is supplied by the UI layer. It is unavailable here,
    and projecting with CPlaneZ when the user asked for the view
    direction would be a silent lie. Loose is a *surface* projection mode; onto a
    plane the exact and loose results coincide, so deliberately offers no
    `loose` argument, and a `Loose` that did nothing would be a lie. `OutputLayer`
    is out of scope this implementation. All three therefore REPORT "not implemented yet"
    through `on_option_set` -- and `execute` re-checks them as a guard, so a run
    reached with one of them set projects nothing rather than the wrong thing.

    **Selection** is 's `objects` step: satisfied by the SELECTION (or a
    typed name). As with the other `objects`-step commands (ExtrudeCrv, Offset,
    BoundingBox) it takes the first selected object -- a multi-selection projects
    the first, which is the step's shape, not a special case here.
    """

    name = "Project"
    description = ("Project the selected curve or point onto the active "
                   "construction plane (CPlaneZ).")
    # One `objects` step: the curve or point to project. It is the ONLY prompt --
    # the reference CAD application's "select the target object" step is the active CPlane here (the
    # scope for this implementation), so there is no second selection to ask for.
    steps = [("Object:", OBJECTS)]
    # preselect_names=1: if something is already selected, `begin` fills the
    # `objects` step from it and runs, as the reference CAD application does (select, then run).
    preselect_names = 1
    # the four documented options, declared so the prompt is honest. Only
    # CPlaneZ + DeleteInput are implemented this implementation; the rest REPORT (the explicit-refusal policy).
    options = (
        opts.Option("Direction", opts.ENUM,
                    default="CPlaneZ",
                    choices=("CPlaneZ", "View"),
                    help=("CPlaneZ projects in the construction plane z "
                          "direction; View projects in the view direction.")),
        opts.Option("Loose", opts.TOGGLE, default=False,
                    help=("Projects edit points toward the direction onto the "
                          "surface (a surface mode; not offered for a plane).")),
        opts.Option("DeleteInput", opts.TOGGLE, default=False,
                    help="Controls whether the original geometry is deleted."),
        opts.Option("OutputLayer", opts.ENUM,
                    default="Current",
                    choices=("Current", "Input", "TargetObject"),
                    help="The layer the projected geometry is placed on."),
    )

    def on_option_set(self, option, value, ctx):
        """Apply (or honestly refuse) an option value as it is set.

        CPlaneZ and DeleteInput are implemented and fall through to the default
        `Name=Value` acknowledgement. View, Loose and OutputLayer are not
        implementable this implementation, so they REPORT rather than look accepted --
        an unimplemented option must never look implemented (the explicit-refusal policy).
        """
        if option.name == "Direction" and value == "View":
            return ("Project: Direction=View is not implemented yet (it needs "
                    "the viewport's camera, which is not available here)", False)
        if option.name == "Loose" and value:
            return ("Project: Loose is not implemented yet (it is a surface-"
                    "projection mode; onto a plane the exact and loose results "
                    "coincide, so it is deliberately not offered)", False)
        if option.name == "OutputLayer" and value != "Current":
            return ("Project: OutputLayer is not implemented yet (projection uses "
                    "the active CPlane on the current layer)", False)
        return None

    def execute(self, ctx: NarwhalContext):
        # Guard the not-implemented options here too, so a run that IS reached
        # with one set projects NOTHING rather than the wrong thing (the explicit-refusal policy).
        if (self.opt.get("Direction", "CPlaneZ") or "CPlaneZ") != "CPlaneZ":
            return ("Project: Direction=View is not implemented yet (it needs "
                    "the viewport's camera, which is not available here)", True)
        if self.opt.get("Loose"):
            return ("Project: Loose is not implemented yet (it is a surface-"
                    "projection mode; onto a plane the exact and loose results "
                    "coincide, so it is deliberately not offered)", True)
        if (self.opt.get("OutputLayer", "Current") or "Current") != "Current":
            return ("Project: OutputLayer is not implemented yet (projection uses "
                    "the active CPlane on the current layer)", True)

        name = self.args[0] if self.args else None
        if not name:
            sel = ctx.selected_objects()
            name = sel[0].name if sel else None
        if not name:
            return "Project: select a curve or point to project", True
        geom = ctx.geom_by_name(name)
        if geom is None:
            return f"No object named {name!r}", True

        is_curve = isinstance(geom, r3.Curve)
        is_point = isinstance(geom, r3.Point)
        if not (is_curve or is_point):
            return (f"Project: {name!r} is not a curve or a point (projecting "
                    "other geometry is not implemented yet)", True)

        # scope: onto the ACTIVE CPlane, along its normal (CPlaneZ).
        plane = self._active_cplane(ctx)
        try:
            if is_curve:
                result = project.project_curve(geom, plane,
                                               tolerance=_document_tolerance(ctx))
            else:
                loc = geom.Location
                result = bridge.make_point(project.project_point(loc, plane))
        except ValueError as e:
            return str(e), True

        delete_input = bool(self.opt.get("DeleteInput"))
        # one undo record for the whole change. note_change fires BEFORE
        # the document mutates (the add), so a DeleteInput removal + the add are
        # a single undo step -- the same shape as Explode.
        ctx.note_change(self.name)
        layer_index = _layer_index_of(ctx, name)
        if delete_input:
            ob = ctx.object_by_name(name)
            if ob is not None:
                _remove_object_from_scene(ob)
            oid = ctx.model.object_id_by_name(name)
            if oid is not None:
                ctx.model.remove_object(oid)
        _, display_name = ctx.add_geometry(result, "Projection", layer_index)
        extra = " (input deleted)" if delete_input else ""
        return (f"Projected {name} onto the construction plane -> "
                f"{display_name}{extra}"), True


class ProjectToCPlaneCmd(Command):
    """Flatten selected objects onto the active viewport's construction plane.

    (the command behavior): the target is "the construction plane in the
    viewport that is current when the command ends"; every control point is projected
    and its structure retained; the user chooses whether to keep or delete inputs.
    (`bridge.project_to_cplane_geometry`) proves the exact supported geometry and
    names every refusal -- no display mesh is ever promoted to document geometry.
    """
    name = "ProjectToCPlane"
    description = "Flatten selected objects onto the active construction plane."
    # MEASURED (research21 tooling probe): after the selection, "Delete input objects? <No>
    # ( No  Yes ):" -- Enter keeps the inputs.
    DELETE_PROMPT = "Delete input objects? <No>:"
    steps = [("Select objects to project:", OBJECTS_MULTI), (DELETE_PROMPT, TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("DeleteInput", opts.TOGGLE, default=False,
                    help="Delete the input objects after creating their projections."),
    )

    @property
    def prompt(self):
        if self.step_index == 1 and self.pending_option is None:
            return "Delete input objects? <No> ( No  Yes ):"
        return super().prompt

    def _feed(self, text, ctx):
        if self.step_index == 1 and self.pending_option is None:
            raw = (text or "").strip().lower()
            if raw in ("", "n", "no"):
                self.opt["DeleteInput"] = False
            elif raw in ("y", "yes"):
                self.opt["DeleteInput"] = True
            else:
                return "Please answer No or Yes", False
            self.args.append(raw)
            self.step_index = 2
            return self.execute(ctx)
        return super()._feed(text, ctx)

    def _names(self):
        if not self.args:
            return []
        value = self.args[0]
        return list(dict.fromkeys(value if isinstance(value, (list, tuple)) else [value]))

    def execute(self, ctx: NarwhalContext):
        names = self._names()
        if not names:
            return "ProjectToCPlane: select objects to project", True
        # Read the CPlane NOW, not at command start: this is exactly the viewport that is
        # current when the command ends (the reference CAD application's wording).
        plane = self._active_cplane(ctx)
        plan = []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                return f"ProjectToCPlane: no object named {name!r} -- nothing projected", True
            prepared = geom
            if isinstance(geom, r3.Brep) and bridge.picture_frame(geom) is None:
                source = next((fobj for fobj in ctx.model.file.Objects
                               if (fobj.Attributes.Name or "").lower() == name.lower()), None)
                derive_frame = getattr(ctx, "_texture_frame", None)
                frame = (derive_frame(geom, source)
                         if source is not None and callable(derive_frame) else None)
                if frame is not None:
                    prepared = geom.Duplicate()
                    if prepared is None:
                        return (f"ProjectToCPlane: {name}: Picture could not be duplicated "
                                "-- nothing projected"), True
                    from . import picture

                    prepared.SetUserString("Narwhal3D:Picture", picture.frame_to_json(frame))
            out, msg = bridge.project_to_cplane_geometry(prepared, plane)
            if out is None:
                return f"ProjectToCPlane: {name}: {msg} -- nothing projected", True
            plan.append((name, out, _layer_index_of(ctx, name)))

        ctx.note_change(self.name)
        if self.opt.get("DeleteInput"):
            for name, _out, _layer in plan:
                ob = ctx.object_by_name(name)
                if ob is not None:
                    _remove_object_from_scene(ob)
                oid = ctx.model.object_id_by_name(name)
                if oid is not None:
                    ctx.model.remove_object(oid)
        made = []
        for _name, out, layer in plan:
            _i, display = ctx.add_geometry(out, "Projection", layer)
            made.append(display)
        extra = "; inputs deleted" if self.opt.get("DeleteInput") else "; inputs kept"
        return f"Projected {len(made)} object(s) to the construction plane{extra}", True


class ExtendCmd(Command):
    """`Extend` -- the command behavior. Extend a curve's end by a typed length.

    (the command behavior):

        Steps:
          * "Select a curve or an surface edge near the end."
          * "Pick the end of the extension or type a length."
        Options:
          * ``Type`` -- Natural | Line | Arc | Smooth
            (Natural: "Lines, polylines, and polycurves with a line at the end
             to extend are extended by line. Arcs and polycurves with arc at the
             end to extend are extended by same radius arc. Other curves are
             extended smooth." Line: "Makes a line extension tangent to the
             original curve." Arc: "Makes an arc extension tangent to the
             original curve." Smooth: "Makes a smooth curve extension curvature
             continuous with the original curve.")

    **Scope this implementation: the typed-LENGTH form, Type = Natural | Line |
    Arc.** The kernel half is exact (`narwhal3d/extend.py`): a line
    extending a line MERGES to one longer line, and a same-radius arc extending
    an arc MERGES to one wider arc on the same circle -- exactly as Where the extension is a different kind of geometry (a tangent line off a
    free-form curve) it is JOINED as a PolyCurve segment, which is the
    documented fallback, not a shortcut.

    **`Type=Smooth` and `Natural` on a free-form curve REPORT rather than
    approximate.** The kernel (`extend.extend_curve`) raises ValueError with a
    message written to be surfaced verbatim -- Smooth is curvature-continuous
    (a fit, not a formula), and `Natural` on a free-form resolves to Smooth by
    the rule ("Other curves are extended smooth"). Silently falling
    back to a tangent line where the reference CAD application makes a smooth curve would be a lie, so
    the refusal is surfaced, not hidden (the explicit-refusal policy). The same path surfaces
    the kernel's other refusals: `Arc` on a non-arc end, a closed curve, and a
    non-positive length.

    **The `end` is fixed at the curve's End.** the reference CAD application picks the end you clicked
    NEAREST ("Select a curve... near the end"); with a typed length and no
    click there is no click to resolve, so this defaults to the End. The
    near-end (Start) selection and the boundary-object form ("Select a boundary
    object") are OUTSTANDING -- the latter needs curve/curve intersection
    against the boundary and is a separate task. `Type=Arc`'s `Center` / `ToPoint`
    sub-options and `Join` (Yes/No) are the pick / non-merge forms and are
    likewise outstanding.

    **Object management (flagged):** the reference CAD application's `Join` default is MERGE --
    "the extension is merged into the curve itself rather than added as a
    segment," so the net result is ONE longer curve. We reproduce that net
    effect: the input curve is removed and the extension added as a new object
    (the same pattern as `Project`'s `DeleteInput`), inside one undo record.
    The in-place merge (keeping the input's name/ID) is OUTSTANDING -- see the
    entry. An unimplemented thing must never look implemented (the explicit-refusal policy), so the
    message says the input was replaced rather than implying it was edited in
    place.
    """

    name = "Extend"
    description = ("Extend curves to boundary objects, or by a typed length "
                   "(Type Natural, Line or Arc).")
    # MEASURED (research_preview_14 R5 Extend): the first prompt asks for BOUNDARIES or a
    # length -- "Select boundary objects or enter extension length. Press Enter for dynamic
    # extend ( Type=Natural  Join=Merge )"; picks accumulate, Enter ends the set; a typed 2
    # sets ExtensionLength=2 and advances. Then "Select curve to extend", which repeats with
    # ". Press Enter when done" (each picked curve is extended at the end NEAREST the pick)
    # until Enter. Dynamic extend (Enter with no boundary) is refused by name.
    BOUNDARY_PROMPT = ("Select boundary objects or enter extension length. "
                       "Press Enter for dynamic extend:")
    CURVE_PROMPT = "Select curve to extend:"
    steps = [(BOUNDARY_PROMPT, OBJECTS), (CURVE_PROMPT, OBJECTS)]

    def __init__(self):
        super().__init__()
        self._boundaries = []
        self._length = None
        self._targets = []          # [(name, pick point or None)]

    @property
    def prompt(self):
        base = super().prompt
        if self.pending_option is not None or self.step_index > 1:
            return base
        label = self.steps[self.step_index][0][:-1]
        extra = ""
        if self.step_index == 1:
            if self._length is not None:
                extra = f"  ExtensionLength={_num(self._length)}"
            if self._targets:
                base = base.replace(label, label + ". Press Enter when done", 1)
                extra += "  Undo"
        if extra and base.endswith(" ):"):
            base = base[:-3] + extra + " ):"
        return base

    def on_mode(self, name, ctx):
        if name == "Undo" and self.step_index == 1 and self._targets:
            self._targets.pop()
            return None, False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        raw = (text or "").strip()
        if self.pending_option is not None or (
                raw and self.options and opts.match(raw, self.options, self.opt).status
                != opts.NONE):
            return super()._feed(text, ctx)
        if raw.lower() == "undo" and self.step_index == 1:
            return self.on_mode("Undo", ctx)
        if self.step_index == 0:
            if not raw:
                if not self._boundaries:
                    return ("Extend: dynamic extend is not implemented yet -- pick boundary "
                            "objects or type an extension length"), False
                self.step_index = 1
                return None, False
            try:
                length = parse_number(raw)
            except ValueError:
                length = None
            if length is not None:
                if not length > 0:
                    return "Extend: the extension length must be positive", False
                self._length = float(length)
                self.step_index = 1
                return None, False
            if ctx.geom_by_name(raw) is None:
                return f"No object named {raw!r}", False
            self._boundaries.append(raw)
            return None, False
        if not raw:
            if not self._targets:
                return "Extend cancelled", True
            self.args = [list(self._boundaries), list(self._targets)]
            self.step_index = len(self.steps)
            return self.execute(ctx)
        if ctx.geom_by_name(raw) is None:
            return f"No object named {raw!r}", False
        pick = (getattr(self, "pick_points", None) or {}).get(1)
        self._targets.append((raw, tuple(pick) if pick is not None else None))
        if getattr(self, "pick_points", None):
            self.pick_points.pop(1, None)
        return None, False

    @staticmethod
    def _nearest_end(geom, pick):
        if pick is None:
            return "End"
        a, b = geom.PointAtStart, geom.PointAtEnd
        da = math.dist((a.X, a.Y, a.Z), pick)
        db = math.dist((b.X, b.Y, b.Z), pick)
        return "Start" if da < db else "End"

    def _length_to_boundary(self, ctx, geom, end, style, tol):
        """The extension length that ends `geom` exactly on the nearest boundary crossing.

        The extension is first built LONG (same style, so the same exact line/arc), its
        crossings with every boundary curve are found, and the length that puts the new end
        on each crossing is solved by bisection along that exact extension (to 1e-12)."""
        pts = [geom.PointAtStart, geom.PointAtEnd]
        boundaries = []
        for name in self._boundaries:
            b = ctx.geom_by_name(name)
            if not isinstance(b, r3.Curve):
                raise ValueError(f"{name!r} is not a curve -- extending to surface "
                                 f"boundaries is not implemented yet")
            boundaries.append(b)
            pts += [b.PointAtStart, b.PointAtEnd]
        span = max(math.dist((p.X, p.Y, p.Z), (q.X, q.Y, q.Z)) for p in pts for q in pts)
        big = 4.0 * span + 1.0
        kind = style if style in ("Line", "Arc") else extend.natural_style_for(geom, end)
        if kind == "Smooth":
            # surfaces the kernel's own refusal wording
            extend.extend_curve(geom, end, 1.0, style=style, tolerance=tol)
        dom = geom.Domain
        if end == "End":
            e, t = geom.PointAtEnd, geom.TangentAt(dom.T1)
        else:
            e, t = geom.PointAtStart, geom.TangentAt(dom.T0)
            t = r3.Vector3d(-t.X, -t.Y, -t.Z)
        origin = (e.X, e.Y, e.Z)
        out = (t.X, t.Y, t.Z)
        if kind == "Line":
            # the extension is the tangent line: its length to a crossing is the distance
            probe = bridge.make_line(origin, tuple(origin[i] + out[i] * big for i in range(3)))
            measure = lambda x: math.dist(origin, x)  # noqa: E731
        else:
            seg = extend._end_segment(geom, extend._normalise_end(end))
            arc = extend._try(seg, "TryGetArc")
            if arc is None:
                raise ValueError("Type=Arc needs the end being extended to be an arc")
            c, r = arc.Center, float(arc.Radius)
            n = arc.Plane.ZAxis
            center, normal = (c.X, c.Y, c.Z), (n.X, n.Y, n.Z)
            radial = _sub3(origin, center)
            sense = 1.0 if cplane.dot(cplane.cross(normal, radial), out) > 0 else -1.0
            probe = r3.Circle(r).ToNurbsCurve()
            probe.Transform(r3.Transform.PlaneToPlane(r3.Plane.WorldXY(), arc.Plane))

            def measure(x):
                v = _sub3(x, center)
                ang = math.atan2(cplane.dot(normal, cplane.cross(radial, v)),
                                 cplane.dot(radial, v)) * sense
                return r * (ang % (2 * math.pi))
        best = None
        for b in boundaries:
            for hit in bridge.curve_curve_intersections(probe, b, tol):
                p = hit.point
                x = (p.X, p.Y, p.Z) if hasattr(p, "X") else tuple(float(v) for v in p)
                length = measure(x)
                if length <= tol:
                    continue
                if best is None or length < best:
                    best = length
        if best is None:
            raise ValueError("the curve does not reach any boundary")
        return best
    # the `Type` option -- the only one that affects the typed-length
    # geometry. All four documented values are settable; the kernel is the
    # authority on which it can APPLY (Smooth, and Natural-on-a-free-form,
    # raise, and the command surfaces that verbatim -- see the docstring).
    options = (
        opts.Option("Type", opts.ENUM,
                    default="Natural",
                    choices=("Natural", "Line", "Arc", "Smooth"),
                    help=("How the extension is built. Natural: lines and "
                          "polylines by line, arcs by a same-radius arc, other "
                          "curves smooth. Line: a tangent line. Arc: a tangent "
                          "arc. Smooth: a curvature-continuous curve (not "
                          "implemented yet).")),
        # MEASURED (R5 Extend): "Join=Merge" is shown; Merge is what is built.
        opts.Option("Join", opts.ENUM, choices=("Merge", "Yes", "No"), default="Merge",
                    help="Merge: the extension becomes part of the curve."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "Join" and value != "Merge":
            self.opt["Join"] = "Merge"
            return f"Extend: Join={value} is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def execute(self, ctx: NarwhalContext):
        lines = []
        style = self.opt.get("Type", "Natural")
        tol = _document_tolerance(ctx)
        for name, pick in self._targets:
            geom = ctx.geom_by_name(name)
            if not isinstance(geom, r3.Curve):
                lines.append(f"Extend: {name!r} is not a curve (extending a point, surface "
                             f"or edge is not implemented yet)")
                continue
            end = self._nearest_end(geom, pick)
            try:
                length = (self._length if self._length is not None
                          else self._length_to_boundary(ctx, geom, end, style, tol))
            except ValueError as e:
                lines.append(f"Extend: {name}: {e}")
                continue
            lines.append(self._extend_one(ctx, name, geom, end, length))
        return "\n".join(lines) or "Extend: nothing was extended", True

    def _extend_one(self, ctx, name, geom, end, length):
        if not isinstance(geom, r3.Curve):
            # Extend works on curves. A point, surface or edge is not a curve, and
            # "extending a surface edge" is the boundary-object form, which is a
            # separate task -- report it rather than ship something else (the explicit-refusal policy).
            return (f"Extend: {name!r} is not a curve (Extend works on curves; "
                    "extending a point, surface or edge is not implemented yet)"), True

        # `end` is fixed at the curve's End: the reference CAD application resolves it from the click
        # NEAREST the end, and a typed length has no click (the Start / near-end
        # selection is outstanding -- see the docstring). The kernel is the
        # authority on the geometry, and its ValueError messages are written to
        # be surfaced verbatim (Smooth, Natural-on-free-form, Arc-on-non-arc,
        # closed, non-positive length).
        try:
            result = extend.extend_curve(
                geom, end, float(length),
                style=self.opt.get("Type", "Natural"),
                tolerance=_document_tolerance(ctx))
        except ValueError as e:
            return str(e)

        # one undo record for the whole change -- the input removal and the
        # extension add are a SINGLE undo step, the same shape as Project. the reference CAD application's
        # Join default is MERGE, whose net effect is ONE longer curve, so we
        # remove the input and add the extension (not an in-place merge --
        # keeping the input's name/ID is outstanding, see the docstring/entry).
        ctx.note_change(self.name)
        layer_index = _layer_index_of(ctx, name)
        oid = ctx.model.object_id_by_name(name)
        if oid is not None:
            ctx.model.remove_object(oid)
        ob = ctx.object_by_name(name)
        if ob is not None:
            _remove_object_from_scene(ob)
        base = "Line" if isinstance(result, r3.LineCurve) else "Curve"
        _, display_name = ctx.add_geometry(result, base, layer_index)
        t = self.opt.get("Type", "Natural")
        return (f"Extended {name} by {_num(length)} (Type={t}) -> {display_name} "
                f"(input replaced)")


class DivideCmd(Command):
    """`Divide` -- the command behavior.

    (the command behavior):

        "The Divide command creates point objects along a curve or splits a
         curve by equal length, equal chord length or number of segments."
        "Select curves. Type the number of segments. Or, click the Length or
         EqualChordLength option and enter a number."

    Selection-driven ("Select curves"), exactly like Explode / Move. The
    default step asks for the number of SEGMENTS (n+1 points). The `Length`
    and `EqualChordLength` MODE options change what the typed number means
    (arc length vs. chord length); the pure arithmetic lives in the `divide`
    module.

    `Split` cuts each curve at exact arc-length parameters instead of placing points
    (`EqualChordLength` with `Split` is refused by name); `DeleteRemainder` then drops
    a Length run's final, shorter piece. `MarkEnds` adds a point at each end of each
    curve (one for a closed curve) unless a division point is already there, and
    `GroupOutput` puts everything one run creates into one new group. `SubCrv` is not
    implemented yet and reports honestly.
    """

    name = "Divide"
    description = ("Place point objects along selected curves, by segment "
                   "count, arc length or chord length.")
    # (the command behavior) "Select curves. Type the number of
    # segments." The curve selection is the FIRST step now, asked for when
    # nothing is preselected; the number follows.
    steps = [("Select curves to divide:", OBJECTS_MULTI),
             ("Number of segments:", NUMBER)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("Length", opts.MODE,
                    help="Divides the curve at equal ARC lengths. "
                         "Measurement starts from the natural start of the curve."),
        opts.Option("EqualChordLength", opts.MODE,
                    help="Divides the curve at equal CHORD (straight-line) lengths."),
        opts.Option("Split", opts.TOGGLE, default=False,
                    help="Splits the curve instead of placing point objects."),
        opts.Option("DeleteRemainder", opts.TOGGLE, default=False,
                    help="Deletes the last segment when the curve length cannot "
                         "be divided evenly (Split=Yes only)."),
        opts.Option("MarkEnds", opts.TOGGLE, default=False,
                    help="Places a point at the ends of the curve."),
        opts.Option("GroupOutput", opts.TOGGLE, default=False,
                    help="Groups the resulting objects."),
        opts.Option("SubCrv", opts.MODE,
                    help="Selects part of a curve as input."),
    )

    def on_mode(self, name, ctx):
        if name in ("Length", "EqualChordLength"):
            self._divide_mode = name
            # Update the NUMBER step's label so the prompt says "Length:" or
            # "Chord length:" instead of "Number of segments:" (the explicit-refusal policy: a prompt
            # that lies about the quantity is a quiet bug). Found by kind, not by
            # a fixed index, since prepended the curve-selection step.
            label = "Length:" if name == "Length" else "Chord length:"
            for i, (_p, k) in enumerate(self.steps):
                if k == NUMBER:
                    self.steps[i] = (label, NUMBER)
                    break
            return (f"Divide: mode is now {name} -- type a value at the "
                    f"next prompt.", False)
        # SubCrv: honest.
        return super().on_mode(name, ctx)

    def _split_params(self, curve, mode, value):
        """Cut parameters for Split at exact arc length, and whether a remainder is left.

        A closed curve is also cut at its seam, so n cuts give n pieces. With a Length,
        the last piece is the remainder when the length does not divide the curve.
        """
        from . import arclength

        total = arclength.length_of(curve)
        if total <= 1e-12:
            raise ValueError("the curve has zero length")
        if mode == "Length":
            step = float(value)
            if step <= 0:
                raise ValueError(f"length must be positive (got {value!r})")
            if step > total + 1e-9:
                raise ValueError(f"length ({step:g}) is longer than the curve ({total:g})")
            count = int(math.floor(total / step + 1e-9))
            stations = [k * step for k in range(1, count + 1) if k * step < total - 1e-9]
            remainder = total - count * step > 1e-9
        else:
            stations = [total * k / value for k in range(1, value)]
            remainder = False
        params = [arclength.parameter_at_length(curve, s) for s in stations]
        if bridge.curve_ends(curve) is not None and curve.IsClosed:
            params.insert(0, curve.Domain.T0)
        return params, remainder

    def _group_output(self, ctx, display_names):
        """Put the objects named `display_names` into one new group; returns its name."""
        file = ctx.model.file
        n = 0
        while _group_index_by_name(file, f"Group{n}") is not None:
            n += 1
        group = r3.Group()
        group.Name = f"Group{n}"
        file.Groups.Add(group)
        gi = _group_index_by_name(file, group.Name)
        for display in display_names:
            for fobj in _objects_named(ctx, display):
                fobj.Attributes.AddToGroup(gi)
        return group.Name

    def execute(self, ctx: NarwhalContext):
        mode = getattr(self, "_divide_mode", None) or "count"
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "Divide: select one or more curves first", True

        curves, non_curves = [], []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is not None and isinstance(geom, r3.Curve):
                curves.append((name, geom))
            else:
                non_curves.append(name)
        if not curves:
            return ("Divide: only curves can be divided -- the selection "
                    "contains no curves"), True

        if not self.args:
            return "Divide: no value given", True
        raw = self.args[-1]
        if mode == "count":
            # the count must be a whole number of segments (the reference CAD application expects an int)
            try:
                f = float(raw)
            except (TypeError, ValueError):
                return f"Divide: the segment count must be a whole number (got {raw!r})", True
            if f < 1 or f != int(f):
                return f"Divide: the segment count must be a whole number >= 1 (got {raw!r})", True
            value = int(f)
        else:
            value = raw

        # bridge.sample_curve returns rhino3dm Point3d objects; the pure
        # `divide` functions operate on plain (x, y, z) tuples, so normalise
        # here (the only place rhino3dm meets the pure math).
        def _samples(geom):
            return [(p.X, p.Y, p.Z) for p in bridge.sample_curve(geom, 64)]

        split = bool(self.opt.get("Split"))
        if split and mode == "EqualChordLength":
            return ("Divide: Split with EqualChordLength is not implemented yet; "
                    "use the segment count or Length"), True

        # validate before mutating
        plans = []
        for name, geom in curves:
            try:
                if split:
                    plans.append(self._split_params(geom, mode, value))
                    continue
                pts = _samples(geom)
                if mode == "Length":
                    plans.append(divide.by_length(pts, value))
                elif mode == "EqualChordLength":
                    plans.append(divide.by_chord(pts, value))
                else:
                    plans.append(divide.by_count(pts, value))
            except ValueError as e:
                return f"Divide: {e}", True

        ctx.note_change("Divide")
        from . import intersect

        created, total_points, pieces_made, deleted = [], 0, 0, 0
        for (name, geom), plan in zip(curves, plans):
            layer_index = _layer_index_of(ctx, name)
            ends = bridge.curve_ends(geom) or ()
            ends = ends[:1] if geom.IsClosed else ends
            if split:
                params, remainder = plan
                pieces = intersect.split_curve_at_params(geom, params)
                if remainder and self.opt.get("DeleteRemainder") and len(pieces) > 1:
                    pieces = pieces[:-1]
                    deleted += 1
                created.extend(_replace_curve(ctx, name, pieces))
                pieces_made += len(pieces)
                placed = []
            else:
                for pos in plan:
                    created.append(ctx.add_geometry(bridge.make_point(pos), "Divide",
                                                    layer_index)[1])
                    total_points += 1
                placed = plan
            if self.opt.get("MarkEnds"):
                for end in ends:
                    if any(math.dist(end, p) <= 1e-9 for p in placed):
                        continue
                    created.append(ctx.add_geometry(bridge.make_point(end), "Divide",
                                                    layer_index)[1])
                    total_points += 1
        if split:
            msg = f"Split {len(curves)} curve(s) into {pieces_made} piece(s)"
            if deleted:
                msg += f"; deleted the shorter remainder piece of {deleted} curve(s)"
            if total_points:
                msg += f"; marked {total_points} end point(s)"
        else:
            msg = f"Placed {total_points} point(s) on {len(curves)} curve(s)"
        if self.opt.get("DeleteRemainder") and not split:
            msg += " (DeleteRemainder applies only with Split=Yes)"
        if self.opt.get("GroupOutput") and created:
            msg += f"; grouped {len(created)} object(s) as {self._group_output(ctx, created)}"
        if non_curves:
            msg += f" ({len(non_curves)} non-curve(s) skipped)"
        return msg, True


def _layer_index_of(ctx: NarwhalContext, name: str) -> int:
    """The layer the named document object sits on (0 when it cannot be found)."""
    for fobj in ctx.model.file.Objects:
        if (fobj.Attributes.Name or "").lower() == (name or "").lower():
            return int(fobj.Attributes.LayerIndex)
    return 0


# ---------------------------------------------------------------------------
# visibility -- `Hide` / `Show` / `Lock` / `Unlock`
# ---------------------------------------------------------------------------
#
# The three right-click context menus in `navigate.py` name these commands. The document half is an attribute
# change, not new geometry: rhino3dm.ObjectAttributes.Mode is one of
# ObjectMode.{Normal, Hidden, Locked, InstanceDefinitionObject} (-
# the enum values are Normal 0, Hidden 1, Locked 2), so hiding/locking is
# writing Mode and Show/Unlock writes it back to Normal.
#
# and the reason the four split two ways:
#   Hide   -- "conceals SELECTED objects"  (the command behavior)
#   Lock   -- "Select objects to lock"     (the command behavior)
#   Show   -- "re-displays ALL hidden objects"   (a hidden object cannot be
#   Unlock -- "unlocks ALL locked objects"            selected, so neither of
#                                                    these takes a selection)
# So Hide/Lock act on the SELECTION and refuse with none; Show/Unlock act on
# the WHOLE DOCUMENT and report honestly when there is nothing to change.
# (the reference CAD application's own context-menu rows are `Hide`, `Lock`, `ShowSelected`,
# `UnlockSelected`; the implementation names the command set `Hide`/`Show`/`Lock`/
# `Unlock`, and the help's all-object Show/Unlock is the form a hidden/locked
# object -- which cannot be selected -- actually needs.)
#
# The document and display halves are separate. Hiding sets Mode==Hidden in the
# document, but display code must also omit hidden objects; locked objects must stay
# visible but unselectable. Unsupported display behavior is reported rather than faked.


def _objects_named(ctx, name):
    """Every file object whose display name is `name` (case-insensitive).

    `ctx.geom_by_name` returns only the geometry (no attributes), so the
    attribute lives on the file object: resolve the name straight on the
    document table, the same way `model.object_id_by_name` does.
    """
    target = (name or "").lower()
    for fobj in ctx.model.file.Objects:
        if (fobj.Attributes.Name or "").lower() == target:
            yield fobj


class _VisibilityCmd(Command):
    """Shared shape for the four visibility commands.

    All four are selection- or document-driven, zero-prompt, and change an
    object's `Mode` attribute. The difference is which objects are touched and
    what Mode they are set to -- and the message that says so. `note_change`
    fires BEFORE the mutation (one undo step, ), matching every other
    document-mutating command.
    """

    steps = []

    # set this to the ObjectMode value the command writes (or None to compute)
    _new_mode = None

    def _targets(self, ctx):
        """(names, message-if-empty) the command will change."""
        raise NotImplementedError

    def _new(self):
        return self._new_mode

    def _mode_of(self, ctx, name):
        """The Mode of the first file object named `name` (or None if absent).

        The selected-objects forms  use this to scope the selection to
        the objects that are actually in the state the command acts on -- the
        same scoping the all-objects `Show`/`Unlock` apply to the whole document.
        """
        for fobj in _objects_named(ctx, name):
            return fobj.Attributes.Mode
        return None

    def _message(self, names):
        raise NotImplementedError

    def execute(self, ctx):
        names, empty = self._targets(ctx)
        if not names:
            return empty, True
        new = self._new()
        ctx.note_change(self.name)
        for name in names:
            for fobj in _objects_named(ctx, name):
                fobj.Attributes.Mode = new
        return self._message(names), True


# MEASURED (Research13 #160, #161, #181, docs/measured/research_all_13/notes): the
# exact wording of the reference CAD application for the all-object visibility commands.
NO_HIDDEN_MESSAGE = "No objects are hidden."
NO_LOCKED_MESSAGE = "No objects are locked."


def _counted_message(verb, count, noun):
    """``Showing one hidden object.`` / ``Unlocking 2 objects.`` (the reference CAD application wording)."""
    if count == 1:
        return f"{verb} one {noun}."
    return f"{verb} {count} {noun}s."


class HideCmd(_VisibilityCmd):
    """`Hide` -- conceal the SELECTION from view (the command behavior).

    "The Hide command conceals selected objects from view." A hidden
    object cannot be selected again, so `Show` (all hidden) is the way back --
    `Hide` therefore acts on the selection and is refused with none, rather
    than silently hiding everything (the implementation's required behaviour).
    """

    name = "Hide"
    description = "Hide the selected objects from view."
    # the command behavior -- "Select the objects you want to show, and press
    # Enter." Ask for the selection (a set-step) instead of refusing; a
    # pre-selection fills it and runs immediately, an empty confirm cancels.
    steps = [("Select objects to hide:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    # MEASURED (research_all_13 note 070): Enter at an empty prompt ends silently.
    silent_empty_cancel = True
    _new_mode = r3.ObjectMode.Hidden

    def _targets(self, ctx):
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return [], "Hide: select objects to hide"
        return names, None

    def _message(self, names):
        # MEASURED (research_all_13 note 070): a preselected Hide prints nothing.
        return None


class LockCmd(_VisibilityCmd):
    """`Lock` -- lock the SELECTION (the command behavior).

    "Select objects to lock, and press Enter." A locked object stays
    in the document (it is not deleted -- the behavior contract pins exactly that)
    and is meant to remain visible but unselectable. This command records the document
    attribute; the viewport layer enforces pickability.
    """

    name = "Lock"
    description = "Lock the selected objects."
    # the command behavior -- "Select objects to lock, and press Enter."
    # Ask for the selection (a set-step) instead of refusing; a pre-selection
    # fills it and runs immediately, an empty confirm cancels.
    steps = [("Select objects to lock:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    # MEASURED (research_all_13 note 084): Enter at an empty prompt ends silently.
    silent_empty_cancel = True
    _new_mode = r3.ObjectMode.Locked

    def _targets(self, ctx):
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return [], "Lock: select objects to lock"
        return names, None

    def _message(self, names):
        # MEASURED (research_all_13 note 084): Lock prints nothing.
        return None


class ShowCmd(_VisibilityCmd):
    """`Show` -- re-display ALL hidden objects (the command behavior).

    "The Show command re-displays ALL hidden objects." A hidden
    object cannot be selected, so this acts on the whole document, not the
    selection -- and reports honestly when nothing is hidden rather than
    claiming success (the implementation's required behaviour).
    """

    name = "Show"
    description = "Show all hidden objects."
    _new_mode = r3.ObjectMode.Normal

    def _targets(self, ctx):
        hidden = [f.Attributes.Name for f in ctx.model.file.Objects
                  if f.Attributes.Mode == r3.ObjectMode.Hidden]
        if not hidden:
            return [], NO_HIDDEN_MESSAGE
        return hidden, None

    def _message(self, names):
        return _counted_message("Showing", len(names), "hidden object")


class UnlockCmd(_VisibilityCmd):
    """`Unlock` -- unlock ALL locked objects (the command behavior).

    "The Unlock command unlocks ALL locked objects." Like `Show`, it
    acts on the whole document (a locked object is meant to be unselectable)
    and reports honestly when nothing is locked.
    """

    name = "Unlock"
    description = "Unlock all locked objects."
    _new_mode = r3.ObjectMode.Normal

    def _targets(self, ctx):
        locked = [f.Attributes.Name for f in ctx.model.file.Objects
                  if f.Attributes.Mode == r3.ObjectMode.Locked]
        if not locked:
            return [], NO_LOCKED_MESSAGE
        return locked, None

    def _message(self, names):
        return _counted_message("Unlocking", len(names), "object")


class _SelectedStateCmd(_VisibilityCmd):
    """ShowSelected / UnlockSelected: PROMPT for the objects in `_from_mode`.

    MEASURED (research_all_13 notes 070, 161, 182): the reference CAD application opens its selection prompt
    even while a visible/unlocked object is selected -- that selection does not answer
    it -- and Enter on a set holding none of the target state ends silently.
    ShowSelected with nothing hidden in the document prints "No objects are hidden."
    without prompting.

    OURS: the reference CAD application deselects an object as it hides/locks it, so a preselection can never
    hold one there. Ours survives Hide/Lock (`ui.sync_locked_selectability` blocks only a
    re-pick), so a preselection that DOES hold hidden/locked objects still runs at once.
    """

    _from_mode = None
    _none_in_document = None
    cancel_on_empty_set = True
    silent_empty_cancel = True

    def _in_state(self, ctx, names):
        return [n for n in names if self._mode_of(ctx, n) == self._from_mode]

    def begin(self, ctx):
        if self._none_in_document is not None and not any(
                f.Attributes.Mode == self._from_mode for f in ctx.model.file.Objects):
            return self._none_in_document, True
        getter = getattr(ctx, "selected_objects", None)
        picked = [o.name for o in (getter() or [])] if callable(getter) else []
        if self._in_state(ctx, picked):
            self.args = [picked]
            self.step_index = len(self.steps)
            return self._recorded(ctx, lambda: self.execute(ctx))
        return None, False

    def _targets(self, ctx):
        if self.args:
            names = list(self.args[0])
        else:
            names = [ob.name for ob in ctx.selected_objects()]
        return self._in_state(ctx, names), None


class ShowSelectedCmd(_SelectedStateCmd):
    """`ShowSelected` -- re-display the chosen hidden objects (see `_SelectedStateCmd`).

    (the command behavior): the all-objects form is `Show` -- "re-displays ALL hidden
    objects"; this is the selected-objects form. A hidden object can be named at the
    prompt (MEASURED: `_SelLast` picked a hidden line there, note 070).
    """

    name = "ShowSelected"
    description = "Show the selected hidden objects."
    steps = [("Select objects to show:", OBJECTS_MULTI)]
    _new_mode = r3.ObjectMode.Normal
    _from_mode = r3.ObjectMode.Hidden
    _none_in_document = NO_HIDDEN_MESSAGE

    def _message(self, names):
        return f"Showed {len(names)} object{'s' if len(names) != 1 else ''}"


class UnlockSelectedCmd(_SelectedStateCmd):
    """`UnlockSelected` -- unlock the chosen locked objects (see `_SelectedStateCmd`).

    (the command behavior): "UnlockSelected -- unlock selected locked objects." The reference CAD application's
    message with nothing locked in the document was not measured, so no early exit.
    """

    name = "UnlockSelected"
    description = "Unlock the selected locked objects."
    steps = [("Select objects to unlock:", OBJECTS_MULTI)]
    _new_mode = r3.ObjectMode.Normal
    _from_mode = r3.ObjectMode.Locked

    def _message(self, names):
        return f"Unlocked {len(names)} object{'s' if len(names) != 1 else ''}"


def _plural(count, noun):
    return f"{count} {noun}{'s' if count != 1 else ''}"


class _SwapModeCmd(Command):
    """HideSwap / LockSwap: exchange two `Mode` states across the whole document.

    Zero-prompt and document-driven like `Show`/`Unlock`. Every object in `_mode_a`
    goes to `_mode_b` and every object in `_mode_b` goes to `_mode_a`; objects in any
    other Mode are left alone. It walks the file objects themselves, not names, so
    two objects sharing a name are each swapped from their own state. Only `Mode` is
    written: IDs, geometry, names, layers, groups and the selection are untouched.
    `Mode` is in the undo snapshot, so the whole swap is one Undo step.
    """

    steps = []
    records_undo = True        # a Mode change is a document change
    _mode_a = None
    _mode_b = None
    _empty_message = None

    def _message(self, to_b, to_a):
        raise NotImplementedError

    def execute(self, ctx):
        a, b = self._mode_a, self._mode_b
        to_b = [f for f in ctx.model.file.Objects if f.Attributes.Mode == a]
        to_a = [f for f in ctx.model.file.Objects if f.Attributes.Mode == b]
        if not to_b and not to_a:
            return self._empty_message, True
        ctx.note_change(self.name)
        for fobj in to_b:
            fobj.Attributes.Mode = b
        for fobj in to_a:
            fobj.Attributes.Mode = a
        return self._message(len(to_b), len(to_a)), True


class HideSwapCmd(_SwapModeCmd):
    """`HideSwap` -- hide every visible object and show every hidden one.

    Locked objects stay Locked: they are neither Normal nor Hidden, so the swap
    does not see them.
    """

    name = "HideSwap"
    description = "Hide the visible objects and show the hidden ones."
    _mode_a = r3.ObjectMode.Normal
    _mode_b = r3.ObjectMode.Hidden
    # OURS: the reference CAD application's wording was not measured.
    _empty_message = "No visible or hidden objects to swap."

    def _message(self, to_b, to_a):
        # OURS: wording not measured.
        return f"Hid {_plural(to_b, 'object')}, showed {_plural(to_a, 'object')}."


class LockSwapCmd(_SwapModeCmd):
    """`LockSwap` -- lock every unlocked object and unlock every locked one.

    Hidden objects stay Hidden. Viewport pickability follows the document: the
    pickers re-read `Mode` through `ui._locked_names_cached` and
    `ui.sync_locked_selectability`, keyed on the document state this recorded
    command moves, so a newly locked object stops being pickable and a newly
    unlocked one becomes pickable on the next click without extra work here.
    """

    name = "LockSwap"
    description = "Lock the unlocked objects and unlock the locked ones."
    _mode_a = r3.ObjectMode.Normal
    _mode_b = r3.ObjectMode.Locked
    # OURS: the reference CAD application's wording was not measured.
    _empty_message = "No unlocked or locked objects to swap."

    def _message(self, to_b, to_a):
        # OURS: wording not measured.
        return f"Locked {_plural(to_b, 'object')}, unlocked {_plural(to_a, 'object')}."


class IsolateCmd(Command):
    """`Isolate` -- hide everything except the selected and locked objects.

    "hide everything not selected, and show exactly that set again."

    (the command reference Isolate -> the command behavior):
        "The Isolate command hides all objects except the currently selected and
        locked objects."

    So the target set is every object in the document that is NOT in the
    selection and NOT locked. Those are set to `ObjectMode.Hidden`. The set of
    names hidden is recorded in `isolate_state` (keyed by the model) so that
    `Unisolate` can show EXACTLY that set again -- the document alone cannot
    distinguish Isolate-hidden objects from manually-hidden ones (both carry
    `ObjectMode.Hidden`).

    `Mode` is in `model._SNAPSHOT_ATTRS`, so the hide is undoable via the
    snapshot the usual way; the isolate bookkeeping (which set) is separate and
    is NOT undone (undoing the hide does not un-hide the record, which is what
    lets a second `Unisolate` after a manual `Undo` still restore the right set).
    """

    name = "Isolate"
    description = "Hide all objects except the selected and locked ones."
    # MEASURED (Research13 #77): with nothing selected the reference CAD application asks
    # "Select objects to isolate:" and an empty Enter cancels with nothing hidden.
    # A pre-selection fills the step and runs at once. The old zero-step form hid
    # EVERY unlocked object when nothing was selected.
    steps = [("Select objects to isolate:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True        # a Mode change is a document change

    def execute(self, ctx: NarwhalContext):
        # The set to keep visible: the answered selection, plus every locked
        # object (locked objects stay visible per the wording).
        sel = set()
        answered = self.args[0] if self.args and isinstance(self.args[0], list) else None
        if answered:
            sel.update((str(n) or "").lower() for n in answered)
        else:
            try:
                for ob in (ctx.selected_objects() or []):
                    sel.add((ob.name or "").lower())
            except Exception:  # noqa: BLE001
                pass
        if not sel:
            return "Isolate cancelled", True

        keep = set(sel)   # locked objects stay visible
        to_hide = []
        for fobj in ctx.model.file.Objects:
            nm = (fobj.Attributes.Name or "")
            if fobj.Attributes.Mode == r3.ObjectMode.Locked:
                keep.add(nm.lower())          # locked objects stay visible
            elif nm.lower() not in keep:
                to_hide.append(nm)

        if not to_hide:
            return "Isolate: nothing to hide", True

        ctx.note_change(self.name)
        for nm in to_hide:
            for fobj in _objects_named(ctx, nm):
                fobj.Attributes.Mode = r3.ObjectMode.Hidden

        # Remember the exact set Isolate hid, so Unisolate can restore it.
        isolate_state.remember(ctx.model, set(to_hide))
        return f"Isolated -- hid {len(to_hide)} object(s)", True


class UnisolateCmd(Command):
    """`Unisolate` -- show exactly the set the `Isolate` command hid.

    "show exactly that set again."

    (the command reference Unisolate -> the command behavior):
        "The Unisolate command shows all objects hidden by the Isolate command."

    That is NOT "show all hidden objects" (that is `Show` / `ShowSelected`).
    It restores EXACTLY the names `Isolate` recorded, from `isolate_state`
    (keyed by the model). If `Isolate` never ran there is no record, and the
    command REFUSES by name (the explicit-refusal policy) rather than silently showing everything.
    """

    name = "Unisolate"
    description = "Show exactly the objects the Isolate command hid."
    steps = []
    records_undo = True        # a Mode change is a document change

    def execute(self, ctx: NarwhalContext):
        names = isolate_state.pop(ctx.model)
        if names is None or not names:
            return "Unisolate: Isolate was not run first (nothing to restore)", True

        ctx.note_change(self.name)
        restored = 0
        for nm in sorted(names):
            for fobj in _objects_named(ctx, nm):
                if fobj.Attributes.Mode == r3.ObjectMode.Hidden:
                    fobj.Attributes.Mode = r3.ObjectMode.Normal
                    restored += 1
        # MEASURED (research_all_13 #179): the Show wording.
        return _counted_message("Showing", restored, "hidden object"), True


def _expanded_group_names(model, names):
    """Display names in ``names`` plus their transitive document-group closure.

    Missing/stale group indices are ignored by name rather than making selection fail. The
    return order follows document order, which keeps the active object deterministic.
    """
    wanted = {str(n).lower() for n in (names or [])}
    if not wanted:
        return []
    rows = []
    for fobj in model.file.Objects:
        name = fobj.Attributes.Name or ""
        groups = set(fobj.Attributes.GetGroupList2() or [])
        rows.append((name, name.lower(), groups))
    active_groups = set()
    changed = True
    while changed:
        changed = False
        for _name, low, groups in rows:
            if low in wanted or groups.intersection(active_groups):
                before_names = len(wanted)
                before_groups = len(active_groups)
                wanted.add(low)
                active_groups.update(groups)
                changed = changed or len(wanted) != before_names \
                    or len(active_groups) != before_groups
    return [name for name, low, _groups in rows if name and low in wanted]


def _selection_names(ctx):
    """`{name.lower: display name}` for the current selection (dedup, name order).

    Group / Ungroup are selection-driven (the reference CAD application's "select, then run the command"
    shape), so they act on exactly the selected set. Headless-safe: a missing
    selector or one that fails yields an empty selection, which both commands
    refuse by name rather than silently doing nothing."""
    out = {}
    getter = getattr(ctx, "selected_objects", None)
    if not callable(getter):
        return out
    try:
        for ob in (getter() or []):
            nm = getattr(ob, "name", None)
            if nm:
                out.setdefault(nm.lower(), nm)
    except Exception:  # noqa: BLE001 -- headless: no live selection
        pass
    return out


def _group_indices_by_name(file, name):
    """Return all exact, case-sensitive matches for a group name.

    the reference CAD application group names are case-sensitive for command lookup.  Normally the native
    table keeps names unique, but a document can contain duplicate names after an
    import or round-trip, so callers that need one target must reject ambiguity
    rather than silently choosing an index.
    """
    matches = []
    for i in range(len(file.Groups)):
        try:
            if (file.Groups.FindIndex(i).Name or "") == name:
                matches.append(i)
        except Exception:  # noqa: BLE001 -- a torn entry should not hide the rest
            continue
    return matches


def _group_index_by_name(file, name):
    """Return the unique exact group-name match, or ``None``."""
    matches = _group_indices_by_name(file, name)
    return matches[0] if len(matches) == 1 else None


class AddToGroupCmd(Command):
    """`AddToGroup` -- add the selected objects to a group.

    MEASURED (research_preview_14 R6 AddToGroup): with an object preselected the prompt is
    "Select group:"; a typed group name that does not exist MAKES that group ("Making a new
    group called "Group1".") and then "Added object to group.". So the objects come first
    (the selection) and the group second. A group is chosen by typing its name or by
    picking a member of exactly one group. OURS: the objects prompt (nothing preselected)
    was not measured.

    Only ``AddToGroup`` is called on the target group: memberships are never replaced or
    removed. The normal command record captures the group table for Undo/Redo.
    """

    name = "AddToGroup"
    description = "Add the selected objects to a group (a new name makes the group)."
    steps = [("Select objects to add to group:", OBJECTS_MULTI),
             ("Select group:", OBJECTS)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True

    def _target_group(self, ctx, target):
        """``(group index | None, error | None, created: bool)``."""
        file = ctx.model.file
        matches = _group_indices_by_name(file, target)
        if len(matches) > 1:
            return None, (f"AddToGroup: group name {target!r} is ambiguous "
                          f"({len(matches)} matches)"), False
        if matches:
            return matches[0], None, False
        objects = list(_objects_named(ctx, target))
        if objects:
            memberships = sorted({int(g) for obj in objects
                                  for g in (obj.Attributes.GetGroupList2() or [])})
            if not memberships:
                return None, f"AddToGroup: {target!r} is not in a group", False
            if len(memberships) > 1:
                return None, (f"AddToGroup: {target!r} is in {len(memberships)} groups -- "
                              f"type the group name"), False
            return memberships[0], None, False
        return None, None, True

    def execute(self, ctx):
        if len(self.args) < 2:
            return "AddToGroup: select objects, then a group", True
        additions, target = self.args[0], str(self.args[1])
        if not isinstance(additions, (list, tuple)):
            additions = [additions]
        gi, error, create = self._target_group(ctx, target)
        if error:
            return error, True
        targets, seen = [], set()
        for name in additions:
            for fobj in _objects_named(ctx, name):
                oid = str(fobj.Attributes.Id)
                if oid not in seen:
                    seen.add(oid)
                    targets.append(fobj)
        if not targets:
            return "AddToGroup: the selection resolves to no objects", True
        ctx.note_change()
        lines = []
        if create:
            file = ctx.model.file
            group = r3.Group()
            group.Name = target
            file.Groups.Add(group)
            gi = _group_index_by_name(file, target)
            lines.append(f'Making a new group called "{target}".')
        to_add = [obj for obj in targets
                  if gi not in set(obj.Attributes.GetGroupList2() or [])]
        if not to_add:
            return "AddToGroup: all selected objects are already in the group", True
        for fobj in to_add:
            fobj.Attributes.AddToGroup(gi)
        lines.append("Added object to group." if len(to_add) == 1
                     else f"Added {len(to_add)} objects to group.")
        return "\n".join(lines), True


class RemoveFromGroupCmd(Command):
    """`RemoveFromGroup` -- detach selected objects from one chosen group.

    The first selection/name identifies exactly one parent group, using the same
    target resolution as ``AddToGroup``.  A typed group name is exact and case-
    sensitive; a selected member is accepted only when it belongs to one group,
    because guessing among overlapping memberships would detach the wrong one.
    The target selection is then cleared and the second selection supplies the
    objects to detach.

    This is deliberately narrower than ``Ungroup``: it removes only the target
    group index and preserves every other membership and every group-table row.

    ``Copy`` is the the reference CAD application top-level option. Its default is explicitly
    (No): the reference CAD application's persisted ``RememberCopyOptions`` setting is not yet
    wired into this command. With ``Copy=Yes``, originals remain in place and
    each duplicate keeps its geometry, object properties, and unrelated group
    memberships while omitting only the target group.
    """

    name = "RemoveFromGroup"
    description = "Remove selected objects from one existing group."
    steps = [("Select group or type its name:", OBJECTS),
             ("Select objects to remove from group:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True
    options = (
        opts.Option("Copy", opts.TOGGLE, default=False,
                    help="Specifies whether or not the objects are copied."),
    )

    def begin(self, ctx):
        """Use the current selection as the group target, then ask for objects."""
        getter = getattr(ctx, "selected_objects", None)
        try:
            selected = list(getter()) if callable(getter) else []
        except Exception:  # noqa: BLE001
            selected = []
        if selected:
            self.args.append(selected[0].name)
            self.step_index = 1
            selector = getattr(ctx, "select_by_names", None)
            if callable(selector):
                try:
                    selector([])
                except Exception:  # noqa: BLE001
                    pass
            return None, False
        return super().begin(ctx)

    def _target_group(self, ctx, target):
        file = ctx.model.file
        matches = _group_indices_by_name(file, target)
        if len(matches) > 1:
            return None, (f"RemoveFromGroup: group name {target!r} is ambiguous "
                          f"({len(matches)} matches)"), []
        if matches:
            return matches[0], None, []

        objects = list(_objects_named(ctx, target))
        if not objects:
            return None, f"RemoveFromGroup: group {target!r} was not found", []
        memberships = sorted({int(g) for obj in objects
                              for g in (obj.Attributes.GetGroupList2() or [])})
        if not memberships:
            return None, (f"RemoveFromGroup: {target!r} is not a group or "
                          "group member"), []
        if len(memberships) > 1:
            return None, (f"RemoveFromGroup: selected target {target!r} is "
                          f"ambiguous ({len(memberships)} groups)"), []
        return memberships[0], None, objects

    @staticmethod
    def _copy_object(ctx, source, target_group):
        """Copy one object, preserving properties and non-target memberships."""
        identity = r3.Transform.Identity()
        name = _duplicate_with_transform(
            ctx, source.Attributes.Name, identity, "RemoveFromGroup")
        duplicate = next((obj for obj in ctx.model.file.Objects
                          if (obj.Attributes.Name or "") == name), None)
        if duplicate is None:
            raise RuntimeError("RemoveFromGroup: copy was not added to the document")
        source_attrs = source.Attributes
        target_attrs = duplicate.Attributes
        for field in ("LayerIndex", "Visible", "ObjectColor", "ColorSource",
                      "Mode", "MaterialIndex", "MaterialSource"):
            try:
                setattr(target_attrs, field, getattr(source_attrs, field))
            except Exception:  # noqa: BLE001 -- fields vary by rhino3dm build
                pass
        for pair in (source_attrs.GetUserStrings2() or []):
            if len(pair) == 2:
                target_attrs.SetUserString(str(pair[0]), str(pair[1]))
        for group_index in (source_attrs.GetGroupList2() or []):
            if int(group_index) != int(target_group):
                target_attrs.AddToGroup(int(group_index))
        return duplicate

    def execute(self, ctx):
        if len(self.args) < 2:
            return "RemoveFromGroup: select a group, then select objects to remove", True
        target, removals = self.args[0], self.args[1]
        if not isinstance(removals, (list, tuple)):
            removals = [removals]
        gi, error, _target_objects = self._target_group(ctx, str(target))
        if error:
            return error, True
        targets = []
        seen = set()
        for name in removals:
            for fobj in _objects_named(ctx, name):
                oid = str(fobj.Attributes.Id)
                if oid not in seen:
                    seen.add(oid)
                    targets.append(fobj)
        if not targets:
            return "RemoveFromGroup: the selection resolves to no objects", True
        to_remove = [obj for obj in targets
                     if gi in set(obj.Attributes.GetGroupList2() or [])]
        if not to_remove:
            return "RemoveFromGroup: no selected objects are in the group", True
        ctx.note_change()
        if self.opt.get("Copy"):
            made = [self._copy_object(ctx, fobj, gi) for fobj in to_remove]
            return f"RemoveFromGroup: copied {len(made)} object(s)", True
        for fobj in to_remove:
            fobj.Attributes.RemoveFromGroup(gi)
        return f"RemoveFromGroup: removed {len(to_remove)} object(s)", True


class SetGroupNameCmd(Command):
    """`SetGroupName` -- rename one selected group or combine selected groups.

    the reference CAD application's shape is selection first, followed by the new exact,
    case-sensitive name.  A selection can contain members of several groups;
    those groups are consolidated into one surviving table row.  Group-table
    rows are deliberately retained: deleting a row compacts integer indices in
    rhino3dm without remapping object memberships, so empty former rows are a
    safer and lossless representation of the combined operation.
    """

    name = "SetGroupName"
    description = "Name the selected group, combining selected groups when needed."
    # MEASURED (research_all_13 note 154): the reference CAD application's prompt is "Select groups to name:",
    # and TYPED text there is a group name -- an existing one goes on to "New group name:",
    # anything else (even `_SelLast`) prints "Not a group name." and ends the command.
    # Picking members then Enter (the selection route) was not measured and is kept.
    steps = [("Select groups to name:", OBJECTS_MULTI),
             ("New group name:", TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True

    def begin(self, ctx):
        self._typed_groups = None
        return super().begin(ctx)

    def _feed_objects_multi_step(self, text, ctx):
        typed = (text or "").strip()
        if not typed:
            return super()._feed_objects_multi_step(text, ctx)
        matches = _group_indices_by_name(ctx.model.file, typed)
        if not matches:
            return "Not a group name.", True
        if len(matches) > 1:
            return (f"SetGroupName: group name {typed!r} is ambiguous "
                    f"({len(matches)} matches)"), True
        self._typed_groups = [matches[0]]
        self.args.append([])
        self.step_index += 1
        return None, False

    @staticmethod
    def _selected_groups(ctx, names):
        indices = set()
        objects = []
        seen = set()
        for name in names:
            for fobj in _objects_named(ctx, name):
                oid = str(fobj.Attributes.Id)
                if oid not in seen:
                    seen.add(oid)
                    objects.append(fobj)
                indices.update(int(g) for g in (fobj.Attributes.GetGroupList2() or []))
        return sorted(indices), objects

    def execute(self, ctx):
        if len(self.args) < 2:
            return "SetGroupName: select grouped objects, then type a name", True
        selected, supplied = self.args[0], self.args[1]
        if not isinstance(selected, (list, tuple)):
            selected = [selected]
        new_name = str(supplied).strip()
        if not new_name:
            return "SetGroupName: the group name cannot be blank", True
        group_indices, _selected_objects = self._selected_groups(ctx, selected)
        if getattr(self, "_typed_groups", None):
            group_indices = list(self._typed_groups)
        if not group_indices:
            return "SetGroupName: the selection is not in a group", True

        file = ctx.model.file
        matches = _group_indices_by_name(file, new_name)
        if len(matches) > 1:
            return (f"SetGroupName: group name {new_name!r} is ambiguous "
                    f"({len(matches)} matches)"), True
        selected_set = set(group_indices)
        if matches and matches[0] not in selected_set:
            return f"SetGroupName: group name {new_name!r} already exists", True

        # Prefer an already-named selected row as survivor. Otherwise retaining
        # the lowest selected index makes the operation deterministic.
        survivor = matches[0] if matches else group_indices[0]
        if len(group_indices) == 1 and file.Groups[survivor].Name == new_name:
            return f"SetGroupName: group is already named {new_name!r}", True

        # Include every member of every selected group, not merely the picked
        # objects: combining groups moves the complete groups as the reference CAD application does.
        members = []
        for fobj in file.Objects:
            if selected_set.intersection(int(g) for g in
                                         (fobj.Attributes.GetGroupList2() or [])):
                members.append(fobj)

        ctx.note_change()
        file.Groups[survivor].Name = new_name
        for fobj in members:
            for group_index in list(fobj.Attributes.GetGroupList2() or []):
                if int(group_index) in selected_set and int(group_index) != survivor:
                    fobj.Attributes.RemoveFromGroup(int(group_index))
            if survivor not in set(fobj.Attributes.GetGroupList2() or []):
                fobj.Attributes.AddToGroup(survivor)
        if len(group_indices) == 1:
            return f"SetGroupName: renamed group to {new_name!r}", True
        return (f"SetGroupName: combined {len(group_indices)} groups into "
                f"{new_name!r}"), True


class GroupCmd(Command):
    """`Group` -- create ONE named group from the selected objects.

    (`the command reference --raw group`, section "Group"):

        "The Group command creates an single named unit from selected objects.
         Note: Every group will be given a unique name in the format of
         `Group<Number>`. The number suffix will increase for every new group."

    So the group is NAMED `Group<N>` where N is the next free number (Group0,
    Group1,...), and the selection is added to it. The group is FRESH (empty),
    so every selected object is new to it and is added exactly once; `AddToGroup`
    is idempotent (adding the same index twice leaves `GetGroupList2`
    `[0]`), so an object matched by two display names is still added once, not
    twice. Objects NOT in the selection are left out of the group. A selection
    whose names resolve to NO document object is REFUSED by name (the explicit-refusal policy) rather
    than answered by silently creating an empty group.

    Undoable: the ordered group-table and per-object membership
    state to `NarwhalModel` snapshots, so the normal command record restores both
    the group identity/name table and every membership on Undo and Redo.
    """

    name = "Group"
    description = "Group the selected objects into a new named group."
    steps = []
    records_undo = True         # snapshots carry table + memberships

    def execute(self, ctx: NarwhalContext):
        sel = _selection_names(ctx)
        if not sel:
            return ("Group: select the objects to group first "
                    "(nothing is selected)"), True
        # Resolve the selection to real document objects FIRST. A selection whose
        # names match no object (e.g. a stale name) must be refused by name -- not
        # answered by silently creating an empty group (the explicit-refusal policy).
        targets = []
        for nm in sel.values():
            targets.extend(_objects_named(ctx, nm))
        if not targets:
            return ("Group: the selection resolves to no objects in the document "
                    f"({', '.join(repr(n) for n in sel.values())})"), True
        file = ctx.model.file
        n = 0
        while _group_index_by_name(file, f"Group{n}") is not None:
            n += 1
        gname = f"Group{n}"
        # Group-table and attribute edits bypass model.add_object, so explicitly
        # capture the pre-change snapshot while the command record is open.
        ctx.note_change()
        group = r3.Group()
        group.Name = gname
        file.Groups.Add(group)
        gi = _group_index_by_name(file, gname)   # the index it actually landed at
        # Add every selected object exactly once. AddToGroup is idempotent (
        # adding the same index twice leaves `GetGroupList2` `[0]`), so a name
        # matched by two display objects is added once, not twice.
        for fobj in targets:
            fobj.Attributes.AddToGroup(gi)
        return f"Group: added {len(targets)} object(s) to {gname}", True


class UngroupAllCmd(Command):
    """`UngroupAll` -- remove selected groups and their nested/overlapping groups.

    the reference CAD application's command removes the selected group and every group nested in
    it in one action. In this document model groups are represented by object
    membership indices, so nesting/overlap is the transitive connected component
    of the selected objects' group memberships. Only components reached from the
    current selection are changed; disconnected groups remain untouched.

    Group-table rows are intentionally retained. ``File3dmGroupTable.Delete``
    compacts indices without remapping memberships in this rhino3dm build, so
    removing rows would corrupt unrelated groups. The ordinary command snapshot
    supplies one exact Undo/Redo record for the table and all memberships.
    """

    name = "UngroupAll"
    description = "Remove the selected group and nested groups."
    steps = []
    records_undo = True

    @staticmethod
    def _component(file, seeds):
        """Return the group-index component reached from ``seeds``."""
        active = set(int(index) for index in seeds)
        changed = True
        while changed:
            changed = False
            for fobj in file.Objects:
                memberships = {int(index) for index in
                               (fobj.Attributes.GetGroupList2() or [])}
                if memberships.intersection(active):
                    before = len(active)
                    active.update(memberships)
                    changed = changed or len(active) != before
        return active

    def execute(self, ctx: NarwhalContext):
        sel = _selection_names(ctx)
        if not sel:
            return ("UngroupAll: select the group to ungroup first "
                    "(nothing is selected)"), True

        selected = []
        seen = set()
        for name in sel.values():
            for fobj in _objects_named(ctx, name):
                oid = str(fobj.Attributes.Id)
                if oid not in seen:
                    seen.add(oid)
                    selected.append(fobj)
        if not selected:
            return ("UngroupAll: the selection resolves to no objects "
                    "in the document"), True

        seeds = {int(index) for fobj in selected
                 for index in (fobj.Attributes.GetGroupList2() or [])}
        if not seeds:
            return ("UngroupAll: the selection is not in any group "
                    "(nothing to remove)"), True

        file = ctx.model.file
        component = self._component(file, seeds)
        targets = [fobj for fobj in file.Objects
                   if component.intersection(int(index) for index in
                                              (fobj.Attributes.GetGroupList2() or []))]
        if not targets:
            return "UngroupAll: no grouped objects were found", True

        ctx.note_change()
        removed = 0
        for fobj in targets:
            memberships = [int(index) for index in
                           (fobj.Attributes.GetGroupList2() or [])]
            if set(memberships).intersection(component):
                fobj.Attributes.RemoveFromAllGroups()
                # Restore memberships outside this operation's component. The
                # API only exposes RemoveFromAllGroups, so preserve overlap and
                # disconnected memberships explicitly, including their order.
                for index in memberships:
                    if index not in component:
                        fobj.Attributes.AddToGroup(index)
                removed += 1
        return None, True             # MEASURED (research_all_13 #178): silent


class UngroupCmd(Command):
    """`Ungroup` -- detach the selected objects from their group(s).

    (`the command reference --raw group`, section "Ungroup"):

        "The Ungroup command removes the group status from a selected group."

    The "group status" lives on the object's attributes (its membership list),
    so removing it is `RemoveFromAllGroups` on each selected object. Objects NOT
    in the selection keep their groups.

    Deliberately does NOT delete the (now possibly empty) group table entries.
    hazard: `File3dmGroupTable.Delete(index)` compacts the table and
    SHIFTS the integer indices of every higher group, but rhino3dm does NOT
    remap the integer memberships already stored on objects -- after such a
    delete, a member's stored index points at the wrong group and an out-of-range
    membership can fail. Deleting a group entry is therefore unsafe on
    this build, and the reference CAD application's own `Ungroup` removes the membership, not the
    entry, so the faithful action is to detach the objects and leave the (empty)
    group in place. `Purge Groups` (a separate, not-yet-built command) is the
    natural home for removing the now-empty entries, where the shift can be
    handled safely.

    Undo snapshots carry the group table and every object's
    membership, so the normal command record restores detachments on Undo/Redo.
    """

    name = "Ungroup"
    description = "Remove the group status from the selected objects."
    steps = []
    records_undo = True         # snapshots carry table + memberships

    def execute(self, ctx: NarwhalContext):
        sel = _selection_names(ctx)
        if not sel:
            return ("Ungroup: select the objects to ungroup first "
                    "(nothing is selected)"), True
        targets = []
        for nm in sel.values():
            for fobj in _objects_named(ctx, nm):
                if fobj.Attributes.GroupCount:
                    targets.append(fobj)
        if not targets:
            return ("Ungroup: the selection was not in any group "
                    "(nothing to remove)"), True
        ctx.note_change()
        for fobj in targets:
            fobj.Attributes.RemoveFromAllGroups()
        return None, True             # MEASURED (research_all_13 #177): silent


def _selected_surface_edges(ctx, only_closed):
    """The exact edges of the selected surfaces, as ``(display_name, layer_index, curve)``.

    Shared by `DupEdge` (`only_closed=False`, every edge) and `DupBorder`
    (`only_closed=True`, only the closed rim edges).

    The distinction the two commands rely on is that a cylinder has three edges:

    * a **cylinder** is two CLOSED circles (its rim) plus one OPEN line (its seam);
    * a **cone** is a closed base circle plus an open apex-edge;
    * a **box** is twelve OPEN lines with no closed boundary.

    So `DupBorder` on a box yields NOTHING (correct -- a box has no closed rim),
    while `DupEdge` yields all 12. The "closed" test is `curve.IsClosed` on the
    EXACT extracted edge. The curves come from `bridge.extract_subobject(geom,
    "edge", id)`  -- the exact edges of the analytic master/recipe, never
    the display proxy's facets. A curve or point has no edges, so it is
    skipped (it contributes nothing to duplicate).
    """
    sel = _selection_names(ctx)
    out = []
    for nm in sorted(sel.values()):
        geom = ctx.geom_by_name(nm)
        if geom is None:
            continue
        parts = bridge.subobject_parts(geom)
        if not parts or not parts.get("edges"):
            continue                  # a curve / point has no edges to duplicate
        layer_index = _layer_index_of(ctx, nm)
        for eid, _poly in parts["edges"]:
            try:
                curve = bridge.extract_subobject(geom, "edge", eid)
            except bridge.SubObjectError:
                continue
            if only_closed and not bool(getattr(curve, "IsClosed", False)):
                continue
            out.append((nm, layer_index, curve))
    return out


class DupEdgeCmd(Command):
    """`DupEdge` -- duplicate the edges of the selected surfaces as curves.

    (the command behavior): "Select the edges of a surface, mesh or
    SubD object." Zero-step and selection-driven (the `Group` / `Unisolate`
    shape): it acts on the current selection and creates a NEW curve object for
    EVERY edge of each selected surface -- a cylinder's two rim circles AND its
    seam line, a box's twelve straight lines.

    The curves come from `bridge.extract_subobject(geom, "edge", id)`  --
    the EXACT edges of the analytic master/recipe, never the display proxy's
    facets (rule 0). `OutputLayer` (Current/Input) is a documented option that
    is not built, so typing it reports that by name (the explicit-refusal policy).

    `DupBorder` is the closed-rim variant (see `DupBorderCmd`): the same edges
    minus the open ones (a cylinder's seam, a box's straight lines).
    """

    name = "DupEdge"
    description = "Create curves from the edges of the selected surfaces."
    steps = []
    records_undo = True        # new curve objects are added -- undoable
    options = (
        opts.Option("OutputLayer", opts.MODE,
                    help="Specifies the layer for the results (Current or Input)."),
    )

    def on_mode(self, name, ctx):
        return f"DupEdge: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        edges = _selected_surface_edges(ctx, only_closed=False)
        if not edges:
            return ("DupEdge: select a surface or polysurface first "
                    "(a curve or point has no edges to duplicate)"), True
        made = 0
        for _nm, layer_index, curve in edges:
            if made == 0:
                ctx.note_change("DupEdge")
            ctx.add_geometry(curve, "Edge", layer_index=layer_index)
            made += 1
        return f"Duplicate Edge -- {made} curve(s) from the selection", True


class DupBorderCmd(Command):
    """`DupBorder` -- duplicate the CLOSED boundary of the selected surfaces.

    (the command behavior): `DupBorder` is `DupEdge` restricted to
    the closed rim edges -- the boundary loops of a surface, not its interior
    seams or straight lines. Zero-step and selection-driven, like `DupEdge`.

    distinction (probed): a cylinder is 3 edges -- two CLOSED circles
    (its rim) plus one OPEN line (its seam); a cone is a closed base circle plus
    an open apex-edge; a box is 12 open lines with NO closed boundary. So
    `DupBorder` on a box yields NOTHING (correct) while `DupEdge` yields all 12.

    The gate: a cylinder's `DupBorder` gives two circles of the EXACT
    radius -- each extracted rim is a degree-2 rational curve whose tight
    bounding box spans exactly 2r in the plane (radius = r to < 1e-9).

    The curves come from `bridge.extract_subobject`  -- exact, never the
    proxy's facets (rule 0). `OutputLayer` is refused by name (the explicit-refusal policy).
    """

    name = "DupBorder"
    description = "Create curves from the closed boundary of the selected surfaces."
    steps = []
    records_undo = True        # new curve objects are added -- undoable
    options = (
        opts.Option("OutputLayer", opts.MODE,
                    help="Specifies the layer for the results (Current or Input)."),
    )

    def on_mode(self, name, ctx):
        return f"DupBorder: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        edges = _selected_surface_edges(ctx, only_closed=True)
        if not edges:
            return ("DupBorder: the selected surfaces have no closed boundary "
                    "(a box's edges are all open lines)"), True
        made = 0
        for _nm, layer_index, curve in edges:
            if made == 0:
                ctx.note_change("DupBorder")
            ctx.add_geometry(curve, "Border", layer_index=layer_index)
            made += 1
        return f"Duplicate Border -- {made} curve(s) from the selection", True


class PurgeCmd(Command):
    """Purge -- remove unused entries from the document.

    the `Layers` and `Groups` options. The other eight options are DECLARED (so the
    command's option list is faithful) but NOT built: picking one REFUSES
    by name (the explicit-refusal policy -- an unimplemented thing must never look implemented) rather
    than silently purging nothing or purging the wrong thing.

    `Groups`: an unused group is one with no members (`model.purge_unused_groups`).
    `Block Definitions` refuses with its reason: the block definition table has no
    delete here.

    (the command reference Purge -> the command behavior): the command-line options
    are exactly
        Annotation Styles, Bitmaps, Block Definitions, Environments, Groups,
        Hatch Patterns, Layers, Linetypes, Materials, Textures
    and "Layers -- Purges unused layers." An "unused layer" is one holding no
    objects; `model.delete_layer` already enforces that (it refuses a layer that
    still holds objects, the current layer, and the last layer) and does the
    round trip that leaves a clean rhino3dm layer table. So this command only
    has to find the empty, non-current layers and ask `delete_layer` for each.

    Deleting high index -> low keeps the lower indices valid as the table shrinks,
    and `delete_layer` re-fixes `current_layer_index` after its round trip.
    """

    name = "Purge"
    description = "Purge unused layers or groups (the other unused types are not implemented)."
    steps = []
    records_undo = True        # deleting layers is a document change
    options = (
        opts.Option("Annotation Styles", opts.MODE,
                    help="Purges unused annotation styles."),
        opts.Option("Bitmaps", opts.MODE,
                    help="Purges unused embedded bitmap images."),
        opts.Option("Block Definitions", opts.MODE,
                    help="Purges unused block definitions."),
        opts.Option("Environments", opts.MODE,
                    help="Purges unused environments."),
        opts.Option("Groups", opts.MODE,
                    help="Purges unused groups."),
        opts.Option("Hatch Patterns", opts.MODE,
                    help="Purges unused hatch patterns."),
        opts.Option("Layers", opts.MODE,
                    help="Purges unused layers."),
        opts.Option("Linetypes", opts.MODE,
                    help="Purges unused linetypes."),
        opts.Option("Materials", opts.MODE,
                    help="Purges unused materials."),
        opts.Option("Textures", opts.MODE,
                    help="Purges unused textures."),
    )

    def begin(self, ctx):
        # No type chosen on the naming Enter: stay open and say what is in force --
        # the same shape as the other zero-step + MODE commands (GumballAlignment).
        return ("Purge: choose what to purge "
                "(Annotation Styles / Bitmaps / Block Definitions / Environments / "
                "Groups / Hatch Patterns / Layers / Linetypes / Materials / Textures). "
                "Only Layers and Groups are implemented."), False

    def execute(self, ctx):
        # A bare Enter with no option: nothing was chosen. Say so by name rather
        # than guessing (the explicit-refusal policy) -- a bare `Purge` must not purge everything.
        return ("Purge: no type chosen; only the Layers and Groups options are implemented "
                "(the other types are not built yet)"), True

    def on_mode(self, name, ctx):
        if name == "Layers":
            return self._purge_layers(ctx)
        if name == "Groups":
            return self._purge_groups(ctx)
        if name == "Block Definitions":
            return ("Purge: the Block Definitions option is not implemented yet -- the "
                    "block definition table cannot delete entries in this document "
                    "model"), True
        return f"Purge: the {name} option is not implemented yet", True

    def _purge_groups(self, ctx):
        if not ctx.model.unused_group_ids():
            return "Purge Groups: no unused groups to delete", True
        ctx.note_change(self.name)
        return f"Purged {ctx.model.purge_unused_groups()} unused group(s)", True

    def _purge_layers(self, ctx):
        m = ctx.model
        n = len(m.file.Layers)
        if n <= 1:
            return "Purge Layers: there is only one layer", True

        # "Unused" == no object on it. The current layer is kept even if empty:
        # it is where the next object lands, and `delete_layer` would refuse it
        # anyway, so exclude it up front for a clean refusal-free message.
        empty = [i for i in range(n)
                 if sum(1 for o in m.file.Objects
                        if int(o.Attributes.LayerIndex) == i) == 0]
        empty = [i for i in empty if i != m.current_layer_index]
        if not empty:
            return "Purge Layers: no unused layers to delete", True

        ctx.note_change(self.name)
        deleted = 0
        # High -> low so deleting a higher layer never shifts a lower one's index
        # out from under the loop.
        for i in sorted(empty, reverse=True):
            ok, _msg = m.delete_layer(i)
            if ok:
                deleted += 1
        return f"Purged {deleted} unused layer(s)", True


class RenameCmd(Command):
    """`Rename` -- give ONE selected object a new name.

    set `Attributes.Name` on one selected object; refuse a duplicate name.

    (the command reference): there is NO dedicated help page for `Rename`
    (`the command reference Rename` -> "no help page"), and it is not in the command-coverage
    COMMANDS table, so the shape is defined by the implementation text the way 's was:
    one selected object + a new name, and a duplicate name is refused rather than
    silently accepted. There is nothing to MEASURE the options of, so none are
    declared (the faithful answer, as with `What` and `Point`).

    The name is the document's `Attributes.Name` -- the source of truth every
    other command resolves against (`geom_by_name`, `object_id_by_name`, the
    selection). It is mutated IN PLACE on the file object, exactly the way
    `_VisibilityCmd` sets `Attributes.Mode`: one `note_change` before the
    mutation, so one rename is one "Rename" undo step. `Name` is in
    `model._SNAPSHOT_ATTRS`, so the undo snapshot carries the old name back and
    redo carries the new one forward.

    A duplicate is REFUSED by name (the explicit-refusal policy -- an unimplemented/invalid thing must
    never look implemented): renaming an object to a name another object already
    owns would make `geom_by_name` ambiguous, so it is reported and left
    unchanged instead of clobbering the name silently.

    Blender display objects and live selection are keyed by name, so the viewport label
    and any held selection retain the old name until the display is rebuilt.
    """

    name = "Rename"
    description = "Give one selected object a new name."
    steps = [("Select object to rename:", OBJECTS), ("New name:", TEXT)]
    # the reference CAD application selects first, then runs the command; the selection satisfies the
    # object step and the command runs on the typed name (like `Offset`/`Length`,
    # which pair a NAME/objects step with a follow-up prompt).
    preselect_names = 1
    records_undo = True        # a name change is a document change

    def execute(self, ctx: NarwhalContext):
        # The OBJECTS step appends the ONE object's name (selection or typed --
        # `_take_objects_selection` / `_feed_objects_step` both give args[0] a name);
        # the TEXT step appends the new name as a raw string (not parsed as a number).
        # Fall back to the live selection when driven without steps, like Explode /
        # Divide, so the same guard holds on every path.
        name = self.args[0] if self.args else None
        if not name:
            try:
                sel = ctx.selected_objects() or []
                if sel:
                    name = sel[0].name
            except Exception:  # noqa: BLE001
                sel = []
        new = (self.args[1] if len(self.args) > 1 else "") or ""
        new = str(new).strip()
        if not name:
            return "Rename: select an object to rename", True
        target = None
        target_id = None
        for fobj in ctx.model.file.Objects:
            if (fobj.Attributes.Name or "").lower() == name.lower():
                target = fobj
                target_id = str(fobj.Attributes.Id)
                break
        if target is None:
            return f"Rename: no object named {name!r}", True
        if not new:
            return "Rename: no new name given", True
        # A duplicate name is refused (case-insensitive): another object already
        # owns it, and accepting would make name lookups ambiguous.
        for fobj in ctx.model.file.Objects:
            if str(fobj.Attributes.Id) == target_id:
                continue
            if (fobj.Attributes.Name or "").lower() == new.lower():
                return (f"Rename: refused -- an object is already named {new!r} "
                        f"(names must be unique)"), True
        ctx.note_change(self.name)
        target.Attributes.Name = new
        return f"Renamed {name} to {new}", True


# ---------------------------------------------------------------------------
# analysis commands (report in the command bar, CAD-style)
# ---------------------------------------------------------------------------

# rhino3dm has no mass-properties classes. a solid with an analytic master
# (or a recipe that rebuilds one) is on THAT, through OCCT at an explicit
# eps -- never on its display proxy, whose triangles under-report a curved solid
# (box-union-cylinder 1140.845 for a true 1141.372, -4.6e-4, and it was
# labelled "exact"). Only geometry with no master falls back to grid integration.
#
# Output wording is the reference CAD application's (the command behavior, ):
#     Area = 60 (+/- 1e-008)
# "The numbers shown in parenthesis report the accuracy of the computation." The
# estimate is a CONVERGENCE estimate (the answer at two resolutions), never a
# constant -- a fixed "+/- 0" or "exact" would be a claim nobody (the reference CAD application's
# three-digit exponent is MSVC printf, not a format choice; Python prints 1e-08.)


def _err_up(err):
    """`err` rounded UP to one significant digit. Rounding to-nearest would print an
    accuracy BETTER than the one computed (1.23e-3 -> "1e-03"); a bound must not."""
    import math as _m

    if not err > 0.0:
        return 0.0
    k = _m.floor(_m.log10(err))
    step = 10.0 ** k
    return _m.ceil(err / step - 1e-9) * step


def _measured(label, value, err):
    """`label = value (+/- err)`, the reference CAD application's mass-property line.

    The value is printed to ONE decimal place past the error's (the reference CAD application prints
    `5.77350269 (+/- 1e-009)`), and the rounding that printing adds is folded into the
    stated bound -- so the PRINTED value is always within the PRINTED +/- of the truth.
    A fixed `.9g` broke that: `1141.37167 (+/- 2e-09)` is 6e-7 from the true volume.
    """
    import math as _m

    err = max(float(err), abs(value) * 1e-15, 1e-300)
    decimals = min(15, max(0, 1 - _m.floor(_m.log10(err))))
    shown = _err_up(err + 0.5 * 10.0 ** (-decimals))
    text = f"{value:.{decimals}f}"
    if "." in text:                   # the reference CAD application prints `Area = 60`, not `60.000000`
        text = text.rstrip("0").rstrip(".")
    return f"{label} = {text} (+/- {shown:.0e})"


def _grid_measure(geom, fn):
    """`(value, err)` from the grid integrator for geometry with NO master.

    A brep whose faces are all degree <= 1 is exact at nu=1 (a bilinear patch is fixed
    by its corners); anything curved is integrated at 64 and 32 and the error is the
    Richardson estimate 4/3 |f(64) - f(32)| (the grid is second order).
    """
    from . import analysis

    if analysis.analysis_density(geom) == 1:
        v = fn(geom, 1, 1)
        return v, abs(v) * 1e-12
    hi = fn(geom, 64, 64)
    lo = fn(geom, 32, 32)
    return hi, max(abs(hi - lo) * 4.0 / 3.0, abs(hi) * 1e-12)


class LengthCmd(Command):
    """Report lengths for selected curves and curve segments.

    Preselected curves and Ctrl+Shift segments are measured immediately, including a
    cumulative total for multiple items. At the prompt, a clicked curve or typed name is
    measured directly, while Enter measures the current selection. A selected segment
    takes precedence over its parent curve. Solid-edge measurement is not implemented.

    rhino3dm has no ``Curve.GetLength`` member. Polyline lengths are exact sums of straight
    segments; curved geometry uses Gauss-Legendre integration through
    ``analysis.curve_length``.
    """
    name = "Length"
    description = "Report the length of curves or curve segments."
    steps = [("Select curves to measure:", OBJECTS)]
    preselect_names = 1
    #: A Ctrl+Shift segment click selects the segment and confirms this prompt;
    #: ``_measure_selection`` reads that sub-object selection.
    accepts_subobjects = True

    def begin(self, ctx):
        lines = self._measure_selection(ctx)
        if lines:
            return lines, True
        return None, False

    def _feed_objects_step(self, text, ctx):
        if not text:
            lines = self._measure_selection(ctx)
            if lines:
                return lines, True
            return "No curve selected -- select curves, then press Enter", False
        geom = ctx.geom_by_name(text)
        if not isinstance(geom, r3.Curve):
            # stay at the prompt: the reference CAD application's curve filter would not have picked it either
            return f"{text} is not a curve -- select curves to measure", False
        return super()._feed_objects_step(text, ctx)

    def execute(self, ctx: NarwhalContext):
        name = self.args[0]
        geom = ctx.geom_by_name(name)
        if not isinstance(geom, r3.Curve):
            return f"No curve named {name!r}", True
        return self._format(ctx, [(name, self._curve_length(geom), geom.IsClosed)]), True

    # -- measuring ----------------------------------------------------------------------

    @staticmethod
    def _curve_length(geom):
        from . import analysis

        verts = bridge._polyline_vertices(geom)
        if verts is not None and len(verts) >= 2:
            return math.fsum(math.dist(a, b) for a, b in zip(verts, verts[1:]))
        return analysis.curve_length(geom)

    @staticmethod
    def _segment_length(geom, index):
        """Length of sub-object segment `index`, numbered as `bridge.curve_segments`
        numbers them (what the UI's Ctrl+Shift pick records), or None."""
        from . import analysis

        verts = bridge._polyline_vertices(geom)
        if verts is not None:
            if len(verts) >= 3 and 0 <= index < len(verts) - 1:
                return math.dist(verts[index], verts[index + 1])
            return None
        if isinstance(geom, r3.PolyCurve):
            try:
                if 0 <= index < int(geom.SegmentCount):
                    seg = geom.SegmentCurve(index)
                    return None if seg is None else LengthCmd._curve_length(seg)
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _selected_subobjects(ctx):
        """The UI's Ctrl+Shift selection, ``[(name, kind, index)]``: from the context
        if it offers one, else `ui.selected_subobjects` (read-only); headless, none."""
        getter = getattr(ctx, "selected_subobjects", None)
        if not callable(getter):
            try:
                from . import ui

                getter = ui.selected_subobjects
            except Exception:  # noqa: BLE001 - no viewport session
                return []
        try:
            return [(str(n), str(k), int(i)) for n, k, i in (getter() or ())]
        except Exception:  # noqa: BLE001
            return []

    def _measure_selection(self, ctx):
        """The report for the current selection (segments + curves), or None."""
        items, notes, owners = [], [], set()
        for name, kind, index in sorted(self._selected_subobjects(ctx)):
            if kind == "Segment":
                length = self._segment_length(ctx.geom_by_name(name), index)
                if length is None:
                    notes.append(f"segment {index} of {name} no longer exists")
                    continue
                items.append((f"segment {index} of {name}", length, False))
                owners.add(name)
            elif kind == "Edge":
                notes.append(f"edge {index} of {name}: measuring a solid's edge is "
                             f"not implemented yet")
        try:
            selected = list(ctx.selected_objects())
        except Exception:  # noqa: BLE001
            selected = []
        for obj in selected:
            name = getattr(obj, "name", None)
            if not name or name in owners:
                continue
            geom = ctx.geom_by_name(name)
            if isinstance(geom, r3.Curve):
                items.append((name, self._curve_length(geom), geom.IsClosed))
        if not items:
            return "\n".join(notes) if notes else None
        return "\n".join([self._format(ctx, items)] + notes)

    @staticmethod
    def _format(ctx, items):
        unit = ""
        try:
            unit = " " + ctx.model.unit_system().lower()
        except Exception:  # noqa: BLE001
            unit = ""
        if len(items) == 1:
            label, length, closed = items[0]
            shut = ", closed" if closed else ""
            return f"Length = {length:.10g}{unit}  ({label}{shut})"
        # MEASURED (Research13 #81): for several curves the reference CAD application prints ONLY the
        # accumulated line ("Cumulative length = 13.283 millimeters for 3 curves").
        total = math.fsum(length for _l, length, _c in items)
        return f"Cumulative length = {total:.10g}{unit} for {len(items)} curves"


def _report_unit(ctx, choice):
    """``(factor, plural unit name)`` from model length to the `Units` choice (Area's names)."""
    from . import units as _units
    try:
        model_unit = ctx.model.unit_system()
    except Exception:  # noqa: BLE001
        model_unit = None
    chosen = AreaCmd._UNIT_KEYS.get(choice)
    if chosen is None or model_unit is None:
        return 1.0, (model_unit or "").lower()
    return _units.scale_factor(model_unit, chosen), chosen.lower()


class AreaCmd(Command):
    """`Area` -- MEASURED (research_all_13 #004):

    * prompt "Select surfaces, polysurfaces, hatches, closed planar curves or polygon
      meshes for area calculation"; a preselection runs at once;
    * a closed circle r=2 -> "Area = 12.566371 (+/- 4.8e-06) square millimeters" -- the
      EXACT area, now from Green's theorem on the exact NURBS
      (`analysis.planar_curve_area_centroid`), not a 512-gon (which was 3e-4 short);
    * several objects -> "Cumulative Area = ... square millimeters for 2 objects";
    * an open curve is not measured: the prompt is asked again.
    The `Units` option and the "Calculating Area..." line are not built.
    """

    name = "Area"
    description = "Report the area enclosed by curves, or the surface area."
    steps = [("Select surfaces, polysurfaces, hatches, closed planar curves or polygon "
              "meshes for area calculation:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = False
    # MEASURED (research_all_13 #004): `Units <ModelUnits> ( ModelUnits  Micron ...  Mile )`,
    # sticky; Units=Inch on a 66 mm^2 box -> "Area = 0.102300205 ... square inches".
    # Liter is listed by the reference but is not an area unit -- refused by name.
    UNIT_CHOICES = ("ModelUnits", "Micron", "Millimeter", "Centimeter", "Liter",
                    "Decimeter", "Meter", "Kilometer", "Microinch", "Mil", "Inch",
                    "Foot", "Yard", "Mile")
    _UNIT_KEYS = {"Micron": "Microns", "Millimeter": "Millimeters",
                  "Centimeter": "Centimeters", "Decimeter": "Decimeters",
                  "Meter": "Meters", "Kilometer": "Kilometers",
                  "Microinch": "Microinches", "Mil": "Mils", "Inch": "Inches",
                  "Foot": "Feet", "Yard": "Yards", "Mile": "Miles"}
    _sticky_units = ["ModelUnits"]
    options = (opts.Option("Units", opts.ENUM, choices=UNIT_CHOICES, default="ModelUnits",
                           help="The units the area is reported in."),)

    def __init__(self):
        super().__init__()
        self.opt["Units"] = AreaCmd._sticky_units[0]

    def on_option_set(self, option, value, ctx):
        if option.name == "Units":
            if value == "Liter":
                self.opt["Units"] = AreaCmd._sticky_units[0]
                return "Area: Liter is a volume unit, not an area unit", False
            AreaCmd._sticky_units[0] = value
        return super().on_option_set(option, value, ctx)

    def _report_unit(self, ctx):
        """``(factor, plural unit name)``: model length -> report length."""
        return _report_unit(ctx, self.opt.get("Units", "ModelUnits"))

    @staticmethod
    def _one(geom, name):
        """``(area, err)``, None for an open curve, or a refusal string."""
        from . import analysis

        if isinstance(geom, r3.Curve):
            if not geom.IsClosed:
                return None
            exact = analysis.planar_curve_area_centroid(geom)
            if exact is not None:
                return abs(exact[0]), exact[1]
            area = analysis.closed_curve_area(geom)
            if area is None:
                return f"{name} is not a planar closed curve"
            finer = analysis.closed_curve_area(geom, n=1024)
            return area, max(abs(finer - area) * 4.0 / 3.0, abs(area) * 1e-12)
        mp = analysis.exact_mass(geom, solid=False)
        if mp is not None:
            return mp["area"], mp["area_err"]
        if analysis.is_proxy(geom):
            # A proxy whose master could not be recovered must not have its triangles
            # reported as the answer (the explicit-refusal policy).
            return (f"{name}: its exact geometry is unavailable, so its area cannot be "
                    f"measured (the display mesh is not the shape)")
        return _grid_measure(geom, analysis.surface_area)

    def execute(self, ctx: NarwhalContext):
        names = self.args[0] if self.args else []
        if isinstance(names, str):
            names = [names]
        total, total_err, count, notes = 0.0, 0.0, 0, []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                notes.append(f"No object named {name!r}")
                continue
            got = self._one(geom, name)
            if got is None:
                continue                    # an open curve: not measured
            if isinstance(got, str):
                notes.append(got)
                continue
            total += got[0]
            total_err += got[1]
            count += 1
        if count == 0 and not notes:
            # MEASURED: only open curves -> the object prompt again (they are discarded).
            self.args, self.step_index = [], 0
            selector = getattr(ctx, "select_by_names", None)
            if callable(selector):
                selector([])
            return None, False
        if count == 0:
            return "\n".join(notes), True
        factor, unit_name = self._report_unit(ctx)
        total, total_err = total * factor * factor, total_err * factor * factor
        unit = " square " + unit_name if unit_name else ""
        if count == 1:
            line = _measured("Area", total, total_err) + unit
        else:
            line = (_measured("Cumulative Area", total, total_err) + unit
                    + f" for {count} objects")
        return "\n".join([line] + notes), True


class VolumeCmd(Command):
    name = "Volume"
    description = "Report the volume enclosed by a solid."
    steps = [("Solid:", "name")]
    # the reference CAD application selects first, then runs the command; the selection
    # fallback these commands already tried was unreachable, because an
    # empty answer at a fixed prompt CANCELS (see Command.feed).
    preselect_names = 1

    def execute(self, ctx: NarwhalContext):
        from . import analysis

        name = self.args[0]
        if not name and ctx.selected_objects():
            name = ctx.selected_objects()[0].name
        geom = ctx.geom_by_name(name)
        if geom is None or not isinstance(geom, r3.Brep):
            return f"No solid named {name!r}", True
        if not analysis.is_closed_solid(geom):
            # NOTE: this used to read `return f"...",` with an orphaned `True`
            # on the next line, which returned a 1-TUPLE. Unpacking it in
            # state.feed_line raised ValueError, so the user saw "Bad input:
            # not enough values to unpack" instead of the surface area.
            # Repro: Revolve an open profile (uncapped -> IsSolid False).
            mp = analysis.exact_mass(geom, solid=False)
            if mp is not None:
                area = mp["area"]
            else:
                area, _err = _grid_measure(geom, analysis.surface_area)
            return (f"{name} is not a closed solid "
                    f"(open surface area: {area:.6g})"), True
        mp = analysis.exact_mass(geom, solid=True)
        if mp is not None:
            return _measured("Volume", mp["volume"], mp["volume_err"]), True
        if analysis.is_proxy(geom):
            return (f"{name}: its exact geometry is unavailable, so its volume cannot "
                    f"be measured (the display mesh is not the shape)"), True
        volume, err = _grid_measure(geom, analysis.solid_volume)
        return _measured("Volume", abs(volume), err), True


class DistanceCmd(Command):
    name = "Distance"
    description = "Report the distance between two points."
    # MEASURED (research_all_13 #040): the first prompt, and for w0,0,0 -> w3,4,0 the report
    #   World angles and deltas: xy = 53.130  elevation = 0.000  dx = 3.000  dy = 4.000  dz = 0.000
    #   Distance = 5.000 millimeters
    # MEASURED (research_preview_14 P34): the second prompt is "Second point for distance".
    steps = [("First point for distance:", "point"), ("Second point for distance:", "point")]

    def execute(self, ctx: NarwhalContext):
        import math as _math

        p1, p2 = self.args
        dx, dy, dz = (p2[i] - p1[i] for i in range(3))
        d = _math.dist(p1, p2)
        xy = _math.degrees(_math.atan2(dy, dx)) if (dx or dy) else 0.0
        elevation = _math.degrees(_math.atan2(dz, _math.hypot(dx, dy))) if d else 0.0
        try:
            unit = " " + ctx.model.unit_system().lower()
        except Exception:  # noqa: BLE001
            unit = ""

        def f(v):
            return f"{v + 0.0:.3f}"            # +0.0: never print -0.000

        return (f"World angles and deltas: xy = {f(xy)}  elevation = {f(elevation)}  "
                f"dx = {f(dx)}  dy = {f(dy)}  dz = {f(dz)}\n"
                f"Distance = {f(d)}{unit}"), True


class BoundingBoxCmd(Command):
    """`BoundingBox` -- selection, coordinate system, cumulative mode and solids.

    The source bounds are by ``analysis.bounding_box``.  Output boxes are
    always made by the exact analytic box producer; Meshes and Curves are refused
    explicitly rather than being silently approximated.
    """

    name = "BoundingBox"
    description = "Create and report bounding boxes for selected objects."
    steps = [("Select objects:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("CoordinateSystem", opts.ENUM, default="CPlane",
                    choices=("CPlane", "World"),
                    help="Sets the coordinate system for the bounding box."),
        opts.Option("Cumulative", opts.TOGGLE, default=True,
                    help="Creates one box around all selected objects."),
        opts.Option("Output", opts.ENUM, default="Solids",
                    choices=("Solids", "Meshes", "Curves"),
                    help="Sets the output object type."),
    )

    def _take_objects_selection(self, ctx):
        """Keep legacy string args while retaining the complete preselection."""
        if self.args or not self.preselect_names:
            return 0
        selected = list(ctx.selected_objects())
        if not selected or self.steps[self.step_index][1] != OBJECTS_MULTI:
            return 0
        self._selected_names = [obj.name for obj in selected]
        self.args.append(self._selected_names[0])
        self.step_index += 1
        return 1

    def _feed_objects_multi_step(self, text, ctx):
        """Keep the existing named-object path when nothing is preselected."""
        if text.strip() and not ctx.selected_objects():
            name = text.strip()
            if ctx.geom_by_name(name) is not None:
                self.args.append(name)
                self.step_index += 1
                return self.execute(ctx)
        return super()._feed_objects_multi_step(text, ctx)

    @staticmethod
    def _point(point):
        return (float(point.X), float(point.Y), float(point.Z))

    def _frame(self, ctx):
        from . import cplane
        if self.opt.get("CoordinateSystem") == "World":
            return cplane.CPlane()
        return self._active_cplane(ctx)

    def execute(self, ctx: NarwhalContext):
        from . import analysis, bounding_boxes
        from . import bridge
        from . import cplane

        output = self.opt.get("Output", "Solids")
        if output != "Solids":
            return (f"BoundingBox: Output={output} is not implemented yet; "
                    "only Solids are supported"), True

        if getattr(self, "_selected_names", None):
            names = list(self._selected_names)
        elif self.args:
            names = [self.args[0]]
        else:
            names = []
        if not names:
            return "BoundingBox: Nothing selected", True
        frame = self._frame(ctx)
        measured = []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                return f"No object named {name!r}", True
            lo, hi = analysis.bounding_box(geom)
            world_bounds = (self._point(lo), self._point(hi))
            measured.append((name, bounding_boxes.frame_bounds(*world_bounds, frame)))

        if self.opt.get("Cumulative", True):
            groups = [("BoundingBox", bounding_boxes.union_bounds(
                bounds for _name, bounds in measured))]
        else:
            groups = measured

        def add_boxes():
            reports = []
            for label, bounds in groups:
                minimum, maximum = bounds
                # make_box builds in the frame's local XY plane. Shift its origin
                # to the lower normal extreme so the exact producer preserves W.
                shifted = cplane.CPlane(
                    origin=frame.to_world(0.0, 0.0, minimum[2]),
                    xaxis=frame.xaxis, yaxis=frame.yaxis)
                p1, p2, height = bounding_boxes.box_points(bounds, frame)
                _obj_id, display_name = ctx.add_geometry(
                    bridge.make_box(p1, p2, height, shifted), "BoundingBox")
                reports.append(bounding_boxes.report(display_name, bounds))
            return "\n".join(reports)

        return self._recorded(ctx, add_boxes), True


# ---------------------------------------------------------------------------
# Angle, Radius, Diameter, AreaCentroid, VolumeCentroid, EvaluatePt
# ---------------------------------------------------------------------------
#
# Steps and options come from each command's help page; prompts and output lines use
# the reference CAD application's command-UI format strings:
#     "Start of first line" / "End of first line" / "Start of second line" /...
#     "Select two planar surfaces or lines to measure the angle between them"
#     "Angle = %s"   "Radius = %s"   "Diameter = %s"   "Radius of curvature is infinite."
#     "Select point on curve for radius measurement. Press Enter when done"
#     "Area Centroid = %.Ng,%.Ng,%.Ng (+/- %.2g,%.2g,%.2g)"  (+ "Cumulative... for %i
#      objects", and the same for "Volume Centroid" / "for %i solids")
#     "Point to evaluate. Press Enter when done"
#     "Point in world coordinates = %s    CPlane coordinates = %s"
# What fills a `%s` is the reference CAD application's unit-aware number formatter, which we do not have:
# the value to 10 significant digits with no padding zeros (a 5 prints "5").


def _num(v):
    """A number for a `%s` slot: 10 significant digits, no -0."""
    v = float(v)
    if v == 0.0:
        v = 0.0                                  # -0.0 -> 0
    return f"{v:.10g}"


def _xyz_text(p):
    """the reference CAD application's comma-separated `x,y,z` (as in `Area Centroid = x,y,z`)."""
    return ",".join(_num(c) for c in p)


def _up2(err):
    """`err` rounded UP to two significant digits, for the reference CAD application's `%.2g` error slot."""
    import math as _m

    if not err > 0.0:
        return 0.0
    step = 10.0 ** (_m.floor(_m.log10(err)) - 1)
    return _m.ceil(err / step - 1e-9) * step


def _centroid_line(label, c, err, suffix=""):
    """`label = x,y,z (+/- ex,ey,ez)` -- the reference CAD application's `%.Ng` triple and `%.2g` errors.

    N is chosen as `_measured` chooses decimals: every coordinate printed one digit past
    its own error, and the rounding that printing adds folded into the printed error, so
    each printed coordinate is within its printed +/- of the truth. A coordinate smaller
    than its error prints as 0 (a centroid on an axis is not "3.1e-17").
    """
    import math as _m

    shown, n_sig = [], 1
    vals = []
    for v, e in zip(c, err):
        e = max(float(e), 1e-300)
        v = 0.0 if abs(v) <= e else float(v)
        vals.append((v, e))
        if v:
            mag = _m.floor(_m.log10(abs(v)))
            n_sig = max(n_sig, mag + 1 + (1 - _m.floor(_m.log10(e))))
    n_sig = min(15, max(1, n_sig))
    for v, e in vals:
        if v:
            last = 10.0 ** (_m.floor(_m.log10(abs(v))) + 1 - n_sig)
            e = e + 0.5 * last
        shown.append(_up2(e))
    xyz = ",".join(f"{v:.{n_sig}g}" for v, _e in vals)
    errs = ",".join(f"{e:.2g}" for e in shown)
    return f"{label} = {xyz} (+/- {errs}){suffix}"


class AngleCmd(Command):
    """`Angle` -- the command behavior.

    "reports the angle between two sets of locations in degrees... picked points
    that represent the endpoints of two lines": four picks, two imaginary lines. Options:
    `TwoObjects` ("Measures the angle between two planar surfaces or two lines") -- built;
    `SubCrv` -- declared, not implemented.

    a line's direction is start -> end as picked (or as drawn, for a line object), so
    the answer is 0..180; a planar surface's direction is its normal.
    """

    name = "Angle"
    description = "Report the angle between two lines (four picked points), in degrees."
    steps = [("Start of first line:", POINT), ("End of first line:", POINT),
             ("Start of second line:", POINT), ("End of second line:", POINT)]
    options = (
        opts.Option("TwoObjects", opts.MODE,
                    help="Measures the angle between two planar surfaces or two lines."),
        opts.Option("SubCrv", opts.MODE,
                    help="Selects part of a curve as input."),
    )
    TWO_OBJECTS_PROMPT = ("Select two planar surfaces or lines to measure the angle "
                          "between them:")

    def on_mode(self, name, ctx):
        if name == "TwoObjects":
            if self.args:
                return ("Angle: TwoObjects is chosen before the first point -- start "
                        "the command again"), False
            self.steps = [(self.TWO_OBJECTS_PROMPT, OBJECTS_MULTI)]
            self.step_index = 0
            return None, False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        result = super()._feed(text, ctx)
        # MEASURED (research_all_13 #002): an end ON the start (w0,0,0 twice) re-asks
        # "End of first line:" with no message; DERIVED for the second line likewise.
        if (self.steps and self.steps[0][1] == POINT and len(self.args) == 2
                and math.dist(self.args[0], self.args[1]) <= 0.0):
            self.args.pop()
            self.step_index = len(self.args)
            return None, False
        return result

    @staticmethod
    def _direction(geom):
        """A line's start->end vector, a planar surface's normal, or None."""
        if isinstance(geom, r3.Curve):
            try:
                if not geom.IsLinear():
                    return None
            except Exception:  # noqa: BLE001
                return None
            a, b = geom.PointAtStart, geom.PointAtEnd
            return (b.X - a.X, b.Y - a.Y, b.Z - a.Z)
        faces = getattr(geom, "Faces", None)
        if faces is not None and len(faces) == 1 and faces[0].IsPlanar():
            f = faces[0]
            du, dv = f.Domain(0), f.Domain(1)
            n = f.NormalAt((du.T0 + du.T1) / 2, (dv.T0 + dv.T1) / 2)
            return (n.X, n.Y, n.Z)
        return None

    def execute(self, ctx: NarwhalContext):
        from . import analysis

        if self.steps and self.steps[0][1] == OBJECTS_MULTI:
            names = self.args[0] if self.args else []
            if len(names) != 2:
                return (f"Angle: select exactly two lines or planar surfaces "
                        f"({len(names)} selected)"), True
            dirs = []
            for n in names:
                d = self._direction(ctx.geom_by_name(n))
                if d is None:
                    return f"Angle: {n} is not a line or a planar surface", True
                dirs.append(d)
            u, v = dirs
        else:
            p1, p2, p3, p4 = self.args
            if math.dist(p3, p4) <= 0.0:
                self.args.pop()                  # DERIVED from #002: re-ask silently
                self.step_index = 3
                return None, False
            u = tuple(b - a for a, b in zip(p1, p2))
            v = tuple(b - a for a, b in zip(p3, p4))
        try:
            deg = analysis.angle_between(u, v)
        except ValueError as e:
            return f"Angle: {e}", True
        # MEASURED (research_all_13 #002): three decimals, "Angle = 90.000".
        return f"Angle = {deg + 0.0:.3f}", True


def _flip_refusal(geom):
    """If `geom` cannot be flipped/reversed honestly, the reason why; None if it can.

    (rule 0 + the explicit-refusal policy): the one refusal this codebase can be to need.
    A solid with an analytic MASTER must not have its display proxy flipped alone --
    the master is the geometry that is booleaned and exported, and
    rhino3dm's `Brep.Flip` / `Curve.Reverse` touch ONLY the proxy. Flipping the
    proxy would leave the master unflipped, which is exactly the stale-master failure
    `bridge.transform_geometry` exists to stop (audit gate 5). OCCT (the master's
    home) has no reverse/flip, so the honest answer is to REFUSE BY NAME rather than
    silently flip the proxy alone and ship a solid whose display and geometry disagree.
    """
    from . import kernel

    if kernel.master_of(geom) is not None:
        return ("has an analytic master; Flip/Reverse of it is not implemented yet -- "
                "flipping the display proxy alone would leave the master unflipped")
    if isinstance(geom, r3.Curve):
        return None
    if isinstance(geom, r3.Brep):
        return None
    return "is neither a curve nor a polysurface/solid -- Flip/Reverse does not apply"


def _perform_flip(geom):
    """Flip/reverse `geom` in place (the caller has already refused it if it must not
    be done). Returns the past-tense word for the report. Both `Curve.Reverse` and
    `Brep.Flip` mutate in place and persist in the File3dm, so no
    re-insertion is needed."""
    if isinstance(geom, r3.Curve):
        geom.Reverse()
        return "reversed"
    geom.Flip()
    return "flipped"


class FlipCmd(Command):
    """`Flip` / `Dir` -- the command behavior, the command behavior. reverse direction.

    (the command behavior): "Select objects. The command line reports the number
    of objects flipped." Supported input: Curve, surface, polysurface, mesh, SubD. Note:
    "Closed polysurfaces and SubDs cannot have their normal direction facing toward the
    interior of the object." (the command behavior): "Arrows show the directions on
    curves or normal directions on surfaces... Click the mouse button on an object to
    flip (reverse) the direction for that object." -- `Dir`'s click-to-flip action is
    this same flip; its arrows/inspect display is the UI's job, not this command's.

    The mutation is `Curve.Reverse` (curves) or `Brep.Flip` (surfaces/solids), both
    present in rhino3dm and both mutating in place. THE MASTER RULE:
    a solid with an analytic master must flip the master too, or refuse by name -- and
    it cannot, because OCCT has no reverse/flip. So a master-bearing solid is REFUSED
    by name (the explicit-refusal policy: never let a proxy flip silently leave its master behind). This is
    the exact failure `bridge.transform_geometry` exists to prevent (audit gate 5).
    """

    name = "Flip"
    description = "Reverse the direction of curves, or the normal direction of surfaces and solids."
    steps = [("Select objects:", OBJECTS_MULTI)]
    # the reference CAD application selects first, then runs the command; the selection satisfies the step
    # and the command runs on the naming Enter (like `What`). A typed name still
    # works -- the step is answered by the selection OR a name.
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True         # a direction change is a document change

    def execute(self, ctx: NarwhalContext):
        # The OBJECTS_MULTI step appends a LIST of names; a single typed
        # name arrives the same way (one-element list). Normalise both.
        arg = self.args[0] if self.args else None
        if isinstance(arg, (list, tuple)):
            names = list(arg)
        elif arg:
            names = [arg]
        else:
            names = []
        if not names:
            return "Nothing selected", True
        # Resolve and classify FIRST (read-only), so `note_change` is called
        # BEFORE any mutation (a run that flips nothing leaves no undo step).
        plan = []
        for name in names:
            geom = ctx.geom_by_name(name)
            plan.append((name, geom,
                         None if geom is None else _flip_refusal(geom)))
        flipped = 0
        lines = []
        for name, geom, refusal in plan:
            if geom is not None and refusal is None:
                if flipped == 0:
                    ctx.note_change(self.name)   # capture "before" before the 1st change
                lines.append(f"{name}: {_perform_flip(geom)}")
                flipped += 1
            elif geom is None:
                lines.append(f"{name}: no object by that name")
            else:
                lines.append(f"{name}: {refusal}")
        head = f"{flipped} object{'s' if flipped != 1 else ''} flipped"
        if flipped < len(names):
            head += f" -- {len(names) - flipped} refused"
        return "\n".join([head] + lines), True


class DirCmd(FlipCmd):
    """`Dir` -- the command behavior. the click-to-flip action of `Flip`.

    `Dir`'s interactive part (arrows showing direction, cursor to inspect) is a
    display concern the UI owns; the command-line action -- clicking an object to
    flip/reverse it -- is exactly `Flip`. `Dir` is therefore `Flip` under a second
    registered name, keeping both commands thin as the implementation asks.
    """

    name = "Dir"
    description = "Show the direction of curves / normal of surfaces, and click to flip it."


def _degree_refusal(geom):
    """If `geom` cannot have its degree changed honestly, the reason; None if it can.

    (rule 0 + the explicit-refusal policy): the same master rule as A curve with an analytic
    MASTER must change its degree on the MASTER too -- but rhino3dm's `IncreaseDegree`
    touches ONLY the display proxy, and OCCT (the master's home) has no degree change.
    Raising the proxy's degree alone would leave the master at the old degree, which is
    exactly the stale-master failure `bridge.transform_geometry` exists to stop (audit
    gate 5). So a master-bearing curve is REFUSED BY NAME rather than silently diverging.
    A non-curve is refused too: `ChangeDegree` applies to curves (surfaces need separate
    u and v values, which this command does not take).
    """
    from . import kernel

    if kernel.master_of(geom) is not None:
        return ("has an analytic master; ChangeDegree of it is not implemented yet -- "
                "changing the proxy's degree would leave the master behind")
    if not isinstance(geom, r3.Curve):
        return ("is not a curve -- ChangeDegree applies to curves (surfaces need "
                "separate u and v values, which this command does not take)")
    return None


class ChangeDegreeCmd(Command):
    """`ChangeDegree` -- the command behavior. raise a curve's degree.

    (`the command reference ChangeDegree`): Steps: "Select the objects. Type a degree
    value. For surfaces type values in the u and v directions." Options: `Deformable`
    (Yes/No) and `SubCrv`. With `Deformable` OFF (the default), the reference CAD application: if the current
    degree is LESS than requested it keeps the SAME geometry and parameterization and
    just adds knot multiplicity (an exact raise); if the current degree is GREATER than
    requested it is an APPROXIMATION. So raising is exact and lowering is not -- which
    is why this command RAISES via `NurbsCurve.IncreaseDegree` (present, exact,
    mutates in place) and REFUSES LOWERING BY NAME (the explicit-refusal policy: an unimplemented thing must
    never look implemented).

    THE MASTER RULE (as with ): a curve with an analytic master is refused
    by name, because `IncreaseDegree` would change only the display proxy and leave the
    OCCT master at the old degree -- the stale-master failure `bridge.transform_geometry`
    exists to prevent (audit gate 5).

    The `SubCrv` option is not implemented (it degree-changes one span of a curve) and is
    therefore not declared, so it reports "not implemented yet" instead of silently
    ignoring it. The `Deformable` option is the approximation toggle, which this command
    refuses by name, so it is likewise not declared.
    """

    name = "ChangeDegree"
    description = "Raise a curve's degree (exact); lowering is an approximation."
    # Step 0: the objects (a set, ). Step 1: the requested degree (a typed number).
    steps = [("Select the objects:", OBJECTS_MULTI), ("Degree:", NUMBER)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True         # a degree change is a document change

    def execute(self, ctx: NarwhalContext):
        # The OBJECTS_MULTI step appends a LIST of names; a single typed name
        # arrives the same way (one-element list). Normalise both (as Flip does).
        arg = self.args[0] if self.args else None
        if isinstance(arg, (list, tuple)):
            names = list(arg)
        elif arg:
            names = [arg]
        else:
            names = []
        if not names:
            return "Nothing selected", True
        # The degree is a NUMBER step -> a float. A degree must be a whole number >= 1;
        # reject a fractional value rather than silently truncating it.
        degree = self.args[1] if len(self.args) > 1 else None
        if degree is None:
            return "ChangeDegree: a degree is required", True
        try:
            target = int(degree)
        except (TypeError, ValueError):
            return "ChangeDegree: the degree must be a whole number", True
        if float(degree) != float(target) or target < 1:
            return "ChangeDegree: the degree must be a whole number of at least 1", True
        # Resolve and classify FIRST (read-only), so `note_change` is called BEFORE any
        # mutation (a run that changes nothing leaves no undo step).
        plan = []
        for name in names:
            geom = ctx.geom_by_name(name)
            plan.append((name, geom,
                         None if geom is None else _degree_refusal(geom)))
        changed = 0
        lines = []
        for name, geom, refusal in plan:
            if geom is None:
                lines.append(f"{name}: no object by that name")
            elif refusal is not None:
                lines.append(f"{name}: {refusal}")
            else:
                current = int(geom.Degree)
                if current > target:
                    # LOWERING is an approximation (Deformable=No is the default, but the
                    # approximation is the same) -- refuse by name, do not approximate.
                    lines.append(f"{name}: lowering the degree from {current} to {target} "
                                 f"is an approximation -- not implemented yet")
                elif current == target:
                    lines.append(f"{name}: already degree {current} (nothing to raise)")
                else:
                    if changed == 0:
                        ctx.note_change(self.name)   # capture "before" before the 1st change
                    geom.IncreaseDegree(target)      # exact, in place,
                    lines.append(f"{name}: degree raised {current} -> {target}")
                    changed += 1
        head = f"{changed} object{'s' if changed != 1 else ''} changed"
        if changed < len(names):
            head += f" -- {len(names) - changed} not changed"
        return "\n".join([head] + lines), True


def _closest_param(curve, loc, n=256):
    """The parameter of the point on `curve` closest to the world location `loc`.

    (flagged): rhino3dm has no `ClosestPoint`/`NearestPoint` on curves, so the
    parameter is found by sampling the domain densely and taking the closest sample --
    the same sampling `bridge.distance_to_curve` and `bridge.sample_curve_offset` rely on
    (pure, no bpy). `loc` is a world (x, y, z) tuple. This is what the implementation means by
    "the parameter is the closest point to the pick": the pick is a location, and the
    knot goes at the parameter where the curve lies nearest that location.
    """
    d = curve.Domain
    best, best_t = math.inf, d.T0
    for i in range(n + 1):
        t = d.T0 + (d.T1 - d.T0) * i / n
        p = curve.PointAt(t)
        dd = (p.X - loc[0]) ** 2 + (p.Y - loc[1]) ** 2 + (p.Z - loc[2]) ** 2
        if dd < best:
            best, best_t = dd, t
    return best_t


class InsertKnotCmd(Command):
    """`InsertKnot` -- the command behavior add one knot.

    (`the command reference InsertKnot`): Steps: "Select the object. Pick a location
    for the knot." Options: `Automatic` (add halfway between existing knots), `Midpoints`
    (place guide markers), `Symmetrical` (both sides of the centre), `Undo`, `Direction`
    / `Toggle` (surfaces only), `SubCrv`. This command is the BASE form -- one curve,
    one picked location -- so the curve gets exactly ONE knot at the parameter closest to
    the pick, as the implementation requires. `Automatic`, `Midpoints` and `Symmetrical` are
    built; `SubCrv` and the surface options are not declared, so they report "not
    implemented yet" rather than being silently ignored (the explicit-refusal policy).

    The mutation is `NurbsCurve.Knots.InsertKnot(t, 1)` (present, mutates in
    place): inserting at a parameter that is not already a knot adds exactly one control
    point and keeps the curve's shape (a circle 9 -> 10 points and a line
    2 -> 3, both with the shape unchanged to ~1e-15). `t` is the parameter of the point
    on the curve CLOSEST to the picked location (`_closest_param`, sampled -- rhino3dm
    has no `ClosestPoint`).

    THE MASTER RULE (as with ): a curve with an analytic master is
    refused by name, because `Knots.InsertKnot` would change only the display proxy and
    leave the OCCT master at the old knot structure -- the exact stale-master failure
    `bridge.transform_geometry` exists to stop (audit gate 5).
    """

    name = "InsertKnot"
    description = "Insert knots into a curve at picked points; Enter when done."
    # MEASURED (research_all_13 note 073): "Select curve or surface for knot insertion"
    # (nothing selected), then a LOOP "Point on curve to add knot. Press Enter when
    # done ( Automatic  Midpoints=No  Symmetrical=No ):"; a pick is projected onto the
    # curve; `Automatic` adds one knot halfway in each interval; `Symmetrical=Yes` adds
    # the pick and its mirror across the centre (pick 1 on a 0-4 line -> knots 1 and 3).
    LOOP_PROMPT = "Point on curve to add knot. Press Enter when done:"
    steps = [("Select curve or surface for knot insertion:", OBJECTS), (LOOP_PROMPT, POINT)]
    options = (
        opts.Option("Automatic", opts.MODE,
                    help="Adds knots halfway between the existing knots."),
        opts.Option("Midpoints", opts.TOGGLE, default=False,
                    help="Places markers between existing knots."),
        opts.Option("Symmetrical", opts.TOGGLE, default=False,
                    help="Adds knots symmetrically about the centre of the curve."),
    )
    # preselect_names=1: select the curve first, then run (the reference CAD application's flow); a typed name
    # still works (the selection is a shortcut, not a replacement).
    preselect_names = 1
    records_undo = True         # a knot insertion is a document change

    def on_option_set(self, option, value, ctx):
        if option.name == "Midpoints" and value and self.step_index == 1:
            return self._mark_midpoints(ctx)
        return super().on_option_set(option, value, ctx)

    def begin(self, ctx):
        out = super().begin(ctx)
        if self.step_index == 1 and self.opt.get("Midpoints"):
            return self._recorded(ctx, lambda: self._mark_midpoints(ctx))
        return out

    def _curve(self, ctx):
        """``(name, curve)`` to insert into, or ``(None, refusal)``."""
        name = self.args[0] if self.args else None
        if not name and ctx.selected_objects():
            name = ctx.selected_objects()[0].name
        if not name:
            return None, "InsertKnot: select a curve to insert a knot into"
        geom = ctx.geom_by_name(name)
        if geom is None:
            return None, f"No object named {name!r}"
        if not isinstance(geom, r3.Curve):
            # InsertKnot works on curves. A surface's knot insertion needs a u/v direction
            # (the `Direction`/`Toggle` options), which this command does not take --
            # report it rather than ship something else (the explicit-refusal policy).
            return None, (f"InsertKnot: {name!r} is not a curve (knot insertion on "
                          "surfaces needs a direction, which this command does not take)")
        # THE MASTER RULE: refuse a master-bearing curve -- InsertKnot would
        # mutate only the display proxy and leave the OCCT master behind (audit gate 5).
        from . import kernel

        if kernel.master_of(geom) is not None:
            return None, (f"InsertKnot: {name!r} has an analytic master; inserting a knot "
                          "into it is not implemented yet -- mutating the proxy alone would "
                          "leave the master behind")
        return name, geom

    def _mark_midpoints(self, ctx):
        """`Midpoints=Yes`: a point object midway along each span between distinct knots.

        The curve is only read (`PointAt` is an exact evaluation), so no knot is
        inserted. OURS (#50): the command finishes here instead of entering the
        knot-point loop.
        """
        name, geom = self._curve(ctx)
        if name is None:
            return geom, True
        knots = sorted({float(k) for k in geom.Knots})
        mids = [(a + b) / 2.0 for a, b in zip(knots, knots[1:]) if b > a]
        ctx.note_change(self.name)
        for t in mids:
            p = geom.PointAt(t)
            ctx.add_geometry(bridge.make_point((p.X, p.Y, p.Z)), "Point")
        return f"InsertKnot: {len(mids)} midpoint markers placed", True

    def _insert(self, ctx, name, geom, params):
        ctx.note_change(self.name)      # capture "before" before the mutation
        for t in params:
            geom.Knots.InsertKnot(t, 1)
        self.args = self.args[:1]
        self.step_index = 1
        shown = ", ".join(f"{t:g}" for t in params)
        return f"{name}: knot{'s' if len(params) != 1 else ''} inserted at {shown}", False

    def on_mode(self, name, ctx):
        if name == "Automatic":
            if self.step_index != 1:
                return "InsertKnot: select the curve first", False
            cname, geom = self._curve(ctx)
            if cname is None:
                return geom, True
            knots = sorted({float(k) for k in geom.Knots})
            mids = [(a + b) / 2.0 for a, b in zip(knots, knots[1:]) if b > a]
            return self._insert(ctx, cname, geom, mids)
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        if (self.step_index == 1 and not (text or "").strip()
                and self.pending_option is None):
            return None, True       # "Press Enter when done"
        before = self.step_index
        out = super()._feed(text, ctx)
        if before == 0 and self.step_index == 1 and self.opt.get("Midpoints"):
            return self._mark_midpoints(ctx)   # the curve was just chosen
        return out

    def execute(self, ctx: NarwhalContext):
        name, geom = self._curve(ctx)
        if name is None:
            return geom, True
        loc = self.args[1] if len(self.args) > 1 else None
        if loc is None:
            return "InsertKnot: pick a location for the knot", True
        t = _closest_param(geom, loc)   # the parameter of the closest point to the pick
        params = [t]
        if self.opt.get("Symmetrical"):
            d = geom.Domain
            mirror = float(d.T0) + float(d.T1) - t   # DERIVED: mirrored in parameter
            if abs(mirror - t) > 1e-12:
                params.append(mirror)
        return self._insert(ctx, name, geom, sorted(params))


class _CrvEndCmdBase(Command):
    """Shared by `CrvStart` / `CrvEnd` -- the command behavior. (`the command reference CrvStart`): Steps: "Select the curves." The CrvEnd
    page is a redirect stub to the same page (no separate help). These are the
    *mark-the-end* point commands: CrvStart places a point object at the start
    of a curve, CrvEnd at its end. The help cross-references the `MarkEnds`
    mode of Divide / the `Mark` action of ShowEnds, both of which place point
    objects at the ends -- same result.

    The endpoints are the `PointAtStart` / `PointAtEnd` properties (present, a `Point3d`, NOT a method). This command only READS those
    coordinates and creates a NEW, independent point object -- it does not
    mutate the curve, so the master rule (/152/153) does not apply: a
    master-bearing curve's endpoints are still readable from its proxy, and the
    point object we add is separate from the master.

    For a CLOSED curve (e.g. a circle) start and end coincide, so CrvStart and
    CrvEnd place the same point -- that is correct, it is where the curve starts
    and ends. Selection-driven (one `OBJECTS_MULTI` step), exactly like Divide /
    Explode: select the curves, run, one point object per curve.
    """

    which = "start"          # "start" or "end" (set by the subclasses)
    name = "CrvStart"        # overridden
    description = "Place a point object at the start of selected curves."  # overridden
    steps = [("Select curves:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True      # a point object added is a document change

    def _endpoint(self, geom):
        return geom.PointAtStart if self.which == "start" else geom.PointAtEnd

    def execute(self, ctx: NarwhalContext):
        # The objects_multi step hands the curve names as a list (typed OR from the
        # selection -- `_feed_objects_multi_step` / `_take_objects_selection` both
        # append `[names]`). Read that; if execute was driven straight (no step yet),
        # fall back to the live selection, as Explode / Divide do.
        if self.args and isinstance(self.args[0], (list, tuple)):
            names = list(self.args[0])
        elif self.args and isinstance(self.args[0], str):
            names = [self.args[0]]
        else:
            names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return f"{self.name}: select one or more curves", True
        curves, non_curves = [], []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is not None and isinstance(geom, r3.Curve):
                curves.append((name, geom))
            else:
                non_curves.append(name)
        if not curves:
            return (f"{self.name}: only curves can be marked -- the selection "
                    "contains no curves"), True

        ctx.note_change(self.name)          # capture "before" before any addition
        made = 0
        for name, geom in curves:
            loc = self._endpoint(geom)      # a Point3d (start or end)
            layer_index = _layer_index_of(ctx, name)
            ctx.add_geometry(bridge.make_point((loc.X, loc.Y, loc.Z)),
                             self.name, layer_index)
            made += 1
        where = "start" if self.which == "start" else "end"
        msg = f"{self.name}: placed {made} point object(s) at the {where} of {len(curves)} curve(s)"
        if non_curves:
            msg += f" ({len(non_curves)} non-curve(s) skipped)"
        return msg, True


class CrvStartCmd(_CrvEndCmdBase):
    """`CrvStart` -- a point object at the start of each selected curve."""
    name = "CrvStart"
    description = "Place a point object at the start of selected curves."
    which = "start"


class CrvEndCmd(_CrvEndCmdBase):
    """`CrvEnd` -- a point object at the end of each selected curve."""
    name = "CrvEnd"
    description = "Place a point object at the end of selected curves."
    which = "end"


class ExtractPtCmd(Command):
    """`ExtractPt` -- the command behavior. a point object per control point.

    (`the command reference ExtractPt`): Steps: "Select curves, surfaces, or polygon
    mesh objects. the reference CAD application creates point objects for every control point in the object."
    Options: `OutputLayer` (Current | Input).

    This command creates a NEW point object per CONTROL POINT of the selection -- it is
    READ-ONLY on the input (it does not mutate it), so the master rule (/152/153)
    does not apply: a master-bearing solid's control points are still readable from its
    proxy, and the added points are separate from the master.

    The control points are the ANALYTIC control net -- rule 0 is respected, we read the
    NURBS control points, never a tessellation:
      * a curve's `Points` (Point4d, dehomogenized by W); a PolylineCurve has no `Points`,
        so it is converted with `ToNurbsCurve` first (gives the same net);
      * a surface's control grid -- a Brep (polysurface) is the sum over its faces, each
        `face.ToNurbsSurface.Points` (CountU x CountV via `GetControlPoint(u, v)`);
        a single surface (e.g. `NurbsSurface`) is its own `Points` grid;
      * a polygon mesh's vertices (`Vertices.ToPoint3dArray`).
    One point object is created per control point; a polysurface's shared-corner control
    points are counted per face, as NURBS stores them (a box: 6 faces x 4 = 24).

    Selection-driven (one `OBJECTS_MULTI` step), exactly like Divide / Explode / CrvEnd.
    `OutputLayer = Input` places the points on each source's layer; `Current` (default) on
    the current layer. An object with no control points (a point object) is reported, never
    silently dropped (the explicit-refusal policy).
    """

    name = "ExtractPt"
    description = ("Create a point object per control point of the selected "
                   "curves, surfaces or meshes.")
    steps = [("Select curves, surfaces, or meshes:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True         # a point object per control point is a document change
    options = (
        opts.Option("OutputLayer", opts.ENUM,
                    default="Current", choices=("Current", "Input"),
                    help=("Current places the points on the current layer; "
                          "Input places them on the same layer as each input.")),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "OutputLayer" and value not in ("Current", "Input"):
            return (f"ExtractPt: OutputLayer must be Current or Input "
                    f"(got {value!r})"), False
        return None

    def _points(self, geom):
        """The ANALYTIC control points of `geom` as (x, y, z) tuples, or None if the
        object has none to extract (reported by name, the explicit-refusal policy)."""
        if isinstance(geom, r3.Curve):
            try:
                pts = list(geom.Points)
            except (AttributeError, TypeError):        # PolylineCurve: no.Points
                pts = list(geom.ToNurbsCurve().Points)
            return [(p.X / p.W, p.Y / p.W, p.Z / p.W) for p in pts]
        if isinstance(geom, r3.Brep):
            out = []
            for face in geom.Faces:
                grid = face.ToNurbsSurface().Points
                for u in range(grid.CountU):
                    for v in range(grid.CountV):
                        p = grid.GetControlPoint(u, v)
                        out.append((p.X / p.W, p.Y / p.W, p.Z / p.W))
            return out or None
        if isinstance(geom, r3.Surface):
            grid = geom.Points
            out = []
            for u in range(grid.CountU):
                for v in range(grid.CountV):
                    p = grid.GetControlPoint(u, v)
                    out.append((p.X / p.W, p.Y / p.W, p.Z / p.W))
            return out or None
        if isinstance(geom, r3.Mesh):
            return [(p.X, p.Y, p.Z) for p in geom.Vertices.ToPoint3dArray()]
        return None

    def execute(self, ctx: NarwhalContext):
        # The objects_multi step hands the names as a list (typed OR from the selection);
        # if execute was driven straight (no step yet), fall back to the live selection.
        if self.args and isinstance(self.args[0], (list, tuple)):
            names = list(self.args[0])
        elif self.args and isinstance(self.args[0], str):
            names = [self.args[0]]
        else:
            names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "ExtractPt: select curves, surfaces, or meshes", True

        # validate before mutating: collect (name, positions); report the rest by name
        items, skipped = [], []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                return f"No object named {name!r}", True
            pts = self._points(geom)
            if pts is None:
                skipped.append(name)
                continue
            items.append((name, pts))
        if not items:
            return ("ExtractPt: nothing to extract -- the selection contains no "
                    "curves, surfaces or meshes (a point object has no control "
                    "points)"), True

        layer_mode = self.opt.get("OutputLayer") or "Current"
        ctx.note_change(self.name)          # capture "before" before any addition
        total = 0
        for name, pts in items:
            layer_index = (_layer_index_of(ctx, name) if layer_mode == "Input"
                           else None)        # None == the current layer
            for pos in pts:
                ctx.add_geometry(bridge.make_point(pos), "ExtractPt", layer_index)
                total += 1
        msg = f"ExtractPt: created {total} point object(s) for {len(items)} object(s)"
        if skipped:
            msg += f" ({len(skipped)} skipped: {', '.join(skipped)})"
        return msg, True


class ExtractControlPolygonCmd(Command):
    """`ExtractControlPolygon` -- the command behavior. (`the command reference ExtractControlPolygon`): Steps: "Select curves or
    surfaces. This creates **polylines** and/or **meshes** that have vertices at
    the original objects' control points." Supported input: curves, polycurves,
    surfaces. Options: `OutputLayer` (Current | Input).

    So the output depends on the input's kind, exactly as the help says:
      * a CURVE (a `NurbsCurve` or a `PolylineCurve`) -> a POLYLINE (an
        `r3.PolylineCurve`) whose vertices are the curve's ANALYTIC control
        points (`bridge.control_point_count` / `control_point_at`, which read
        the NURBS net -- rule 0 -- and handle both the `Points` and the
        `PointCount`/`Point(i)` flavours);
      * a SURFACE (a single surface such as a `NurbsSurface`, or a `Brep` /
        polysurface) -> a MESH with a vertex per control point of its
        `Points` grid -- a `Brep` is the union of its faces' grids, a single
        surface its own grid. The mesh vertices are the control points verbatim
        (the help: "meshes that have vertices at the original objects' control
        points"); we do NOT triangulate, we just lay out the net as vertices.

    The command is READ-ONLY on the input (it builds NEW objects), so the master
    rule (/152/153) does not apply: a master-bearing solid's control points
    are still readable from its proxy, and the polyline/mesh we add is separate
    from the master. An object with no control points (a lone point) is
    REPORTED, never silently dropped (the explicit-refusal policy).

    Selection-driven (one `OBJECTS_MULTI` step), exactly like ExtractPt. The
    typed-name path is the same step answered by a name. `OutputLayer = Input`
    places the results on each source's layer; `Current` (default) on the
    current layer.
    """

    name = "ExtractControlPolygon"
    description = ("Create a polyline through a curve's control points, or a mesh "
                   "with vertices at a surface's control points.")
    steps = [("Select curves or surfaces:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = True         # a polyline / mesh per input is a document change
    options = (
        opts.Option("OutputLayer", opts.ENUM,
                    default="Current", choices=("Current", "Input"),
                    help=("Current places the results on the current layer; "
                          "Input places them on the same layer as each input.")),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "OutputLayer" and value not in ("Current", "Input"):
            return (f"ExtractControlPolygon: OutputLayer must be Current or Input "
                    f"(got {value!r})"), False
        return None

    def _curve_points(self, geom):
        """The curve's ANALYTIC control points as (x, y, z) tuples (NURBS net,
        rule 0). Both the `Points` (NurbsCurve) and the `PointCount`/`Point(i)`
        (PolylineCurve) flavours are handled by `bridge`."""
        n = bridge.control_point_count(geom)
        if n is None or n < 2:
            return None
        return [bridge.control_point_at(geom, i) for i in range(n)]

    def _surface_grid(self, geom):
        """The surface's control grid as (x, y, z) tuples -- a `Brep` is the union
        of its faces' `ToNurbsSurface.Points` grids; a single surface its own
        grid. Returns None when there is no grid to extract."""
        if isinstance(geom, r3.Brep):
            faces = geom.Faces
            if len(faces) == 0:
                return None
            out = []
            for face in faces:
                grid = face.ToNurbsSurface().Points
                for u in range(grid.CountU):
                    for v in range(grid.CountV):
                        p = grid.GetControlPoint(u, v)
                        out.append((p.X / p.W, p.Y / p.W, p.Z / p.W))
            return out or None
        if isinstance(geom, r3.Surface):
            grid = geom.Points
            out = []
            for u in range(grid.CountU):
                for v in range(grid.CountV):
                    p = grid.GetControlPoint(u, v)
                    out.append((p.X / p.W, p.Y / p.W, p.Z / p.W))
            return out or None
        return None

    def execute(self, ctx: NarwhalContext):
        if self.args and isinstance(self.args[0], (list, tuple)):
            names = list(self.args[0])
        elif self.args and isinstance(self.args[0], str):
            names = [self.args[0]]
        else:
            names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "ExtractControlPolygon: select curves or surfaces", True

        # validate before mutating: classify each (curve -> polyline, surface -> mesh);
        # report the rest by name (the explicit-refusal policy).
        results, skipped = [], []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                return f"No object named {name!r}", True
            if isinstance(geom, r3.Curve):
                pts = self._curve_points(geom)
                if pts is None:
                    skipped.append(name)
                    continue
                results.append((name, "curve", pts))
                continue
            if isinstance(geom, r3.Surface) or isinstance(geom, r3.Brep):
                grid = self._surface_grid(geom)
                if grid is None:
                    skipped.append(name)
                    continue
                results.append((name, "surface", grid))
                continue
            skipped.append(name)
        if not results:
            return ("ExtractControlPolygon: nothing to extract -- the selection "
                    "contains no curves or surfaces (a point object has no control "
                    "points)"), True

        layer_mode = self.opt.get("OutputLayer") or "Current"
        ctx.note_change(self.name)          # capture "before" before any addition
        made_polylines = made_meshes = 0
        for name, kind, pts in results:
            layer_index = (_layer_index_of(ctx, name) if layer_mode == "Input"
                           else None)        # None == the current layer
            if kind == "curve":
                pl = r3.PolylineCurve([r3.Point3d(p[0], p[1], p[2]) for p in pts])
                ctx.add_geometry(pl, "Polyline", layer_index)
                made_polylines += 1
            else:
                m = r3.Mesh()
                for p in pts:
                    m.Vertices.Add(float(p[0]), float(p[1]), float(p[2]))
                ctx.add_geometry(m, "Mesh", layer_index)
                made_meshes += 1
        parts = []
        if made_polylines:
            parts.append(f"{made_polylines} polyline(s) from curve(s)")
        if made_meshes:
            parts.append(f"{made_meshes} mesh(es) from surface(s)")
        msg = f"ExtractControlPolygon: created {', '.join(parts)}"
        if skipped:
            msg += f" ({len(skipped)} skipped: {', '.join(skipped)})"
        return msg, True


def _attribute_object(ctx, name):
    """Return the live document object named *name*, not its display proxy."""
    if not name or getattr(ctx, "model", None) is None:
        return None
    wanted = str(name).casefold()
    for fobj in ctx.model.file.Objects:
        if (fobj.Attributes.Name or "").casefold() == wanted:
            return fobj
    return None


class SetUserTextCmd(Command):
    """`SetUserText` -- the command behavior.

    one selected object, then a key and value. A key beginning with a
    period is hidden from Attribute User Text properties but remains command-
    queryable. The documented value ``""`` removes the key. User strings are
    written to the live ObjectAttributes because those are the 3DM authority;
    no Python-only cache is involved.
    """

    name = "SetUserText"
    description = "Attach a key/value user string to one selected object."
    # MEASURED (research_preview_14 R7 SetUserText): "Text key ( AttachTo=Attributes ):",
    # then "Text ( AttachTo=Attributes ):"; silent on success. Only Attributes is built.
    steps = [("Select an object:", OBJECTS),
             ("Text key:", TEXT),
             ("Text:", TEXT)]
    preselect_names = 1
    options = (
        opts.Option("AttachTo", opts.ENUM, choices=("Attributes", "Object"),
                    default="Attributes",
                    help="Attributes: the object's attribute user text."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "AttachTo" and value != "Attributes":
            self.opt["AttachTo"] = "Attributes"
            return "SetUserText: AttachTo=Object is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def execute(self, ctx: NarwhalContext):
        name = self.args[0] if self.args else None
        key = self.args[1] if len(self.args) > 1 else ""
        value = self.args[2] if len(self.args) > 2 else ""
        obj = _attribute_object(ctx, name)
        if obj is None:
            return f"SetUserText: no object named {name!r}", True
        if not key:
            return "SetUserText: a key is required", True
        attrs = obj.Attributes
        old = attrs.GetUserString(key)
        removing = value == '""'
        new = "" if removing else value
        # rhino3dm's ObjectAttributes.SetUserString(key, "") removes the row.
        if (old or "") == new and not (removing and old):
            return (f"SetUserText: {key!r} is unchanged", True)
        ctx.note_change(self.name)
        attrs.SetUserString(key, new)
        if removing:
            return f"SetUserText: removed {key!r} from {name}", True
        return f"SetUserText: set {key!r} on {name}", True


class SetDocumentUserTextCmd(Command):
    """`SetDocumentUserText` -- the command behavior.

    there is no object selection; the command asks for a key and then
    its value. A period-prefixed key is hidden from the Document User Text panel,
    but remains a real File3dm string. The literal value ``""`` removes an
    existing key. The File3dm.Strings compatibility view routes writes through the
    model's metadata owner; no independent Python table is maintained.
    """

    name = "SetDocumentUserText"
    description = "Attach or remove a key/value string on the current document."
    # MEASURED (research21 tooling probe): "Document text key:", then "Document text:".
    steps = [("Document text key:", TEXT), ("Document text:", TEXT)]

    def execute(self, ctx: NarwhalContext):
        key = self.args[0] if self.args else ""
        value = self.args[1] if len(self.args) > 1 else ""
        if not key:
            return "SetDocumentUserText: a key is required", True
        removing = value == '""'
        changed = ctx.model.set_document_user_text(
            key, None if removing else value, before_change=lambda: ctx.note_change(self.name))
        if not changed:
            return f"SetDocumentUserText: {key!r} is unchanged", True
        return f"SetDocumentUserText: {'removed' if removing else 'set'} {key!r}", True


class GetUserTextCmd(Command):
    """`GetUserText` -- the command behavior

    one selected object; Enter lists every key/value, while a typed
    key returns that value. Listing deliberately includes period-prefixed keys:
    hidden means hidden from Properties, not inaccessible to this command.
    """

    name = "GetUserText"
    description = "Read user text from one selected object."
    # MEASURED (research_preview_14 R8 GetUserText): "Text key <All keys>:".
    steps = [("Select an object:", OBJECTS), ("Text key <All keys>:", TEXT)]
    preselect_names = 1
    records_undo = False

    def _feed(self, text, ctx):
        # The key prompt is optional by help: bare Enter is a query/list, not a
        # cancellation. Selection itself still follows the normal OBJECTS path.
        if (self.step_index == 1 and not text and self.args):
            self.args.append("")
            self.step_index += 1
            return self.execute(ctx)
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        name = self.args[0] if self.args else None
        key = self.args[1] if len(self.args) > 1 else ""
        obj = _attribute_object(ctx, name)
        if obj is None:
            return f"GetUserText: no object named {name!r}", True
        attrs = obj.Attributes
        if key:
            value = attrs.GetUserString(key)
            return f"GetUserText: {key} = {value}", True
        rows = attrs.GetUserStrings2() or []
        if not rows:
            return f"GetUserText: {name} has no user text", True
        return "\n".join(f"{key} = {value}" for key, value in rows), True


class GetDocumentUserTextCmd(Command):
    """`GetDocumentUserText` -- the command behavior.

    from the fixed the reference CAD application help: the command retrieves the key/value
    strings written by ``SetDocumentUserText``.  The native ``File3dm.Strings``
    compatibility view follows the metadata owner. Enter at the key prompt lists every row; a typed
    key queries exactly that row.  Period-prefixed keys are hidden from the
    Document User Text panel only, so they remain visible to both command forms.
    """

    name = "GetDocumentUserText"
    description = "Read user text from the current document."
    # MEASURED (research_preview_14 R8 GetDocumentUserText): "Document text key <All
    # document keys>:".
    steps = [("Document text key <All document keys>:", TEXT)]
    records_undo = False

    def _feed(self, text, ctx):
        # Unlike an ordinary text prompt, Enter means list all rows.  This is
        # deliberately local rather than relaxing Command's cancellation rule.
        if self.step_index == 0 and not text:
            self.args.append("")
            self.step_index += 1
            return self.execute(ctx)
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        key = self.args[0] if self.args else ""
        if key:
            value = ctx.model.document_user_text(key)
            if value is None:
                return f"GetDocumentUserText: {key} not found", True
            return f"GetDocumentUserText: {key} = {value}", True
        rows = ctx.model.document_user_text()
        if not rows:
            return "GetDocumentUserText: document has no user text", True
        return "\n".join(f"{row_key} = {value}" for row_key, value in rows), True


class WhatCmd(Command):
    """`What` -- the command behavior. report the object's details.

    (the command behavior): "Select objects. Information about the object and
    its layer will display." -- one OBJECTS_MULTI step, any number of objects, and
    the report is TEXT (the same text the Properties panel's Details button shows,
    the command behavior). Options: none -- the help documents no command-line
    options for `What`, so none are declared (the faithful answer, as with `Point`).

    The report is built by `bridge.object_details`  -- the SAME rows the
    Properties panel  shows, read from the EXACT geometry (master or recipe),
    never the display proxy. The command RETURNS the report (the framework's
    `state.feed_line` logs it to the command line, `state.py` ~330), so the test
    checks the returned text the way `Length`/`Angle` do -- the "log" the implementation
    means is the command-line text window.
    """

    name = "What"
    description = "Show the type, geometry and measurements of the selected object(s)."
    # MEASURED (research_all_13 note 185): "Select objects for displaying detailed object
    # properties ( ShowUnits=No ):". ShowUnits is not declared: it was never toggled in
    # the research, so what it changes is unmeasured.
    steps = [("Select objects for displaying detailed object properties:", OBJECTS_MULTI)]
    # the reference CAD application selects first, then runs the command; the selection satisfies the step
    # and the command runs on the naming Enter (like `Delete`). A typed name still
    # works -- the step is answered by the selection OR a name.
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = False        # read-only: reports, does not change the document

    def execute(self, ctx: NarwhalContext):
        # The OBJECTS_MULTI step appends a LIST of names; a single typed
        # name arrives the same way (one-element list). Normalise both.
        arg = self.args[0] if self.args else None
        if isinstance(arg, (list, tuple)):
            names = list(arg)
        elif arg:
            names = [arg]
        else:
            names = []
        if not names:
            return "Nothing selected", True
        blocks = []
        for name in names:
            geom = ctx.geom_by_name(name)
            if geom is None:
                blocks.append(f"{name}: no object by that name")
                continue
            try:
                rows = bridge.object_details(geom)
            except Exception as e:  # noqa: BLE001
                # A detail row that cannot be computed must not drop the whole
                # report -- say which object, and keep going (the explicit-refusal policy: never
                # pretend the detail is there).
                blocks.append(f"{name}: details unavailable ({e})")
                continue
            block = [f"{name}:"]
            block += [f"  {label}: {value}" for label, value in rows]
            blocks.append("\n".join(block))
        return "\n".join(blocks), True


class _CurvatureRadiusCmd(Command):
    """`Radius` / `Diameter` -- the command behavior (Radius redirects there).

    steps: "Pick a point on the curve. The diameter and radius report to the
    command-line. If the diameter and radius return 'infinite', the curve is linear...".
    The prompt in the command UI ends "Press Enter when done": each pick reports and the
    command waits for the next, Enter finishes.

    An arc/circle reads its OWN radius (`TryGetArc`; `IsCircle` is False for an arc); a
    free curve the analytic curvature at the picked point. also reads a CYLINDER /
    SPHERE / CONE face of a solid -- the face of its EXACT shape (master, or rebuilt from
    the recipe), never the display triangles.

    Options: `SelectCurve` (built: asks for the curve, then the picks are limited to it),
    `MarkRadius`/`MarkDiameter` (built: a point object at the evaluated point and the
    curvature circle through it), `Units` (built: Area's unit choices; converts only the
    reported numbers), `SubCrv` (declared, not implemented).

    Which object a pick means (as we have no pick ray at a POINT step): the
    preselected curve/solid if there is one, else the nearest visible curve or solid --
    refused when the pick is not ON it (farther than `PICK_FRACTION` of its size).
    """

    primary = "Radius"
    preselect_names = 0
    PICK_FRACTION = 0.05

    def __init__(self):
        super().__init__()
        self._limit = None          # SelectCurve: the one object picks are limited to
        self._reported = 0

    def on_mode(self, name, ctx):
        if name == "SelectCurve":
            if self.args or self.steps[0][1] == OBJECTS:
                return f"{self.name}: SelectCurve is chosen before the first pick", False
            self.steps = [("Select curve:", OBJECTS)] + list(self.steps)
            self.step_index = 0
            return None, False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "Units" and value == "Liter":
            self.opt["Units"] = "ModelUnits"
            return f"{self.name}: Liter is a volume unit, not a length unit", False
        return super().on_option_set(option, value, ctx)

    def _feed(self, text, ctx):
        # "Press Enter when done": after at least one report, Enter FINISHES (it is not a
        # cancel -- the measurements stand).
        if (not text.strip() and self.pending_option is None and self._reported
                and self.steps[self.step_index][1] == POINT):
            return None, True
        return super()._feed(text, ctx)

    def _candidates(self, ctx):
        if self._limit:
            return [self._limit]
        try:
            sel = [o.name for o in (ctx.selected_objects() or [])]
        except Exception:  # noqa: BLE001
            sel = []
        if sel:
            return sel
        hidden = getattr(getattr(r3, "ObjectMode", None), "Hidden", None)
        return [o.Attributes.Name for o in ctx.model.file.Objects
                if o.Attributes.Name and (hidden is None or o.Attributes.Mode != hidden)]

    def _measure_at(self, geom, point, tol=None):
        """``(distance, radius, center, normal, foot, kind)`` for one object, or None."""
        from . import analysis, nearest

        if isinstance(geom, r3.Curve):
            cp = nearest.closest_point_on_curve(geom, point, tolerance=tol)
            r, c = analysis.curvature_radius(geom, cp.t)
            normal = None             # of the curvature circle's PLANE: radial x tangent
            if c is not None:
                tan = geom.TangentAt(cp.t)
                x = tuple(f - q for f, q in zip(cp.point, c))
                normal = analysis._cross(x, (tan.X, tan.Y, tan.Z))
            return cp.distance, r, c, normal, tuple(cp.point), "curve"
        shape = analysis.occt_shape_of(geom)
        if shape is None:
            return None
        _k, ob = analysis._kernel_mods()
        info = ob.nearest_face_radius(shape, point)
        if info is None:
            return None
        return (info["distance"], info["radius"], info.get("center"), info.get("axis"),
                info["foot"], info["kind"])

    def execute(self, ctx: NarwhalContext):
        import math as _m

        from . import analysis

        if self.steps[0][1] == OBJECTS and self._limit is None:
            self._limit = self.args[0]
        point = self.args[-1]
        best = None
        for nm in self._candidates(ctx):
            geom = ctx.geom_by_name(nm)
            if geom is None:
                continue
            try:
                m = self._measure_at(geom, point, _document_tolerance(ctx))
            except Exception:  # noqa: BLE001
                m = None
            if m is None:
                continue
            lo, hi = analysis.bounding_box(geom)
            size = _m.dist((lo.X, lo.Y, lo.Z), (hi.X, hi.Y, hi.Z))
            # nearest wins; on a TIE a curve beats a surface -- a curve drawn on a face
            # (a sketch on a box) is what a pick on it means, as the reference CAD application's picker
            # prefers curves (the tie tolerance is the document tolerance)
            tol = self._autoclose_tolerance(ctx)
            if (best is None or m[0] < best[1][0] - tol
                    or (m[0] <= best[1][0] + tol and m[5] == "curve"
                        and best[1][5] != "curve")):
                best = (nm, m, size)
        # ready for the next pick either way ("Press Enter when done")
        keep = [self._limit] if self._limit else []
        self.args = keep
        self.step_index = len(keep)
        if best is None:
            return f"{self.name}: no curve or surface to measure there", False
        nm, (dist, r, center, normal, foot, kind), size = best
        if dist > max(self.PICK_FRACTION * size, 1e-9):
            return (f"{self.name}: that point is not on a curve or surface "
                    f"(nearest is {nm}, {_num(dist)} away)"), False
        self._reported += 1
        if r is None:
            return (f"{self.name}: {nm}'s {kind.lower()} surface has no single radius "
                    f"there"), False
        if _m.isinf(r):
            return "Radius of curvature is infinite.", False
        msg = self._report(r, ctx)
        if self.opt.get("Mark" + self.primary):
            msg += self._mark(ctx, nm, foot, r, center, normal)
        return msg, False

    def _report(self, r, ctx=None):
        # OURS (#62): with Units other than ModelUnits the numbers are converted and the
        # unit is named; ModelUnits keeps the report exactly as before. The measured
        # radius itself is never changed.
        unit = ""
        if self.opt.get("Units", "ModelUnits") != "ModelUnits" and ctx is not None:
            factor, name = _report_unit(ctx, self.opt["Units"])
            r, unit = r * factor, f" {name}"
        rad, dia = f"Radius = {_num(r)}{unit}", f"Diameter = {_num(2.0 * r)}{unit}"
        # "The diameter and radius report": both, the command's own first (order)
        return f"{rad}    {dia}" if self.primary == "Radius" else f"{dia}    {rad}"

    def _mark(self, ctx, nm, foot, r, center, normal):
        """MarkRadius: a point at the evaluated point + the curvature circle through it."""
        import types as _t

        layer = _layer_index_of(ctx, nm)
        ctx.add_geometry(bridge.make_point(foot), self.name, layer)
        if center is None or normal is None:
            return " (point marked; no single curvature circle here)"
        x = tuple(f - c for f, c in zip(foot, center))
        lx = sum(a * a for a in x) ** 0.5
        n = normal
        y = (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2],
             n[0] * x[1] - n[1] * x[0])
        ly = sum(a * a for a in y) ** 0.5
        if lx <= 0 or ly <= 0:
            return " (point marked)"
        frame = _t.SimpleNamespace(xaxis=tuple(a / lx for a in x),
                                   yaxis=tuple(a / ly for a in y))
        ctx.add_geometry(bridge.make_circle(center, r, frame), self.name, layer)
        return " (marked)"


class RadiusCmd(_CurvatureRadiusCmd):
    name = "Radius"
    description = "Report the radius of a curve (or a round surface) at a picked point."
    primary = "Radius"
    steps = [("Select point on curve for radius measurement. Press Enter when done:",
              POINT)]
    options = (
        opts.Option("SelectCurve", opts.MODE,
                    help="Limit the selection to a specified curve."),
        opts.Option("MarkRadius", opts.TOGGLE, default=False,
                    help="Places a point object at the evaluated point on a curve and "
                         "creates a curvature circle through that point."),
        opts.Option("Units", opts.ENUM, choices=AreaCmd.UNIT_CHOICES, default="ModelUnits",
                    help="Set the units for the length display."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )

    def begin(self, ctx):
        # MEASURED (research_all_13 note 120): a PRESELECTED circle of radius 2 runs at
        # once, no point prompt, printing "Diameter = 4.000 millimeters" then
        # "Radius = 2.000 millimeters". Only a single circle was measured; any other
        # preselection keeps the point prompt. Digits follow `Length`'s formatting.
        try:
            picked = [o.name for o in (ctx.selected_objects() or [])]
        except Exception:  # noqa: BLE001
            picked = []
        if len(picked) == 1:
            geom = ctx.geom_by_name(picked[0])
            circle = None
            if isinstance(geom, r3.Curve) and geom.IsClosed:
                try:
                    circle = geom.TryGetCircle(_document_tolerance(ctx) or 1e-6)
                except Exception:  # noqa: BLE001
                    circle = None
            if circle is not None:
                try:
                    unit = " " + ctx.model.unit_system().lower()
                except Exception:  # noqa: BLE001
                    unit = ""
                r = float(circle.Radius)
                return (f"Diameter = {_num(2.0 * r)}{unit}\n"
                        f"Radius = {_num(r)}{unit}"), True
        return super().begin(ctx)


class DiameterCmd(_CurvatureRadiusCmd):
    name = "Diameter"
    description = "Report the diameter of a curve (or a round surface) at a picked point."
    primary = "Diameter"
    steps = [("Select point on curve for diameter measurement. Press Enter when done:",
              POINT)]
    options = (
        opts.Option("SelectCurve", opts.MODE,
                    help="Limit the selection to a specified curve."),
        opts.Option("MarkDiameter", opts.TOGGLE, default=False,
                    help="Places a point object at the evaluated point on a curve and "
                         "creates a curvature circle through that point."),
        opts.Option("Units", opts.ENUM, choices=AreaCmd.UNIT_CHOICES, default="ModelUnits",
                    help="Set the units for the length display."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )


class _CentroidCmd(Command):
    """Shared body of `AreaCentroid` / `VolumeCentroid`: measure every selected object,
    print the reference CAD application's line (single, or `Cumulative... for %i...`), place a point object at
    the centroid ("A point object will be placed at the centroid coordinates" --
    the command behavior, )."""

    preselect_names = 1
    cancel_on_empty_set = True
    solid = False

    def _part(self, geom):
        """``(mass, mass_err, centroid, centroid_err)`` or a refusal string."""
        raise NotImplementedError

    def execute(self, ctx: NarwhalContext):
        names = self.args[0] if self.args else []
        parts, skipped, curves = [], [], 0
        for nm in names:
            geom = ctx.geom_by_name(nm)
            res = self._part(geom) if geom is not None else f"no object named {nm!r}"
            if isinstance(res, str):
                skipped.append(f"{nm}: {res}")
                continue
            parts.append(res)
            curves += isinstance(geom, r3.Curve)
        if not parts:
            if not self.solid and all(ctx.geom_by_name(nm) is not None for nm in names):
                # MEASURED (research_all_13 #005): a selection with nothing measurable (a
                # point) is discarded and the object prompt is asked again.
                self.args, self.step_index = [], 0
                selector = getattr(ctx, "select_by_names", None)
                if callable(selector):
                    selector([])
                return None, False
            return f"{self.name}: " + "; ".join(skipped or ["nothing to measure"]), True
        from . import analysis

        label = "Volume Centroid" if self.solid else "Area Centroid"
        if len(parts) == 1:
            m, m_err, c, c_err = parts[0]
            msg = _centroid_line(label, c, c_err)
        else:
            m, m_err, c, c_err = analysis.combine_centroids(parts)
            msg = _centroid_line("Cumulative " + label, c, c_err,
                                 self._count_suffix(len(parts) - curves, curves))
        layer = _layer_index_of(ctx, names[0])
        ctx.add_geometry(bridge.make_point(c), self.name, layer)
        if skipped:
            msg += "  (skipped " + "; ".join(skipped) + ")"
        return msg, True


class AreaCentroidCmd(_CentroidCmd):
    name = "AreaCentroid"
    description = "Place a point at the area centroid of surfaces, polysurfaces or closed planar curves."
    steps = [("Select surfaces, polysurfaces, meshes or closed planar curves for area "
              "centroid calculation:", OBJECTS_MULTI)]
    solid = False

    @staticmethod
    def _count_suffix(objects, curves):
        """The six tails of `Cumulative Area Centroid =...`."""
        def n(k, one, many):
            return one if k == 1 else f"{k} {many}"
        if curves and objects:
            return (f" for {n(objects, '1 object', 'objects')} and "
                    f"{n(curves, '1 planar curve', 'planar curves')}")
        if curves:
            return f" for {curves} planar curves"
        return f" for {objects} objects"

    def _part(self, geom):
        from . import analysis

        if isinstance(geom, r3.Curve):
            res = analysis.planar_curve_area_centroid(geom)
            return res if res is not None else "not a closed planar curve"
        if isinstance(geom, r3.Mesh):
            return "meshes are not supported yet"
        if not hasattr(geom, "Faces"):
            return "not a surface, polysurface or closed planar curve"
        res = analysis.exact_centroid(geom, solid=False)
        if res is not None:
            return res
        if analysis.is_proxy(geom):
            return "its exact geometry is unavailable (the display mesh is not the shape)"
        return analysis.grid_centroid(geom, solid=False) or "zero area"


class VolumeCentroidCmd(_CentroidCmd):
    name = "VolumeCentroid"
    description = "Place a point at the volume centroid of closed solids."
    steps = [("Select solids for volume centroid calculation:", OBJECTS_MULTI)]
    solid = True

    @staticmethod
    def _count_suffix(objects, curves):
        return f" for {objects} solids"

    def _part(self, geom):
        from . import analysis

        if not isinstance(geom, r3.Brep) and not hasattr(geom, "Faces"):
            return "not a solid"
        if not analysis.is_closed_solid(geom):
            return "not a closed solid"
        res = analysis.exact_centroid(geom, solid=True)
        if res is not None:
            return res
        if analysis.is_proxy(geom):
            return "its exact geometry is unavailable (the display mesh is not the shape)"
        return analysis.grid_centroid(geom, solid=True) or "zero volume"


class EvaluatePtCmd(Command):
    """`EvaluatePt` -- the command behavior.

    "Pick a location in a viewport. The Cartesian coordinates of the location,
    in both world and construction plane coordinates are displayed... in x,y,z format."
    Prompt and line from the command UI: "Point to evaluate. Press Enter when done" and
    "Point in world coordinates = %s    CPlane coordinates = %s". Each pick reports;
    Enter finishes. `Label` (an annotation dot or leader) is declared, not implemented --
    there are no annotation objects yet.
    """

    name = "EvaluatePt"
    description = "Report a picked location in world and CPlane coordinates."
    steps = [("Point to evaluate. Press Enter when done:", POINT)]
    options = (
        opts.Option("Label", opts.MODE,
                    help="Specifies whether a leader or dot label will be added to the "
                         "model."),
    )

    def __init__(self):
        super().__init__()
        self._reported = 0

    def _feed(self, text, ctx):
        if not text.strip() and self.pending_option is None and self._reported:
            return None, True
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        p = self.args[-1]
        local = self._active_cplane(ctx).from_world(p)
        self.args = []
        self.step_index = 0
        self._reported += 1
        return (f"Point in world coordinates = {_xyz_text(p)}    "
                f"CPlane coordinates = {_xyz_text(local)}"), False


# ---------------------------------------------------------------------------
# the curve tools whose kernels already existed -- Fillet, Chamfer, Split,
# Trim, Intersect
# ---------------------------------------------------------------------------
#
# Prompts are the reference CAD application's in the command UI: "Select first curve to fillet",
# "Select second curve to fillet", "Select first curve to chamfer", "First chamfer
# distance", "Second chamfer distance", "Select objects to split. Press Enter when
# done", "Select cutting objects. Press Enter when done", "Select object to trim. Press
# Enter when done", "Select objects to intersect".
#
# SURFACES ARE REFUSED BY NAME. The only surface trimmer we have,
# `trim.trim_surface_mesh`, is mesh-wiring it would turn a NURBS surface into
# triangles, which is exactly what the geometry integrity rule forbids (audit gate 1).

_SURFACE_REFUSAL = ("{cmd}: {what} POLYSURFACES and solids is not implemented yet -- single "
                    "surfaces, planar or curved, are exact, but the only polysurface "
                    "{verb} available here is mesh-derived and would facet it")


def _planar_surface_under_pick(ctx, point, pick_fraction=0.05):
    """``(name, geom)`` of the SURFACE (one face, planar or curved -- ) a pick lies on,
    or ``(None, reason)``. as `_curve_under_pick`: on the face to within
    `pick_fraction` of its size."""
    import math as _m

    from . import analysis

    best = None
    for nm in _visible_names(ctx):
        geom = ctx.geom_by_name(nm)
        if isinstance(geom, r3.Curve) or not bridge.is_single_surface(geom):
            continue
        d = bridge.surface_distance(geom, point)
        lo, hi = analysis.bounding_box(geom)
        size = _m.dist((lo.X, lo.Y, lo.Z), (hi.X, hi.Y, hi.Z))
        if d <= max(pick_fraction * size, 1e-9) and (best is None or d < best[0]):
            best = (d, nm, geom)
    if best is None:
        return None, "no surface there"
    return best[1], best[2]


def _replace_curve(ctx, name, new_curves, base="Curve", keep_selection=False):
    """Remove document object `name` and add `new_curves` on its layer. Returns the new
    display names. One undo record holds the whole change (the caller's command).

    With ``keep_selection``, replacement pieces inherit the original object's selected
    state so repeated editing can continue on the new objects.
    """
    ctx.note_change(base)
    layer_index = _layer_index_of(ctx, name)
    source = next((fobj for fobj in ctx.model.file.Objects
                   if (fobj.Attributes.Name or "").lower() == (name or "").lower()), None)
    source_geom = source.Geometry if source is not None else None
    picture_frame = bridge.picture_frame(source_geom) if source_geom is not None else None
    if picture_frame is None and source is not None:
        derive_frame = getattr(ctx, "_texture_frame", None)
        picture_frame = derive_frame(source_geom, source) if callable(derive_frame) else None
    picture_text = None
    if picture_frame is not None:
        from . import picture

        picture_text = picture.frame_to_json(picture_frame)
    oid = ctx.model.object_id_by_name(name)
    if oid is not None:
        ctx.model.remove_object(oid)
    ob = ctx.object_by_name(name)
    was_selected = False
    if ob is not None:
        try:
            was_selected = bool(ob.select_get())
        except Exception:  # noqa: BLE001
            was_selected = False
        _remove_object_from_scene(ob)
    out = []
    for crv in new_curves:
        if picture_text is not None and isinstance(crv, r3.Brep):
            crv.SetUserString("Narwhal3D:Picture", picture_text)
        _n, display = ctx.add_geometry(crv, base, layer_index)
        out.append(display)
        if keep_selection and was_selected:
            nob = ctx.object_by_name(display)
            if nob is not None:
                try:
                    nob.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
    return out


def _visible_names(ctx):
    hidden = getattr(getattr(r3, "ObjectMode", None), "Hidden", None)
    return [o.Attributes.Name for o in ctx.model.file.Objects
            if o.Attributes.Name and (hidden is None or o.Attributes.Mode != hidden)]


def _curve_under_pick(ctx, point, names=None, pick_fraction=0.05):
    """``(name, curve)`` of the curve a picked `point` lies on, or ``(None, reason)``.

    as `Radius`: a POINT step carries no pick ray, so the nearest visible curve is
    taken, and refused when the pick is farther from it than `pick_fraction` of its size.
    """
    import math as _m

    from . import analysis, nearest

    best = None
    for nm in (names if names is not None else _visible_names(ctx)):
        geom = ctx.geom_by_name(nm)
        if not isinstance(geom, r3.Curve):
            continue
        try:
            d = nearest.closest_point_on_curve(
                geom, point, tolerance=_document_tolerance(ctx)).distance
        except Exception:  # noqa: BLE001
            continue
        if best is None or d < best[0]:
            lo, hi = analysis.bounding_box(geom)
            best = (d, nm, geom, _m.dist((lo.X, lo.Y, lo.Z), (hi.X, hi.Y, hi.Z)))
    if best is None:
        return None, "there is no curve to pick"
    d, nm, geom, size = best
    if d > max(pick_fraction * size, 1e-9):
        return None, f"that point is not on a curve (nearest is {nm}, {_num(d)} away)"
    return nm, geom


class _TwoCurveCmd(Command):
    """Shared by `Fillet` / `Chamfer`: two `OBJECTS` steps, answered by two DIFFERENT
    curves.

    NOT filled from a preselection at start (`preselect_names = 0`): the reference CAD application shows these
    commands' options -- `Radius=1`, `Trim=Yes` -- at the first curve prompt, and running
    straight off a selection would take the radius away before it could be typed. A
    selection still answers each prompt on Enter; the base `OBJECTS` confirm takes
    `selected[0]` every time, which would hand the SAME curve to both steps, so a confirm
    here takes the first selected curve not already chosen."""

    preselect_names = 0

    def _feed_objects_step(self, text, ctx):
        if not text:
            try:
                sel = [o.name for o in (ctx.selected_objects() or [])]
            except Exception:  # noqa: BLE001
                sel = []
            chosen = [a for a in self.args if isinstance(a, str)]
            fresh = [n for n in sel if n not in chosen]
            if not fresh:
                return ("Select a different curve" if sel else "Nothing selected"), False
            text = fresh[0]
        return super()._feed_objects_step(text, ctx)

    def _curves(self, ctx):
        """``((name_a, a), (name_b, b))`` or a refusal string."""
        names = [a for a in self.args if isinstance(a, str)]
        if len(names) < 2:
            return f"{self.name}: select two curves"
        if names[0].lower() == names[1].lower():
            return f"{self.name}: select two DIFFERENT curves"
        out = []
        for nm in names[:2]:
            geom = ctx.geom_by_name(nm)
            if geom is None:
                return f"No object named {nm!r}"
            if not isinstance(geom, r3.Curve):
                return f"{self.name}: {nm} is not a curve"
            out.append((nm, geom))
        return tuple(out)

    def _commit(self, ctx, pair, result, what):
        """Apply a `fillet.CornerResult` per the Trim / Join options."""
        (na, _a), (nb, _b) = pair
        trim, join = bool(self.opt.get("Trim", True)), bool(self.opt.get("Join", False))
        if join:
            pieces = [result.trimmed_a, result.curve, result.trimmed_b] if trim \
                else [result.curve]
            joined = bridge.join_curves(list(enumerate(pieces)), self._autoclose_tolerance(ctx))
            if trim and len(joined) == 1 and len(joined[0][0]) == 3:
                _replace_curve(ctx, nb, [])
                (new,) = _replace_curve(ctx, na, [joined[0][1]])
                return f"{self.name}: {what} and joined -> {new}"
            if trim:
                return (f"{self.name}: the {what.split()[0].lower()} did not join to the "
                        f"trimmed curves (nothing changed)")
            # Join with Trim=No: the reference CAD application joins the arc to UNtrimmed curves only where they
            # meet it -- they generally do not, so this is reported, not faked
            return (f"{self.name}: Join needs Trim=Yes (an untrimmed curve does not end at "
                    f"the {what.split()[0].lower()})")
        layer = _layer_index_of(ctx, na)
        if trim:
            _replace_curve(ctx, na, [result.trimmed_a])
            _replace_curve(ctx, nb, [result.trimmed_b])
        _n, new = ctx.add_geometry(result.curve, self.name, layer)
        return f"{self.name}: {what} -> {new}" + (" (inputs trimmed)" if trim else "")


class FilletCmd(_TwoCurveCmd):
    """`Fillet` (curves) -- the command behavior.

    steps: "Select the first curve near the end. Select the second curve near the
    end." Options built: `Radius`, `Join`, `Trim`. Declared, not implemented:
    `ExtendArcsBy`, `ExtendOtherCurvesBy` (the kernel does not extend a curve to meet the
    arc -- a fillet that would need it is refused by the kernel, not faked), `Dynamic`.

    The kernel is `fillet.fillet_curves`: exact for lines and arcs, Newton on the exact
    distance for free-form curves; the arc is a rational degree-2 NURBS, never a polyline.
    with no pick location, WHICH corner is rounded is the kernel's rule (the arms
    run away from the intersection; the smallest-sweep arc wins), not "near the end".

     WHERE a joined curve is picked says which SEGMENT is meant. The viewport
    records it in `pick_points[step]`, and a typed name has none.
    * two picks on the SAME polyline/polycurve fillet the corner between the picked
      segments, and the result stays ONE joined curve (it replaces the input);
    * picks on two different joined curves use the picked segment of each, and each
      curve is rebuilt around its trimmed segment. A fillet that would cut a curve in two
      (an interior segment) is refused, not done;
    * no pick points: the behaviour above, unchanged.
    """

    name = "Fillet"
    description = "Round the corner between two curves with a tangent arc."
    steps = [("Select first curve to fillet:", OBJECTS),
             ("Select second curve to fillet:", OBJECTS)]
    options = (
        opts.Option("Radius", opts.VALUE, default=1.0,
                    help="Changes the fillet radius."),
        opts.Option("Join", opts.TOGGLE, default=False,
                    help="Joins the fillet arc to the input curves."),
        opts.Option("Trim", opts.TOGGLE, default=True,
                    help="Trims the input curves to the fillet arc."),
        # MEASURED (research_preview_14 R5 Fillet): "ExtendArcsBy=Arc  ExtendOtherCurvesBy=Line
        # Dynamic=No" -- the values are shown; only the defaults are built.
        opts.Option("ExtendArcsBy", opts.ENUM, choices=("Arc", "Line"), default="Arc",
                    help="How an arc is extended to meet the fillet arc."),
        opts.Option("ExtendOtherCurvesBy", opts.ENUM, choices=("Line", "Arc", "Smooth"),
                    default="Line",
                    help="How a curve that is not a line or arc is extended."),
        opts.Option("Dynamic", opts.TOGGLE, default=False,
                    help="Interactive preview while picking or typing the radius."),
    )
    #: MEASURED (R5 Fillet item 5): a typed Radius stays for the next fillet.
    _sticky_radius = [1.0]

    def __init__(self):
        super().__init__()
        self.opt["Radius"] = FilletCmd._sticky_radius[0]

    def on_option_set(self, option, value, ctx):
        if option.name == "Radius":
            try:
                r = float(value)
            except (TypeError, ValueError):
                r = -1.0
            if not r > 0.0:
                self.opt["Radius"] = FilletCmd._sticky_radius[0]
                return "Fillet: the radius must be positive", False
            FilletCmd._sticky_radius[0] = r
            return None
        if option.name in ("ExtendArcsBy", "ExtendOtherCurvesBy", "Dynamic") \
                and value != option.default:
            self.opt[option.name] = option.default
            return f"Fillet: {option.name}={value} is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    #: the reference CAD application's own refusal names both causes in the command UI
    UNABLE = ("Unable to fillet the curves. The fillet radius may be too big or the curves "
              "may not be on the same plane near the fillet.")

    def _picked_segment(self, geom, step):
        """``(segments, index)`` when `geom` is a JOINED curve picked at a point on step
        `step`; None for a single-segment curve or a typed name (no pick point)."""
        point = (getattr(self, "pick_points", None) or {}).get(step)
        if point is None:
            return None
        segments = self._flat_segments(geom)
        if len(segments) < 2:
            return None
        from . import fillet as _fillet

        return segments, _fillet.segment_near(segments, point)

    @staticmethod
    def _flat_segments(geom):
        """Every SINGLE segment of `geom`, in order. A joined result can hold a polyline
        inside a polycurve (Join=Yes of two polylines and an arc explodes to
        [PolylineCurve, NurbsCurve, PolylineCurve]), so explode until nothing splits."""
        out, todo = [], [geom]
        while todo:
            g = todo.pop(0)
            parts = bridge.explode_curve(g)
            if parts:
                todo[0:0] = list(parts)
            else:
                out.append(g)
        return out

    def _rejoin(self, ctx, picked, trimmed):
        """The joined curve rebuilt around its trimmed segment, or None if it would not
        stay ONE curve (the trim cut it in two)."""
        segments, index = picked
        pieces = [s for k, s in enumerate(segments) if k != index] + [trimmed]
        joined = bridge.join_curves(list(enumerate(pieces)), self._autoclose_tolerance(ctx))
        if len(joined) == 1 and len(joined[0][0]) == len(pieces):
            return joined[0][1]
        return None

    def _fillet_one_curve(self, ctx, name, radius, trim):
        """both picks on ONE joined curve -- round the corner between the picked
        segments, and keep the curve one curve."""
        from . import fillet as _fillet

        geom = ctx.geom_by_name(name)
        if not isinstance(geom, r3.Curve):
            return f"{self.name}: {name} is not a curve"
        picked = self._picked_segment(geom, 0)
        if picked is None:
            return f"{self.name}: select two DIFFERENT curves"
        segments, i = picked
        p0, p1 = self.pick_points[0], self.pick_points[1]
        j = _fillet.segment_near(segments, p1)
        near = tuple((a + b) / 2.0 for a, b in zip(p0, p1))
        try:
            arc, pieces = _fillet.fillet_adjacent_segments(
                segments, bool(geom.IsClosed), i, j, radius, trim, near)
        except ValueError as e:
            return f"{self.UNABLE} ({e})"
        if not trim:
            _n, new = ctx.add_geometry(arc, self.name, _layer_index_of(ctx, name))
            return f"{self.name}: fillet R{_num(radius)} -> {new}"
        joined = bridge.join_curves(list(enumerate(pieces)), self._autoclose_tolerance(ctx))
        if len(joined) != 1 or len(joined[0][0]) != len(pieces):
            return (f"{self.name}: the fillet did not join back into one curve "
                    f"(nothing changed)")
        (new,) = _replace_curve(ctx, name, [joined[0][1]])
        return (f"{self.name}: fillet R{_num(radius)} at the corner of segments {i + 1} and "
                f"{j + 1} of {name} -> {new}")

    def execute(self, ctx: NarwhalContext):
        radius = float(self.opt.get("Radius", 1.0))
        trim = bool(self.opt.get("Trim", True))
        names = [a for a in self.args if isinstance(a, str)]
        picks = getattr(self, "pick_points", None) or {}
        if (len(names) >= 2 and names[0].lower() == names[1].lower()
                and 0 in picks and 1 in picks):
            return self._fillet_one_curve(ctx, names[0], radius, trim), True
        pair = self._curves(ctx)
        if isinstance(pair, str):
            return pair, True
        (na, a), (nb, b) = pair
        sa, sb = self._picked_segment(a, 0), self._picked_segment(b, 1)
        ca = sa[0][sa[1]] if sa else a
        cb = sb[0][sb[1]] if sb else b
        try:
            res = bridge.fillet_curves(ca, cb, radius, trim)
        except ValueError as e:
            return f"{self.UNABLE} ({e})", True
        if trim and (sa or sb):
            from . import fillet as _fillet

            new_a = self._rejoin(ctx, sa, res.trimmed_a) if sa else res.trimmed_a
            new_b = self._rejoin(ctx, sb, res.trimmed_b) if sb else res.trimmed_b
            if new_a is None or new_b is None:
                which = na if new_a is None else nb
                return (f"{self.name}: filleting that segment would cut {which} in two -- "
                        f"not implemented yet (nothing changed)"), True
            res = _fillet.CornerResult(res.curve, new_a, new_b)
        return self._commit(ctx, pair, res, f"fillet R{_num(radius)}"), True


class ChamferCmd(_TwoCurveCmd):
    """`Chamfer` (curves) -- the command behavior.

    steps: "Select the first curve near the end for the chamfer. Select the
    second curve...". Options built: `Distances` (the reference CAD application's two prompts "First chamfer
    distance" / "Second chamfer distance"), `Join`, `Trim`. Declared, not implemented:
    `ExtendArcsBy`, `ExtendOtherCurvesBy`. The kernel is `fillet.chamfer_curves` (exact
    for lines; arc-length walk-back for any other curve).
    the default distances are 1, 1 (the reference CAD application's are document settings we do not have).
    """

    name = "Chamfer"
    description = "Cut the corner between two curves with a straight line."
    steps = [("Select first curve to chamfer:", OBJECTS),
             ("Select second curve to chamfer:", OBJECTS)]
    options = (
        opts.Option("Distances", opts.MODE,
                    help="The distance from the intersection of the curves to the "
                         "chamfer."),
        opts.Option("Join", opts.TOGGLE, default=False,
                    help="Joins the chamfer line to the input curves."),
        opts.Option("Trim", opts.TOGGLE, default=True,
                    help="Trims the input curves to the chamfer line."),
        opts.Option("ExtendArcsBy", opts.ENUM, choices=("Arc", "Line"), default="Arc",
                    help="How an arc is extended to meet the chamfer line."),
        opts.Option("ExtendOtherCurvesBy", opts.ENUM, choices=("Line", "Arc", "Smooth"),
                    default="Line",
                    help="How a curve that is not a line or arc is extended."),
    )
    #: MEASURED (research_preview_14 R5 Chamfer): "Distances=1,1" in the prompt, sticky;
    #: "First chamfer distance <1.000>", then "Second chamfer distance <first>".
    _sticky_distances = [1.0, 1.0]

    def __init__(self):
        super().__init__()
        self.distances = list(ChamferCmd._sticky_distances)

    @property
    def prompt(self):
        base = super().prompt
        shown = f"Distances={_num(self.distances[0])},{_num(self.distances[1])}"
        return base.replace("( Distances  ", f"( {shown}  ").replace("( Distances )", f"( {shown} )")

    def on_option_set(self, option, value, ctx):
        if option.name in ("ExtendArcsBy", "ExtendOtherCurvesBy") and value != option.default:
            self.opt[option.name] = option.default
            return f"Chamfer: {option.name}={value} is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def _enter_default(self, ctx):
        if self.step_index < len(self.steps) and self.steps[self.step_index][1] == NUMBER:
            return float(self.distances[0])
        return super()._enter_default(ctx)

    def on_mode(self, name, ctx):
        if name == "Distances":
            # the two numbers are asked for HERE, before the curve still to pick
            at = self.step_index
            self.steps[at:at] = [(f"First chamfer distance <{self.distances[0]:.3f}>:", NUMBER),
                                 ("Second chamfer distance:", NUMBER)]
            return None, False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        at = self.step_index
        kind = self.steps[at][1] if at < len(self.steps) else None
        out = super()._feed(text, ctx)
        if kind == NUMBER and self.step_index == at + 1 and self.args \
                and isinstance(self.args[-1], float):
            label = self.steps[at][0]
            value = self.args.pop()
            del self.steps[at]
            self.step_index = at
            if value < 0:
                self.steps[at:at] = [(label, NUMBER)]
                return "Chamfer: a distance cannot be negative", False
            first = label.startswith("First")
            self.distances[0 if first else 1] = value
            ChamferCmd._sticky_distances[:] = self.distances
            if first and at < len(self.steps) and self.steps[at][0].startswith("Second"):
                # MEASURED: the second default is the first distance just typed
                self.steps[at] = (f"Second chamfer distance <{value:.3f}>:", NUMBER)
        return out

    def execute(self, ctx: NarwhalContext):
        pair = self._curves(ctx)
        if isinstance(pair, str):
            return pair, True
        (_na, a), (_nb, b) = pair
        d1, d2 = self.distances
        try:
            res = bridge.chamfer_curves(a, b, d1, d2, bool(self.opt.get("Trim", True)))
        except ValueError as e:
            return f"Unable to chamfer the curves: {e}", True
        return self._commit(ctx, pair, res, f"chamfer {_num(d1)},{_num(d2)}"), True


def _cplane_z(cmd, ctx):
    """The active CPlane's Z -- the projection direction for a cutting LINE (a line has no
    unique plane; in a plan view the reference CAD application projects along the view direction, the command behavior). World Z when no viewport is known."""
    plane = cmd._active_cplane(ctx)
    z = getattr(plane, "zaxis", None) if plane is not None else None
    return tuple(float(c) for c in z) if z is not None else (0.0, 0.0, 1.0)


class SplitCmd(Command):
    """`Split` (curves) -- the command behavior.

    steps: "Select objects.... Select the cutting objects.... Press Enter to end
    the command." Curves are split where the cutting CURVES cross them
    (`intersect.split_curve_by`: exact transversal hits, a closed curve opens at its
    cuts). Declared, not implemented: `Point`, and the surface-only `Isocurve` /
    `Shrink`. a PLANAR surface is split EXACTLY by the cutting curves
    (`bridge.split_planar_surface`; a Picture keeps its image registered). a CURVED
    single surface splits exactly too (`bridge.split_surface`: a cutting curve on the
    surface is used as is, a planar one projected along its normal, a 3-D one pulled).
    Polysurfaces, and surfaces as cutters, are still REFUSED by name.
    """

    name = "Split"
    description = "Split curves where cutting curves cross them, or at picked points."
    # MEASURED (research_all_13 #167): with a preselected line the prompt is "Select
    # cutting objects ( Point ):"; `Point` asks "Point to split curve. Press Enter when
    # done:" and a point (3,0,0) on (0,0,0)-(6,0,0) then Enter splits it in two, the first
    # piece (0,0,0)-(3,0,0). A pick off the curve is projected to its closest point (OURS).
    POINT_PROMPT = "Point to split curve. Press Enter when done:"
    steps = [("Select objects to split. Press Enter when done:", OBJECTS_MULTI),
             ("Select cutting objects:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def __init__(self):
        super().__init__()
        self._split_points = None

    def on_mode(self, name, ctx):
        if name == "Point":
            if self.step_index != 1:
                return "Split: select the curves to split first", False
            targets = self.args[0] if self.args else []
            if not targets or not all(isinstance(ctx.geom_by_name(n), r3.Curve)
                                      for n in targets):
                return "Split: Point splits curves only", False
            self._split_points = []
            self.steps[1] = (self.POINT_PROMPT, POINT)
            self.options = ()             # MEASURED: the point prompt lists no options
            return None, False
        return super().on_mode(name, ctx)

    def _feed(self, text, ctx):
        if self._split_points is None or self.step_index != 1 or self.pending_option:
            return super()._feed(text, ctx)
        raw = (text or "").strip()
        if raw:
            try:
                self._split_points.append(parse_point(raw, self._active_cplane(ctx),
                                                      self._last_point()))
            except ValueError as e:
                return str(e), False
            return None, False
        if not self._split_points:
            return "Split cancelled", True
        return self._split_at_points(ctx)

    def _split_at_points(self, ctx):
        from . import intersect, nearest

        report, done = [], 0
        tol = _document_tolerance(ctx)
        for nm in self.args[0]:
            g = ctx.geom_by_name(nm)
            if not isinstance(g, r3.Curve):
                continue
            params = [nearest.closest_point_on_curve(g, p, tolerance=tol).t
                      for p in self._split_points]
            pieces = intersect.split_curve_at_params(g, params)
            if len(pieces) < 2:
                report.append(f"{nm}: the points are at its ends")
                continue
            new = _replace_curve(ctx, nm, pieces)
            done += 1
            report.append(f"{nm} -> {len(new)} pieces")
        head = f"Split {done} curve(s)" if done else "Split: nothing was split"
        return head + (" (" + "; ".join(report) + ")" if report else ""), True
    options = (
        opts.Option("Point", opts.MODE,
                    help="(Curves only) Pick locations on the curve."),
        opts.Option("Isocurve", opts.MODE,
                    help="(Surfaces only) Split by the surface's own isocurves."),
        opts.Option("Shrink", opts.MODE,
                    help="(Surfaces only) Shrink the underlying surface."),
    )

    def execute(self, ctx: NarwhalContext):
        targets, cutters = self.args[0], self.args[1]
        surfaces = 0
        cut_geoms = []
        for nm in cutters:
            g = ctx.geom_by_name(nm)
            if g is None:
                return f"No object named {nm!r}", True
            if not isinstance(g, r3.Curve):
                return (f"Split: {nm} is not a curve -- splitting with a surface is not "
                        f"implemented yet"), True
            cut_geoms.append((nm, g))
        report, done, untouched = [], 0, []
        for nm in targets:
            g = ctx.geom_by_name(nm)
            if g is None:
                report.append(f"no object named {nm!r}")
                continue
            if not isinstance(g, r3.Curve):
                # a single surface, planar or curved, splits exactly (OCCT)
                if not bridge.is_single_surface(g):
                    report.append(_SURFACE_REFUSAL.format(cmd=nm, what="splitting",
                                                          verb="splitter"))
                    continue
                try:
                    pieces, _how = bridge.split_surface(g, [c for _n, c in cut_geoms],
                                                        _cplane_z(self, ctx))
                except ValueError as e:
                    report.append(f"{nm}: {e}")
                    continue
                if len(pieces) < 2:
                    untouched.append(nm)
                    continue
                new = _replace_curve(ctx, nm, pieces, base="Surface")
                done += 1
                surfaces += 1
                report.append(f"{nm} -> {len(new)} pieces")
                continue
            mine = [c for n, c in cut_geoms if n.lower() != nm.lower()]
            pieces = bridge.split_curve(g, mine) if mine else [g]
            if len(pieces) < 2:
                untouched.append(nm)
                continue
            new = _replace_curve(ctx, nm, pieces)
            done += 1
            report.append(f"{nm} -> {len(new)} pieces")
        if untouched:
            report.append("not crossed by the cutting objects: " + ", ".join(untouched))
        head = (f"Split {done} object(s)" if surfaces else f"Split {done} curve(s)") \
            if done else "Split: nothing was split"
        return head + (" (" + "; ".join(report) + ")" if report else ""), True


class TrimCmd(Command):
    """`Trim` (curves) -- the command behavior.

    steps: "Select the cutting objects.... Select the parts of other objects or
    the cutting objects themselves to trim away." -- so the pick marks the part REMOVED,
    and a cutting object may itself be trimmed. Each pick trims and the command waits for
    the next ("Select object to trim. Press Enter when done", the command UI); Enter ends.
    Declared, not implemented: `ExtendCuttingLines`, `ApparentIntersections`, `Line`.
    a pick ON a planar surface (and not on a curve) trims that surface EXACTLY --
    the piece under the pick is removed (`bridge.trim_planar_surface`). CURVED single
    surfaces trim exactly too (`bridge.trim_surface`). Polysurfaces, and surfaces as
    cutters, are still REFUSED by name (rule 0: the only polysurface
    trimmer is mesh-).
    """

    name = "Trim"
    description = "Trim away the picked part of a curve at its crossings with cutting curves."
    # MEASURED (research_preview_14 R5 Trim): "... ( ExtendCuttingLines=No
    # ApparentIntersections=Yes  Line )", then "Select object to trim, select pressing Shift
    # to extend"; a click trims at once, Enter ends. Nothing is previewed on hover.
    TRIM_PROMPT = "Select object to trim, select pressing Shift to extend:"
    steps = [("Select cutting objects. Press Enter when done:", OBJECTS_MULTI),
             (TRIM_PROMPT, POINT)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("ExtendCuttingLines", opts.TOGGLE, default=False,
                    help="Imaginary extensions of the line are used (not implemented yet)."),
        opts.Option("ApparentIntersections", opts.TOGGLE, default=True,
                    help="Trim where curves appear to intersect in the active view."),
        opts.Option("Line", opts.MODE,
                    help="Draw a temporary line to use for the cutting object."),
    )

    @property
    def prompt(self):
        base = super().prompt
        if self.step_index == 1 and getattr(self, "_trimmed", 0) and base.startswith(self.TRIM_PROMPT[:-1]):
            label = self.TRIM_PROMPT[:-1]
            return label + ". Press Enter when done" + base[len(label):]
        return base

    def on_option_set(self, option, value, ctx):
        if option.name in ("ExtendCuttingLines", "ApparentIntersections") \
                and value != option.default:
            self.opt[option.name] = option.default
            return f"Trim: {option.name}={'Yes' if value else 'No'} is not implemented yet", False
        return super().on_option_set(option, value, ctx)

    def __init__(self):
        super().__init__()
        self._trimmed = 0

    def _feed(self, text, ctx):
        if (not text.strip() and self.pending_option is None and self.step_index == 1):
            return (f"Trimmed {self._trimmed} time(s)" if self._trimmed else None), True
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        cutters = self.args[0]
        pick = self.args[1]
        self.args = [cutters]                 # ready for the next pick
        self.step_index = 1
        for nm in cutters:
            g = ctx.geom_by_name(nm)
            if g is not None and not isinstance(g, r3.Curve):
                return (f"Trim: {nm} is not a curve -- trimming with a surface is not "
                        f"implemented yet"), True
        name, geom = _curve_under_pick(ctx, pick)
        if name is None:
            # not on a curve -- is it on a surface? That trims exactly.
            sname, sgeom = _planar_surface_under_pick(ctx, pick)
            if sname is None:
                return (f"Trim: {geom} -- " + _SURFACE_REFUSAL.format(
                    cmd="Trim", what="trimming", verb="trimmer").split(": ", 1)[1]), False
            curves = [ctx.geom_by_name(n) for n in cutters]
            curves = [c for c in curves if isinstance(c, r3.Curve)]
            try:
                pieces, _how = bridge.trim_surface(sgeom, curves, pick, _cplane_z(self, ctx))
            except ValueError as e:
                return f"Trim: {e}", False
            if len(pieces) == 1 and pieces[0] is sgeom:
                return f"Trim: the cutting objects do not separate {sname}", False
            new = _replace_curve(ctx, sname, pieces, base="Surface",
                                 keep_selection=True)
            self._trimmed += 1
            return f"Trimmed {sname} -> {', '.join(new) if new else 'nothing left'}", False
        mine = [ctx.geom_by_name(n) for n in cutters if n.lower() != name.lower()]
        mine = [c for c in mine if c is not None]
        if not mine:
            return f"Trim: {name} is the only cutting object -- it cannot cut itself", False
        if len(bridge.split_curve(geom, mine)) < 2:
            return f"Trim: the cutting objects do not cross {name}", False
        pieces = bridge.trim_curve(geom, mine, pick)
        new = _replace_curve(ctx, name, pieces, keep_selection=True)
        # a trimmed CUTTER keeps cutting (the reference CAD application trims "the cutting objects themselves")
        if any(n.lower() == name.lower() for n in cutters):
            self.args = [[n for n in cutters if n.lower() != name.lower()] + new]
        self._trimmed += 1
        return f"Trimmed {name} -> {', '.join(new) if new else 'nothing left'}", False


class IntersectCmd(Command):
    """`Intersect` -- the command behavior.

    "Select objects. This creates curves or point object at the intersection of
    the two objects." MEASURED (research_all_13 note 075):

    * two crossing lines -> one POINT, "Found 1 intersection.";
    * two collinear, overlapping lines -> one new CURVE, the shared part (2,0,0)-(4,0,0),
      and that overlap counts as 1 intersection;
    * a line through a planar surface -> one point (2,0,2); a miss -> "Found 0
      intersections."; the inputs are kept.

    Curve x curve uses `intersect.curve_curve` (Newton-refined to ~1e-12); an overlap is
    the exact sub-curve of the first curve (`Trim`). Curve x surface is EXACT in OCCT on
    the surface's own shape (`occt_backend.curve_shape_intersection`, trims honoured).
    `InfinitePlane=Yes` intersects curves with the UNBOUNDED plane of each selected
    planar surface instead, so hits beyond the surface's boundary count; any other
    object is then refused by name. Not implemented, and said so: surface x surface, a
    curve lying IN a face, surfaces with no exact shape, `SubCrv`.
    """

    name = "Intersect"
    description = "Create points or curves where the selected objects intersect."
    steps = [("Select objects to intersect:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("InfinitePlane", opts.TOGGLE, default=False,
                    help="Use an infinite plane through a planar object."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
    )

    @staticmethod
    def _surface_hits(curve, shape):
        from .kernel import occt_backend as ob
        profile = bridge.curve_recipe(curve)
        if profile is None:
            return None, "its curve type cannot be carried to the kernel"
        try:
            points, edges = ob.curve_shape_intersection(profile, shape)
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        if edges:
            return None, ("the curve lies in a face -- an overlap curve there is not "
                          "implemented yet")
        return points, None

    def execute(self, ctx: NarwhalContext):
        from . import analysis, intersect

        names = self.args[0]
        infinite = bool(self.opt.get("InfinitePlane"))
        curves, surfaces, skipped = [], [], []
        for nm in names:
            g = ctx.geom_by_name(nm)
            if isinstance(g, r3.Curve):
                curves.append((nm, g))
            elif g is not None and infinite:
                # OURS (#47): a "planar object" is a single planar surface; its plane is
                # taken unbounded and intersected by the same kernel path.
                face = bridge.planar_face_shape(g) if bridge.is_planar_surface(g) else None
                if face is None:
                    return (f"Intersect: InfinitePlane needs a planar surface, and {nm} "
                            f"is not one; nothing was created"), True
                from .kernel import occt_backend as ob
                surfaces.append((nm, ob.infinite_plane_face(face)))
            elif g is not None:
                shape = analysis.occt_shape_of(g)
                if shape is None:
                    skipped.append(f"{nm} (no exact surface)")
                else:
                    surfaces.append((nm, shape))
        if len(curves) + len(surfaces) < 2:
            msg = "Intersect: select at least two objects"
            if skipped:
                msg += f" ({', '.join(skipped)})"
            return msg, True
        points, overlaps, notes = [], [], []
        for i in range(len(curves)):
            for j in range(i + 1, len(curves)):
                a = curves[i][1]
                for x in intersect.curve_curve(a, curves[j][1]):
                    if not x.overlap:
                        points.append(tuple(x.point))
                        continue
                    t0, t1 = sorted((float(x.t_a), float(x.t_a_end)))
                    piece = a.Trim(t0, t1) if t1 > t0 else None
                    if piece is None:
                        notes.append(f"an overlap of {curves[i][0]} and {curves[j][0]} "
                                     f"could not be cut out")
                    else:
                        overlaps.append(piece)
        for cname, curve in curves:
            for sname, shape in surfaces:
                hits, why = self._surface_hits(curve, shape)
                if hits is None:
                    notes.append(f"{cname} x {sname}: {why}")
                else:
                    points.extend(hits)
        if len(surfaces) > 1:
            notes.append("surface x surface intersection is not implemented yet")
        first = (curves or surfaces)[0][0]
        layer = _layer_index_of(ctx, first)
        for p in points:
            ctx.add_geometry(bridge.make_point(p), "Intersect", layer)
        for piece in overlaps:
            ctx.add_geometry(piece, "Intersect", layer)
        n = len(points) + len(overlaps)
        msg = f"Found {n} intersection{'' if n == 1 else 's'}."
        if skipped:
            notes.append(f"skipped {', '.join(skipped)}")
        if notes:
            msg += " (" + "; ".join(notes) + ")"
        return msg, True


# ---------------------------------------------------------------------------
# BlendCrv -- a blend curve with chosen continuity at each end
# ---------------------------------------------------------------------------

class BlendCrvCmd(Command):
    """`BlendCrv` -- the command behavior.

    "creates a blend curve between curves and / or surface edges with control over
    the continuity". Prompts in the command UI: "Select first curve to blend - select near
    end", "Select second curve to blend - select near end" -- each pick chooses the curve
    AND its end (`BlendStart=CurveEnd`: "Starts the blend at the end of the curve where the
    curve was selected").

    Options built: `Continuity1` / `Continuity2` (Position, Tangency, Curvature, G3, G4 --
    the command UI's names for the per-end setting), `Flip1` / `Flip2`, `Trim`, `Join`,
    `BlendStart=CurveEnd`. Declared, not implemented: `BlendStart=PickPoint`, `Edges`,
    `Point`, `SubCrv`, `ShowCurvature`. The INTERACTIVE handle adjustment the reference CAD application offers in
    its preview is not implemented; `blend.py` documents the
    handle length it uses.

    The result is ONE NURBS curve of the lowest degree carrying the continuity
    (`blend.blend_curves`): Position 1, Tangency 3, Curvature 5, G3 7, G4 9 (mixed ends add).
    With `BlendStart=CurveEnd` the blend starts exactly at the picked ends, so `Trim` has
    nothing to remove (it matters for PickPoint, not built); `Join` makes one curve.
    """

    name = "BlendCrv"
    description = "Blend two curve ends with Position/Tangency/Curvature/G3/G4 continuity."
    # MEASURED (research_preview_14 R5 BlendCrv): both picks read "Select curve to blend";
    # the pick chooses the nearer end. (The reference program then opens an Adjust Curve
    # Blend dialog, default Curvature at both ends -- here the same choices are options.)
    steps = [("Select curve to blend:", POINT),
             ("Select curve to blend:", POINT)]
    options = (
        opts.Option("Continuity1", opts.ENUM, default="Curvature",
                    choices=("Position", "Tangency", "Curvature", "G3", "G4"),
                    help="Continuity with the first curve."),
        opts.Option("Continuity2", opts.ENUM, default="Curvature",
                    choices=("Position", "Tangency", "Curvature", "G3", "G4"),
                    help="Continuity with the second curve."),
        opts.Option("Flip1", opts.TOGGLE, default=False,
                    help="Reverses the direction of the first curve."),
        opts.Option("Flip2", opts.TOGGLE, default=False,
                    help="Reverses the direction of the second curve."),
        opts.Option("Trim", opts.TOGGLE, default=False,
                    help="Trims the input curves to the resulting curve."),
        opts.Option("Join", opts.TOGGLE, default=False,
                    help="Joins the resulting curves."),
        opts.Option("BlendStart", opts.ENUM, default="CurveEnd",
                    choices=("CurveEnd", "PickPoint"),
                    help="Where on the curve the blend starts."),
        opts.Option("Edges", opts.MODE, help="Restricts selection to surface edges."),
        opts.Option("Point", opts.MODE, help="Specifies a point to blend to."),
        opts.Option("SubCrv", opts.MODE, help="Selects part of a curve as input."),
        opts.Option("ShowCurvature", opts.MODE, help="Displays the curvature graph."),
    )

    def on_option_set(self, option, value, ctx):
        if option.name == "BlendStart" and value == "PickPoint":
            self.opt["BlendStart"] = "CurveEnd"
            return "BlendCrv: BlendStart=PickPoint is not implemented yet", False
        return None

    def execute(self, ctx: NarwhalContext):
        from . import blend

        picks = []
        for p in self.args[:2]:
            nm, geom = _curve_under_pick(ctx, p)
            if nm is None:
                return f"BlendCrv: {geom}", True
            picks.append((nm, geom, blend.nearer_end(geom, p)))
        (n1, c1, e1), (n2, c2, e2) = picks
        if n1.lower() == n2.lower() and e1 == e2:
            return "BlendCrv: pick the two curves (or two different ends)", True
        try:
            crv, info = blend.blend_curves(
                c1, e1, c2, e2, self.opt.get("Continuity1", "Curvature"),
                self.opt.get("Continuity2", "Curvature"),
                bool(self.opt.get("Flip1")), bool(self.opt.get("Flip2")))
        except ValueError as e:
            return f"BlendCrv: {e}", True
        if self.opt.get("Join"):
            items = [(0, c1), (1, crv)] + ([(2, c2)] if n2.lower() != n1.lower() else [])
            joined = bridge.join_curves(items, self._autoclose_tolerance(ctx))
            if len(joined) == 1 and len(joined[0][0]) == len(items):
                if n2.lower() != n1.lower():
                    _replace_curve(ctx, n2, [])
                (new,) = _replace_curve(ctx, n1, [joined[0][1]])
                return (f"BlendCrv: degree-{info['degree']} blend, joined -> {new}"), True
            return "BlendCrv: the blend did not join to the curves (nothing changed)", True
        _n, new = ctx.add_geometry(crv, "Blend", _layer_index_of(ctx, n1))
        return f"BlendCrv: degree-{info['degree']} blend -> {new}", True


# ---------------------------------------------------------------------------
# Rebuild (curves)
# ---------------------------------------------------------------------------

class RebuildCmd(Command):
    """`Rebuild` (curves) -- the command behavior (`rebuild.py` holds the fit).

    the reference CAD application's Rebuild is a dialog; this is its command-line form. Prompt and report are
    the command UI's: "Select curves to rebuild", "Maximum deviation = %g". Options:
    `PointCount`, `Degree` (up to 11; "Point count = Degree + 1" kept automatically --
    whichever was set last wins, as the help describes), `DeleteInput`, `CurrentLayer`,
    `PreserveTangents`. Not filled from a preselection at start, so the options can be set
    at the prompt first; Enter confirms the selection. Surfaces are REFUSED by name.

    flagged: an option left unset takes the curve's OWN value (the reference CAD application's dialog starts
    from its last-used values, which we do not keep); DeleteInput defaults to Yes.
    """

    name = "Rebuild"
    description = "Rebuild curves to a degree and control-point count."
    steps = [("Select curves to rebuild:", OBJECTS_MULTI)]
    options = (
        opts.Option("PointCount", opts.VALUE, value_kind="number",
                    help="The number of control points in the result."),
        opts.Option("Degree", opts.VALUE, value_kind="number",
                    help="The degree of the result (up to 11)."),
        opts.Option("DeleteInput", opts.TOGGLE, default=True,
                    help="Deletes the original geometry."),
        opts.Option("CurrentLayer", opts.TOGGLE, default=False,
                    help="Creates the new objects on the current layer."),
        opts.Option("PreserveTangents", opts.TOGGLE, default=False,
                    help="Matches the input end tangents (open, degree >= 2, 4+ points)."),
    )

    def __init__(self):
        super().__init__()
        self._last_set = "Degree"

    def on_option_set(self, option, value, ctx):
        from . import rebuild

        if option.name not in ("PointCount", "Degree"):
            return None
        if value is None or value != int(value) or value < 1:
            self.opt[option.name] = None
            return f"Rebuild: {option.name} must be a whole number of at least 1", False
        if option.name == "Degree" and value > rebuild.MAX_DEGREE:
            self.opt["Degree"] = None
            return f"Rebuild: degree can be at most {rebuild.MAX_DEGREE}", False
        self._last_set = option.name
        n, p = self.opt.get("PointCount"), self.opt.get("Degree")
        if n is not None and p is not None:
            n2, p2 = rebuild.normalize_counts(n, p, self._last_set)
            self.opt["PointCount"], self.opt["Degree"] = n2, p2
            if (n2, p2) != (int(n), int(p)):
                return (f"Rebuild: PointCount={n2} Degree={p2} (Point count = Degree + 1, "
                        f"kept automatically)"), False
        return f"Rebuild: {option.name}={int(value)}", False

    @staticmethod
    def _own_counts(geom):
        n = geom.ToNurbsCurve() if not isinstance(geom, r3.NurbsCurve) else geom
        count = len(n.Points)
        if geom.IsClosed:
            count -= n.Degree if n.IsPeriodic else 1     # "one less point"
        return count, n.Degree

    def execute(self, ctx: NarwhalContext):
        from . import rebuild

        names = self.args[0] if self.args else []
        done, worst, skipped, notes, made = 0, 0.0, [], [], []
        for nm in names:
            geom = ctx.geom_by_name(nm)
            if not isinstance(geom, r3.Curve):
                skipped.append(f"{nm} (rebuilding surfaces is not implemented yet)")
                continue
            own_n, own_p = self._own_counts(geom)
            n = self.opt.get("PointCount") or own_n
            p = self.opt.get("Degree") or own_p
            n, p = rebuild.normalize_counts(n, p, self._last_set)
            try:
                out, info = rebuild.rebuild_curve(geom, n, p,
                                                  bool(self.opt.get("PreserveTangents")))
            except ValueError as e:
                skipped.append(f"{nm} ({e})")
                continue
            notes += info["notes"]
            worst = max(worst, info["deviation"])
            current = bool(self.opt.get("CurrentLayer"))
            if self.opt.get("DeleteInput", True) and not current:
                made += _replace_curve(ctx, nm, [out])      # the original's layer
            else:
                if self.opt.get("DeleteInput", True):
                    _replace_curve(ctx, nm, [])
                _n, new = ctx.add_geometry(out, "Curve",
                                           None if current else _layer_index_of(ctx, nm))
                made.append(new)
            done += 1
        if not done:
            return "Rebuild: nothing rebuilt" + (
                " -- " + "; ".join(skipped) if skipped else ""), True
        msg = (f"Rebuilt {done} curve(s) -> {', '.join(made)}. Maximum deviation = "
               f"{worst:g}")
        if notes:
            msg += " (" + "; ".join(sorted(set(notes))) + ")"
        if skipped:
            msg += "; skipped " + "; ".join(skipped)
        return msg, True


# ---------------------------------------------------------------------------
# Picture -- an image on an exact planar surface
# ---------------------------------------------------------------------------

class PictureCmd(Command):
    """`Picture` -- the command behavior.

    steps: "Select one of the supported image file types. Pick the first corner of
    the plane. Pick the other corner, or type the length. The defining rectangle retains
    the same aspect (length to width) ratio as the image." Prompts are the reference CAD application's
    in the command UI: "Image file name", "First corner of picture", "Other corner or
    length", "Center of picture", "Corner or length".

    The file comes from the file dialog (`filedialog.ask_open_path`), else a typed path.
    Options built: (Default) two corners, `3Point`, `Vertical`, `Center`, `1to1`;
    declared: `EdgeMidpoint`, `AroundCurve`.

    The surface is EXACT (one planar face). The image FRAME is stored on it
    (`picture.KEY`) so the texture stays registered after a trim, and a Picture material
    (named after the file) is assigned by `NarwhalContext.add_geometry`.

    flagged: how the aspect lock meets the pick (`picture.fit_size` -- the rectangle
    grows until it reaches the picked corner); a typed length is the WIDTH; 3Point keeps
    the image unmirrored, so a side point on the right makes the first edge its TOP; 1to1's
    pick is the lower-left corner; an image with no declared density is taken at 72 dpi
    (and the message says so).
    """

    name = "Picture"
    description = "Place an image on an exact rectangular planar surface."
    steps = [("Image file name:", TEXT),
             ("First corner of picture:", POINT), ("Other corner or length:", DISTANCE)]
    options = (
        opts.Option("3Point", opts.MODE,
                    help="Two adjacent corner locations and a location on the opposite "
                         "side."),
        opts.Option("EdgeMidpoint", opts.MODE,
                    help="The midpoint of the first edge, an end, and the opposite side."),
        opts.Option("Vertical", opts.MODE,
                    help="Draws the rectangle perpendicular to the construction plane."),
        opts.Option("Center", opts.MODE,
                    help="Draws the rectangle from the center point and a corner."),
        opts.Option("AroundCurve", opts.MODE,
                    help="Draws a rectangle perpendicular to a curve."),
        opts.Option("1to1", opts.MODE,
                    help="The plane size is the print size of the image (pixels / dpi)."),
    )
    FLOWS = {
        None: [("First corner of picture:", POINT), ("Other corner or length:", DISTANCE)],
        "3Point": [("First corner of picture:", POINT), ("Second corner:", POINT),
                   ("Opposite side:", POINT)],
        "Vertical": [("First corner of picture:", POINT),
                     ("Other corner or length:", DISTANCE)],
        "Center": [("Center of picture:", POINT), ("Corner or length:", DISTANCE)],
        "1to1": [("First corner of picture:", POINT)],
    }

    def __init__(self):
        super().__init__()
        self._info = None
        self._flow = None

    def begin(self, ctx):
        from . import filedialog, picture

        status, path = filedialog.ask_open_path("Picture", filedialog.start_dir(),
                                                picture.FILTERS)
        if status == filedialog.CANCEL:
            return "Picture cancelled", True
        if status == filedialog.OK:
            try:
                self.args = [self._parse(TEXT, path, ctx)]
            except ValueError as e:
                return f"Picture: {e}", True
            self.step_index = 1
            return None, False
        return ("No file dialog is available (kdialog / zenity) -- type the image file "
                "name."), False

    def _parse(self, kind, text, ctx=None):
        if kind == TEXT and self.step_index == 0:
            from . import picture

            path = os.path.abspath(os.path.expanduser(text.strip().strip('"')))
            self._info = picture.read_image_info(path)    # ValueError -> re-prompt
            return path
        return super()._parse(kind, text, ctx)

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        return parse_point(stripped, self._active_cplane(ctx), self._last_point())

    def on_mode(self, name, ctx):
        if name in self.FLOWS:
            if len(self.args) > 1:
                return f"Picture: {name} is chosen before the first corner", False
            self._flow = name
            self.steps[1:] = list(self.FLOWS[name])
            return f"Picture: {name} mode", False
        return super().on_mode(name, ctx)

    def _frame(self, ctx):
        """The image frame ``(origin, u, v)`` from the picks, or a refusal string."""
        from . import picture, units

        p = self._active_cplane(ctx)
        x, y, z = (tuple(float(c) for c in a) for a in (p.xaxis, p.yaxis, p.zaxis))
        info = self._info
        aspect = info.aspect
        picks = self.args[1:]

        def world(u, v, w):
            return p.to_world(u, v, w)

        def vec(a, s):
            return tuple(c * s for c in a)

        if self._flow == "1to1":
            wd, ht = picture.print_size(info, ctx.model.unit_system(), units.convert)
            return picks[0], vec(x, wd), vec(y, ht)
        if self._flow == "3Point":
            a, b, s = picks
            au, av, aw = p.from_world(a)
            bu, bv, _ = p.from_world(b)
            su, sv, _ = p.from_world(s)
            eu, ev = bu - au, bv - av
            width = math.hypot(eu, ev)
            if width <= 1e-12:
                return "the two corners are the same point"
            pu, pv = -ev / width, eu / width              # left perpendicular
            side = (su - au) * pu + (sv - av) * pv
            if abs(side) <= 1e-12:
                return "the third point is on the line through the two corners"
            height = width / aspect
            u_vec = tuple(eu * x[i] + ev * y[i] for i in range(3))
            up = tuple((pu * x[i] + pv * y[i]) * height for i in range(3))
            a_w = world(au, av, aw)
            if side > 0:
                return a_w, u_vec, up
            return tuple(a_w[i] - up[i] for i in range(3)), u_vec, up
        if self._flow == "Vertical":
            c1, a2 = picks
            if isinstance(a2, tuple):
                du, dv, _ = (q - r for q, r in zip(p.from_world(a2), p.from_world(c1)))
                width = math.hypot(du, dv)
                d = tuple((du * x[i] + dv * y[i]) / width for i in range(3)) if width else x
            else:
                width, d = abs(float(a2)), (x if float(a2) >= 0 else vec(x, -1.0))
            if width <= 1e-12:
                return "the rectangle has no width"
            return c1, vec(d, width), vec(z, width / aspect)
        # Default and Center: the rectangle runs along the CPlane axes
        c1, a2 = picks
        cu, cv, cw = p.from_world(c1)
        if isinstance(a2, tuple):
            ku, kv, _ = p.from_world(a2)
            du, dv = ku - cu, kv - cv
        else:
            du, dv = float(a2), 0.0
        if self._flow == "Center":
            if isinstance(a2, tuple):
                du, dv = 2 * du, 2 * dv
            width, height, _su, _sv = picture.fit_size(aspect, du, dv)
            u0, v0 = cu - width / 2, cv - height / 2
        else:
            width, height, su, sv = picture.fit_size(aspect, du, dv)
            u0 = cu if su > 0 else cu - width
            v0 = cv if sv > 0 else cv - height
        if width <= 1e-12:
            return "the rectangle has no size"
        return world(u0, v0, cw), vec(x, width), vec(y, height)

    def live_frame(self, cursor, ctx):
        """Return the picture frame preview for ``cursor``, or ``None``.

        The UI draws the image in this frame while it follows the final placement pick.
        Preview and placement both use ``_frame`` so their geometry stays identical.
        """
        if self._info is None or cursor is None or not self.args:
            return None
        if self.step_index != len(self.steps) - 1 or self.step_index < 1:
            return None
        saved = list(self.args)
        try:
            self.args = saved + [tuple(float(c) for c in cursor)]
            res = self._frame(ctx)
        except Exception:  # noqa: BLE001
            return None
        finally:
            self.args = saved
        return None if isinstance(res, str) else (saved[0], res)

    def execute(self, ctx: NarwhalContext):
        from . import picture

        res = self._frame(ctx)
        if isinstance(res, str):
            return f"Picture: {res}", True
        origin, u, v = res
        frame = picture.make_frame(self.args[0], origin, u, v)
        try:
            geom = bridge.make_picture(frame)
        except ValueError as e:
            return str(e), True
        _, display_name = ctx.add_geometry(geom, "Picture")
        info = self._info
        w = math.sqrt(sum(c * c for c in u))
        h = math.sqrt(sum(c * c for c in v))
        msg = (f"Created {display_name} ({_num(w)} x {_num(h)}; {info.format} "
               f"{info.width}x{info.height})")
        if self._flow == "1to1" and not info.dpi_declared:
            msg += (f" -- the image declares no resolution, so {picture.DEFAULT_DPI:g} dpi "
                    f"was assumed")
        return msg, True


# ---------------------------------------------------------------------------
# Grid -- document/viewport settings backend; display wiring is deferred
# ---------------------------------------------------------------------------

class GridCmd(Command):
    """Set Grid properties without drawing.

    option names come from ``documentproperties/grid``.  The help has
    eight fields (the queue's phrase "seven options" conflicts with its own
    field list and the page), so all eight are declared here.  The
    supplied:class:`GridSettingsStore` is updated on Enter and records one
    pure model snapshot when a change occurs.  Production contexts may provide
    a ``grid_settings_changed`` callback; it invalidates the renderer after the
    store changes without coupling this command to Blender UI.
    """

    name = "Grid"
    description = "Set construction-plane grid settings."
    steps = []
    records_undo = True
    options = (
        opts.Option("SnapSpacing", opts.VALUE, value_kind="number"),
        opts.Option("MinorLineSpacing", opts.VALUE, value_kind="number"),
        opts.Option("MajorLineInterval", opts.VALUE, value_kind="number"),
        opts.Option("GridLineCount", opts.VALUE, value_kind="number"),
        opts.Option("ShowGrid", opts.TOGGLE, default=True),
        opts.Option("ShowGridAxes", opts.TOGGLE, default=True),
        opts.Option("ShowWorldAxes", opts.TOGGLE, default=True),
        opts.Option("ApplyTo", opts.ENUM, default=grid_settings.ACTIVE,
                    choices=(grid_settings.ACTIVE, grid_settings.ALL)),
    )

    def __init__(self):
        super().__init__()
        self._store = None
        self._loaded = None
        self._last_valid_opt = None

    @staticmethod
    def _store_for(ctx):
        store = getattr(ctx, "grid_settings_store", None)
        if store is None:
            model = getattr(ctx, "model", None)
            store = getattr(model, "grid_settings_store", None)
        if store is None:
            raise ValueError("Grid requires a supplied document/viewport settings store")
        required = ("settings_for", "apply", "active_viewport_id")
        if any(not hasattr(store, name) for name in required):
            raise ValueError("Grid settings store has an invalid interface")
        return store

    def begin(self, ctx):
        # Grid is option-only.  Unlike ordinary zero-step commands, naming it
        # must not apply the current values before the user changes anything.
        self._store = self._store_for(ctx)
        self._loaded = self._store.settings_for(self._store.active_viewport_id)
        self.opt.update({
            "SnapSpacing": self._loaded.snap_spacing,
            "MinorLineSpacing": self._loaded.minor_spacing,
            "MajorLineInterval": self._loaded.major_interval,
            "GridLineCount": self._loaded.grid_line_count,
            "ShowGrid": self._loaded.show_grid,
            "ShowGridAxes": self._loaded.show_grid_axes,
            "ShowWorldAxes": self._loaded.show_world_axes,
            "ApplyTo": self._loaded.apply_to,
        })
        self._last_valid_opt = dict(self.opt)
        return None, False

    def _candidate(self):
        values = {
            "snap_spacing": self.opt["SnapSpacing"],
            "minor_spacing": self.opt["MinorLineSpacing"],
            "major_interval": self.opt["MajorLineInterval"],
            "grid_line_count": self.opt["GridLineCount"],
            "show_grid": self.opt["ShowGrid"],
            "show_grid_axes": self.opt["ShowGridAxes"],
            "show_world_axes": self.opt["ShowWorldAxes"],
            "apply_to": self.opt["ApplyTo"],
        }
        # VALUE options are parsed as floats by the shared option framework;
        # requires the two count/interval fields to remain integers.
        for name in ("major_interval", "grid_line_count"):
            value = values[name]
            if not float(value).is_integer():
                raise ValueError(f"{name} must be a positive integer")
            values[name] = int(value)
        return grid_settings.GridSettings(**values)

    def on_option_set(self, option, value, ctx):
        try:
            self._candidate()
        except (TypeError, ValueError) as exc:
            # Keep the previous valid option value; invalid input is explicit
            # and leaves the command available for correction.
            if self._last_valid_opt is not None:
                self.opt.update(self._last_valid_opt)
            return f"Grid: {exc}", False
        self._last_valid_opt = dict(self.opt)
        return None

    def execute(self, ctx):
        if self._store is None:
            self._store = self._store_for(ctx)
        try:
            candidate = self._candidate()
        except (TypeError, ValueError) as exc:
            return f"Grid: {exc}", False
        note_change = getattr(ctx, "note_change", None)
        if changed := tuple(
                viewport_id for viewport_id, value in candidate.application_plan(
                    self._store.viewport_ids,
                    active_viewport_id=self._store.active_viewport_id)
                if self._store.settings_for(viewport_id) != value):
            if callable(note_change):
                # Capture the document snapshot before the store mutates it.
                note_change(self.name)
            self._store.apply(candidate)
            changed_callback = getattr(ctx, "grid_settings_changed", None)
            if callable(changed_callback):
                changed_callback()
        target = ", ".join(map(str, changed)) if changed else "none"
        return f"Grid settings updated (viewports: {target})", True


class SnapSizeCmd(Command):
    """Set the measured Grid Snap spacing for the active or all viewports."""

    name = "SnapSize"
    description = "Set Grid Snap spacing in model units."
    # MEASURED (research21 tooling probe): "Snap size <1> ( ApplyTo=ActiveViewport ):".
    steps = [("Snap size:", DISTANCE)]
    records_undo = False
    options = (
        opts.Option("ApplyTo", opts.ENUM, default="ActiveViewport",
                    choices=("ActiveViewport", "AllViewports")),
    )

    def _distance_base(self):
        return None

    def _current(self, ctx):
        try:
            store = GridCmd._store_for(ctx)
            return float(store.settings_for(store.active_viewport_id).snap_spacing)
        except Exception:  # noqa: BLE001
            return None

    def begin(self, ctx):
        self._shown = self._current(ctx)
        return super().begin(ctx)

    @property
    def prompt(self):
        base = super().prompt
        value = getattr(self, "_shown", None)
        if value is not None and self.pending_option is None and base.startswith("Snap size"):
            return f"Snap size <{_num(value)}>" + base[len("Snap size"):]
        return base

    def _enter_default(self, ctx):
        value = getattr(self, "_shown", None)
        return float(value) if value is not None else None

    def execute(self, ctx):
        try:
            store = GridCmd._store_for(ctx)
            spacing = float(self.args[0])
            scope = (grid_settings.ALL if self.opt["ApplyTo"] == "AllViewports"
                     else grid_settings.ACTIVE)
            changed = store.apply_snap_spacing(spacing, scope)
        except (TypeError, ValueError) as exc:
            return f"SnapSize: {exc}", True
        callback = getattr(ctx, "grid_settings_changed", None)
        if changed and callable(callback):
            callback()
        target = ", ".join(map(str, changed)) if changed else "none"
        return f"Grid snap spacing {spacing:g} (viewports: {target})", True


# ---------------------------------------------------------------------------
# Units -- the document's unit system and absolute tolerance
# ---------------------------------------------------------------------------

class UnitsCmd(Command):
    """Set model units and absolute tolerance from the command line.

    When a non-empty model changes unit systems, the command asks whether geometry should
    be scaled by the conversion factor. Units may be entered through common abbreviations
    such as ``mm``, ``in``, and ``ft`` or through names accepted by ``units.parse_unit``.

    Enter at the unit prompt keeps the current unit, allowing an
    ``AbsoluteTolerance``-only change. Enter at the scaling question accepts scaling.
    Both settings are stored in the .3dm document and undo as one operation.
    """

    name = "Units"
    description = "Set the model unit system and absolute tolerance (optionally scaling the model)."
    steps = [("Model units:", TEXT)]
    options = (
        opts.Option("AbsoluteTolerance", opts.VALUE, value_kind="number",
                    help="Tolerance in model units used when creating new geometry "
                         "that cannot be absolutely accurate."),
    )

    def __init__(self):
        super().__init__()
        self._current = None
        self._new = None
        self._tolerance = None

    def begin(self, ctx):
        model = getattr(ctx, "model", None)
        if model is not None and hasattr(model, "unit_system"):
            self._current = model.unit_system()
            self.steps[0] = (f"Model units <{self._current}>:", TEXT)
            self.opt["AbsoluteTolerance"] = model.absolute_tolerance()
        return super().begin(ctx)

    def on_option_set(self, option, value, ctx):
        if option.name == "AbsoluteTolerance":
            if not value > 0:
                self.opt["AbsoluteTolerance"] = ctx.model.absolute_tolerance()
                return "Units: the absolute tolerance must be greater than zero", False
            self._tolerance = float(value)
        return None

    def _enter_default(self, ctx):
        if self.step_index == 0:
            return self._current or ""          # keep the unit
        return "Yes"                            # the scale question

    def execute(self, ctx: NarwhalContext):
        from . import units

        model = ctx.model
        if self._new is None:
            try:
                new = units.parse_unit(self.args[0])
            except ValueError as e:
                self.args.pop()
                self.step_index = 0
                return f"Units: {e}", False
            if new not in units.UNIT_SCALE_MM:
                self.args.pop()
                self.step_index = 0
                return f"Units: {new} needs a custom scale, which is not implemented yet", False
            self._new = new
            old = model.unit_system()
            if new != old and model.object_count() > 0 and old in units.UNIT_SCALE_MM:
                factor = units.scale_factor(old, new)
                self.steps.append((f"Model units change from {old} to {new}, scale model "
                                   f"by {_num(factor)}? <Yes>:", TEXT))
                return None, False
            return self._apply(ctx, scale=False)
        answer = opts.parse_bool(self.args[-1])
        if answer is None:
            self.args.pop()
            self.step_index = len(self.steps) - 1
            return "Units: answer Yes or No", False
        return self._apply(ctx, scale=answer)

    def _apply(self, ctx, scale):
        model = ctx.model
        old = model.unit_system()
        ctx.note_change(self.name)
        try:
            factor = model.set_unit_system(self._new, scale=scale)
            if self._tolerance is not None:
                model.set_absolute_tolerance(self._tolerance)
        except ValueError as e:
            return f"Units: {e}", True
        if factor != 1.0:
            rebuild = getattr(ctx, "rebuild_display", None)
            if callable(rebuild):
                rebuild()
        if old == self._new and self._tolerance is None:
            return f"Model Units = {self._new} (unchanged)", True
        msg = (f"Model units and tolerances modified. Model Units = {self._new}, "
               f"absolute tolerance {_num(model.absolute_tolerance())}")
        if factor != 1.0:
            msg += f" (model scaled by {_num(factor)})"
        elif old != self._new and model.object_count() > 0:
            msg += " (model NOT scaled -- the numbers are unchanged)"
        return msg, True


def _clipboard_selected_file_objects(ctx):
    """Resolve the live display selection to exact File3dm objects, once each."""
    getter = getattr(ctx, "selected_objects", None)
    try:
        selected = list(getter() or []) if callable(getter) else []
    except Exception as exc:  # noqa: BLE001
        raise document_clipboard.ClipboardFormatError(
            f"could not read the object selection: {exc}") from exc
    if not selected:
        raise document_clipboard.ClipboardFormatError("nothing is selected")
    by_name = {}
    for fobj in ctx.model.file.Objects:
        name = fobj.Attributes.Name or ""
        if name:
            by_name.setdefault(name.casefold(), fobj)
    objects = []
    seen = set()
    for display in selected:
        name = getattr(display, "name", None)
        fobj = by_name.get(str(name).casefold()) if name else None
        if fobj is None:
            raise document_clipboard.ClipboardFormatError(
                f"selected object {name!r} is not in the document")
        key = str(fobj.Attributes.Id)
        if key not in seen:
            seen.add(key)
            objects.append(fobj)
    if not objects:
        raise document_clipboard.ClipboardFormatError("nothing is selected")
    return objects


def _set_document_clipboard(ctx, text):
    """Write text through a context hook, Blender, or the headless process fallback.

    An explicit context hook is authoritative: if it fails, Cut must not quietly claim
    success through a different clipboard and delete the sources.
    """
    setter = getattr(ctx, "set_clipboard_text", None)
    if callable(setter):
        try:
            accepted = setter(text)
        except Exception as exc:  # noqa: BLE001
            return False, f"clipboard write failed: {exc}"
        if accepted is False:
            return False, "clipboard write was refused"
        document_clipboard.remember_process_text(text)
        return True, None
    try:
        import bpy

        bpy.context.window_manager.clipboard = text
    except Exception:  # noqa: BLE001 -- no Blender/window manager: use process fallback
        document_clipboard.remember_process_text(text)
        return True, None
    document_clipboard.remember_process_text(text)
    return True, None


def _get_document_clipboard(ctx):
    """Read current text through a context hook, Blender, or the process fallback."""
    getter = getattr(ctx, "get_clipboard_text", None)
    if callable(getter):
        try:
            text = getter()
        except Exception as exc:  # noqa: BLE001
            raise document_clipboard.ClipboardFormatError(
                f"clipboard read failed: {exc}") from exc
        if not isinstance(text, str):
            raise document_clipboard.ClipboardFormatError(
                "clipboard does not contain text")
        return text
    try:
        import bpy

        text = bpy.context.window_manager.clipboard
    except Exception:  # noqa: BLE001 -- no Blender/window manager
        text = document_clipboard.process_text()
    if not isinstance(text, str):
        raise document_clipboard.ClipboardFormatError("clipboard is empty")
    return text


def _unique_clipboard_name(preferred, occupied, fallback):
    base = str(preferred or fallback)
    if base.casefold() not in occupied:
        occupied.add(base.casefold())
        return base
    candidate = f"{base} Copy"
    number = 2
    while candidate.casefold() in occupied:
        candidate = f"{base} Copy {number}"
        number += 1
    occupied.add(candidate.casefold())
    return candidate


def _rehydrate_clipboard_geometry(geometry):
    """Give a decoded master/proxy solid a fresh master, never the source's registry key."""
    from . import kernel
    import uuid

    if not kernel.master_key_of(geometry):
        return
    # CommonObject faithfully preserved the source key. In the same process that key still
    # resolves to the SOURCE master, so make it deliberately stale before using the normal
    # save/reload rehydration path. The recipe tree is the self-contained exact geometry.
    geometry.SetUserString(kernel.MASTER_KEY, f"clipboard-{uuid.uuid4().hex}")
    if not kernel.recipe_of(geometry):
        raise document_clipboard.ClipboardFormatError(
            "an analytic clipboard solid has no construction recipe; faceted paste is refused")
    if not bridge.rehydrate_master(geometry):
        raise document_clipboard.ClipboardFormatError(
            "the analytic clipboard solid could not be rehydrated; faceted paste is refused")


def _paste_document_fragment(ctx, fragment):
    """Apply one validated fragment, returning its fresh display names."""
    import uuid

    file = ctx.model.file
    for item in fragment.objects:
        _rehydrate_clipboard_geometry(item.geometry)

    ctx.note_change("Paste")
    material_indices = {}

    def material_index(key):
        if key is None:
            return -1
        if key in material_indices:
            return material_indices[key]
        material = fragment.materials[key]
        material.Id = uuid.uuid4()
        index = int(file.Materials.Add(material))
        if index < 0:
            raise RuntimeError(f"Paste: could not add material {material.Name!r}")
        material_indices[key] = index
        return index

    layer_indices = {}

    def layer_index(key):
        if key in layer_indices:
            return layer_indices[key]
        source = fragment.layers[key]
        wanted = (source.layer.Name or "").casefold()
        for index in range(len(file.Layers)):
            if (file.Layers[index].Name or "").casefold() == wanted:
                layer_indices[key] = index
                return index
        parent_index = layer_index(source.parent_key) if source.parent_key else None
        layer = source.layer
        layer.Id = uuid.uuid4()
        try:
            layer.ParentLayerId = (file.Layers[parent_index].Id if parent_index is not None
                                   else uuid.UUID(int=0))
        except Exception:  # noqa: BLE001 -- older rhino3dm may not expose the setter
            pass
        if source.material_key is not None:
            try:
                layer.RenderMaterialIndex = material_index(source.material_key)
            except Exception:  # noqa: BLE001 -- preserve every available layer field
                pass
        index = int(file.Layers.Add(layer))
        if index < 0:
            raise RuntimeError(f"Paste: could not create layer {layer.Name!r}")
        layer_indices[key] = index
        return index

    for item in fragment.objects:
        layer_index(item.layer_key)

    occupied_groups = {
        (file.Groups.FindIndex(index).Name or "").casefold()
        for index in range(len(file.Groups))
    }
    group_indices = {}
    for key, group in fragment.groups.items():
        group.Id = uuid.uuid4()
        group.Name = _unique_clipboard_name(group.Name, occupied_groups, "Group")
        index = len(file.Groups)
        file.Groups.Add(group)
        if len(file.Groups) != index + 1:
            raise RuntimeError(f"Paste: could not create group {group.Name!r}")
        group_indices[key] = index

    occupied_objects = {
        (fobj.Attributes.Name or "").casefold()
        for fobj in file.Objects if fobj.Attributes.Name
    }
    pasted = []
    for item in fragment.objects:
        attributes = item.attributes
        attributes.Id = uuid.uuid4()
        attributes.Name = _unique_clipboard_name(
            attributes.Name, occupied_objects, type(item.geometry).__name__)
        attributes.LayerIndex = layer_indices[item.layer_key]
        if item.material_key is not None:
            attributes.MaterialIndex = material_index(item.material_key)
            attributes.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject
        attributes.RemoveFromAllGroups()
        for key in item.group_keys:
            attributes.AddToGroup(group_indices[key])
        object_id = file.Objects.Add(item.geometry, attributes)
        if str(object_id) == str(uuid.UUID(int=0)):
            raise RuntimeError(f"Paste: could not add object {attributes.Name!r}")
        pasted.append(attributes.Name)

    for name in pasted:
        selection_state.record_created(ctx.model, name)

    rebuild = getattr(ctx, "rebuild_display", None)
    if callable(rebuild):
        rebuild()
    selector = getattr(ctx, "select_by_names", None)
    if callable(selector):
        try:
            selector(pasted, already_expanded=True)
        except TypeError:
            selector(pasted)
    return pasted


def _apply_document_fragment(ctx, fragment):
    """Apply a fragment transactionally so an insertion failure leaves no document rows."""
    before = ctx.model.snapshot()
    material_ids = {
        str(ctx.model.file.Materials[index].Id)
        for index in range(len(ctx.model.file.Materials))
    }
    try:
        return _paste_document_fragment(ctx, fragment)
    except Exception:
        ctx.model.restore(before)
        materials = ctx.model.file.Materials
        added_ids = [
            materials[index].Id
            for index in range(len(materials))
            if str(materials[index].Id) not in material_ids
        ]
        for material_id in added_ids:
            materials.Delete(material_id)
        raise


def _remove_clipboard_sources(ctx, objects):
    """Delete captured File3dm objects after and only after a successful clipboard write."""
    ctx.note_change("Cut")
    removed = 0
    for fobj in objects:
        if ctx.model.remove_object(str(fobj.Attributes.Id)):
            removed += 1
    if removed != len(objects):
        raise RuntimeError(
            f"Cut: removed only {removed} of {len(objects)} captured object(s)")
    rebuild = getattr(ctx, "rebuild_display", None)
    if callable(rebuild):
        rebuild()
    return removed


class CopyToClipboardCmd(Command):
    name = "CopyToClipboard"
    description = "Copy selected document objects to the clipboard."
    steps = [("Select objects to copy:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = False

    def execute(self, ctx):
        try:
            objects = _clipboard_selected_file_objects(ctx)
            text = document_clipboard.encode_fragment(ctx.model.file, objects)
            written, error = _set_document_clipboard(ctx, text)
            if not written:
                return f"CopyToClipboard: {error}", True
        except (document_clipboard.ClipboardFormatError, RuntimeError) as exc:
            return f"CopyToClipboard: {exc}", True
        return f"Copied {len(objects)} object(s) to the document clipboard", True


class CutCmd(Command):
    name = "Cut"
    description = "Copy selected document objects to the clipboard, then delete them."
    steps = [("Select objects to cut:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def execute(self, ctx):
        try:
            objects = _clipboard_selected_file_objects(ctx)
            text = document_clipboard.encode_fragment(ctx.model.file, objects)
            written, error = _set_document_clipboard(ctx, text)
            if not written:
                return f"Cut: {error}; no objects were deleted", True
            removed = _remove_clipboard_sources(ctx, objects)
        except (document_clipboard.ClipboardFormatError, RuntimeError) as exc:
            return f"Cut: {exc}", True
        return f"Cut {removed} object(s) to the document clipboard", True


class PasteCmd(Command):
    name = "Paste"
    description = "Paste a Narwhal3D document fragment from the clipboard."
    steps = []

    def __init__(self):
        super().__init__()
        self.pasted_names = ()

    def _execute(self, ctx):
        text = _get_document_clipboard(ctx)
        fragment = document_clipboard.decode_fragment(text)
        pasted = _apply_document_fragment(ctx, fragment)
        self.pasted_names = tuple(pasted)
        return f"Pasted {len(pasted)} object(s): {', '.join(pasted)}", True

    def execute(self, ctx):
        # Keep error handling outside the record. If application fails after its first table
        # insertion, `_recorded` abandons the undo entry after the transaction rolls back.
        try:
            return self._recorded(ctx, lambda: self._execute(ctx))
        except (document_clipboard.ClipboardFormatError, RuntimeError) as exc:
            self.pasted_names = ()
            if "clipboard is empty" in str(exc):
                # MEASURED (research_preview_14 R9 Paste), with the product name left out
                return "Unable to paste, no objects, images or text on the clipboard.", True
            return f"Paste: {exc}", True


class DeleteCmd(Command):
    name = "Delete"
    description = "Delete selected objects."
    # the command behavior -- "Select objects or subobjects." Ask for the
    # selection (a set-step) instead of refusing when it is empty; a pre-selection
    # fills it and runs immediately, an empty confirm cancels (point 4).
    steps = [("Select objects to delete:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True

    def begin(self, ctx):
        # with control points selected, Delete removes THOSE points
        # (the command behavior: "Select objects or subobjects"), never the curve they
        # belong to -- even when the curve itself is selected (its points show because
        # it is the one selected curve).
        try:
            from . import ui

            grips = getattr(ui, "selected_control_points", None)
        except ImportError:           # no UI (headless callers): objects only
            grips = None
        if grips is not None and grips():
            return self._recorded(
                ctx, lambda: (ui.delete_selected_control_points(ctx), True))
        return super().begin(ctx)

    def execute(self, ctx: NarwhalContext):
        import bpy

        sel = ctx.selected_objects()
        if not sel:
            return "Nothing selected", True
        # snapshot before anything is destroyed, so Delete is undoable
        ctx.note_change("Delete")
        names = {ob.name for ob in sel}
        for ob in sel:
            data = ob.data
            bridge.note_display_changed([ob.name], removed=bridge.is_app_drawn(ob))
            bpy.data.objects.remove(ob, do_unlink=True)   # from every collection
            if data and data.users == 0:
                bpy.data.meshes.remove(data)
        # remove from the model file too (match by name) — File3dmObjectTable
        # has no Remove/RemoveAt; Delete(id) is the only removal API.
        # The Ids come from the UI's shared document rows in ONE pass: looking each name
        # up with `object_id_by_name` walked the whole table PER NAME (
        # a large production model: 55 ms for one curve late in the table; Select All x 5,702).
        try:
            from . import ui

            ids = ui.object_ids_for_names(names)
        except Exception:  # noqa: BLE001  (no UI: headless callers)
            ids = {n: ctx.model.object_id_by_name(n) for n in names}
        removed = 0
        for name in names:
            oid = ids.get(name)
            if oid is not None and ctx.model.remove_object(oid):
                removed += 1
        return f"Deleted {removed} object(s)", True


class UndoCmd(Command):
    # the command behavior: "The Undo command reverses recent changes."
    # Shortcut Ctrl+Z, menu Edit > Undo. The help documents NO command-line
    # options for it, so none are declared -- that is the faithful answer, not an
    # oversight (the same call as `Point`).
    #
    # the reference CAD application's own limit is MEMORY, not a step count: "The number of commands you
    # can undo depends on the memory size reserved for undo" (the command behavior) and
    # the command behavior exposes exactly one undo setting, "Max memory used
    # (MB)". is a record count -- see undo.DEFAULT_LIMIT, flagged
    name = "Undo"
    description = "Reverse the most recent change."
    steps = []
    #: Undo navigates history rather than changing the document, so it must run
    #: OUTSIDE a record -- the stack refuses to undo while one is open.
    records_undo = False

    def execute(self, ctx: NarwhalContext):
        if not ctx.undo.can_undo:
            return "Nothing to undo", True
        name = ctx.undo_last()
        if name is None:
            return "Nothing to undo", True
        # MEASURED (Research13 #123/#176): "Undoing Line", "Undoing TweenCurves".
        return f"Undoing {name}", True


class RedoCmd(Command):
    # the command behavior: "The Redo command steps back through geometry
    # changes activated by the Undo command." Shortcut Ctrl+Y, menu Edit > Redo.
    # "Redo is available immediately after you use Undo" -- so a fresh change
    # invalidates the redo chain, which `UndoStack.end` implements by clearing it.
    name = "Redo"
    description = "Re-apply the most recently undone change."
    steps = []
    #: Like Undo, this navigates history and must run outside a record.
    records_undo = False

    def execute(self, ctx: NarwhalContext):
        # MEASURED (Research13 #123): "Nothing to redo." / "Redoing Line".
        if not ctx.undo.can_redo:
            return "Nothing to redo.", True
        name = ctx.redo_last()
        if name is None:
            return "Nothing to redo.", True
        return f"Redoing {name}", True


class GumballNumericCmd(Command):
    """The Gumball's numeric entry: a handle was CLICKED, so type a value.

    the command behavior, "Common operations": "Numeric input -- Click on any
    handle. Type a number for distance, rotation angle, or scale factor."

    Deliberately **not** in the command registry. It is not a the reference CAD application command name
    the user can type -- it is the prompt a click on a handle raises -- and
    registering it would make `Gumball...` autocomplete to something the reference CAD application has no
    command for. `ui` builds an instance, fills in the frame, and installs it as
    the active command so the existing command-bar prompt/input plumbing drives it
    for free.

    `name` is "Gumball" so the undo record reads like the drag equivalent.
    """

    name = "Gumball"
    description = "Type a value for the clicked Gumball handle."
    steps = [("Value:", NUMBER)]

    def __init__(self):
        super().__init__()
        # filled in by ui._gumball_numeric_begin before the prompt is shown
        self.mode = None
        self.axis = None
        self.origin = None
        self.axis_dir = None
        self.plane_axes = None
        #: Shift at submit reverses the value. (the command behavior):
        #: "While typing the distance, pressing Shift + /Enter/Space/Tab moves the
        #: object in the opposite direction."
        self.reverse = False

    def configure(self, mode, axis, origin, axis_dir, plane_axes=None):
        """Attach the clicked handle's frame, and set the prompt to match it."""
        self.mode = mode
        self.axis = axis
        self.origin = origin
        self.axis_dir = axis_dir
        self.plane_axes = plane_axes
        prompt = gumball.numeric_prompt(mode, axis) or "Value:"
        self.steps = [(prompt, NUMBER)]
        return self

    def execute(self, ctx: NarwhalContext):
        value = self.args[0]
        if self.reverse:
            value = -value
        kind = gumball.numeric_kind(self.mode)
        if kind is None:
            return gumball.numeric_not_available_message(self.mode), True
        if not ctx.selected_objects():
            return "Gumball: nothing selected", True

        if kind == gumball.NUMERIC_DISTANCE:
            if value == 0.0:
                return "Gumball: a distance of 0 changes nothing", True
            xform = bridge.translation_xform(
                gumball.translation(self.axis_dir, value))
        elif kind == gumball.NUMERIC_ANGLE:
            if value % 360.0 == 0.0:
                return "Gumball: an angle of 0 changes nothing", True
            xform = bridge.rotation_xform(math.radians(value), self.axis_dir,
                                          self.origin)
        else:  # a scale factor
            if value == 0.0:
                return "Gumball: a scale factor of 0 would flatten the object", True
            if value == 1.0:
                return "Gumball: a scale factor of 1 changes nothing", True
            if self.mode == gumball.SCALE_2D:
                if not self.plane_axes:
                    return "Gumball: that handle has no plane to scale in", True
                a, b = self.plane_axes
                xform = bridge.scale_2d_xform(value, a, b, self.origin)
            elif self.mode == gumball.SCALE_3D:
                xform = bridge.scale_3d_xform(value, self.origin)
            else:
                xform = bridge.scale_1d_xform(value, self.axis_dir, self.origin)

        moved = apply_transform(ctx, xform)
        if not moved:
            return "Gumball: nothing was transformed", True
        return gumball.drag_label(self.mode, self.axis, value), True


class GumballDragStrengthCmd(Command):
    """`Drag Strength` from the Gumball menu, asked for on the command line.

    (the command behavior, Gumball menu): "Drag Strength -- Sets the amount
    of the gumball drag as a percentage of mouse movement to control the speed of the
    gumball movement. The default 100% moves the gumball consistently with the mouse.
    To fine-tune placing objects (such as with control point editing) set the
    strength lower, so large hand/mouse movements result in smaller gumball changes."

    the reference CAD application puts this on a slider in the Gumball menu. We have no slider, so it is
    typed -- which is where every other numeric value in this app is entered. The
    name is the reference CAD application has no `GumballDragStrength` command, so this is deliberately
    NOT in the registry (see `_build_registry`) and cannot be typed as a command;
    it is only ever raised by the menu, exactly like `GumballNumericCmd`.
    """

    name = "GumballDragStrength"
    description = "Set the Gumball drag strength, as a percentage of mouse movement."
    steps = [("Drag strength (%):", "number")]

    def execute(self, ctx: NarwhalContext):
        from . import gumball as gb

        value = gb.clamp_drag_strength(self.args[0])
        if value is None:
            return "Gumball: that is not a drag strength", True
        try:
            from . import ui

            got = ui.set_gumball_drag_strength(value)
        except Exception:  # noqa: BLE001
            return "Gumball: no viewport session", True
        if got is None:
            return "Gumball: that is not a drag strength", True
        note = ""
        if got != float(self.args[0]):
            note = (f" (clamped to {gb.MIN_DRAG_STRENGTH:g}-"
                    f"{gb.MAX_DRAG_STRENGTH:g})")
        return f"Gumball drag strength {got:g}%{note}", True


class GumballAlignmentCmd(Command):
    # CPlane, Object, and World match the alignment choices exposed by the toolbar.
    # View is also declared so unsupported use receives an explicit response. This
    # command is registered directly because it has no standalone command-reference entry.
    name = "GumballAlignment"
    description = "Set which frame the Gumball's axes follow."
    steps = []
    records_undo = False        # changes the widget, not the document
    options = (
        opts.Option("CPlane", opts.MODE,
                    help=("Gumball widgets align to the construction plane xyz "
                          "directions.")),
        opts.Option("Object", opts.MODE,
                    help="Gumball widgets align to the object."),
        opts.Option("World", opts.MODE,
                    help="Gumball widgets align to world xyz directions."),
        opts.Option("View", opts.MODE,
                    help=("Gumball widgets align to the view plane of the "
                          "current viewport.")),
        opts.Option("Cycle", opts.MODE,
                    help="Step to the next alignment (CPlane, Object, World)."),
    )

    # MEASURED (research_preview_14 R8): "Gumball alignment <CPlane> ( CPlane  Object  World
    # View  Cycle ):"; Enter keeps the current alignment.
    def begin(self, ctx):
        try:
            from . import ui

            self._current = ui.gumball_alignment()
        except Exception:  # noqa: BLE001
            return "GumballAlignment: no viewport session", True
        return None, False

    @property
    def prompt(self):
        if self.pending_option is not None:
            return super().prompt
        return opts.format_prompt(f"Gumball alignment <{getattr(self, '_current', 'CPlane')}>:",
                                  self.options, self.opt)

    def _feed(self, text, ctx):
        if not (text or "").strip() and self.pending_option is None:
            return None, True
        return super()._feed(text, ctx)

    def on_mode(self, name, ctx):
        if name == "Cycle":
            order = ("CPlane", "Object", "World")
            cur = getattr(self, "_current", "CPlane")
            name = order[(order.index(cur) + 1) % 3] if cur in order else "CPlane"
        if not gumball.alignment_is_implemented(name):
            return gumball.alignment_not_implemented_message(name), True
        try:
            from . import ui

            now = ui.set_gumball_alignment(name)
        except Exception:  # noqa: BLE001
            return "GumballAlignment: no viewport session", True
        if now is None:
            return gumball.alignment_not_implemented_message(name), True
        return f"Gumball aligned to {now}", True


class PointsOnCmd(Command):
    # Display editable control points or grips for the selected objects. The command is
    # also available through F10, the Control Points menu, and the ``POn`` alias.
    name = "PointsOn"
    description = "Display the control points of the selected objects."
    # the command behavior -- "Select objects." Ask for the selection
    # (a set-step) instead of refusing; a pre-selection fills it and runs
    # immediately, an empty confirm cancels.
    steps = [("Select objects to show points on:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    records_undo = False        # a display state, not a document change

    def execute(self, ctx: NarwhalContext):
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return "PointsOn: no viewport session", True
        sel = [ob.name for ob in ctx.selected_objects()]
        if not sel:
            return "PointsOn: select objects first", True
        # Report per object, because only curves can be shown: a surface's control
        # points are a 2-D grid we do not traverse yet, and saying nothing would
        # look like the object simply had no points.
        # ONE document walk, not `geom_by_name` per name (Select All on a 5,702-object
        # file was 5,702 walks and died at a 5 GB cap); same split, tested equal.
        split = getattr(ui, "split_by_control_points", None)
        if split is not None:
            shown, empty = split(sel)
        else:                    # a stand-in `ui` (headless): the same split, one walk
            first = {}
            for fobj in ctx.model.file.Objects:
                first.setdefault((fobj.Attributes.Name or "").lower(), fobj.Geometry)
            shown, empty = [], []
            for name in sel:
                geom = first.get(name.lower())
                (shown if geom is not None and bridge.control_points(geom)
                 else empty).append(name)
        ui.set_points_on(shown, True)
        if not shown:
            return (f"PointsOn: no control points we can display on "
                    f"{', '.join(empty)} (only NURBS curves so far)"), True
        # MEASURED (research_all_13 note 111): with a preselection PointsOn runs with NO
        # message. Only the honesty note about unsupported objects is printed.
        msg = None
        if empty:
            msg = (f"PointsOn: not supported yet for {', '.join(empty)} "
                   f"(only NURBS curves so far)")
        return msg, True


class PointsOffCmd(Command):
    # the command behavior: shortcut F11, alias `POff`, menu
    # Edit > Control Points > Control Points Off.
    name = "PointsOff"
    description = "Turn off the display of control points."
    # MEASURED (Research13 #110): the reference CAD application's PointsOff never asks for objects -- with or
    # without a selection it runs on the naming Enter. With nothing selected every
    # object's points go off; a pre-selection limits it to those objects (OURS: the reference CAD application's
    # scope with a selection was not separately measured).
    steps = []
    records_undo = False

    def execute(self, ctx: NarwhalContext):
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return "PointsOff: no viewport session", True
        sel = [ob.name for ob in ctx.selected_objects()]
        # MEASURED (research_all_13 note 110): PointsOff prints nothing.
        if sel:
            ui.set_points_on(sel, False)
            return None, True
        ui.clear_points_on()
        return None, True


class SelAllCmd(Command):
    """`SelAll` / `SelNone` / `Invert` -- one class, three registered names.

    built the decision logic as a pure table (`sel_commands.COMMANDS`,
    name -> function of (all names, current selection)). This is the wiring: it
    supplies the live document and selection, and applies the result.

    One class rather than three because the three differ ONLY in which pure
    function they call; `sel_key` picks it.
    """

    name = "SelAll"
    description = "Select all objects."
    steps = []
    records_undo = False        # selection is not a document change
    sel_key = "selall"

    def execute(self, ctx: NarwhalContext):
        fn = sel_commands.COMMANDS.get(self.sel_key)
        if fn is None:
            return f"{self.name}: not implemented yet", True
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return f"{self.name}: no viewport session", True
        # every NAMED object in the document -- the document is the source of
        # truth, not the Blender scene, which also holds display-only objects.
        # MEASURED (Research13 #76/#134): SelAll and Invert only ever select
        # VISIBLE, unlocked objects; hidden and locked ones are never added.
        all_names = [f.Attributes.Name for f in ctx.model.file.Objects
                     if f.Attributes.Name
                     and f.Attributes.Mode not in (r3.ObjectMode.Hidden,
                                                   r3.ObjectMode.Locked)]
        current = [ob.name for ob in ctx.selected_objects()]
        wanted = fn(all_names, current)
        found = ui.apply_selection(wanted)
        if self.sel_key == "selnone":
            return None, True
        return _added_to_selection(ctx, list(found), current), True


class SelNoneCmd(SelAllCmd):
    name = "SelNone"
    description = "Deselect all objects."
    sel_key = "selnone"


class InvertCmd(SelAllCmd):
    name = "Invert"
    description = "Select everything that is not selected, and deselect the rest."
    sel_key = "invert"


class _SelKindCmd(Command):
    """The six object-type selection filters -- one class, six names.

    (`the command reference --raw selection_commands`, all six redirect there):

        SelCrv       "selects all curves."
        SelSrf       "selects all surfaces."
        SelPolysrf   "selects all polysurfaces."
        SelPt        "selects all point objects."
        SelClosedCrv "selects all closed curves."
        SelOpenCrv   "selects all open curves."

    Zero-step: each runs on the naming Enter, like `SelAll`. It scans the
    WHOLE document and sets the selection to exactly the objects of its kind.

    CLASSIFY by the rhino3dm geometry type, per the implementation spec (rule 1: measure
    the real API, do not guess):

        curve          `isinstance(geom, r3.Curve)`
        point          `isinstance(geom, r3.Point)`
        surface        `isinstance(geom, r3.Brep) and len(Faces) == 1`
        polysurface    `isinstance(geom, r3.Brep) and len(Faces) > 1`
        closed curve   a curve with `IsClosed` truthy  (property, not a method)
        open curve     a curve with `IsClosed` falsy

    ADD-vs-REPLACE (a deliberate call, flagged for the user): the help text says
    each command "selects all <kind>" -- it sets the selection to that kind, it
    does NOT append to whatever was already selected. We therefore REPLACE, via
    `select_by_names` (the same mechanism `SelAllCmd` uses). This is the faithful
    reading of the help: each command selects exactly its kind rather than adding to
    the existing selection.
    to replace, and replace is the safe, faithful behaviour (no stale selection
    survives), so that is what ships.
    """

    steps = []
    records_undo = False        # selection is not a document change
    _kinds = None               # tuple of kind labels this command selects

    @staticmethod
    def _classify(geom):
        """Return the set of kind labels a geometry matches.

        A closed circle is both a `curve` and a `closed_curve`; an open line is a
        `curve` and an `open_curve`; a single-face brep is a `surface`; a
        multi-face brep is a `polysurface`; a point is a `point`. Anything else
        (a mesh we do not produce) matches none of these six filters.
        """
        import rhino3dm as r3
        kinds = set()
        if isinstance(geom, r3.Curve):
            kinds.add("curve")
            try:
                kinds.add("closed_curve" if geom.IsClosed else "open_curve")
            except Exception:  # noqa: BLE001 -- never let one odd curve kill the scan
                pass
        elif isinstance(geom, r3.Point):
            kinds.add("point")
        elif isinstance(geom, r3.Brep):
            try:
                n = len(geom.Faces)
            except Exception:  # noqa: BLE001
                n = 0
            kinds.add("surface" if n == 1 else ("polysurface" if n > 1 else None))
            kinds.discard(None)
        return kinds

    def execute(self, ctx: NarwhalContext):
        wanted = []
        for fobj in ctx.model.file.Objects:
            name = fobj.Attributes.Name
            if not name:
                continue
            if self._kinds and (self._kinds & self._classify(fobj.Geometry)):
                wanted.append(name)
        before = [ob.name for ob in ctx.selected_objects()]
        try:
            ctx.select_by_names(wanted)
        except Exception:  # noqa: BLE001 -- headless stubs always provide this
            pass
        return _added_to_selection(ctx, wanted, before), True


class SelCrvCmd(_SelKindCmd):
    name = "SelCrv"
    description = "Select all curves."
    _kinds = {"curve"}


class SelSrfCmd(_SelKindCmd):
    name = "SelSrf"
    description = "Select all surfaces (single-face breps)."
    _kinds = {"surface"}


class SelPolysrfCmd(_SelKindCmd):
    name = "SelPolysrf"
    description = "Select all polysurfaces (multi-face breps)."
    _kinds = {"polysurface"}


class SelPtCmd(_SelKindCmd):
    name = "SelPt"
    description = "Select all point objects."
    _kinds = {"point"}


class SelClosedCrvCmd(_SelKindCmd):
    name = "SelClosedCrv"
    description = "Select all closed curves."
    _kinds = {"closed_curve"}


class SelOpenCrvCmd(_SelKindCmd):
    name = "SelOpenCrv"
    description = "Select all open curves."
    _kinds = {"open_curve"}


def _geometry_fingerprint(geom):
    """A hashable fingerprint of a geometry's SHAPE and POSITION, or None.

    Two objects are "geometrically identical... in the same location" (the
    `SelDup` wording) when their fingerprints match. The fingerprint
    is built from:

    * the rhino3dm class name (a curve is never a point, a point never a brep);
    * for curves and meshes, the ordered world-space CONTROL-POINT / VERTEX
      list, read through the existing `bridge.control_points` helper (which
      de-homogenizes weights and handles the `LineCurve` / `PolylineCurve` /
      `PolyCurve` shapes the codebase's own `PointsOn` uses);
    * for points, the location;
    * for breps, the face/edge/vertex counts, the per-face planarity list,
      and the TIGHT bounding box (`analysis.bounding_box`, the same tight-box
      route `BoundingBoxCmd` and `Move`'s Enter-default use). A brep's shape
      is read from its analytic faces and edges, so an analytic solid and a
      mesh-proxy of a DIFFERENT shape are not conflated; a brep whose
      faces we cannot read returns None rather than a guess.

    rhino3dm geometries have no value equality (`a == b` is always
    False, `__hash__` is identity-based), so the fingerprint is built from
    coordinates. It is a FINGERPRINT, not a proof of identity: two genuinely
    different shapes can share a bounding box (a circle and a square both span
    -5..5), which is why curves and meshes carry their point list and breps
    their face list in addition.
    """
    from . import analysis

    try:
        if isinstance(geom, r3.Curve):
            pts = bridge.control_points(geom)
            return (type(geom).__name__, tuple((p[0], p[1], p[2]) for p in pts))
        if isinstance(geom, r3.Point):
            loc = geom.Location
            return (type(geom).__name__, (loc.X, loc.Y, loc.Z))
        if isinstance(geom, r3.Brep):
            faces = [bool(f.IsPlanar()) for f in geom.Faces]
            lo, hi = analysis.bounding_box(geom)
            return (type(geom).__name__,
                    (len(geom.Vertices), len(geom.Edges), len(geom.Faces)),
                    tuple(faces),
                    (lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z))
        if isinstance(geom, r3.Mesh):
            try:
                pts = [(p.X, p.Y, p.Z) for p in geom.Vertices]
            except Exception:  # noqa: BLE001
                pts = None
            if pts is None:
                return None
            return (type(geom).__name__, tuple(pts))
        return None
    except Exception:  # noqa: BLE001 -- one unreadable object never kills the scan
        return None


class SelDupCmd(Command):
    """`SelDup` -- select objects duplicated onto themselves.

    (`the command reference SelDup` -> the command behavior;
    the page's section is "Select Objects > Duplicate Objects"):

        "The SelDup command selects objects that are geometrically identical
        with another object, visible, and in the same location regardless of
        other object properties."

    So this is a SELECTION command (zero-step, like `SelAll`/`SelCrv`), not a
    duplication command: it finds objects that have a geometrically identical
    twin at the same location -- the "a copy left on top of the original" case.
    It scans the WHOLE document, groups the VISIBLE named objects by their
    geometry fingerprint, and selects EXACTLY the objects that share a
    fingerprint with at least one OTHER object. A single unique object is NOT
    selected. Layer, colour, mode and every other property are ignored, per
    the wording.

    (flagged): rhino3dm has no value equality, so "geometrically
    identical" is decided by `_geometry_fingerprint` -- type + ordered control
    points (curves, meshes) / face list + tight box (brep) / location (point).
    That is the strongest signal the rhino3dm API provides without a kernel.
    """

    name = "SelDup"
    description = ("Select objects geometrically identical with another object "
                   "in the same location.")
    steps = []
    records_undo = False        # selection is not a document change

    def execute(self, ctx: NarwhalContext):
        groups = {}
        for fobj in ctx.model.file.Objects:
            name = fobj.Attributes.Name
            if not name:
                continue
            # "visible" -- hidden objects are out of scope, and a
            # hidden twin does not make a visible object a duplicate.
            if fobj.Attributes.Mode == r3.ObjectMode.Hidden:
                continue
            fp = _geometry_fingerprint(fobj.Geometry)
            if fp is None:
                continue
            groups.setdefault(fp, []).append(name)
        # MEASURED (Research13 #137): two identical lines -> "Found 1 duplicate."
        # and ONE curve selected. The reference CAD application leaves one member of each identical set
        # unselected. OURS: the kept member is the FIRST in document order (the
        # original); which member the reference CAD application keeps was not measured.
        wanted = [name for names in groups.values() if len(names) >= 2
                  for name in names[1:]]
        try:
            ctx.select_by_names(wanted)
        except Exception:  # noqa: BLE001 -- headless stubs always provide this
            pass
        n = len(wanted)
        if n == 0:
            return "No duplicates found. Nothing added to the selection.", True
        noun = "duplicate" if n == 1 else "duplicates"
        objects = "object" if n == 1 else "objects"
        return f"Found {n} {noun}. {n} {objects} added to selection.", True


class SelNameCmd(Command):
    """`SelName` -- add visible, unlocked objects matching a name pattern.

    (`the command reference SelName --raw`): the scriptable command supports
    only ``?`` and ``*`` wildcard characters.  Name matching follows this app's
    case-insensitive object-name lookup convention; no fuzzy matching is added.
    Each eligible document row is retained, so duplicate display names select
    every matching object rather than being treated as an ambiguity.
    """

    name = "SelName"
    description = "Select objects by name."
    steps = [("Object name:", TEXT)]
    records_undo = False

    def execute(self, ctx: NarwhalContext):
        pattern = str(self.args[0]) if self.args else ""
        if pattern == "":
            return "SelName: no object name or pattern given", True

        file = ctx.model.file
        names = property_select.named_object_names(
            file.Objects, pattern, normal_mode=r3.ObjectMode.Normal)
        if not names:
            return f"SelName: no visible, unlocked object matching {pattern!r}", True
        return _apply_selection(ctx, names, False, "SelName")


class SetObjectNameCmd(Command):
    """`SetObjectName` -- assign a document name to every selected object.

    (the command behavior): object names are entered after
    selection; `""` removes the name.  The documented `AppendCounter` option
    changes a multi-selection to ``name 1``, ``name 2``,...; its nested `No`
    behavior is the default.  Names are deliberately not treated as unique:
    CAD SDK's `ObjectAttributes.Name` documentation says more than one
    object may have the same name.
    """

    name = "SetObjectName"
    description = "Assign a name to selected objects."
    steps = [("Select objects:", OBJECTS_MULTI), ("Object name:", TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("AppendCounter", opts.TOGGLE, default=False,
                    help="Append ` 1`, ` 2`, ... to names in a multi-selection."),
    )

    def execute(self, ctx: NarwhalContext):
        if len(self.args) < 2:
            return "SetObjectName: select objects, then type a name", True
        selected = self.args[0]
        if not isinstance(selected, (list, tuple)):
            selected = [selected]
        objects = []
        seen = set()
        for supplied in selected:
            for fobj in _objects_named(ctx, str(supplied)):
                oid = str(fobj.Attributes.Id)
                if oid not in seen:
                    seen.add(oid)
                    objects.append(fobj)
        if not objects:
            return "SetObjectName: no selected objects", True

        supplied = str(self.args[1]).strip()
        # the command help says to type two quote characters to remove
        # the object name, rather than using an empty command-line answer.
        new_name = "" if supplied == '""' else supplied
        append = bool(self.opt.get("AppendCounter"))
        names = ([f"{new_name} {i}" for i in range(1, len(objects) + 1)]
                 if append and new_name else [new_name] * len(objects))
        old_names = [f.Attributes.Name or "" for f in objects]
        if old_names == names:
            return "SetObjectName: names are unchanged", True

        ctx.note_change(self.name)
        for fobj, object_name in zip(objects, names):
            fobj.Attributes.Name = object_name

        # The persisted ObjectAttributes are authoritative.  If a live context
        # exposes its normal rebuild hook, use it rather than mutating Blender
        # objects directly (display-only names must never be the source of truth).
        rebuild = getattr(ctx, "rebuild_display", None)
        if callable(rebuild):
            try:
                rebuild()
            except Exception:  # noqa: BLE001 -- headless/fake contexts have no display
                pass
        noun = "object" if len(objects) == 1 else "objects"
        return f"SetObjectName: named {len(objects)} {noun}", True


class SelGroupCmd(Command):
    """`SelGroup` -- add visible, unlocked members of an exact named group.

    (`the command reference SelGroup --raw`, section
    `the command behavior`): "The SelGroup command selects groups
    by name."  The name lookup is exact and case-sensitive, and this command has
    no documented options.  Selection commands add to the existing selection;
    this command never follows overlapping groups beyond the named group.
    """

    name = "SelGroup"
    description = "Select groups by exact name."
    steps = [("Group name:", TEXT)]
    records_undo = False

    def execute(self, ctx: NarwhalContext):
        supplied = str(self.args[0]) if self.args else ""
        if not supplied:
            return "SelGroup: no group name given", True

        file = ctx.model.file
        groups = [file.Groups.FindIndex(i) for i in range(len(file.Groups))]
        matches = property_select.exact_group_indices(groups, supplied)
        if not matches:
            return f"SelGroup: group {supplied!r} was not found", True
        if len(matches) > 1:
            return (f"SelGroup: group name {supplied!r} is ambiguous "
                    f"({len(matches)} matches)"), True

        names = property_select.group_member_names(
            file.Objects, matches[0], normal_mode=r3.ObjectMode.Normal)
        if not names:
            return (f"SelGroup: group {supplied!r} has no visible, unlocked "
                    "members"), True
        return _apply_selection(ctx, names, False, "SelGroup")


class _SelUserTextCmd(Command):
    """Shared wiring for the SelKey/SelValue family."""

    records_undo = False
    _kind = None
    _value_prompt = None

    def execute(self, ctx: NarwhalContext):
        if self._kind == "keyvalue":
            key = str(self.args[0]) if self.args else ""
            value = str(self.args[1]) if len(self.args) > 1 else ""
            if not key or not value:
                return f"{self.name}: key and value are required", True
        else:
            pattern = str(self.args[0]) if self.args else ""
            if not pattern:
                return f"{self.name}: a pattern is required", True
            key = pattern
            value = None

        names = property_select.user_text_object_names(
            ctx.model.file.Objects, self._kind, key, value,
            normal_mode=r3.ObjectMode.Normal)
        if not names:
            if self._kind == "keyvalue":
                detail = f"key {key!r} and value {value!r}"
            else:
                detail = f"{self._kind} pattern {key!r}"
            # MEASURED (research_preview_14 R6 SelKeyValue/SelValue): "No objects added to
            # selection." when nothing matches.
            return "No objects added to selection.", True
        return _apply_selection(ctx, names, False, self.name)


class SelKeyCmd(_SelUserTextCmd):
    """`SelKey` -- select objects by their user attribute key.

    (the command behavior): `?`, `*`, and `~`
    wildcard characters are supported.  Hidden period-prefixed keys are still
    read from live ObjectAttributes: command selection is not Properties listing.
    """

    name = "SelKey"
    description = "Select objects by their user attribute key."
    # DERIVED from SelKeyValue's measured "Enter text key:" (research_preview_14 R6).
    steps = [("Enter text key:", TEXT)]
    _kind = "key"


class SelValueCmd(_SelUserTextCmd):
    """`SelValue` -- select objects by their user attribute value."""

    name = "SelValue"
    description = "Select objects by their user attribute value."
    # MEASURED (research_preview_14 R6): "Enter text value:".
    steps = [("Enter text value:", TEXT)]
    _kind = "value"


class SelKeyValueCmd(_SelUserTextCmd):
    """`SelKeyValue` -- select an exact same-row key/value pair.

    help supports `?`, `*`, and `~`; the key and value predicates are
    deliberately ANDed on one row, never ORed across the object's table.
    """

    name = "SelKeyValue"
    description = "Select objects by a same-row user attribute key and value."
    # MEASURED (research_preview_14 R6): "Enter text key:", then "Enter text value:".
    steps = [("Enter text key:", TEXT), ("Enter text value:", TEXT)]
    _kind = "keyvalue"


class SelLayerCmd(Command):
    """`SelLayer` -- select all objects on a layer.

    (`the command reference --raw selection_commands` -> `#SelLayer`, section
    "Select by Object Property > By Layer"):

        "The SelLayer command selects all objects on a layer."

    The scriptable form takes the layer name on the command line, with the
    syntax:

        -SelLayer LayerA::Layer01   sublayer names use double colons
        -SelLayer *                 `*` and `?` wildcards are supported
        -SelLayer LayerA::*
        -SelLayer Layer?01
        -SelLayer "Layer 01"        names containing spaces are quoted

    (The interactive "Pick an object in the model to specify the layer" form
    is the GUI's job; this command takes the name form, which is also
    the headless-testable one.) A layer name is dynamic, so it is a TEXT step
    ("Layer name:") rather than a pre-declared option.

    `execute` selects EXACTLY the objects whose `Attributes.LayerIndex` is one
    of the layers whose name matches (case-insensitive; `*`/`?` via `fnmatch`,
    which is the same two wildcards the reference CAD application's help names). A name that matches NO
    layer is REFUSED by name (the explicit-refusal policy); a matched layer holding no objects
    selects nothing and says so -- it does not look like it selected something
    it did not. Selecting the parent name does not reach a sublayer's objects
    (the help lists the sublayer's own name for that), matching the syntax rather than inventing parent-inclusion.
    """

    name = "SelLayer"
    description = "Select all objects on a layer."
    steps = [("Layer name:", TEXT)]
    records_undo = False        # selection is not a document change

    def execute(self, ctx: NarwhalContext):
        import fnmatch

        raw = (self.args[0] if self.args else "").strip()
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]                 # names with spaces are quoted
        if not raw:
            return "SelLayer: no layer name given", True

        layers = list(ctx.model.file.Layers)
        pattern = raw.lower()
        matches = [i for i, layer in enumerate(layers)
                   if fnmatch.fnmatchcase((layer.Name or "").lower(), pattern)]
        if not matches:
            have = ", ".join(layer.Name or "?" for layer in layers)
            return (f"SelLayer: no layer matching {raw!r} "
                    f"(the document has: {have})"), True

        wanted = []
        for fobj in ctx.model.file.Objects:
            nm = fobj.Attributes.Name
            if not nm:
                continue
            if int(fobj.Attributes.LayerIndex) in matches:
                wanted.append(nm)
        try:
            ctx.select_by_names(wanted)
        except Exception:  # noqa: BLE001 -- headless stubs always provide this
            pass
        n = len(wanted)
        noun = "object" if n == 1 else "objects"
        which = ", ".join(layers[i].Name or "?" for i in sorted(matches))
        return f"SelLayer: {n} {noun} selected on {which}", True


def _apply_selection(ctx, names, deselect: bool, base: str):
    """Select `names`, honouring `DeselectOthersBeforeSelect`.

    With `deselect` (the `DeselectOthersBeforeSelect` option, Yes), the
    new set REPLACES the current selection. Without it (the default, No), the
    new set is APPENDED to whatever is already selected -- the two are unioned
    in name order, existing first, so the user's current picks keep their order
    and the freshly-named objects are added after them. The selection is applied
    through `ctx.select_by_names`, the single choke point that also records the
    change for `SelPrev`.
    """
    current = []
    getter = getattr(ctx, "selected_objects", None)
    if callable(getter):
        try:
            current = [o.name for o in getter()]
        except Exception:  # noqa: BLE001 -- headless: no current selection
            current = []
    if deselect:
        wanted = [str(n) for n in names]
    else:
        seen = {str(c).lower() for c in current}
        wanted = [str(c) for c in current]
        wanted += [str(n) for n in names if str(n).lower() not in seen]
    try:
        found = ctx.select_by_names(wanted)
    except Exception:  # noqa: BLE001 -- headless stubs always provide this
        found = list(wanted)
    # MEASURED (research_all_13 #142, #148): "1 curve added to selection.".
    return _added_to_selection(ctx, [str(n) for n in names],
                               [] if deselect else current), True


class SelLastCmd(Command):
    """`SelLast` -- select the last created objects.

    (`the command reference --raw selection_commands` -> `#SelLast`, section
    "Select Objects > Last Created Objects"):

        "The SelLast command selects the last changed objects, including
         importing objects."

    Option: `DeselectOthersBeforeSelect` (a Yes/No TOGGLE). When Yes, the last
    created objects REPLACE the current selection; when No (the default), they
    are APPENDED to it.

    "Last created" is state the document cannot tell us -- it only shows the
    current objects, not which were just added by a drawing command -- so it is
    recorded in `selection_state` as objects are created through
    `add_geometry` (the single choke point for creation). `SelLast` selects
    EXACTLY that set. If nothing has been created this session it is REFUSED by
    name (the explicit-refusal policy): an empty "last created" set is not a thing to select.

    (flagged): the wording is "last changed objects, INCLUDING
    IMPORTING objects". Imported-via-Open objects are NOT recorded here (they
    arrive from a file, not from `add_geometry`), so this implementation
    covers app-created objects only. That is the honest, testable subset; the
    import route is a separate concern and is not silently pretended to.
    """

    name = "SelLast"
    description = "Select the last created objects."
    steps = []
    records_undo = False        # selection is not a document change
    options = (
        opts.Option("DeselectOthersBeforeSelect", opts.TOGGLE, default=False,
                    help="When Yes, replace the current selection with the "
                         "last created objects; when No (default), add them "
                         "to it."),
    )

    def unknown_option_message(self, text: str):
        return (f"{self.name}: unknown option {text!r} "
                f"(option: DeselectOthersBeforeSelect)")

    def execute(self, ctx: NarwhalContext):
        created = selection_state.last_batch(ctx.model)
        if not created:
            return ("SelLast: nothing has been created this session to select "
                    "(the 'last created objects' set is empty)"), True
        deselect = bool(self.opt.get("DeselectOthersBeforeSelect"))
        return _apply_selection(ctx, created, deselect, "SelLast")


class SelPrevCmd(Command):
    """`SelPrev` -- re-select the previous selection set.

    (`the command reference --raw selection_commands` -> `#SelPrev`, section
    "Select Objects > Previous Selection"):

        "The SelPrev command re-selects the previous selection set of objects
         or sub-objects."

    Option: `DeselectOthersBeforeSelect` (a Yes/No TOGGLE). When Yes, the
    previous set REPLACES the current selection; when No (the default), it is
    APPENDED to it.

    The "previous selection" is state the document cannot tell us at all -- a
    selection is not in the document, it is a view of it -- so it is recorded
    in `selection_state` as the selection changes: the OLD current set becomes
    the new "previous" every time `select_by_names` runs (the single choke
    point for selection changes). `SelPrev` re-selects that set.

    With no prior selection there is nothing to re-select, so it is REFUSED by
    name (the explicit-refusal policy) -- an empty "previous" is not a thing to select.
    """

    name = "SelPrev"
    description = "Re-select the previous selection set."
    steps = []
    records_undo = False        # selection is not a document change
    options = (
        opts.Option("DeselectOthersBeforeSelect", opts.TOGGLE, default=False,
                    help="When Yes, replace the current selection with the "
                         "previous selection; when No (default), add it to "
                         "it."),
    )

    def unknown_option_message(self, text: str):
        return (f"{self.name}: unknown option {text!r} "
                f"(option: DeselectOthersBeforeSelect)")

    def execute(self, ctx: NarwhalContext):
        prev = selection_state.previous_selection(ctx.model)
        if prev is None:
            return ("SelPrev: there is no previous selection to re-select "
                    "(a selection has never been made)"), True
        deselect = bool(self.opt.get("DeselectOthersBeforeSelect"))
        return _apply_selection(ctx, prev, deselect, "SelPrev")


def _selection_noun(geom, count):
    """The reference CAD application's object noun for a selection count. MEASURED (research_all_13 note 138):
    "3 curves added to selection."; the other nouns are OURS (the reference CAD application's own type names)."""
    if isinstance(geom, r3.Curve):
        noun = "curve"
    elif isinstance(geom, r3.Brep):
        noun = "surface" if len(geom.Faces) == 1 else "polysurface"
    elif isinstance(geom, r3.Extrusion):
        noun = "extrusion"
    elif isinstance(geom, r3.Mesh):
        noun = "mesh"
    elif isinstance(geom, r3.Point):
        noun = "point"
    else:
        noun = "object"
    if count == 1:
        return noun
    return "meshes" if noun == "mesh" else noun + "s"


def _added_to_selection(ctx, names, before=()):
    """The selection report for the objects in `names` that were NOT already selected.

    MEASURED (research_all_13 #134, #136, #138, #142, #148, #150): "3 curves added to
    selection.", "1 surface added to selection.", "No objects added to selection.". Mixed
    kinds use "objects" (OURS)."""
    prior = {n.lower() for n in before}
    new = [n for n in names if n.lower() not in prior]
    lookup = getattr(ctx, "geom_by_name", None)
    if not callable(lookup):
        if not new:
            return "No objects added to selection."
        return f"{len(new)} object{'s' if len(new) != 1 else ''} added to selection."
    geoms = [g for g in (lookup(n) for n in new) if g is not None]
    if not geoms:
        return "No objects added to selection."
    if len({_selection_noun(g, 1) for g in geoms}) == 1:
        return f"{len(geoms)} {_selection_noun(geoms[0], len(geoms))} added to selection."
    return f"{len(geoms)} objects added to selection."


class SelectCmd(Command):
    """`Select` -- MEASURED (research_all_13 note 138): the prompt is "Select objects:";
    typing `All` selects every selectable object ("3 curves added to selection.") and the
    prompt STAYS open until Enter. A typed object name adds that object. `None` clears
    the selection (OURS: the reference CAD application's selection keyword, not measured in this command)."""

    name = "Select"
    description = "Select objects: All, None, or object names; Enter when done."
    steps = [("Select objects:", OBJECTS_MULTI)]
    records_undo = False        # selection is not a document change

    def _selected_names(self, ctx):
        getter = getattr(ctx, "selected_objects", None)
        return [o.name for o in (getter() or [])] if callable(getter) else []

    def begin(self, ctx):
        # OURS, unchanged from the earlier app: Select starts from an empty selection.
        # How the reference CAD application treats a PRIOR selection was not measured;
        # within one run every answer adds to the selection ("added to selection").
        selector = getattr(ctx, "select_by_names", None)
        if callable(selector):
            selector([])
        return super().begin(ctx)

    def _added_message(self, ctx, names):
        return _added_to_selection(ctx, names)

    def _feed_objects_multi_step(self, text, ctx):
        word = (text or "").strip()
        if not word:
            return None, True
        current = self._selected_names(ctx)
        if word.lower() == "all":
            candidates = [f.Attributes.Name for f in ctx.model.file.Objects
                          if f.Attributes.Name and f.Attributes.Mode == r3.ObjectMode.Normal]
        elif word.lower() == "none":
            ctx.select_by_names([])
            return None, False
        else:
            if ctx.geom_by_name(word) is None:
                return f"No object named {word!r}", False
            candidates = [word]
        added = [n for n in candidates if n not in current]
        ctx.select_by_names(current + added)
        return self._added_message(ctx, added), False

    def execute(self, ctx: NarwhalContext):
        return None, True


class ZoomCmd(Command):
    # the reference CAD application has no `ZoomExtents` -- the real command is `Zoom`
    # (the command behavior) with mode options. `Extents` (frame everything) and
    # `Selected` (frame the selection) are implemented; the remaining documented
    # modes are declared so the prompt shows what the reference CAD application shows, and `on_mode`
    # says plainly that they are not implemented rather than silently doing the
    # default and appearing to work.
    name = "Zoom"
    description = ("Frame objects in the viewport. "
                   "Options: Extents / Selected / All / 1to1 / Dynamic "
                   "(Extents and Selected implemented).")
    steps = []
    options = (
        opts.Option("Extents", opts.MODE, help="Frame all objects."),
        opts.Option("Selected", opts.MODE, help="Frame the selected objects."),
        opts.Option("All", opts.MODE, help="Zoom all viewports."),
        opts.Option("1to1", opts.MODE, help="Set a 1:1 screen scale."),
        opts.Option("Dynamic", opts.MODE, help="Interactive zoom."),
    )

    def on_mode(self, name, ctx):
        if name == "All":
            # (the command behavior): "All -- Zooms all viewports", with its own
            # Extents / Selected /... sub-options (ZEA = '_Zoom _All _Extents).
            self._all = True
            return "Zoom: all viewports -- Extents or Selected", False
        if name == "Extents":
            return self._zoom(selected=False)
        if name == "Selected":
            return self._zoom(selected=True)
        return f"Zoom: the {name} option is not implemented yet", True

    def _zoom(self, selected: bool):
        """Frame the selection or all visible objects in the active viewport.

        After ``All``, every viewport is framed. The active-pane lookup is used instead of
        Blender's first area so the viewport receiving the command is the one that moves.
        """
        import bpy

        every = getattr(self, "_all", False)
        if selected and not bridge.selected_display_objects():
            return ("Unable to zoom some views - no objects are selected." if every
                    else "Unable to zoom - no objects are selected."), True
        visible = [o for o in bpy.context.view_layer.objects if bridge.display_visible(o)]
        if not selected and not visible:
            return ("Unable to zoom some views - no objects are visible." if every
                    else "Unable to zoom - no objects are visible."), True
        try:
            from . import ui
            areas = ui._view3d_areas() if every else [ui.active_pane_area()]
        except Exception:  # noqa: BLE001
            areas = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"][:1]
        areas = [a for a in areas if a is not None]
        if not areas:
            return "No 3D viewport", True
        for area in areas:
            region = next((r for r in area.regions
                           if r.type == "WINDOW" and r.alignment == "NONE"), None)
            if region is None:
                continue
            # Blender's framing operators only see what Blender draws; curves are not
            with bridge.blender_sees_curves(), \
                    bpy.context.temp_override(area=area, region=region):
                if selected:
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                else:
                    bpy.ops.view3d.view_all(use_all_regions=False, center=False)
        what = "selection" if selected else "extents"
        return (f"Zoomed all viewports to {what}" if every else f"Zoomed to {what}"), True

    def execute(self, ctx: NarwhalContext):
        # a bare `Zoom` does Extents, which is the reference CAD application's default behaviour
        return self._zoom(selected=False)


class _NewValueMixin:
    """MEASURED (research_preview_14 R7): the setting commands do not act on their own --
    they show the state and wait: "<state>. New value ( On  Off  Toggle ):" (or Enable /
    Disable / Toggle). An option sets it; Enter keeps it. Subclasses give `_state_text()`
    (None when there is no viewport session) and their own report line from `on_mode`."""

    def _state_text(self):
        raise NotImplementedError

    @property
    def prompt(self):
        if self.pending_option is not None:
            return super().prompt
        text = self._state_text()
        return opts.format_prompt(f"{text}. New value:", self.options, self.opt) if text else ""

    def begin(self, ctx):
        if self._state_text() is None:
            return f"{self.name}: no viewport session", True
        return None, False

    def _feed(self, text, ctx):
        if not (text or "").strip() and self.pending_option is None:
            return None, True
        return super()._feed(text, ctx)


class GumballCmd(_NewValueMixin, Command):
    # the Gumball transform widget. the command behavior documents the
    # command itself as a visibility switch -- "Click the Gumball pane in the
    # status bar to toggle visibility of the Gumball transform widget" -- with the
    # command-line options "On / Off / Toggle: Turns the Gumball widget on or off
    # for the selected object, or toggles between on and off."
    #
    # We have no status bar yet, so this command IS the switch. The widget's
    # handles (Move / Rotate / Scale1D / origin) are implemented; Move-2D, Extrude,
    # Cut, the menu ball, numeric entry and the modifier behaviours are not, and
    # `gumball.ALIGNMENTS` records that only the documented CPlane alignment is
    # built. `GumballSettings` is a separate the reference CAD application command and is deliberately NOT
    # declared here, since declaring a command we do not implement would make it
    # look available (the explicit-refusal policy).
    name = "Gumball"
    description = "Turn the Gumball transform widget on or off."
    steps = []
    options = (
        opts.Option("On", opts.MODE,
                    help="Turns the Gumball widget on for the selected object."),
        opts.Option("Off", opts.MODE,
                    help="Turns the Gumball widget off."),
        opts.Option("Toggle", opts.MODE,
                    help="Toggles the Gumball widget between on and off."),
    )

    def _state_text(self):
        try:
            from . import ui
            return f"Gumball is {'on' if ui.gumball_enabled() else 'off'}"
        except Exception:  # noqa: BLE001
            return None

    def unknown_option_message(self, text: str):
        # the three options are a small fixed set, so naming the valid
        # ones (not just the rejected one) tells the user exactly what to type.
        return (f"{self.name}: unknown option {text!r} "
                f"(options: On, Off, Toggle)")

    def _set(self, mode: str):
        """Apply On/Off/Toggle. `ui` is imported lazily -- it imports this module."""
        try:
            from . import ui
        except Exception:  # noqa: BLE001
            return "Gumball: no viewport to show the widget in", True
        try:
            if mode == "On":
                want = True
            elif mode == "Off":
                want = False
            else:
                want = not ui.gumball_enabled()
            now = ui.set_gumball(want)
        except Exception:  # noqa: BLE001
            return "Gumball: no viewport to show the widget in", True
        return None, True                 # MEASURED (R7 Gumball): silent

    def on_mode(self, name, ctx):
        if name in ("On", "Off", "Toggle"):
            return self._set(name)
        return f"Gumball: the {name} option is not implemented yet", True

    def execute(self, ctx: NarwhalContext):
        # The help describes the command as toggling visibility, so a bare
        # `Gumball` toggles.
        return self._set("Toggle")


class CPlaneCmd(Command):
    # the `CPlane` command -- set the origin/orientation of the active
    # viewport's construction plane (the command behavior). the reference CAD application's own words:
    # "Pick a new origin point or choose an option." So the DEFAULT action is a
    # point (the variable-point path), and the documented options are what is in
    # `options`. We implement the parts that reduce to an already-tested
    # transformation in `cplane.py` (World / Elevation / pick-origin); the rest
    # are declared for a faithful prompt and reported as not implemented (rule
    # 4) -- an unimplemented option must never look implemented.
    name = "CPlane"
    description = ("Set the origin and orientation of the active viewport's "
                   "construction plane. Pick a new origin point or choose an "
                   "option. (World / Elevation implemented.)")
    steps = []
    variable_points = True
    # MEASURED (research_preview_14 R7 CPlane): "CPlane origin <0.000,0.000,0.000> ( All=No
    # Curve  Elevation  Gumball  Object  Rotate  Surface  Through  View  World  3Point  Undo
    # Redo ):"; an origin click ENDS the command; World asks "Choose world CPlane ( Back
    # Bottom  Front  Left  Right  Top )" and the choice ends it.
    WORLD_CHOICES = ("Back", "Bottom", "Front", "Left", "Right", "Top")
    options = (
        opts.Option("All", opts.TOGGLE, default=False,
                    help="Move the origin of the construction planes in ALL viewports "
                         "(not implemented yet)."),
        opts.Option("Curve", opts.MODE, help="Orient the CPlane perpendicular to a curve."),
        opts.Option("Elevation", opts.VALUE, value_kind="number",
                    help="Move the origin along the plane's normal (up positive)."),
        opts.Option("Gumball", opts.MODE, help="Match the gumball of the selected object."),
        opts.Option("Object", opts.MODE, help="Set the CPlane to an object."),
        opts.Option("Rotate", opts.MODE, help="Rotate the construction plane about an axis."),
        opts.Option("Surface", opts.MODE, help="Match a surface (kept tangent)."),
        opts.Option("Through", opts.MODE, help="Move the CPlane so it passes through a point."),
        opts.Option("View", opts.MODE, help="Make the CPlane parallel to the view."),
        opts.Option("World", opts.MODE,
                    help="Restore one of the six named world construction planes."),
        opts.Option("3Point", opts.MODE, help="Set the CPlane from three picked points."),
        opts.Option("Undo", opts.MODE, help="Restore the previously used CPlane."),
        opts.Option("Redo", opts.MODE, help="Re-apply the CPlane that was undone."),
    )

    def begin(self, ctx):
        o = tuple(self._active_cplane(ctx).origin)
        self._origin_text = ",".join(f"{float(v):.3f}" for v in o)
        self._choosing_world = False
        return super().begin(ctx)

    def variable_prompt(self, n):
        return f"CPlane origin <{getattr(self, '_origin_text', '0.000,0.000,0.000')}>:"

    @property
    def prompt(self):
        if getattr(self, "_choosing_world", False) and self.pending_option is None:
            return "Choose world CPlane ( " + "  ".join(self.WORLD_CHOICES) + " ):"
        return super().prompt

    def _feed(self, text, ctx):
        if getattr(self, "_choosing_world", False) and self.pending_option is None:
            raw = (text or "").strip().lower()
            hits = [c for c in self.WORLD_CHOICES if raw and c.lower().startswith(raw)]
            if len(hits) != 1:
                return "Choose one of: " + ", ".join(self.WORLD_CHOICES), False
            self._choosing_world = False
            msg, _done = self._apply(cplane.WORLD_CPLANES[hits[0]], ctx)
            return msg, True
        return super()._feed(text, ctx)

    def _apply(self, plane, ctx):
        """Make `plane` the active viewport's CPlane, or report that there was no
        viewport to apply it to (headless) -- never claim success on failure."""
        if not ctx.set_active_cplane(plane):
            return ("CPlane: no active viewport to set the construction plane "
                    "in", True)
        return None, False

    def on_option_set(self, option, value, ctx):
        if option.name == "All" and value:
            self.opt["All"] = False
            return "CPlane: All=Yes is not implemented yet", False
        if option.name == "Elevation":
            plane = self._active_cplane(ctx).elevated(value)
            return self._apply(plane, ctx)
        return None          # unimplemented options never reach here

    def on_point(self, point, ctx):
        # the reference CAD application's default action: "Pick a new origin point" -- move the origin
        # there, keeping orientation, and keep the command running so you can
        # pick again or choose an option.
        plane = self._active_cplane(ctx).moved_to(point)
        msg, _done = self._apply(plane, ctx)
        return msg, True                 # MEASURED (R7): the origin click ends CPlane

    def on_mode(self, name, ctx):
        if name == "World":
            self._choosing_world = True
            return None, False
        # Every MODE option we did not implement: say so, change nothing.
        return f"{self.name}: the {name} option is not implemented yet", True

    def execute(self, ctx: NarwhalContext):
        # Enter accepts the shown origin: nothing changes.
        return None, True


class NamedCPlaneCmd(Command):
    # the `NamedCPlane` command -- save the active viewport's CPlane
    # under a name and restore it later (the command behavior). In the reference CAD application
    # this is a PANEL, not a point-driven command, so there is no default
    # point action; the actions are the panel's documented toolbar/context
    # menu items. We implement the parts that reduce to the pure
    # `named_cplanes` model (Save As / Restore / Rename / Delete / List / the
    # Name Ascending/Descending sorts); the clipboard (.3dm Import, Copy, Paste)
    # and the panel-drag (Move up / Move down) affordances are NOT modelled and
    # are reported honestly (the explicit-refusal policy -- an unimplemented option never looks
    # implemented).
    #
    # The stored value is `cplane.CPlane` (the implementation is explicit: keep CPlane
    # as the stored value, do not invent a second representation). The list
    # itself lives on the context (the session's NamedCPlanes collection), so
    # Save/Restore across command invocations persists.
    name = "NamedCPlane"
    description = ("Manage the Named CPlanes list: save the active viewport's "
                   "construction plane under a name and restore it later. "
                   "(SaveAs / Restore / Rename / Delete / List / Sort "
                   "implemented.)")
    steps = []
    variable_points = False
    options = (
        # -- implemented: a thin call to the named_cplanes model ------------
        opts.Option("SaveAs", opts.VALUE, value_kind="text",
                    help="Save the active viewport's construction plane under "
                         "this name (panel: Save As)."),
        opts.Option("Restore", opts.VALUE, value_kind="text",
                    help="Restore a named CPlane to the active viewport "
                         "(panel: Restore / double-click)."),
        opts.Option("Rename", opts.VALUE, value_kind="text",
                    help="Rename an existing named CPlane: enter OldName->NewName "
                         "(panel: Rename Named CPlane)."),
        opts.Option("Delete", opts.VALUE, value_kind="text",
                    help="Delete the named CPlane with this name "
                         "(panel: Delete)."),
        opts.Option("Sort", opts.ENUM,
                    choices=("Ascending", "Descending"),
                    help="Sort the list by name (panel: Sort By Name "
                         "Ascending / Name Descending)."),
        # -- declared for a faithful prompt; not implemented yet ------------
        opts.Option("List", opts.MODE,
                    help="List the named CPlanes (the panel's default view)."),
    )

    # -- the session's named-CPlane list (the panel) ------------------------

    def _list(self, ctx):
        """The session's NamedCPlanes collection, creating it on first use.

        Lives on the context (the session) so Save/Restore persist across
        command invocations. A context without one (a minimal test fake) gets
        a fresh empty collection, which is the honest starting state.
        """
        lst = getattr(ctx, "named_cplanes", None)
        if lst is None:
            lst = named_cplanes.NamedCPlanes()
            ctx.named_cplanes = lst
        return lst

    def _active(self, ctx):
        """The active viewport's CPlane, or None (headless / no viewport)."""
        getter = getattr(ctx, "active_cplane", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001
            return None

    # -- the implemented actions ------------------------------------------

    def _save_as(self, name, ctx):
        plane = self._active(ctx)
        if plane is None:
            return ("NamedCPlane: no active viewport to save a construction "
                    "plane from", True)
        lst = self._list(ctx)
        try:
            lst.save(name, plane)
        except ValueError as e:
            return str(e), True
        return f"Saved the active viewport's CPlane as {name!r}", True

    def _restore(self, name, ctx):
        lst = self._list(ctx)
        plane = lst.get(name)
        if plane is None:
            # a gone name is reported, not silent (- the help is silent)
            return (f"NamedCPlane: no named CPlane called {name!r} to restore "
                    f"(known: {', '.join(lst.names()) or 'none'})", True)
        if not ctx.set_active_cplane(plane):
            return ("NamedCPlane: no active viewport to restore the CPlane "
                    "into", True)
        return f"Restored {name!r} to the active viewport", True

    def _delete(self, name, ctx):
        lst = self._list(ctx)
        try:
            lst.delete(name)
        except ValueError as e:
            return str(e), True
        return f"Deleted the named CPlane {name!r}", True

    def _rename(self, old, new, ctx):
        lst = self._list(ctx)
        try:
            lst.rename(old, new)
        except ValueError as e:
            return str(e), True
        return f"Renamed {old!r} to {new!r}", True

    @staticmethod
    def _split_rename(value):
        """`OldName->NewName` -> (old, new); a ValueError if malformed."""
        text = str(value).strip()
        if "->" not in text:
            raise ValueError(
                f"Rename expects OldName->NewName (got {value!r})")
        old, _, new = text.partition("->")
        old, new = old.strip(), new.strip()
        if not old or not new:
            raise ValueError("Rename needs both an old and a new name "
                             "(OldName->NewName)")
        return old, new

    def _sort(self, ascending, ctx):
        lst = self._list(ctx)
        order = lst.sorted_names(ascending)
        if not order:
            return "NamedCPlane: there are no named CPlanes to sort", True
        return (f"Named CPlanes (by name, "
                f"{'ascending' if ascending else 'descending'}): "
                + ", ".join(order), True)

    def _list_view(self, ctx):
        lst = self._list(ctx)
        names = lst.names()
        if not names:
            return ("NamedCPlane: there are no named CPlanes yet (use SaveAs "
                    "to save the active viewport's CPlane)", True)
        return "Named CPlanes: " + ", ".join(names), True

    # -- command hooks ------------------------------------------------------

    def on_option_set(self, option, value, ctx):
        name = option.name
        if name == "SaveAs":
            return self._save_as(value, ctx)
        if name == "Restore":
            return self._restore(value, ctx)
        if name == "Delete":
            return self._delete(value, ctx)
        if name == "Rename":
            try:
                old, new = self._split_rename(value)
            except ValueError as e:
                return str(e), True
            return self._rename(old, new, ctx)
        if name == "Sort":
            return self._sort(value == "Ascending", ctx)
        return None          # unknown option value: fall back to the framework

    def on_mode(self, name, ctx):
        if name == "List":
            return self._list_view(ctx)
        return f"{self.name}: the {name} option is not implemented yet", True

    def execute(self, ctx: NarwhalContext):
        # The panel's default view: show the list (there is no point action).
        return self._list_view(ctx)


def _document_alias_table(ctx):
    """Return the alias table attached to this command's document/context.

    's ``AliasTable`` is document-scoped state.  Headless contexts used by
    command tests have no document adapter, so the context itself is the small
    persistence boundary.  No global mutable table is used by these commands.
    """
    table = getattr(ctx, "alias_table", None)
    if table is None:
        model = getattr(ctx, "model", None)
        table = getattr(model, "alias_table", None)
    if table is None:
        table = alias_table.AliasTable()
        try:
            ctx.alias_table = table
        except AttributeError:
            model = getattr(ctx, "model", None)
            if model is None:
                raise RuntimeError("no document available for alias settings")
            model.alias_table = table
    return table


class OptionsImportCmd(Command):
    """Import aliases from a text file, not the rest of the reference CAD application's Options.

    (options/aliases): ``OptionsImport`` restores selected Options
    settings and is the documented way to share aliases.  deliberately
    bounds that command to the alias table already modeled by """
    name = "OptionsImport"
    description = "Import aliases from a text file (Alias scope only)."
    steps = [("Alias file path:", TEXT)]

    def execute(self, ctx: NarwhalContext):
        path = os.path.abspath(os.path.expanduser(self.args[0].strip().strip('"')))
        try:
            with open(path, "r", encoding="utf-8") as stream:
                text = stream.read()
        except (OSError, UnicodeError) as error:
            return (f"Alias scope: could not import {path}: {error}; "
                    "no aliases changed; other reference application options are not imported", True)

        table = _document_alias_table(ctx)
        report = table.import_text(text)
        if not report.ok:
            details = "; ".join(report.errors + report.collisions)
            return (f"Alias scope: import refused for {path}: {details}; "
                    "no aliases changed; other reference application options are not imported", True)
        count = len(report.imported)
        return (f"Alias scope: imported {count} alias(es) from {path}; "
                "other reference application options are not imported", True)


class OptionsExportCmd(Command):
    """Export this document's aliases as text, not all reference application options."""
    name = "OptionsExport"
    description = "Export aliases to a text file (Alias scope only)."
    steps = [("Alias file path:", TEXT)]

    def execute(self, ctx: NarwhalContext):
        path = os.path.abspath(os.path.expanduser(self.args[0].strip().strip('"')))
        table = _document_alias_table(ctx)
        text = table.export_text()
        try:
            with open(path, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
        except (OSError, UnicodeError) as error:
            return (f"Alias scope: could not export {path}: {error}; "
                    "other reference application options are not exported", True)
        return (f"Alias scope: exported {len(table.aliases)} alias(es) to {path}; "
                "other reference application options are not exported", True)


class SaveCmd(Command):
    name = "Save"
    description = "Save the model as .3dm. Enter a path."
    steps = [("Path:", "text")]

    def execute(self, ctx: NarwhalContext):
        path = self.args[0] or "/tmp/narwhal_model.3dm"
        if not path.endswith(".3dm"):
            path += ".3dm"
        ok = ctx.model.save(path)
        return f"Saved {path}" if ok else f"Failed to save {path}", True


class OpenCmd(Command):
    name = "Open"
    description = "Open a .3dm file (replaces current model)."
    steps = [("Path:", "text")]

    def execute(self, ctx: NarwhalContext):
        from . import state as _state

        path = self.args[0]
        if not path.endswith(".3dm"):
            path += ".3dm"
        try:
            new_model = _state.NarwhalModel.load(path)
        except FileNotFoundError as e:
            return f"Could not open {path}: {e}", True
        _state.set_model(new_model)
        # set_model built a fresh context; rebuild the viewport through THAT one,
        # not the stale `ctx` this command was invoked with, or the new document
        # would be drawn into the old context's registry.
        drawn = _state.app_state.ctx.rebuild_display()
        return (f"Opened {path} ({new_model.object_count()} objects, "
                f"{drawn} drawn)"), True


class HelpCmd(Command):
    name = "Help"
    description = "List all commands."
    steps = []

    def execute(self, ctx: NarwhalContext):
        lines = ["Commands:"]
        for cmd in COMMANDS.values():
            lines.append(f"  {cmd.name:<12} {cmd.description}")
        return "\n".join(lines), True


def _layer_state_manager(ctx):
    """Return the manager owned by this document/context, creating it once."""
    manager = getattr(ctx, "layer_state_manager", None)
    if manager is None:
        model = getattr(ctx, "model", None)
        manager = getattr(model, "layer_state_manager", None)
        if manager is None:
            manager = layer_states.LayerStateManager()
            try:
                ctx.layer_state_manager = manager
            except AttributeError:
                if model is not None:
                    model.layer_state_manager = manager
    return manager


def _current_layer_records(ctx):
    """Read the record shape from a document adapter."""
    adapter = getattr(ctx, "document", None) or getattr(ctx, "model", None) or ctx
    getter = getattr(adapter, "layer_records", None)
    if callable(getter):
        return getter()
    records = getattr(adapter, "layers", None)
    if callable(records):
        records = records()
    if records is not None:
        return records
    raise RuntimeError("LayerStateManager: document adapter has no layer_records")


def _apply_layer_records(ctx, records):
    """Apply all fields through an adapter, never just layer names."""
    adapter = getattr(ctx, "document", None) or getattr(ctx, "model", None) or ctx
    for name in ("apply_layer_records", "set_layer_records"):
        setter = getattr(adapter, name, None)
        if callable(setter):
            setter(records)
            return
    # The real model exposes the record reader and a public rhino3dm layer table.
    # Matching by Id avoids changing order and makes the merge policy hold.
    table = getattr(adapter, "file", None)
    table = getattr(table, "Layers", None)
    if table is None:
        raise RuntimeError("LayerStateManager: document adapter cannot apply layer records")
    by_id = {str(record["Id"]): record for record in records}
    for index in range(len(table)):
        layer = table[index]
        record = by_id.get(str(layer.Id))
        if record is None:
            continue
        for field in layer_states.LAYER_FIELDS[1:]:
            setattr(layer, field, record[field])


def _layer_state_note_change(ctx):
    """Request the adapter's normal document undo snapshot, when available."""
    note = getattr(ctx, "note_change", None)
    if callable(note):
        note("LayerStateManager")


class LayerStateManagerCmd(Command):
    """Command-line subset of the reference CAD application's ``LayerStateManager``.

    actions are List, Save, Restore, Delete and Rename.  The panel is
    pending UI wiring; Import (the 3dm action) is not
    implemented yet and is refused by name rather than silently approximated.
    """
    name = "LayerStateManager"
    description = ("Save, restore, rename, delete, or list layer states from the "
                   "command line; panel and .3dm import UI pending.")
    steps = []
    records_undo = False
    options = (
        opts.Option("List", opts.MODE, help="List saved layer states."),
        opts.Option("Save", opts.MODE, help="Save current layer settings."),
        opts.Option("Restore", opts.MODE, help="Restore a saved layer state."),
        opts.Option("Delete", opts.MODE, help="Delete a saved layer state."),
        opts.Option("Rename", opts.MODE, help="Rename a saved layer state."),
        opts.Option("Import", opts.MODE, help="Import a state list from a .3dm file."),
    )

    def begin(self, ctx):
        return ("LayerStateManager: choose List, Save, Restore, Delete, Rename, "
                "or Import (panel/.3dm import UI pending)"), False

    def on_mode(self, name, ctx):
        self.mode = name
        if name == "List":
            return self.execute(ctx)
        if name == "Import":
            return "LayerStateManager: .3dm import is not implemented yet; panel UI pending", True
        self.steps = [("Layer state name:", TEXT)]
        if name == "Rename":
            self.steps.append(("New layer state name:", TEXT))
        self.step_index = 0
        self.args = []
        return f"{name} layer state name:", False

    def execute(self, ctx):
        action = getattr(self, "mode", None) or "List"
        manager = _layer_state_manager(ctx)
        try:
            if action == "List":
                names = manager.list()
                return ("Layer states: " + (", ".join(names) if names else "none")), True
            if action == "Save":
                name = self.args[0]
                manager.save(name, _current_layer_records(ctx))
                return f"Saved layer state {name!r}", True
            if action == "Restore":
                name = self.args[0]
                records = manager.restore(name, _current_layer_records(ctx))
                _layer_state_note_change(ctx)
                _apply_layer_records(ctx, records)
                return f"Restored layer state {name!r}", True
            if action == "Delete":
                name = self.args[0]
                manager.delete(name)
                return f"Deleted layer state {name!r}", True
            if action == "Rename":
                old, new = self.args
                manager.rename(old, new)
                return f"Renamed layer state {old!r} to {new!r}", True
            return f"LayerStateManager: the {action} option is not implemented yet", True
        except (ValueError, RuntimeError) as error:
            return f"LayerStateManager: {error}", True


class LayersCmd(Command):
    # the reference CAD application has no `NewLayer` -- the real command is `Layer`
    # (the command behavior). We keep the create-a-layer behaviour; the command
    # is renamed to the honest the reference CAD application name.
    name = "Layer"
    description = "Create a layer. Enter a name."
    steps = [("Name:", "text")]

    def execute(self, ctx: NarwhalContext):
        name = self.args[0] or f"Layer{ctx.model.object_count() + 1}"
        idx = ctx.model.add_layer(name)
        return f"Created layer {name!r} (index {idx})", True


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def _snap_settings():
    """The live Osnap settings, or None when there is no UI (headless tests).

    `ui` imports `commands`, so importing it at module scope would be circular --
    the same lazy-import shape `NarwhalContext.active_cplane` already uses.
    """
    try:
        from . import ui

        return ui.snap_settings()
    except Exception:  # noqa: BLE001
        return None


def _snap_state_line(settings):
    """A readable summary of which snaps are on -- what `Osnap` reports."""
    from . import osnap as _osnap

    on = sorted(settings.enabled)
    parts = [f"Osnap: {', '.join(on) if on else 'none enabled'}"]
    if settings.disabled:
        parts.append("Disable=On (all snaps suspended)")
    if settings.project:
        parts.append("Project=On")
    missing = settings.unimplemented_enabled()
    if missing:
        # the explicit-refusal policy: an unimplemented thing must never look implemented
        parts.append(f"not implemented yet: {', '.join(missing)}")
    return "; ".join(parts)


class OsnapCmd(Command):
    """Report and change the persistent object snaps.

    ⚠ DEVIATION, recorded rather than hidden: in the reference CAD application the individual snaps are
    checkboxes on the **Osnap control** (a panel), and the `Osnap` command merely
    "Toggles the Osnap control" -- there is no `Osnap End` command syntax, and the
    help has no per-snap commands (checked: no the command behavior/the command behavior/the command behavior...). We have
    no panel yet, so this command is a **stand-in** that exposes the same state
    through options. When the Osnap panel is built this should become the panel
    toggle it is in the reference CAD application.
    """

    name = "Osnap"
    description = ("Show or change the persistent object snaps "
                   "(stand-in for the reference CAD application's Osnap panel).")
    steps = []
    # MEASURED (research_preview_14 R8 Osnap): the command line shows "Osnap ( Endpoint=Off
    # Near=Off  Point=Off  Midpoint=Off  Center=Off  Intersection=Off  Perpendicular=Off
    # Tangent=Off  Quadrant=Off  Knot=Off  Vertex=Off  None ):" -- full names, On/Off, in
    # this order; `None` turns every snap off.
    FULL_NAMES = (("Endpoint", osnap.END), ("Near", osnap.NEAR), ("Point", osnap.POINT),
                  ("Midpoint", osnap.MID), ("Center", osnap.CEN),
                  ("Intersection", osnap.INT), ("Perpendicular", osnap.PERP),
                  ("Tangent", osnap.TAN), ("Quadrant", osnap.QUAD), ("Knot", osnap.KNOT),
                  ("Vertex", osnap.VERTEX))
    options = tuple(
        opts.Option(full, opts.TOGGLE, default=False,
                    help=f"Persistent {full} object snap.")
        for full, _mode in FULL_NAMES
    ) + (
        opts.Option("None", opts.MODE, help="Turn every persistent object snap off."),
        opts.Option("Disable", opts.TOGGLE, default=False,
                    help="Suspend all enabled persistent object snaps."),
        opts.Option("Project", opts.TOGGLE, default=False,
                    help="Project object snaps to the construction plane."),
    )

    @property
    def prompt(self):
        if self.pending_option is not None:
            return super().prompt
        shown = [f"{full}={'On' if self.opt.get(full) else 'Off'}" for full, _m in self.FULL_NAMES]
        return "Osnap ( " + "  ".join(shown + ["None"]) + " ):"

    def begin(self, ctx):
        settings = _snap_settings()
        if settings is None:
            return "Osnap: no viewport session", True
        # reflect the LIVE state into the option defaults, so the prompt shows
        # what is actually on rather than a stale copy
        for full, mode in self.FULL_NAMES:
            self.opt[full] = mode in settings.enabled
        self.opt["Disable"] = settings.disabled
        self.opt["Project"] = settings.project
        return None, False

    def _feed(self, text, ctx):
        if not (text or "").strip() and self.pending_option is None:
            return None, True
        return super()._feed(text, ctx)

    def on_mode(self, name, ctx):
        if name == "None":
            settings = _snap_settings()
            if settings is None:
                return "Osnap: no viewport session", True
            for full, mode in self.FULL_NAMES:
                settings.set(mode, False)
                self.opt[full] = False
            return None, False
        return super().on_mode(name, ctx)

    def on_option_set(self, option, value, ctx):
        settings = _snap_settings()
        if settings is None:
            return "Osnap: no viewport session", True
        if option.name == "Disable":
            settings.disabled = bool(value)
        elif option.name == "Project":
            settings.project = bool(value)
        else:
            mode = dict(self.FULL_NAMES).get(option.name, option.name)
            settings.set(mode, bool(value))
        return _snap_state_line(settings), False


class DisableOsnapCmd(_NewValueMixin, Command):
    """the reference CAD application's `DisableOsnap`: "manages the state of persistent object snaps".

    Options taken from the sibling `ProjectOsnap` topic, which documents exactly
    `Enable` / `Disable` / `Toggle` for this shape of command
    (the command behavior).
    """

    name = "DisableOsnap"
    description = "Suspend or reactivate all persistent object snaps."
    steps = []
    options = (
        opts.Option("Enable", opts.MODE, help="Suspend persistent object snaps."),
        opts.Option("Disable", opts.MODE,
                    help="Reactivate persistent object snaps."),
        opts.Option("Toggle", opts.MODE, help="Flip the suspended state."),
    )

    def _state_text(self):
        settings = _snap_settings()
        if settings is None:
            return None
        return f"Osnaps are {'disabled' if settings.disabled else 'enabled'}"

    def on_mode(self, name, ctx):
        settings = _snap_settings()
        if settings is None:
            return "DisableOsnap: no viewport session", True
        # NOTE the naming: `Enable` here means "enable the DISABLING", i.e.
        # suspend the snaps -- that is what the command is named after.
        if name == "Enable":
            settings.disabled = True
        elif name == "Disable":
            settings.disabled = False
        elif name == "Toggle":
            settings.disabled = not settings.disabled
        else:
            return f"DisableOsnap: the {name} option is not implemented yet", True
        return None, True                 # MEASURED (R7 DisableOsnap): silent


def _ui_module():
    """The ui module, or None headless (lazy: `ui` imports `commands`)."""
    try:
        from . import ui

        return ui
    except Exception:  # noqa: BLE001
        return None


class SnapCmd(Command):
    """Toggle Grid Snap, matching the reference CAD application's command and pane."""

    name = "Snap"
    description = "Toggle Grid Snap mode."
    steps = []
    records_undo = False

    def begin(self, ctx):
        ui = _ui_module()
        if ui is None:
            return "Snap: no viewport session", True
        ui.toggle_grid_snap()
        # MEASURED (research_preview_14 R7 Snap): "GridSnap is On" / "GridSnap is Off".
        return f"GridSnap is {'On' if ui.grid_snap_on() else 'Off'}", True


class SetSnapCmd(_NewValueMixin, Command):
    """Turn Grid Snap on, off, or toggle it."""

    name = "SetSnap"
    description = "Turn Grid Snap mode on, off, or toggle it."
    steps = []
    records_undo = False
    options = (
        opts.Option("On", opts.MODE, help="Turns Grid Snap on."),
        opts.Option("Off", opts.MODE, help="Turns Grid Snap off."),
        opts.Option("Toggle", opts.MODE, help="Toggles Grid Snap."),
    )

    def _state_text(self):
        ui = _ui_module()
        return None if ui is None else f"Grid snap is {'on' if ui.grid_snap_on() else 'off'}"

    def on_mode(self, name, ctx):
        ui = _ui_module()
        if ui is None:
            return "SetSnap: no viewport session", True
        if name == "On":
            ui.set_grid_snap(True)
        elif name == "Off":
            ui.set_grid_snap(False)
        elif name == "Toggle":
            ui.set_grid_snap(not ui.grid_snap_on())
        else:
            return f"SetSnap: the {name} option is not implemented yet", True
        return f"Grid snap is {'on' if ui.grid_snap_on() else 'off'}.", True


class OrthoCmd(Command):
    """the reference CAD application's `Ortho`. (the command behavior): "The Ortho command
    restricts the movement of the cursor to multiples of a specified angle from the last
    point created." / "The Ortho command, clicking Ortho in the status bar, and the F8
    key, are all toggles." Holding Shift flips it for a pick (ui.apply_ortho)."""

    name = "Ortho"
    description = "Toggle ortho: restrict picks to multiples of the ortho angle."
    steps = []
    records_undo = False

    def begin(self, ctx):
        ui = _ui_module()
        if ui is None:
            return "Ortho: no viewport session", True
        ui.toggle_ortho()
        # MEASURED (research_preview_14 R7 Ortho): "Ortho is on." / "Ortho is off.".
        return f"Ortho is {'on' if ui.ortho_on() else 'off'}.", True


class SetOrthoCmd(_NewValueMixin, Command):
    """the reference CAD application's `SetOrtho`: "turns ortho mode on, off, or toggles the current state"
    -- options On / Off / Toggle (the command behavior)."""

    name = "SetOrtho"
    description = "Turn ortho mode on, off, or toggle it."
    steps = []
    records_undo = False
    options = (
        opts.Option("On", opts.MODE, help="Turns ortho mode on."),
        opts.Option("Off", opts.MODE, help="Turns ortho mode off."),
        opts.Option("Toggle", opts.MODE, help="Toggles ortho mode."),
    )

    def _state_text(self):
        ui = _ui_module()
        return None if ui is None else f"Ortho is {'on' if ui.ortho_on() else 'off'}"

    def on_mode(self, name, ctx):
        ui = _ui_module()
        if ui is None:
            return "SetOrtho: no viewport session", True
        if name == "On":
            ui.set_ortho(True)
        elif name == "Off":
            ui.set_ortho(False)
        elif name == "Toggle":
            ui.set_ortho(not ui.ortho_on())
        else:
            return f"SetOrtho: the {name} option is not implemented yet", True
        return None, True                 # MEASURED (R7 SetOrtho): silent


class ProjectOsnapCmd(_NewValueMixin, Command):
    """the reference CAD application's `ProjectOsnap`: project snaps onto the construction plane.

    "The ProjectOsnap command projects object snaps from the actual snap point to
    the construction plane." Its three documented command-line options are
    reproduced exactly: "Enable -- Object snaps will be projected to the
    construction plane", "Disable -- Object snaps land on the object snapped to",
    and "Toggle".
    """

    name = "ProjectOsnap"
    description = "Project object snaps to the construction plane."
    steps = []
    options = (
        opts.Option("Enable", opts.MODE,
                    help="Object snaps will be projected to the construction plane."),
        opts.Option("Disable", opts.MODE,
                    help="Object snaps land on the object snapped to."),
        opts.Option("Toggle", opts.MODE, help="Flip projection on or off."),
    )

    def _state_text(self):
        settings = _snap_settings()
        if settings is None:
            return None
        return f"Osnap projection is {'enabled' if settings.project else 'disabled'}"

    def on_mode(self, name, ctx):
        settings = _snap_settings()
        if settings is None:
            return "ProjectOsnap: no viewport session", True
        if name == "Enable":
            settings.project = True
        elif name == "Disable":
            settings.project = False
        elif name == "Toggle":
            settings.project = not settings.project
        else:
            return f"ProjectOsnap: the {name} option is not implemented yet", True
        return f"Osnap projection is {'enabled' if settings.project else 'disabled'}.", True


class SnapToLockedCmd(_NewValueMixin, Command):
    """the reference CAD application's `SnapToLocked`: "snaps work on locked objects and on objects on
    locked layers" (the command behavior).

    that the DEFAULT is OFF: the command behavior documents a
    setting "Snap to locked objects -- Object snaps work on locked objects...",
    and a setting that TURNS ON a behaviour is proof the behaviour is off by
    default. So the engine excludes locked targets (osnap.best's
    `snap_to_locked`) until this command flips `SnapSettings.snap_to_locked`.

    Options: the help page gives no explicit command-line options for this
    toggle, so by-sibling -- the same Enable/Disable/Toggle shape that
    `DisableOsnap`/`ProjectOsnap` use on this same page for a boolean Osnap
    state. A bare Enter toggles, like the "Snap To Locked Objects" menu checkbox.
    """

    name = "SnapToLocked"
    description = "Allow object snaps to work on locked objects."
    steps = []
    options = (
        opts.Option("Enable", opts.MODE,
                    help="Object snaps work on locked objects."),
        opts.Option("Disable", opts.MODE,
                    help="Object snaps ignore locked objects."),
        opts.Option("Toggle", opts.MODE, help="Flip snapping to locked objects."),
    )

    def _state_text(self):
        settings = _snap_settings()
        if settings is None:
            return None
        return f"Snap to locked objects {'enabled' if settings.snap_to_locked else 'disabled'}"

    def on_mode(self, name, ctx):
        settings = _snap_settings()
        if settings is None:
            return "SnapToLocked: no viewport session", True
        if name == "Enable":
            settings.snap_to_locked = True
        elif name == "Disable":
            settings.snap_to_locked = False
        elif name == "Toggle":
            settings.snap_to_locked = not settings.snap_to_locked
        else:
            return f"SnapToLocked: the {name} option is not implemented yet", True
        state = "enabled" if settings.snap_to_locked else "disabled"
        return f"Snapping to locked objects is now {state}.", True


class ExtractWireframeCmd(Command):
    """Create wireframe curves from selected surfaces and polysurfaces.

    The command is selection-driven. ``OutputLayer`` is declared but not implemented and
    reports that explicitly when used.

    Master-backed solids return analytic OCCT edges, such as arcs and lines, rather than
    display-mesh triangle edges. The result message identifies whether edges came from the
    analytic master or directly from the rhino3dm brep.
    """

    name = "ExtractWireframe"
    description = "Create curves from the edges of selected surfaces or polysurfaces."
    steps = [("Select surfaces or polysurfaces:", OBJECTS_MULTI)]
    preselect_names = 1
    cancel_on_empty_set = True
    options = (
        opts.Option("OutputLayer", opts.MODE,
                    help="Specifies the layer for the results (Current or Input)."),
    )

    def on_mode(self, name, ctx):
        return f"ExtractWireframe: the {name} option is not implemented yet", False

    def execute(self, ctx: NarwhalContext):
        names = [ob.name for ob in ctx.selected_objects()]
        if not names:
            return "ExtractWireframe: select surfaces or polysurfaces", True

        made = 0
        sources = set()
        skipped = []
        for name in names:
            geom = ctx.geom_by_name(name)
            # Only a surface/polysurface has a wireframe to extract; a curve already IS one.
            if geom is None or getattr(geom, "Edges", None) is None:
                skipped.append(name)
                continue
            curves, source = bridge.extract_wireframe(geom)
            if not curves:
                skipped.append(name)
                continue
            sources.add(source)
            layer_index = _layer_index_of(ctx, name)
            if made == 0:
                ctx.note_change("ExtractWireframe")
            for curve in curves:
                ctx.add_geometry(curve, "Wireframe", layer_index=layer_index)
                made += 1

        if not made:
            return ("ExtractWireframe: nothing to extract -- select a surface or "
                    "polysurface (a curve is already a wireframe)"), True
        how = "+".join(sorted(sources))
        detail = ("the ANALYTIC edges of the kernel master"
                  if how == "master" else f"edges from {how}")
        return f"Extracted {made} wireframe curve(s) -- {detail}", True


class PipeCmd(Command):
    """`Pipe` -- the command behavior. an EXACT, constant-radius pipe, built by
    `bridge.pipe_occt`. a line rail is one CYLINDER face, an arc rail one
    TORUS face, each airtight when capped with the closed-form volume (a line pipe r2 h10 is
    pi*4*10; an arc rail r2 R10 is pi*4*pi*10) -- against a skinned loft that is a sample and
    can never be exact. This is rule 0's route: the curved surface is one analytic face, never
    subdivided into planes.

    steps (the command UI / the command UI): "Select curves to pipe", "Start radius",
    "End radius". The end radius defaults to the start radius (Enter accepts it) -- the
    constant-radius pipe the reference CAD application's default flow produces.

    (the help gives no default for Cap): `Cap` defaults to **Flat** -- a capped pipe
    is airtight and manufacturable, the default a solid-waiting user expects, and it is
    the one of the three Cap modes we build. `Cap=None` (open) and `Cap=Flat` are built;
    `Cap=Round` (a hemispherical cap) is refused by name, not approximated.

    Declared, not built (each reports "not implemented yet" by name when used): `Thick`
    (two walls), `Diameter/Radius`, `FitRail`, `Output`, `Multiple` (more
    than one rail), `ChainEdges`. A taper (an end radius different from the start) is built
    exactly: Global linear, Local with an S law. A refused option adds nothing. NEVER fall back to `sweep.pipe`'s loft route
    (the geometry integrity rule).
    """
    name = "Pipe"
    description = "Build an exact constant-radius pipe along a curve."
    # MEASURED (research_all_13 #105): "Start radius <1.000>" (Enter = 1), "End radius
    # <1.000>" (Enter = the start radius), then "Point for next radius. Press Enter for
    # none:" -- Enter ends it and builds the pipe; the input curve is kept. A point there
    # (an intermediate radius) is not implemented and says so.
    # The radius steps take a typed number OR a picked point. MEASURED (research_preview_14
    # P52): at "Start radius" a circle is drawn live across the rail at its START, sized by
    # the cursor's distance from the CURVE (a click at (2, 2.5) over the line (0,0,0)-
    # (4,0,0) gave 2.502); "End radius <start>" draws a second live circle at the END.
    steps = [("Select curves to pipe:", OBJECTS),
             ("Start radius <1.000>:", DISTANCE),
             ("End radius:", DISTANCE),
             ("Point for next radius. Press Enter for none:", TEXT)]
    preselect_names = 1

    def _rail_end(self, ctx_or_resolve, end):
        name = self.args[0] if self.args else None
        resolve = (ctx_or_resolve if callable(ctx_or_resolve)
                   else getattr(ctx_or_resolve, "geom_by_name", None))
        rail = resolve(name) if (resolve and name) else None
        if not isinstance(rail, r3.Curve):
            return None
        t = rail.Domain.T1 if end else rail.Domain.T0
        p, v = rail.PointAt(t), rail.TangentAt(t)
        return (p.X, p.Y, p.Z), (v.X, v.Y, v.Z)

    def _parse_distance(self, text, ctx):
        stripped = (text or "").strip()
        try:
            return parse_number(stripped)
        except ValueError:
            pass
        found = self._rail_end(ctx, self.step_index == 2)
        if found is None:
            raise ValueError("Pipe: there is no rail to measure the radius from")
        point = parse_point(stripped, self._active_cplane(ctx), found[0])
        return self._curve_distance(ctx, point)

    def _curve_distance(self, ctx_or_resolve, point):
        name = self.args[0] if self.args else None
        resolve = (ctx_or_resolve if callable(ctx_or_resolve)
                   else getattr(ctx_or_resolve, "geom_by_name", None))
        rail = resolve(name) if (resolve and name) else None
        from . import nearest
        return nearest.distance_to_curve(rail, tuple(point))     # solved, not sampled

    def preview_circles(self, resolve, cursor):
        """The live radius circle(s), across the rail at its start (and end)."""
        out = []
        for end in (False, True):
            if end and self.step_index < 2:
                break
            found = self._rail_end(resolve, end)
            if found is None:
                return []
            center, tangent = found
            if end or self.step_index == 1:
                live = len(self.args) == (3 if end else 2)
                r = (float(self.args[2 if end else 1]) if live
                     else self._curve_distance(resolve, cursor))
            else:
                r = float(self.args[1])
            out.append((center, tangent, r))
        return out
    options = (
        opts.Option("Cap", opts.ENUM, choices=("None", "Flat", "Round"), default="Flat",
                    help="Specifies how to cap the ends (default: Flat)."),
        opts.Option("Thick", opts.TOGGLE, default=False,
                    help="Whether the pipe has two walls or one."),
        opts.Option("Diameter/Radius", opts.MODE,
                    help="Toggle between picking a Radius and picking a Diameter."),
        opts.Option("FitRail", opts.MODE,
                    help="Fit a polycurve of lines and arcs to a single surface."),
        # MEASURED (research_all_13 #105): the prompt shows ShapeBlending=Local.
        opts.Option("ShapeBlending", opts.ENUM, choices=("Local", "Global"), default="Local",
                    help="Local: the radius stays constant at the ends and changes in the "
                         "middle (S-curve law). Global: the radius is blended linearly "
                         "from one end to the other."),
        opts.Option("Output", opts.MODE, help="Output type."),
        opts.Option("Multiple", opts.MODE, help="Allow selecting more than one curve."),
        opts.Option("ChainEdges", opts.MODE,
                    help="Selects surface edges touching the selected edge."),
    )

    def _enter_default(self, ctx):
        # The end radius defaults to the start radius (Enter accepts it) -- the
        # constant-radius pipe is the default flow, so an empty Enter at the "End radius:"
        # step takes the value already given at "Start radius:".
        if self.step_index == 1:
            return 1.0
        if self.step_index == 2 and len(self.args) >= 2:
            return float(self.args[1])
        if self.step_index == 3:
            return ""
        return None

    def escape_commits(self):
        # MEASURED (research_preview_14 P52): Esc at "Point for next radius" BUILT the pipe.
        return self.step_index == 3 and self.pending_option is None

    def _feed(self, text, ctx):
        if self.step_index == 3 and (text or "").strip() and self.pending_option is None:
            return ("Pipe: a radius at a point along the rail is not implemented yet -- "
                    "press Enter for none"), False
        return super()._feed(text, ctx)

    def execute(self, ctx: NarwhalContext):
        name = self.args[0] if self.args else None
        rail = ctx.geom_by_name(name) if name else None
        if rail is None:
            return f"Pipe: no object named {name!r}", True
        if not isinstance(rail, r3.Curve):
            return f"Pipe: {name!r} is not a curve (a pipe follows a curve)", True
        # Declared, not built: each refuses by name, before anything is added.
        if self.mode is not None:
            return f"Pipe: the {self.mode} option is not implemented yet", True
        if self.opt.get("Thick"):
            return "Pipe: the Thick option is not implemented yet (two walls)", True
        cap = self.opt.get("Cap", "Flat")
        if cap == "Round":
            return ("Pipe: the Cap=Round option is not implemented yet "
                    "(a hemispherical cap)"), True
        try:
            start = float(self.args[1])
            end = float(self.args[2]) if len(self.args) > 2 else start
        except (ValueError, IndexError):
            return "Pipe: a positive radius is required", True
        if start <= 0 or end <= 0:
            return "Pipe: the radius must be positive", True
        tapered = abs(start - end) > 1e-9
        blend = self.opt.get("ShapeBlending", "Local")
        # MEASURED (research_preview_14 P52): the reference builds a tapered pipe by default
        # (ShapeBlending=Local). Global is the exact linear taper; Local uses the S law (constant
        # at the ends, fastest in the middle, as the help describes) -- OURS: its exact law was
        # not measured, and the message says so.
        out = bridge.pipe_occt(rail, start, cap=(cap == "Flat"),
                               end_radius=end if tapered else None, blend=blend)
        if out is None:
            reason = bridge.last_occt_decline()
            return (f"Pipe: the analytic pipe could not be built "
                    f"({reason or 'reason unknown'}) -- a faceted fallback is refused "
                    f"(rule 0)"), True
        _, display = ctx.add_geometry(out, "Pipe")
        if tapered:
            law = ("linear taper" if blend == "Global"
                   else "Local S-curve blend, law not yet measured against the reference")
            return (f"Created {display} (radius {start:g} to {end:g}, {cap} cap, {law})"), True
        return (f"Created {display} (radius {start:g}, "
                f"{cap} cap, constant)"), True


def _build_registry():
    cmds: dict[str, Command] = {}
    for cls in [LineCmd, PointCmd, PointsCmd, CircleCmd, PolylineCmd, LinesCmd,
                CurveCmd, HandleCurveCmd,
                InterpCrvCmd, CurveThroughPolylineCmd, CurveThroughPtCmd,
                CurveThroughSrfControlPtCmd, TweenCurvesCmd,
                BoxCmd, SphereCmd, CylinderCmd, ConeCmd,
                RectangleCmd, PlaneCmd, EllipseCmd, PolygonCmd, ArcCmd, ConicCmd,
                ParabolaCmd, Parabola3PtCmd, HyperbolaCmd, CatenaryCmd,
                HelixCmd, SpiralCmd,
                ExtrudeCmd, RevolveCmd,
                LoftCmd, OffsetCmd,
                MoveCmd, RotateCmd, ScaleCmd, CopyCmd, ArrayLinearCmd,
                CopyToClipboardCmd, CutCmd, PasteCmd,
                Rotate3DCmd, Scale1DCmd, Scale2DCmd, OrientCmd, Orient3PtCmd,
                ArrayPolarCmd, MirrorCmd, DeleteCmd, JoinCmd, ExplodeCmd, CloseCrvCmd,
                ProjectCmd, ProjectToCPlaneCmd, ExtendCmd, DivideCmd, ExtractWireframeCmd,
                SelectCmd, ZoomCmd,
                UndoCmd, RedoCmd, PointsOnCmd, PointsOffCmd,
                SelAllCmd, SelNoneCmd, InvertCmd,
                SelCrvCmd, SelSrfCmd, SelPolysrfCmd, SelPtCmd,
                SelClosedCrvCmd, SelOpenCrvCmd, SelDupCmd, SelNameCmd, SetObjectNameCmd,
                SelGroupCmd, SelKeyCmd, SelValueCmd, SelKeyValueCmd, SelLayerCmd,
                SelLastCmd, SelPrevCmd,
                HideCmd, ShowCmd, LockCmd, UnlockCmd,
                ShowSelectedCmd, UnlockSelectedCmd, HideSwapCmd, LockSwapCmd,
                IsolateCmd, UnisolateCmd, AddToGroupCmd, RemoveFromGroupCmd,
                SetGroupNameCmd, GroupCmd, UngroupCmd, UngroupAllCmd,
                DupEdgeCmd, DupBorderCmd,
                PurgeCmd, RenameCmd,
                GumballCmd, GumballAlignmentCmd, SnapCmd, SetSnapCmd, OrthoCmd, SetOrthoCmd,
                CPlaneCmd, NamedCPlaneCmd, SaveCmd, OpenCmd, HelpCmd, LayersCmd,
                                LayerStateManagerCmd, OptionsImportCmd, OptionsExportCmd,
                BooleanUnionCmd, BooleanDifferenceCmd, BooleanIntersectionCmd,
                LengthCmd, AreaCmd, VolumeCmd, DistanceCmd, BoundingBoxCmd,
                AngleCmd, RadiusCmd, DiameterCmd, AreaCentroidCmd,
                VolumeCentroidCmd, EvaluatePtCmd, FlipCmd, DirCmd, WhatCmd,
                ChangeDegreeCmd, InsertKnotCmd,
                CrvStartCmd, CrvEndCmd,
                ExtractPtCmd, ExtractControlPolygonCmd,
                FilletCmd, ChamferCmd, SplitCmd, TrimCmd, IntersectCmd,
                SetUserTextCmd, GetUserTextCmd, SetDocumentUserTextCmd, GetDocumentUserTextCmd,
                GridCmd, SnapSizeCmd, UnitsCmd, PictureCmd, BlendCrvCmd, RebuildCmd,
                PlanarSrfCmd, EdgeSrfCmd, ExtrudeSrfCmd,
                ExtrudeCrvTaperedCmd,
                ShellCmd, OffsetSrfCmd, PipeCmd,
                OsnapCmd, DisableOsnapCmd, ProjectOsnapCmd, SnapToLockedCmd]:
        inst = cls()
        cmds[inst.name.lower()] = inst
    return cmds


COMMANDS: dict[str, Command] = _build_registry()

# `Import` lives in its own module (as Export does) and registers itself into
# COMMANDS on import. Imported HERE -- at the end, once every name it uses exists -- so
# every route to the registry has it without adding a second registration in `state.py`. A failure
# is printed, never swallowed: a missing command must be explicable.
try:
    from . import import_commands  # noqa: E402,F401
except Exception as _e:  # noqa: BLE001
    import sys as _sys
    print(f"[narwhal3d] the Import command is unavailable: {type(_e).__name__}: {_e}",
          file=_sys.stderr)

# FilletEdge, ChamferEdge, Cap, Sweep1, and Sweep2 live in a separate solid-command
# module. Same
# pattern as Import: registered on import, and a failure is printed, never swallowed.
try:
    from . import solid_commands  # noqa: E402,F401
except Exception as _e:  # noqa: BLE001
    import sys as _sys
    print(f"[narwhal3d] the solid commands are unavailable: {type(_e).__name__}: {_e}",
          file=_sys.stderr)

try:
    from . import surface_commands  # noqa: E402,F401
except Exception as _e:  # noqa: BLE001
    import sys as _sys
    print(f"[narwhal3d] the surface commands are unavailable: {type(_e).__name__}: {_e}",
          file=_sys.stderr)
