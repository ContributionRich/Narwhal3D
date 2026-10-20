"""SetDisplayMode -- Wireframe / Shaded / Rendered per viewport.

MEASURED (options/view_displaymode_options.htm, SetDisplayMode): "Command-line options
Viewport: Active Sets the display mode for only the active viewport. All Sets the display
mode for all viewports. Mode Lists all of the current display modes in the model."
Shortcuts "Ctrl (CMD) + Alt + G Ghosted, R Rendered, S Shaded, W Wireframe, X XRay".

Wireframe, Shaded, and Rendered are implemented; Ghosted and X-Ray are recognised
and refused by name.
The viewport work happens in `ui.set_display_mode`, reached through `mode_setter`
(set by the app); headless there are no viewports and the command says so.
"""
from __future__ import annotations

from . import commands
from . import options as opts
from . import views

#: set by the app: ``(mode_name, scope) -> message``. None when there are no viewports.
mode_setter = None


class SetDisplayModeCmd(commands.Command):
    name = "SetDisplayMode"
    description = "Set the viewport display mode: Wireframe, Shaded or Rendered."
    records_undo = False                 # a view setting, not a document change
    variable_points = False
    steps = [("Display mode <" + " | ".join(m[0] for m in views.DISPLAY_MODES)
              + ">:", commands.TEXT)]
    options = (
        opts.Option("Viewport", opts.ENUM, default="Active", choices=("Active", "All"),
                    help="Active: only the active viewport. All: every viewport."),
    )

    def execute(self, ctx):
        mode, err = views.display_mode(self.args[0])
        if mode is None:
            return f"SetDisplayMode: {err}", True
        name, implemented, _sh = mode
        if not implemented:
            return f"SetDisplayMode: the {name} display mode is not implemented yet", True
        if mode_setter is None:
            return "SetDisplayMode: there are no viewports (headless)", True
        return mode_setter(name, self.opt.get("Viewport", "Active")), True


PROVIDED = (SetDisplayModeCmd,)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
