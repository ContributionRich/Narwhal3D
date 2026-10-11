"""Create, rename, delete, and activate document layers.

``Layer`` plus a name creates a uniquely named layer. ``Rename`` accepts
``OldName->NewName``, while ``Current`` activates a named layer. ``Delete`` refuses the
last layer, the current layer, and any layer that still contains objects. Layer operations
are undoable because document snapshots include the layer table.

An omitted new-layer name uses the next available ``Layer NN`` name. New layers start with
a black display colour. The model's layer deletion route uses a write/read round trip to
work around rhino3dm's in-memory layer-table limitations.
"""
from __future__ import annotations

from . import commands
from . import options as opts

TEXT = commands.TEXT


def _app():
    from .state import app_state

    return app_state


def _names(model):
    return [i["name"] for i in model.layers()]


def new_layer(name=None):
    """Create a layer (a unique name), refresh the panel. Returns ``(ok, message)``."""
    app = _app()
    model = app.model
    names = {n.casefold() for n in _names(model)}
    if not name:
        k = 1
        while f"Layer {k:02d}".casefold() in names:
            k += 1
        name = f"Layer {k:02d}"
    if name.casefold() in names:
        return False, f"a layer named {name!r} already exists (names must be unique)"
    app.ctx.note_change("Layer")
    idx = model.add_layer(name, (0, 0, 0))
    app._rebuild_layers()
    return True, f"Created layer {name!r} (index {idx})"


def rename_layer(old, new):
    app = _app()
    model = app.model
    infos = {i["name"].casefold(): i for i in model.layers()}
    info = infos.get((old or "").strip().casefold())
    new = (new or "").strip()
    if info is None:
        return False, f"there is no layer named {old!r}"
    if not new:
        return False, "the new name is empty"
    if new.casefold() in infos and new.casefold() != old.strip().casefold():
        return False, f"a layer named {new!r} already exists (names must be unique)"
    app.ctx.note_change("Layer")
    model.file.Layers[info["index"]].Name = new
    app._rebuild_layers()
    return True, f"Renamed layer {info['name']!r} to {new!r}"


def delete_layer(name):
    app = _app()
    info = {i["name"].casefold(): i for i in app.model.layers()}.get(
        (name or "").strip().casefold())
    if info is None:
        return False, f"there is no layer named {name!r}"
    app.ctx.note_change("Layer")
    ok, msg = app.model.delete_layer(info["index"])
    app._rebuild_layers()
    try:
        app.ctx.rebuild_display()
    except Exception:  # noqa: BLE001 -- headless
        pass
    return ok, msg


class LayerCmd(commands.Command):
    name = "Layer"
    description = ("Create a layer (type a name, or Enter for the next 'Layer NN'); "
                   "Rename OldName->NewName; Current <name>.")
    steps = [("Name of the new layer (Enter for the next free name):", TEXT)]
    options = (
        opts.Option("Rename", opts.VALUE, value_kind="text",
                    help="OldName->NewName"),
        opts.Option("Current", opts.VALUE, value_kind="text",
                    help="Make this layer the current one."),
        opts.Option("Delete", opts.VALUE, value_kind="text",
                    help="Delete this layer (not the current one, nor one with "
                         "objects on it)."),
    )

    def _enter_default(self, ctx):
        return ""

    def on_option_set(self, option, value, ctx):
        app = _app()
        if option.name == "Rename":
            old, sep, new = str(value).partition("->")
            if not sep:
                return "Rename: type OldName->NewName", True
            return rename_layer(old, new)[1], True
        if option.name == "Delete":
            return delete_layer(str(value))[1], True
        if option.name == "Current":
            node = app.layers.find(str(value).strip())
            if node is None:
                return f"there is no layer named {value!r}", True
            app.set_current_layer(node)
            return f"{node.name} is the current layer", True
        return None

    def execute(self, ctx):
        return new_layer((self.args[0] or "").strip() or None)[1], True


def _retint(ob_name, layer_index):
    """Give a display object its NEW layer's material -- the same `narwhal_layer_<i>`
    material `_make_display_object` assigns (and ui.py keeps in its layer colour)."""
    try:
        import bpy
    except ImportError:
        return
    ob = bpy.data.objects.get(ob_name)
    if ob is None or ob.data is None or not hasattr(ob.data, "materials") \
            or ob.get("narwhal_picture"):
        return
    app = _app()
    layers = app.model.layers()
    if not (0 <= layer_index < len(layers)):
        return
    mat = bpy.data.materials.get(f"narwhal_layer_{layer_index}")
    if mat is None:
        r, g, b = (c / 255.0 for c in layers[layer_index]["color"][:3])
        mat = bpy.data.materials.new(f"narwhal_layer_{layer_index}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def change_object_layer(names, layer_name):
    """Move the named objects onto `layer_name`. Returns ``(ok, message)``.

    The operation updates selected objects to the requested layer and is undoable because
    document snapshots include every object's attributes.
    """
    app = _app()
    info = {i["name"].casefold(): i for i in app.model.layers()}.get(
        (layer_name or "").strip().casefold())
    if info is None:
        return False, f"there is no layer named {layer_name!r}"
    wanted = {(n or "").casefold() for n in names if n}
    if not wanted:
        return False, "ChangeLayer: no objects selected"
    idx = int(info["index"])
    app.ctx.note_change("ChangeLayer")
    moved = []
    for fobj in app.model.file.Objects:
        name = fobj.Attributes.Name or ""
        if name.casefold() in wanted:
            fobj.Attributes.LayerIndex = idx
            moved.append(name)
            _retint(name, idx)
    if not moved:
        return False, "ChangeLayer: none of those objects is in the document"
    try:
        from . import ui
        ui.apply_layer_state()
        ui._tag_redraw_viewports()
    except Exception:  # noqa: BLE001 -- headless
        pass
    return True, (f"{len(moved)} object(s) changed to layer {info['name']!r}: "
                  f"{', '.join(sorted(moved))}")


class ChangeLayerCmd(commands.Command):
    """Move selected objects to a typed layer or a layer chosen from the panel menu."""
    name = "ChangeLayer"
    description = "Move the selected objects to another layer."
    steps = [("Select objects to change layer:", commands.OBJECTS_MULTI),
             ("Layer for objects:", TEXT)]
    preselect_names = 1
    cancel_on_empty_set = True

    #: set by the Layers panel's "Change Object Layer" (the layer is the row clicked, so
    #: only the objects are asked for)
    preset_layer = None

    def execute(self, ctx):
        names = self.args[0] if self.args else []
        if isinstance(names, str):
            names = [names]
        layer = self.preset_layer or (self.args[1] if len(self.args) > 1 else None)
        return change_object_layer(list(names), layer)[1], True


PROVIDED = (LayerCmd, ChangeLayerCmd)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
