"""Global application state: current model, active command, history.

Kept as a plain module-level singleton so draw handlers (which run outside
operator context) can read it cheaply every frame.
"""
from __future__ import annotations

import rhino3dm as r3  # noqa: F401  (re-exported for convenience)

from .commands import COMMANDS, RhinoContext
from .model import RhinoModel
# The document's live layer tree. `layers` is pure (no bpy, no rhino3dm),
# so importing it here is safe everywhere; AppState keeps it in step with the
# model's rhino3dm layers (which objects reference by index).
from .layers import LayerTree
# Rhino completes a partly typed command name ("As soon as you have typed
# enough letters to uniquely identify the command, the ... command automatically
# completes" -- user_interface/rhino_window.htm). `cmdbar` owns that ranking and
# is pure/bpy-free, so resolving here keeps one definition of "best match".
from . import cmdbar

# Rhino's default command aliases, represented as a pure table. `alias_table` is
# bpy-free (no rhino3dm, no bpy), so importing it here is safe in every
# environment. The table is consulted LAST -- only when a typed name is not a
# command AND does not autocomplete to one -- so it can never shadow a real
# command name or the command bar's prefix completion.
try:
    from . import alias_table
except ImportError:  # the module is absent -> no aliases, which is honest
    alias_table = None
# NOTE: only ImportError is caught. A *broken* table (a macro that does not
# parse) raises ValueError from alias_table and must stay LOUD -- swallowing it
# would disable all aliases invisibly.


