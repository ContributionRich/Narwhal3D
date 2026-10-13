"""Print -- vector output of the active viewport to PDF, AI, SVG, or CDR.

The command supports viewport or extents output, paper-to-model scale, output colour,
and standard paper sizes. In the app it opens a dialog with a live preview; this module
also provides the command-line form using the same settings.
The page itself is built by `print2d`, which documents its accuracy (exact for
polynomial curves, within 0.001 mm on paper for circles and arcs) and what it does not
yet print (silhouettes, perspective views, points, meshes, text) -- and those are
reported on the command line every time they apply, never skipped silently.

Not built: the dialog and its preview, printers other than PDF/AI/SVG files, Window
area, margins and position, line types, multiple layouts.
"""
from __future__ import annotations

import os

from . import commands
from . import filedialog
from . import options as opts
from . import print2d

TEXT = commands.TEXT

PRINT_FILTERS = (("PDF", "*.pdf"), ("CorelDRAW 2015/X7", "*.cdr"),
                 ("Adobe Illustrator", "*.ai"), ("SVG", "*.svg"))

#: set by the app (ui) or a test: a callable returning the ACTIVE viewport as a
#: `print2d.View`. Left None, the app's own `ui.print_view_spec` is used.
view_provider = None
#: set by the app (ui): opens the Print dialog and returns True. Left None (headless
#: runs, the suites) the command keeps its command-line form.
dialog_opener = None


def _view():
    if view_provider is not None:
        return view_provider()
    from . import ui

    return ui.print_view_spec()


def parse_scale(text):
    """``Fit`` / ``1:1`` / ``1:20`` / ``2:1`` / ``0.5`` -> "Fit" or paper-per-model float.

    Scale is expressed as paper units per model unit, so 1:20 means one unit on paper
    for twenty in the model.
    """
    t = (str(text) if text is not None else "").strip().lower()
    if t in ("", "fit"):
        return "Fit"
    for sep in (":", "="):
        if sep in t:
            a, b = t.split(sep, 1)
            a, b = float(a), float(b)
            if a <= 0 or b <= 0:
                raise ValueError("a scale must be positive")
            return a / b
    v = float(t)
    if v <= 0:
        raise ValueError("a scale must be positive")
    return v


def _describe_scale(s):
    if s == "Fit":
        return "fit to page"
    return f"1:{1 / s:g}" if s < 1 else f"{s:g}:1"


class PrintCmd(commands.Command):
    name = "Print"
    description = ("Print the active viewport as vectors to PDF, AI, SVG, or CDR. "
                   "Press Enter to choose the file.")
    records_undo = False
    variable_points = False
    steps = [("Press Enter to choose the file, or type a file name:", TEXT)]
    options = (
        opts.Option("Size", opts.ENUM, default=print2d.DEFAULT_PAGE,
                    choices=tuple(print2d.PAGE_SIZES_MM),
                    help="Paper size."),
        opts.Option("Orientation", opts.ENUM, default=print2d.DEFAULT_ORIENTATION,
                    choices=("Landscape", "Portrait"), help="Paper orientation."),
        opts.Option("View", opts.ENUM, default="Viewport",
                    choices=("Viewport", "Extents"),
                    help="Viewport: what the active viewport shows. Extents: every "
                         "object."),
        opts.Option("Scale", opts.VALUE, default="Fit", value_kind="text",
                    help="Fit, or paper=model such as 1:1 or 1:20."),
        opts.Option("Color", opts.ENUM, default="PrintColor",
                    choices=("PrintColor", "DisplayColor", "BlackWhite"),
                    help="Print colour (layer/object), display colour, or black."),
    )


    def begin(self, ctx):
        """Open the live-preview dialog in the app; keep the command-line form headless
        or when a file name was typed."""
        if dialog_opener is not None and not getattr(self, "args", None):
            try:
                if dialog_opener():
                    return ("Print: the Print Setup dialog is open -- click a setting to "
                            "change it, Print to choose the file, Close or Esc to "
                            "cancel."), True
            except Exception as e:  # noqa: BLE001
                return f"Print: the dialog could not open ({e!r})", True
        return super().begin(ctx)

    def _enter_default(self, ctx):
        return ""                    # empty Enter at the file prompt = "ask me"

    def execute(self, ctx):
        target = self.args[0] if self.args else ""
        if not target:
            from .state import app_state

            base = os.path.splitext(app_state.doc_title())[0] or "Untitled"
            start = os.path.join(filedialog.start_dir(app_state.doc_path),
                                 base + ".pdf")
            status, target = filedialog.ask_save_path("Print", start, PRINT_FILTERS)
            if status == filedialog.CANCEL:
                return "Print cancelled", True
            if status == filedialog.UNAVAILABLE:
                self.args.clear()
                self.step_index = 0
                return ("No file dialog is available (kdialog / zenity) -- type the "
                        "file name (.pdf, .ai, .svg or .cdr)."), False
        path = os.path.abspath(os.path.expanduser(target.strip().strip('"')))
        ext = os.path.splitext(path)[1].lower()
        if not ext:
            path += ".pdf"
        elif ext not in (".pdf", ".ai", ".svg", ".cdr"):
            return (f"Print writes .pdf, .ai, .svg or .cdr -- not {ext}. (Paper printers "
                    f"are not implemented yet.)"), True
        if not os.path.isdir(os.path.dirname(path)):
            return f"the folder {os.path.dirname(path)!r} does not exist", True
        try:
            scale = parse_scale(self.opt.get("Scale"))
        except ValueError as e:
            return f"Scale: {e}", True
        if ext == ".cdr" and scale == "Fit":
            scale = 1.0
        setup = print2d.PageSetup(size=self.opt["Size"],
                                  orientation=self.opt["Orientation"],
                                  area=self.opt["View"], scale=scale,
                                  color=self.opt["Color"])
        try:
            view = _view()
            page = print2d.build_page(ctx.model, view, setup)
        except ValueError as e:
            return f"Print: {e}", True
        title = os.path.splitext(os.path.basename(path))[0]
        try:
            print2d.write(path, page, title)
        except ValueError as e:
            return f"Print: {e}", True
        msg = (f"Printed {len(page.strokes)} curve(s) to {path} -- {setup.size} "
               f"{setup.orientation}, {setup.area}, {_describe_scale(scale)}"
               + (f" (= {page.scale:g} mm per unit)" if scale == "Fit" else ""))
        if page.notes:
            msg += ". NOTE: " + "; ".join(page.notes)
        return msg, True


PROVIDED = (PrintCmd,)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