class AppState:
    def __init__(self):
        self.model = RhinoModel()
        self.ctx = RhinoContext(self.model)
        self.active_command = None      # Command instance or None
        self.input_buffer = ""          # text typed on the command line
        self.history: list[str] = []    # rendered lines (newest last)
        self.last_command: str | None = None  # for Enter/Space repeat (Rhino)
        # Command key -> callable that opens its window and returns whether it did. Registered by the UI; empty headless, so the command line runs.
        self.dialog_openers = {}
        # The live layer tree mirrors the model's layers. Built last,
        # once the model exists.
        self.layers = LayerTree()
        self._rebuild_layers()
        # The document's file path. None until it is saved or opened -- Rhino's
        # "Untitled". `_saved_serial` is the undo stack's top record when the file
        # was last written; see `doc_modified`.
        self.doc_path: str | None = None
        self._saved_serial = self.ctx.undo.top_serial

    def set_model(self, model: RhinoModel):
        self.model = model
        self.ctx = RhinoContext(model)
        self.active_command = None
        self.input_buffer = ""
        # A new document gets a fresh tree so layer state cannot leak across documents.
        self._rebuild_layers()
        # A fresh document is, by definition, what is on disk or is untitled.
        self._saved_serial = self.ctx.undo.top_serial

    # ------------------------------------------------------------- file

    def mark_saved(self, path=None):
        """Record that the document now matches the file at `path` (or doc_path)."""
        if path:
            self.doc_path = path
        self._saved_serial = self.ctx.undo.top_serial

    def doc_modified(self):
        """True when the model differs from its file (or is Untitled and non-empty).

        Read from the undo history rather than a flag every command must remember to
        set: every document change records an undo step, so a different top
        record means a different document, and undoing back to the saved state
        correctly reads as unmodified again.
        """
        return self.ctx.undo.top_serial != self._saved_serial

    def doc_title(self):
        import os as _os

        return _os.path.basename(self.doc_path) if self.doc_path else "Untitled"

    # ------------------------------------------------------------- layers

    def _rebuild_layers(self):
        """Rebuild the live LayerTree from the model's layers, flat and in order.

        The model (`file.Layers`) is the authoritative, persisted list -- objects
        reference it by index -- so the tree is a mirror of it, not a second
        source of truth. `current` is set to the model's current layer, so it is
        never None once the document exists. The model list is flat, so this mirror
        is flat too.
        """
        tree = LayerTree()
        nodes = []
        for info in self.model.layers():        # ordered by index
            nodes.append(tree.add(info["name"], color=tuple(info["color"][:3]),
                                  visible=info["visible"], locked=info["locked"]))
        idx = self.model.current_layer_index
        if 0 <= idx < len(nodes):
            tree.set_current(nodes[idx])
        elif nodes:
            tree.set_current(nodes[0])
        self.layers = tree

    def _model_index_of(self, layer):
        """The model layer index for a tree node, matched by name, or None.

        Names are unique among the (flat) model layers, so a name match is exact.
        """
        for info in self.model.layers():
            if info["name"] == layer.name:
                return info["index"]
        return None

    def add_layer(self, name, color=(255, 255, 255)):
        """Add a top-level layer to BOTH the model and the live tree, in step.

        Returns the new tree node. Adding to the model is what gives the layer an
        index for objects to sit on (point 4); adding to the tree keeps the live
        view current. (Rejection of duplicate names is the tree's job and raises.)
        """
        node = self.layers.add(name, color=tuple(color[:3]))
        self.model.add_layer(name, color)
        return node

    def set_current_layer(self, layer):
        """Make `layer` current in BOTH the tree and the model (point 4).

        The model's current index is what `model.add_object` uses, so after this
        a new object lands on `layer`.
        """
        self.layers.set_current(layer)          # raises if not in this tree
        idx = self._model_index_of(layer)
        if idx is not None:
            self.model.set_current_layer(idx)

    # ------------------------------------------------------------------

    @property
    def prompt(self) -> str:
        if self.active_command is not None:
            return self.active_command.prompt + " "
        return ""

    def resolve_command(self, name: str):
        """A typed command name -> (key, class), in Rhino's priority order.

        Rhino: *"As soon as you have typed enough letters to uniquely identify the
        command, the Circle command automatically completes. Press the Enter key
        or click the command name on the auto-complete list."*
        (user_interface/rhino_window.htm)

        Resolution is ordered, and fuzzy matching comes last. Try, in this
        order:

          1. an exact command name (case-insensitive),
          2. an exact alias (one whose target we implement),
          3. a unique prefix match -- cmdbar.autocomplete (prefix-only, as before),
          4. only then the best fuzzy match, and ONLY when it is unambiguous
             (a single candidate); an ambiguous fuzzy input resolves to nothing
             rather than guessing.

        Returns ``(None, None)`` when nothing matches (or the fuzzy match is
        ambiguous).

        Keeping aliases ahead of prefix matching means `m`
        runs Move (the alias) rather than Mirror (a prefix neighbour), matching
        real Rhino, where `M` is the alias for `!_Move`.
        """
        key = (name or "").strip().lower()
        if not key:
            return None, None
        # 1. exact command name (case-insensitive)
        cmd_cls = COMMANDS.get(key)
        if cmd_cls is not None:
            return key, cmd_cls
        names = sorted(c.name for c in COMMANDS.values())
        # 2. exact alias, ahead of prefix matching. Only an alias whose
        #    target we actually implement is returned; an unimplemented target
        #    reports "not implemented yet" (rule 4) rather than leaking here.
        if alias_table is not None:
            alias = alias_table.resolve_alias(key)
            if alias is not None and alias.implemented:
                return alias.command.lower(), COMMANDS.get(alias.command.lower())
        # 3. unique prefix match -- today's autocomplete, left prefix-only
        #    (ui.py relies on it for inline Tab completion; its docstring
        #    records why the fuzzy fallback must not live here).
        best = cmdbar.autocomplete(key, names)
        if best is not None:
            return best.lower(), COMMANDS.get(best.lower())
        # 4. best fuzzy match, LAST. Only when unambiguous (a single inexact
        #    candidate): an ambiguous fuzzy input resolves to nothing rather than
        #    guessing. The exact-prefix list is empty here (step 3 already
        #    established no command starts with the key), so only inexact is used.
        _exact, inexact = cmdbar.fuzzy_matches(key, names)
        if len(inexact) == 1:
            only = inexact[0]
            return only.lower(), COMMANDS.get(only.lower())
        return None, None

    def alias_inline_options(self, name: str) -> str:
        """The options baked into an alias, as a string to feed the command.

        Rhino aliases are macros that already carry their options:
        ``ZE`` is ``' _Zoom _Extents``. When a user types ``ZE``, the command
        must behave as if ``Zoom Extents`` were typed, not run Zoom's default.
        Returns ``""`` when the name is not an implemented alias (or the alias
        carries no options), so the caller treats it exactly as before.
        """
        if alias_table is None or not name:
            return ""
        alias = alias_table.resolve_alias(name.strip().lower())
        if alias is None or not alias.implemented or not alias.options:
            return ""
        return " ".join(alias.options)

    def start_command(self, name: str, rest: str | None = None):
        """Start a command by (case-insensitive) name. Returns message or None.

        `rest` is anything typed after the command name on the same line -- an
        inline first argument (``Sphere 0,0,0``) or a command-line option
        (``Zoom Selected``). It matters that this is handled HERE rather than
        after ``begin()``: a zero-step command runs immediately in ``begin()``,
        so feeding the remainder afterwards would be too late and
        ``Zoom Selected`` would silently perform the default Extents instead
        by the option framework.
        """
        # Rhino's convention (MEASURED, user_interface: a leading hyphen runs the command-line
        # version of a dialog command): `Units` opens the window, `-Units` the prompts.
        # The UI registers the window in `dialog_openers`; headless there is none, so
        # the command line runs either way.
        scripted = bool(name) and name.startswith("-") and len(name) > 1
        if scripted:
            name = name[1:]
        key, cmd_cls = self.resolve_command(name)
        if cmd_cls is None:
            self.log(self.alias_message(name) or f"Unknown command: {name!r}  (type Help)")
            return None
        opener = None if scripted else self.dialog_openers.get(key)
        if opener is not None and not (rest or "").strip():
            try:
                if opener():
                    self.last_command = name
                    return None
            except Exception as e:  # noqa: BLE001
                self.log(f"{name}: the window could not open ({e!r}); using the "
                         f"command line")
        # An alias expands to a command with options already chosen
        # (``ZS`` -> ``Zoom Selected``). Merge those with anything the user also
        # typed inline (``ZS`` -> ``Selected``), so the alias is honoured exactly
        # as Rhino runs its macro. A plain command name contributes nothing here.
        rest = (rest or "").strip()
        alias_opts = self.alias_inline_options(name).strip()
        if alias_opts and rest:
            rest = f"{alias_opts} {rest}"
        elif alias_opts:
            rest = alias_opts
        # fresh instance per invocation so state never leaks between runs
        import copy

        cmd = copy.deepcopy(cmd_cls)
        self.active_command = cmd
        # remembered for Enter/Space repeat; store the canonical key
        self.last_command = key
        self.input_buffer = ""
        self.log(f"{cmd.name}: {cmd.description}")

        if rest:
            # Let the command consume the inline token itself: feed() resolves
            # options first, then falls through to the ordinary step logic.
            try:
                # An alias macro is a SEQUENCE of tokens (ZSA = '_Zoom _All _Selected),
                # each answered in turn as Rhino plays the macro; fed as one line,
                # "All Selected" matched no option and the alias did nothing.
                lead = alias_opts.split()[:-1] if alias_opts else []
                message, done = None, False
                for token in lead:
                    message, done = cmd.feed(token, self.ctx)
                    if done:
                        break
                    if message:
                        self.log(message)
                    message = None
                if not done:
                    message, done = cmd.feed(rest[len(" ".join(lead)):].strip(),
                                             self.ctx)
            except ValueError as e:
                self.log(f"Bad input: {e}")
                self.active_command = None
                return None
            except Exception as e:  # noqa: BLE001
                self.log(f"Error: {e!r}")
                self.active_command = None
                return None
        else:
            # zero-step commands run immediately on the naming Enter (Rhino style)
            message, done = cmd.begin(self.ctx)

        if message:
            self.log(message)
        if done:
            self.active_command = None
        return None

    def alias_message(self, name: str):
        """The honest line for a typed alias we cannot run, else None.

        An alias pointing at a command we have not built says so
        plainly ("not implemented yet") instead of reading as a typo. Returns
        None for names that are not aliases at all, so the caller falls back to
        the ordinary Unknown-command message.
        """
        if alias_table is None or not name:
            return None
        alias = alias_table.resolve_alias(name.strip().lower())
        if alias is None:
            return None
        return alias.message if not alias.implemented else None

    def feed_line(self, text: str):
        """Feed one submitted line to the active command (or start a new one)."""
        # Typed lengths convert to this document's units.
        try:
            from . import coords as _coords

            _coords.set_model_unit(self.model.unit_system())
        except Exception:  # noqa: BLE001
            pass
        if self.active_command is not None and self._run_nested(text):
            return
        if self.active_command is not None:
            try:
                message, done = self.active_command.feed(text, self.ctx)
            except ValueError as e:
                message, done = f"Bad input: {e}", True
            except Exception as e:  # noqa: BLE001
                message, done = f"Error: {e!r}", True
            if message:
                self.log(message)
            if done:
                self.active_command = None
                self.input_buffer = ""
        else:
            text = text.strip()
            if not text:
                return
            # first word is the command name (Rhino style); the remainder is an
            # inline argument or option, applied inside start_command.
            parts = text.split(None, 1)
            self.start_command(parts[0], parts[1] if len(parts) > 1 else None)

    def _run_nested(self, text: str) -> bool:
        """Run a NESTABLE command inside the active one. True if it did.

        MEASURED (alias_table: Rhino's macro prefix "'" -- "The next command is a
        nestable command (view / CPlane / osnap ...)"), and Rhino's default aliases
        carry it: ZS = '_Zoom _Selected. Without nesting, typing ZS while ExtrudeCrv
        is active would incorrectly feed it to the distance prompt. A line counts as
        nested when it is typed with a leading "'" or is an alias whose macro has the
        "'" prefix -- and never when it is an option of the running command, which
        must keep its meaning. Only zero-step commands nest (they finish at once), so
        the outer command's prompt is untouched.
        """
        raw = (text or "").strip()
        if not raw:
            return False
        outer = self.active_command
        explicit = raw.startswith("'")
        parts = raw.lstrip("'").split(None, 1)
        if not parts:
            return False
        name = parts[0]
        if not explicit:
            alias = alias_table.resolve_alias(name.lower()) if alias_table else None
            if alias is None or alias.prefix != "'" or not alias.implemented:
                return False
            # Input the CURRENT PROMPT can take is that prompt's: a free-text step
            # (ArrayPolar's "Axis (x/y/z):" -- where `z` is an answer, not '_Zoom), an
            # option's value sub-prompt, or an object step naming a real object.
            from .commands import NAME as _NAME, TEXT as _TEXT, SELECTION_KINDS
            if getattr(outer, "pending_option", None) is not None:
                return False
            steps = getattr(outer, "steps", None) or []
            idx = getattr(outer, "step_index", 0)
            kind = steps[idx][1] if idx < len(steps) else None
            if kind in (_TEXT, _NAME):
                return False
            if kind in SELECTION_KINDS:
                try:
                    if self.ctx.object_by_name(raw) is not None:
                        return False
                except Exception:  # noqa: BLE001
                    pass
            try:
                from . import options as _opts
                if outer.options and _opts.match(raw, outer.options,
                                                 outer.opt).status != _opts.NONE:
                    return False
            except Exception:  # noqa: BLE001
                pass
        _key, cls = self.resolve_command(name)
        if cls is None or getattr(cls, "steps", None):
            return False
        last = self.last_command
        try:
            self.start_command(name, parts[1] if len(parts) > 1 else None)
        finally:
            nested = self.active_command
            self.active_command = outer
            self.last_command = last          # Enter still repeats the OUTER command
            if nested is not None and nested is not outer:
                self.log(f"{nested.name} needs more input than a nested command can "
                         f"take; it was not run")
        return True

    def cancel(self):
        if self.active_command is not None:
            self.log(f"{self.active_command.name} cancelled")
            self.active_command = None
            self.input_buffer = ""

    def log(self, line: str):
        self.history.append(line)
        if len(self.history) > 200:
            del self.history[:len(self.history) - 200]


# the singleton
app_state = AppState()

# Save / SaveAs / New / Open live in their own module and replace the old
# registry entries when it is imported. Imported HERE so every route to the
# commands -- the app, and the headless suites that load `state` -- sees them.
from . import file_commands  # noqa: E402,F401
# Print (vector PDF / .ai / .svg) uses the same arrangement.
from . import print_commands  # noqa: E402,F401
# Export (Export Selected) supports .3dm / .step / .ai / .pdf / .svg.
from . import export_commands  # noqa: E402,F401
# SetDisplayMode (Wireframe / Shaded / Rendered).
from . import display_commands  # noqa: E402,F401
# Layer (create / rename / make current) replaces commands.LayersCmd.
from . import layer_commands  # noqa: E402,F401


def set_model(model: RhinoModel):
    app_state.set_model(model)
