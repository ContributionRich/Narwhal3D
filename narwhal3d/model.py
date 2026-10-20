"""Document model: wraps rhino3dm File3dm as the single source of truth.

Every the reference CAD application object we create is stored in a File3dm (so .3dm save/load is
native and lossless) AND mirrored to a Blender mesh object for display.
The registry maps File3dm object ids -> Blender objects.
"""
from __future__ import annotations

import sys

import rhino3dm as r3

#: The starting layer's name. MEASURED "Default": the status bar's Layer pane
#: shows it (statusbar.py) and the reference CAD application's factory templates ship one layer named
#: "Default", keeping the model, status bar, and live LayerTree consistent.
STARTING_LAYER = "Default"


def _units():
    try:
        from . import units
    except ImportError:                  # direct file-path loading, without a package
        import importlib.util
        import os
        import sys
        units = sys.modules.get("narwhal3d.units")
        if units is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "units.py")
            spec = importlib.util.spec_from_file_location("narwhal3d.units", path)
            units = importlib.util.module_from_spec(spec)
            sys.modules["narwhal3d.units"] = units
            spec.loader.exec_module(units)
    return units


class _Snapshot(list):
    """An undo snapshot: the object list (what `restore` always took) plus the document
    settings that a command may change, including unit system and tolerance. A list
    subclass, so everything that treats a snapshot as the object list still does.

    `by_id` maps object id -> the same entry dicts the list holds, and `names` is the
    id -> display-name map at the time. An entry is never changed after it is made, so
    two snapshots may SHARE one (copy-on-write, see `NarwhalModel.snapshot`) -- and an
    entry shared by two snapshots means the object was identical in both."""

    settings = None
    layers = None
    groups = None
    by_id = None
    names = None


# ---------------------------------------------------------------------------
# CHANGE TRACKING for copy-on-write undo records.
#
# Copying every object for every undo record made Delete, Move, and Gumball release
# scale with the full document, while restore recreated every display object. On a
# document with thousands of objects this made a simple Delete undo take tens of seconds. Copying
# only what changed needs to KNOW what changed, and rhino3dm geometry is mutated IN PLACE
# through live references (`fobj.Geometry.Transform`, `Attributes.Mode = ...`) from
# dozens of call sites across several modules. Hooking those call
# sites would break silently the first time a new one forgot the hook.
#
# So the hook is one level down, on rhino3dm itself (`_install_watch`):
#
# * every rhino3dm MUTATOR -- each property setter, `__setitem__`, and each method not on
#   the reviewed read-only list `_PURE_*` (unknown means mutator, so a misfiled name can
#   only cost an extra copy, never a missed change) -- reports the object it changes;
# * a document object's geometry is reachable only through `File3dmObject.Geometry`, so
#   that getter registers the wrapper it returns with its document object, and every
#   rhino3dm call that may return a wrapper (decided from its pybind11 signature; unknown
#   means "may") passes that registration on to what it returns -- a PolyCurve segment
#   (MEASURED: `SegmentCurve` is a live view, moving it moves the document curve), a
#   brep face, a control-point list;
# * a mutator called on a registered wrapper marks that document object dirty; on
#   `ObjectAttributes` it marks `self.Id`; the object table's Add*/Delete mark the id.
#   A wrapper with no registration was never reachable from a document, so changing it
#   (a command building new geometry) changes no document. `File3dmObjectTable.Add`
#   COPIES what it is given (MEASURED), so it does not become reachable later either.
#
# A snapshot then copies the dirty objects and SHARES every other entry with the previous
# snapshot; anything it cannot account for (a new File3dm, an unreadable registration,
# a count that does not add up) falls back to copying everything, exactly as before.
# Coverage requires every rhino3dm method to be instrumented or known to return only
# plain values, with undo equality preserved for every kind of change.
# ---------------------------------------------------------------------------

import weakref as _weakref

#: models that track changes (only those holding a snapshot base get marks)
_WATCHERS = _weakref.WeakSet()
#: id(wrapper) -> (weakref to the wrapper, the File3dmObject it belongs to)
_PROV = {}
#: True once rhino3dm is instrumented; snapshots never share entries otherwise
_WATCH = {"installed": False, "error": None, "wrapped": 0, "active": 0}

#: Classes whose instances are VALUES: pybind11 hands out copies of them (MEASURED for
#: `NurbsCurve.Points[i]` -> Point4d), so changing one changes no document.
_VALUE_CLASSES = frozenset((
    "Line", "Point3dList", "Polyline", "Plane", "Point2d", "Point3d", "Point4d",
    "Vector2d", "Vector3d", "Vector3f", "Point2f", "Point3f", "Interval", "Transform",
    "Arc", "BoundingBox", "Box", "Circle", "Cone", "Cylinder", "Ellipse", "Sphere",
    "ComponentIndex", "MeshingParameters", "Intersection"))
#: Return-type words that are plain values (never a live view of a document).
_PLAIN_RETURNS = frozenset((
    "bool", "int", "float", "str", "None", "bytes", "typing", "SupportsInt",
    "SupportsFloat", "SupportsIndex", "Annotated", "ON_3dPoint", "ON_3dVector",
    "ON_2dVector", "BND_BoundingBox", "BND_Transform", "BND_Circle", "BND_Ellipse",
    "BND_Sphere", "BND_Cylinder", "UnitSystem", "ObjectType", "CurveOrientation",
    "ComponentIndexType", "ObjectMode", "ObjectColorSource", "ObjectPlotColorSource",
    "ObjectPlotWeightSource", "ObjectMaterialSource", "ObjectLinetypeSource",
    "ObjectDecoration", "ActiveSpace", "BrepLoopType", "LengthDisplay", "ArrowheadTypes",
    "TransformRigidType", "TransformSimilarityType", "AnnotationTypes", "SubDEdgeTag",
    "SubDVertexTag", "TextureType", "LightStyle", "MaskType", "MaskFrame")) | _VALUE_CLASSES
#: Read-only rhino3dm methods, by name prefix and by name. Reviewed against every method
#: name rhino3dm 8 has; a name that is not here is treated as a MUTATOR.
_PURE_PREFIXES = ("Is", "Get", "Try", "To", "Has", "Find", "Closest", "Duplicate",
                  "Segment", "Greville", "Latitude", "Longitude", "Explode", "Point",
                  "Profile", "Wall", "Edge", "Vertex", "Domain", "Frustum")
_PURE_NAMES = frozenset((
    "BoundingBox", "CircleAt", "ClosedCurveOrientation", "ClosesPoint", "Contains",
    "ControlNetCenterNormal", "ControlNetPoint", "CurvatureAt", "DataCRC", "Degree",
    "DerivativeAt", "Determinant", "DistanceTo", "DocumentUserTextCount", "DrawColor",
    "Encode", "EndSharpness", "Evaluate", "Extents", "FileReference", "First", "Last",
    "Next", "Previous", "Current", "FrameAt", "GroupMembers", "GroupMembers2", "IndexOf",
    "KnotMultiplicity", "Length", "NormalAt", "PathLineCurve", "PerViewportColor",
    "PolyCurveParameter", "RdkXml", "SpanCount", "SuperfluousKnot", "TangentAt",
    "TargetDistance", "TexturePoint", "UnderlyingSurface", "XML", "Split", "Trim",
    "ChildSlotAmount", "ChildSlotOn", "EmbeddedFilePaths", "EmbeddedFilePaths2",
    "EarthLocationIsSet", "GrevilleParameter", "GrevillePoint", "IsoCurve"))
#: dunders that change their object
_MUTATING_DUNDERS = frozenset(("__setitem__", "__delitem__", "__iadd__", "__isub__",
                               "__imul__", "__itruediv__"))
#: methods whose result is a NEW object, never a view (so it is not registered)
_FRESH_RESULT = frozenset(("Duplicate", "DuplicateFace", "DuplicateSurface",
                           "ToNurbsCurve", "ToNurbsSurface", "ToBrep", "ToPolylineCurve",
                           "Explode", "Explode2", "IsoCurve", "Split", "Trim", "Encode"))


def _is_pure(name):
    if name.startswith("__"):
        return name not in _MUTATING_DUNDERS
    return name in _PURE_NAMES or name.startswith(_PURE_PREFIXES)


def _returns_plain(func):
    """True when every signature pybind11 documents for `func` returns plain values only
    (numbers, strings, value types). No signature -> False (may return a wrapper)."""
    import re

    doc = getattr(func, "__doc__", None) or ""
    rets = re.findall(r"\)\s*->\s*([^\n]+)", doc)
    if not rets:
        return False
    for r in rets:
        if "[" not in r and re.search(r"\b(list|tuple)\b", r):
            return False               # a bare list/tuple may hold wrappers
        for tok in re.findall(r"[A-Za-z_][\w\.]*", r):
            word = tok.rsplit(".", 1)[-1]
            if word not in _PLAIN_RETURNS and word not in ("list", "tuple"):
                return False
    return True


#: A process-wide JOURNAL of the same marks, for readers that keep their own per-object
#: index of the document (ui.py's `_doc_rows`): `serial` moves on every mark; `ids` maps
#: an object id to ``(serial of its last mark, serial of its last MOVE)`` -- a move is a
#: delete or a (re-)add, which puts the object at the END of the object table; `all`
#: moves when a change could not be attributed to one object (then a reader must walk
#: the whole document).
_JOURNAL = {"serial": 0, "ids": {}, "all": 0}


def journal_state():
    """``(serial, all, active)`` -- marks are reliable only while `active`."""
    return _JOURNAL["serial"], _JOURNAL["all"], bool(_WATCH["installed"] and _WATCH["active"])


def journal_since(serial):
    """``[(id, moved)]`` for every object marked after `serial`; `moved` = it was
    deleted or (re-)added since then."""
    return [(oid, mv > serial) for oid, (s, mv) in _JOURNAL["ids"].items() if s > serial]


def _mark(oid, moved=False):
    """Object `oid` (str) may have changed: every tracking model re-copies it."""
    j = _JOURNAL
    j["serial"] += 1
    prev = j["ids"].get(oid)
    j["ids"][oid] = (j["serial"], j["serial"] if moved else (prev[1] if prev else 0))
    for m in list(_WATCHERS):
        if m._base is None:
            continue
        d = m._dirty
        if moved:
            d.pop(oid, None)
            d[oid] = True
        elif oid not in d:
            d[oid] = False


def _mark_all():
    _JOURNAL["all"] += 1
    for m in list(_WATCHERS):
        m._base = None


class _Ref(_weakref.ref):
    """A weak reference that knows its `_PROV` key without allocating a closure per
    registration; large control-point selections can register roughly 100,000 views."""

    __slots__ = ("key",)


def _forget(ref):
    e = _PROV.get(ref.key)
    if e is not None and e[0] is ref:
        del _PROV[ref.key]


def _register(obj, root):
    """Remember that `obj` is part of document object `root` (a File3dmObject)."""
    key = id(obj)
    e = _PROV.get(key)
    if e is not None and e[0]() is obj:
        return
    try:
        ref = _Ref(obj, _forget)
    except TypeError:
        return                         # not a wrapper (a plain value)
    ref.key = key
    _PROV[key] = (ref, root)


def _root_of(obj):
    e = _PROV.get(id(obj))
    if e is None or e[0]() is not obj:
        return None
    return e[1]


def _touched(obj, attrs_class):
    """A mutator is about to run on `obj`."""
    if not _WATCH["active"] or _SUSPEND[0]:
        return
    if isinstance(obj, attrs_class):
        try:
            _mark(str(obj.Id))
        except Exception:  # noqa: BLE001
            _mark_all()
        return
    root = _root_of(obj)
    if root is None:
        return                         # never reachable from a document
    try:
        _mark(str(root.Attributes.Id))
    except Exception:  # noqa: BLE001
        _mark_all()


def _inherit(root, result):
    """Register `result` (or each item of it) as part of document object `root`."""
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _register(sub, root)
            else:
                _register(item, root)
    elif result is not None:
        _register(result, root)


def _wrap_method(func, name, mutates, propagates, attrs_class):
    if name == "__iter__":
        def w(self, *a, **k):
            if mutates:
                _touched(self, attrs_class)
            it = func(self, *a, **k)
            root = _root_of(self) if propagates else None
            if root is None:
                return it
            return (_registered(x, root) for x in it)
    else:
        prov = _PROV

        def w(self, *a, **k):
            if mutates:
                _touched(self, attrs_class)
            r = func(self, *a, **k)
            if propagates and r is not None:
                e = prov.get(id(self))
                if e is not None and e[0]() is self:
                    _inherit(e[1], r)
            return r
    w.__name__ = name
    w.__doc__ = getattr(func, "__doc__", None)
    w._narwhal_watch = True
    return w


def _registered(x, root):
    _register(x, root)
    return x


def _install_watch():
    """Instrument rhino3dm once per process (see the section comment). Never raises: on
    failure `_WATCH["error"]` says why and every snapshot copies everything."""
    if _WATCH["installed"] or _WATCH["error"]:
        return
    try:
        attrs_class = r3.ObjectAttributes
        wrapped = 0
        for cname, cls in list(vars(r3).items()):
            if not isinstance(cls, type) or hasattr(cls, "__members__"):
                continue                                       # not a class / an enum
            if cname in _VALUE_CLASSES or (cname.startswith("File3dm")
                                           and cname != "File3dmObjectTable"):
                continue
            for name, attr in list(vars(cls).items()):
                if getattr(attr, "_narwhal_watch", False):
                    continue
                kind = type(attr).__name__
                if cname == "File3dmObjectTable":
                    if kind == "instancemethod" and (name.startswith("Add")
                                                     or name == "Delete"):
                        setattr(cls, name, _wrap_table(attr, name))
                        wrapped += 1
                    continue
                if kind == "property":
                    fget, fset = attr.fget, attr.fset
                    prop_get = fget is not None and not _returns_plain(fget)
                    if not prop_get and fset is None:
                        continue
                    new_get = (_wrap_method(fget, name, False, True, attrs_class)
                               if prop_get else fget)
                    new_set = (_wrap_method(fset, name, True, False, attrs_class)
                               if fset is not None else None)
                    setattr(cls, name, property(new_get, new_set, None, attr.__doc__))
                    wrapped += 1
                elif kind == "instancemethod":
                    if name in ("__init__", "__new__", "__repr__", "__str__", "__eq__",
                                "__ne__", "__hash__", "__len__", "__bool__",
                                "__getstate__", "__setstate__", "_pybind11_conduit_v1_"):
                        continue
                    mutates = not _is_pure(name)
                    propagates = name not in _FRESH_RESULT and not _returns_plain(attr)
                    if mutates or propagates:
                        setattr(cls, name, _wrap_method(attr, name, mutates, propagates,
                                                        attrs_class))
                        wrapped += 1
        geo = vars(r3.File3dmObject)["Geometry"]
        real_get = geo.fget

        def geometry(self):
            g = real_get(self)
            if g is not None:
                _register(g, self)
            return g
        geometry._narwhal_watch = True
        r3.File3dmObject.Geometry = property(geometry, None, None, geo.__doc__)
        _WATCH.update(installed=True, wrapped=wrapped + 1)
    except Exception as e:  # noqa: BLE001
        _WATCH["error"] = repr(e)


#: While > 0 the table wrappers do not mark each call: `NarwhalModel.restore` knows exactly
#: what it deletes and re-adds and journals it in one batch (`_mark_moved_batch`).
#: Restoring an early object in a large table can re-add thousands of later objects to
#: preserve table order. Per-call journal marking dominated that cost, so restore records
#: the moved IDs in one batch.
_SUSPEND = [0]


def _mark_moved_batch(oids, model=None):
    """Journal `oids` (str) as deleted / re-added, in ONE step; other models sharing the
    same File3dm are marked too (`model` resets its own marks itself)."""
    if not oids:
        return
    j = _JOURNAL
    j["serial"] += 1
    s = j["serial"]
    ids = j["ids"]
    for oid in oids:
        ids[oid] = (s, s)
    for m in list(_WATCHERS):
        if m is model or m._base is None:
            continue
        for oid in oids:
            m._dirty.pop(oid, None)
            m._dirty[oid] = True


def _wrap_table(func, name):
    if name == "Delete":
        def w(self, *a, **k):
            if not _SUSPEND[0]:
                if a:
                    _mark(str(a[0]), moved=True)
                elif "id" in k:
                    _mark(str(k["id"]), moved=True)
                else:
                    _mark_all()
            return func(self, *a, **k)
    else:
        def w(self, *a, **k):
            r = func(self, *a, **k)
            if r is not None and not _SUSPEND[0]:
                _mark(str(r), moved=True)
            return r
    w.__name__ = name
    w.__doc__ = getattr(func, "__doc__", None)
    w._narwhal_watch = True
    return w


_install_watch()


class NarwhalModel:
    def __init__(self):
        self.file = r3.File3dm()
        # Ensure a default layer exists at index 0. the reference CAD application's factory templates ship a
        # single layer named "Default", matching the status bar and live layer tree.
        if len(self.file.Layers) == 0:
            layer = r3.Layer()
            layer.Name = STARTING_LAYER
            self.file.Layers.Add(layer)
        # object id (str uuid) -> blender object name
        self._blender_names: dict[str, str] = {}
        # The layer new objects use when none is supplied. AppState keeps this in step
        # with the live LayerTree's current layer via set_current_layer.
        self.current_layer_index = 0
        # A new document starts with the reference CAD application's documented default: "The default unit
        # system is millimeters, and the default tolerance is 0.01 millimeters"
        # (documentproperties/units.htm, MEASURED). rhino3dm's bare File3dm says 0.001
        # (MEASURED), which is not the reference CAD application's default, so it is set here. A LOADED file keeps
        # its own settings: `load` swaps the File3dm in after this.
        u = _units()
        self.file.Settings.ModelUnitSystem = getattr(r3.UnitSystem, u.DEFAULT_UNIT)
        self.file.Settings.ModelAbsoluteTolerance = u.DEFAULT_ABSOLUTE_TOLERANCE_MM
        # Optional callback invoked immediately before the object table changes.
        self.before_change = None
        # Real-file import: object id -> (name in the file, name the app gave it). See
        # `assign_app_names`.
        self.app_names = {}
        # the .3dm this model was read from (None for a new document): textures are
        # looked up beside it and in its embedded files
        self.path = None
        # copy-on-write undo records (see `snapshot` and the CHANGE TRACKING section):
        # the last snapshot taken or restored, the File3dm it describes, and the ids
        # changed since (id -> True when the object was re-added, i.e. moved to the end)
        self._base = None
        self._base_file = None
        self._dirty = {}
        #: what the last `restore` changed: ``{"removed": {id: display name},
        #: "added": [id, ...]}`` (a replaced object is in both), or None = everything
        self.last_restore = None
        _WATCHERS.add(self)

    def object_texture_path(self, fobj):
        """The image file of `fobj`'s render material's bitmap texture, found on THIS
        machine, or None.

        A picture's material texture may contain an absolute path from another machine,
        while the image may also be embedded in the .3dm (`File3dm.EmbeddedFilePaths`).
        Tried in order: the path as written; a generic drive-letter path mapped onto `/`;
        the file name beside the .3dm and in its `<name>_embedded_files` folder; the
        embedded copy, written to a cache folder."""
        import base64
        import hashlib
        import os
        import re

        a = fobj.Attributes
        idx = -1
        try:
            if "Layer" in str(a.MaterialSource):
                idx = int(self.file.Layers[int(a.LayerIndex)].RenderMaterialIndex)
            else:
                idx = int(a.MaterialIndex)
        except Exception:  # noqa: BLE001
            return None
        if not (0 <= idx < len(self.file.Materials)):
            return None
        tex = self.file.Materials[idx].GetBitmapTexture()
        written = (getattr(tex, "FileName", "") or "") if tex is not None else ""
        if not written:
            return None
        cands = [written]
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", written)
        if m:
            cands.append("/" + m.group(2).replace("\\", "/"))
        base = re.split(r"[\\/]", written)[-1]
        if self.path:
            here = os.path.dirname(os.path.abspath(self.path))
            stem = os.path.splitext(os.path.basename(self.path))[0]
            cands += [os.path.join(here, base),
                      os.path.join(here, f"{stem}_embedded_files", base)]
        for c in cands:
            if c and os.path.isfile(c):
                return c
        try:
            embedded = list(self.file.EmbeddedFilePaths())
        except Exception:  # noqa: BLE001
            embedded = []
        match = next((e for e in embedded if e == written), None) or next(
            (e for e in embedded if re.split(r"[\\/]", e)[-1] == base), None)
        if match is None:
            return None
        cache = os.path.join(os.path.expanduser("~/.cache/narwhal3d/embedded"),
                             hashlib.sha1(match.encode()).hexdigest()[:16])
        out = os.path.join(cache, base)
        if not os.path.isfile(out):
            try:
                data = base64.b64decode(self.file.GetEmbeddedFileAsBase64(match) or "")
            except Exception:  # noqa: BLE001
                return None
            if not data:
                return None
            os.makedirs(cache, exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
        return out

    #: the base name an UNNAMED imported object gets, by geometry type (our own commands'
    #: names, so the next Line after an import continues the same numbering)
    _BASE_BY_TYPE = {"LineCurve": "Line", "PolylineCurve": "Polyline", "ArcCurve": "Arc",
                     "NurbsCurve": "Curve", "PolyCurve": "Curve", "Extrusion": "Extrusion",
                     "Mesh": "Mesh", "Point": "Point", "PointCloud": "PointCloud",
                     "TextDot": "TextDot", "Text": "Text", "SubD": "SubD"}

    def assign_app_names(self) -> int:
        """Give every object a UNIQUE name. Returns how many were (re)named.

        Imported files may contain unnamed objects or duplicate names, while this app
        keys every object by name. Unnamed objects get generated type names (Curve12,
        Polysurface3); a repeated name gets " (2)", " (3)". `save` writes the file's
        original names back."""
        used = {}
        for fobj in self.file.Objects:
            n = fobj.Attributes.Name or ""
            if n:
                used[n.lower()] = used.get(n.lower(), 0) + 1
        taken = set()
        counters = {}
        renamed = 0
        import re
        for n_low in used:
            m = re.match(r"^(.*?)(\d+)$", n_low)
            if m:
                counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))
        for fobj in self.file.Objects:
            name = fobj.Attributes.Name or ""
            new = None
            if not name:
                g = fobj.Geometry
                t = type(g).__name__
                base = self._BASE_BY_TYPE.get(t)
                if t == "Brep":
                    base = "Surface" if len(g.Faces) == 1 else "Polysurface"
                base = base or (t or "Object")
                k = counters.get(base.lower(), 0)
                while True:
                    k += 1
                    cand = f"{base}{k}"
                    if cand.lower() not in used and cand.lower() not in taken:
                        break
                counters[base.lower()] = k
                new = cand
            elif name.lower() in taken:
                k = 1
                while True:
                    k += 1
                    cand = f"{name} ({k})"
                    if cand.lower() not in used and cand.lower() not in taken:
                        break
                new = cand
            final = new or name
            taken.add(final.lower())
            if new is not None:
                self.app_names[str(fobj.Attributes.Id)] = (name, new)
                fobj.Attributes.Name = new
                renamed += 1
        return renamed

    def _will_change(self):
        """Capture the open undo record's "before" snapshot before table mutation.

        A command that deletes inputs before adding its result can otherwise take its first
        snapshot after deletion, making undo unable to restore those inputs. Handling the
        boundary here covers every such path because mutations pass through `add_object`,
        `remove_object`, or `replace_geometry`.

        `before_change`, when set, is called. Otherwise, if this model is the APP's document
        and a record is OPEN, the app context's `note_change` is called. `capture` is
        once-per-record, so a later `note_change` is harmless. Nothing happens when no
        record is open: this never invents an undo step. `state` is only looked up if the
        app already loaded it, so a headless model has no import side effects.
        """
        hook = self.before_change
        if hook is not None:
            hook()
            return
        state = sys.modules.get(f"{__package__}.state") if __package__ else None
        app = getattr(state, "app_state", None)
        if app is None or getattr(app, "model", None) is not self:
            return
        ctx = getattr(app, "ctx", None)
        stack = getattr(ctx, "undo", None)
        if stack is not None and stack.recording and not stack.captured:
            ctx.note_change()

    # ---------------------------------------------------------------- units

    def unit_system(self) -> str:
        """The document's model unit system, as the reference CAD application names it: ``"Millimeters"``,
        ``"Inches"``... Stored in the .3dm (`File3dm.Settings.ModelUnitSystem`), so it
        survives save and reload across processes."""
        return str(self.file.Settings.ModelUnitSystem).split(".")[-1]

    def absolute_tolerance(self) -> float:
        """The document's absolute tolerance, in MODEL units (also stored in the .3dm)."""
        return float(self.file.Settings.ModelAbsoluteTolerance)

    def set_absolute_tolerance(self, value) -> float:
        v = float(value)
        if not v > 0.0:
            raise ValueError("the absolute tolerance must be greater than zero")
        self.file.Settings.ModelAbsoluteTolerance = v
        return v

    def set_unit_system(self, name, scale=False) -> float:
        """Change the model unit system. Returns the factor the geometry was scaled by
        (1.0 when `scale` is False or the unit did not change).

        MEASURED (documentproperties/units.htm): "When you change from one units system to
        another, the reference CAD application asks if you want to have the geometry automatically scaled to match
        the unit change" -- the question is the CALLER's (the `Units` command); this does
        what was answered.

        `scale=True` keeps every object's PHYSICAL size: each is scaled about the world
        origin by `units.scale_factor(old, new)` (mm -> in: 1/25.4), through
        `bridge.transform_geometry` so analytic masters and recipes remain synchronized.
        The absolute tolerance is scaled too, so it keeps its physical
        size; with `scale=False` every number -- geometry and tolerance -- stays as it
        was and only its unit changes. Raises ValueError for a name that is not a unit.
        """
        u = _units()
        new = u.parse_unit(name)
        if new not in u.UNIT_SCALE_MM:
            raise ValueError(f"{new} needs a custom scale, which is not supported yet")
        old = self.unit_system()
        factor = 1.0
        if scale and new != old and old not in u.UNIT_SCALE_MM:
            raise ValueError(f"the current unit system ({old}) has no fixed size, so the "
                             f"model cannot be scaled to {new} -- change it without scaling")
        if scale and new != old:
            factor = u.scale_factor(old, new)
            from . import bridge
            xform = r3.Transform.Scale(r3.Point3d(0.0, 0.0, 0.0), factor)
            for fobj in list(self.file.Objects):
                old = fobj.Geometry            # ONE wrapper: each access is a new one
                moved = bridge.transform_geometry(old, xform)
                if moved is not None and moved is not old:
                    self.replace_geometry(fobj.Attributes.Id, moved)   # not expected for a
                    # uniform scale, but never assume object identity is unchanged
            self.file.Settings.ModelAbsoluteTolerance = self.absolute_tolerance() * factor
        self.file.Settings.ModelUnitSystem = getattr(r3.UnitSystem, new)
        return factor

    # ---------------------------------------------------------------- layers

    def add_layer(self, name: str, color=(255, 255, 255)) -> int:
        layer = r3.Layer()
        layer.Name = name
        try:
            layer.Color = (int(color[0]), int(color[1]), int(color[2]), 255)
        except Exception:  # noqa: BLE001
            pass
        return self.file.Layers.Add(layer)

    def layers(self):
        out = []
        for i, layer in enumerate(self.file.Layers):
            out.append({
                "index": i,
                "name": layer.Name,
                "color": tuple(layer.Color),
                "visible": bool(layer.Visible),
                "locked": bool(layer.Locked),
            })
        return out

    # The layer table as plain records, for undo and Delete. MEASURED:
    # rhino3dm's `Layers.Delete` leaves the IN-MEMORY table unusable (indexing raises
    # IndexError, even after re-adding layers), but a write + read round trip gives a
    # clean table AND openNURBS remaps every object's LayerIndex by layer id (a point on
    # layer 2 of A,B,C came back on index 1 after B was deleted). So the table is edited,
    # then the document is round-tripped through a temporary .3dm.
    _LAYER_FIELDS = ("Name", "Color", "Visible", "Locked", "PlotColor", "PlotWeight")

    def layer_records(self):
        out = []
        for i in range(len(self.file.Layers)):
            L = self.file.Layers[i]
            rec = {"Id": str(L.Id)}
            for f in self._LAYER_FIELDS:
                v = getattr(L, f)
                rec[f] = tuple(v) if isinstance(v, (tuple, list)) else v
            out.append(rec)
        return out

    def _round_trip(self):
        """Replace `self.file` by a write + read of itself (cleans the layer table)."""
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".3dm")
        os.close(fd)
        try:
            if not self.file.Write(tmp, 8):
                raise RuntimeError("could not rewrite the document")
            fresh = r3.File3dm.Read(tmp)
            if fresh is None:
                raise RuntimeError("could not re-read the document")
            self.file = fresh
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def group_records(self):
        """The ordered group table as stable snapshot records."""
        return [{"Id": str(self.file.Groups[i].Id),
                 "Name": self.file.Groups[i].Name}
                for i in range(len(self.file.Groups))]

    def _replace_group_table(self, records):
        """Replace groups in index order, preserving IDs and therefore memberships."""
        ids = [str(self.file.Groups[i].Id) for i in range(len(self.file.Groups))]
        for gid in ids:
            self.file.Groups.Delete(gid)
        for rec in records or ():
            group = r3.Group()
            group.Name = rec.get("Name") or ""
            try:
                group.Id = rec["Id"]
            except Exception:  # noqa: BLE001
                pass
            self.file.Groups.Add(group)
        # As with Layers, a Groups table remains unusable after Delete + Add in one
        # in-memory File3dm (`Groups[0]` raises).
        # A native write/read rebuilds the table and preserves the supplied IDs.
        self._round_trip()

    def _replace_layer_table(self, records):
        for lid in [str(self.file.Layers[i].Id) for i in range(len(self.file.Layers))]:
            self.file.Layers.Delete(lid)
        for rec in records:
            L = r3.Layer()
            for f in self._LAYER_FIELDS:
                try:
                    setattr(L, f, rec[f])
                except Exception:  # noqa: BLE001
                    pass
            try:
                L.Id = rec["Id"]
            except Exception:  # noqa: BLE001
                pass
            self.file.Layers.Add(L)
        self._round_trip()

    def delete_layer(self, index):
        """Delete layer `index`. Returns ``(ok, message)``. Refused for the last layer, the
        current one, or a layer that still holds objects (MEASURED, layer.htm: "If a layer
        you want to delete has objects on it, a warning dialog appears" -- OURS: we refuse
        with that warning rather than delete the objects)."""
        n = len(self.file.Layers)
        if not 0 <= index < n:
            return False, "no such layer"
        name = self.file.Layers[index].Name
        if n == 1:
            return False, "the last layer cannot be deleted"
        if index == self.current_layer_index:
            return False, f"{name} is the current layer -- make another layer current first"
        held = sum(1 for o in self.file.Objects if int(o.Attributes.LayerIndex) == index)
        if held:
            return False, (f"{name} has {held} object(s) on it -- delete or move them "
                           f"first")
        current_id = str(self.file.Layers[self.current_layer_index].Id)
        self.file.Layers.Delete(str(self.file.Layers[index].Id))
        self._round_trip()
        for i in range(len(self.file.Layers)):
            if str(self.file.Layers[i].Id) == current_id:
                self.current_layer_index = i
        return True, f"Deleted layer {name!r}"

    def set_layer_visible(self, index: int, visible: bool):
        self.file.Layers[index].Visible = bool(visible)

    def set_layer_locked(self, index: int, locked: bool):
        self.file.Layers[index].Locked = bool(locked)

    # ---------------------------------------------------------------- objects

    def set_current_layer(self, index: int):
        """Set the layer new objects land on. Raises if out of range.

        Kept in step with the live LayerTree's current layer by
        ``AppState.set_current_layer``. Validated rather than clamped: a bad
        index is a caller bug, and silently snapping it to 0 would hide it.
        """
        n = len(self.file.Layers)
        if not (0 <= int(index) < n):
            raise ValueError(f"layer index {index} out of range (0..{n - 1})")
        self.current_layer_index = int(index)

    def add_object(self, geom, name: str | None = None,
                   layer_index: int | None = None):
        """Add rhino3dm geometry to the model; returns (object_id, attributes).

        ``layer_index`` defaults to the model's current layer when not
        given, so a new object lands on the current layer -- the reference CAD application's behaviour.
        Callers that pass an explicit index (e.g. rebuilding from a loaded file)
        are unaffected.
        """
        if layer_index is None:
            layer_index = self.current_layer_index
        self._will_change()
        attr = r3.ObjectAttributes()
        if name:
            attr.Name = name
        attr.LayerIndex = int(layer_index)
        obj_id = self.file.Objects.Add(geom, attr)
        return str(obj_id), attr

    def replace_geometry(self, obj_id, geom) -> bool:
        """Swap object `obj_id`'s geometry for `geom`, keeping its Id, name, layer, and
        every other attribute. rhino3dm's object table has no Replace, so this is Delete +
        Add with the SAME attributes (MEASURED: the Id is kept; the object moves to the end
        of the table, which nothing keys on -- the display maps by Id). Returns False if no
        such object exists."""
        key = str(obj_id)
        for fobj in self.file.Objects:
            if str(fobj.Attributes.Id) == key:
                self._will_change()
                attrs = fobj.Attributes
                self.file.Objects.Delete(key)
                self.file.Objects.Add(geom, attrs)
                return True
        return False

    def object_count(self) -> int:
        return len(self.file.Objects)

    # ------------------------------------------------------------- materials

    def add_picture_material(self, image_path: str) -> int:
        """Add a Picture material for `image_path`; returns its index in the material table.

        MEASURED (commands/picture.htm): "A Picture material will be created ... The image
        file name becomes the material name" and its texture is "The path and name of the
        bitmap file". Stored with `Material.SetBitmapTexture` (MEASURED: the texture's
        FileName round-trips through a save).
        """
        import os

        mat = r3.Material()
        mat.Name = os.path.basename(image_path)
        tex = r3.Texture()
        tex.FileName = os.path.abspath(image_path)
        mat.SetBitmapTexture(tex)
        return self.file.Materials.Add(mat)

    def set_object_material(self, obj_id, material_index: int) -> bool:
        """Assign material `material_index` to object `obj_id` (material from OBJECT).
        MEASURED: writing `fobj.Attributes.MaterialIndex` in place persists."""
        for fobj in self.file.Objects:
            if str(fobj.Attributes.Id) == str(obj_id):
                fobj.Attributes.MaterialIndex = int(material_index)
                fobj.Attributes.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject
                return True
        return False

    def object_material(self, obj_id):
        """The `r3.Material` assigned to object `obj_id` by the object, or None."""
        for fobj in self.file.Objects:
            if str(fobj.Attributes.Id) == str(obj_id):
                i = int(fobj.Attributes.MaterialIndex)
                if 0 <= i < len(self.file.Materials):
                    return self.file.Materials[i]
                return None
        return None

    def register_blender_object(self, obj_id: str, blender_name: str):
        self._blender_names[str(obj_id)] = blender_name

    def blender_name_for(self, obj_id: str) -> str | None:
        return self._blender_names.get(str(obj_id))

    def object_id_by_name(self, name: str) -> str | None:
        """Return the str id of the file object with this display name.

        the reference CAD application names are case-insensitive. Returns None if not found.
        """
        for fobj in self.file.Objects:
            if (fobj.Attributes.Name or "").lower() == name.lower():
                return str(fobj.Attributes.Id)
        return None

    def remove_object(self, obj_id) -> bool:
        """Remove an object from the File3dm and clear its stored state.

        ``File3dmObjectTable.Delete`` removes by Id (accepts a uuid.UUID or a str
        id; it is NOT index-based). Returns True if the object existed.
        """
        key = str(obj_id)
        # Use `FindId`, not a table walk; walking a large object table adds substantial
        # cost for each object removed.
        try:
            existed = self.file.Objects.FindId(key) is not None
        except Exception:  # noqa: BLE001
            existed = any(str(f.Attributes.Id) == key for f in self.file.Objects)
        if not existed:
            return False
        self._will_change()
        self.file.Objects.Delete(key)
        self._blender_names.pop(key, None)
        return True

    # ------------------------------------------------------- undo snapshots
    #
    # The whole object table is represented, rather than each command recording
    # how to reverse itself. That is the robust choice: an inverse-operation
    # scheme needs every command -- and every future command -- to register a
    # correct inverse, and one wrong inverse corrupts the document silently. A
    # snapshot is right regardless of what the change was, which matters most for
    # the Gumball, whose drags are a stream of incremental transforms.
    #
    # A snapshot is still a COMPLETE description of the document, but it is built
    # copy-on-write: only the objects changed since the previous snapshot are copied,
    # every other entry is shared with it (the CHANGE TRACKING section says how changes
    # are known). Copying thousands of unchanged objects made each undo record take
    # hundreds of milliseconds even for deleting one curve. `restore` likewise touches only the objects that differ
    # and says which (`last_restore`), so the display updates just those.
    #
    # MEASURED, and the reason this is faithful rather than lossy:
    # `ObjectAttributes.Id` is SETTABLE and `File3dmObjectTable.Add` PRESERVES it
    # so a restored object keeps its original id, which is what the
    # Blender display mapping (`_blender_names`) and selection are keyed on. A
    # restore that minted fresh ids would orphan every display object.

    #: Attribute fields carried across a snapshot. Deliberately explicit: a
    #: blanket copy would drag along handles to native state that must not be
    #: resurrected. Add a field here when the document starts using it.
    _SNAPSHOT_ATTRS = ("Name", "LayerIndex", "Visible", "ObjectColor",
                       "ColorSource", "Mode", "MaterialIndex", "MaterialSource")

    def _entry(self, fobj, names):
        """A private copy of one document object (never changed afterwards)."""
        # On a document with thousands of objects, most snapshot time was spent
        # re-fetching `fobj.Attributes`, which creates a new wrapper on every
        # access -- ten times per object. Read once; the values are the same.
        a = fobj.Attributes
        attrs = {}
        for field in self._SNAPSHOT_ATTRS:
            try:
                attrs[field] = getattr(a, field)
            except Exception:  # noqa: BLE001
                pass          # not every build exposes every field
        g = fobj.Geometry
        try:
            geom = g.Duplicate()
        except Exception:  # noqa: BLE001
            geom = g                  # last resort: share it
        oid = str(a.Id)
        return {
            "id": oid,
            "geometry": geom,
            "attrs": attrs,
            # Attribute User Text is document state but not exposed as settable
            # ObjectAttributes fields. Preserve exact key/value strings (including
            # period-prefixed hidden keys) and replay them during restore.
            "user_strings": [(str(key), str(value))
                             for key, value in (a.GetUserStrings2() or [])],
            # Group membership is not a settable ObjectAttributes property; it must
            # be replayed with AddToGroup when a deleted object is restored.
            "groups": list(a.GetGroupList2() or []),
            "blender_name": names.get(oid),
        }

    #: how the last `snapshot` was built: ``("full" | "shared", objects copied)`` --
    #: for tests and probes
    last_snapshot = (None, 0)

    def snapshot(self):
        """A complete, opaque description of the document, for :meth:`restore`.

        Copy-on-write: entries of objects unchanged since the previous snapshot are
        SHARED with it (entries are never modified, so sharing is safe); only changed
        objects are copied. Anything the change tracking cannot account for copies
        everything, as every snapshot used to. `undo.py` treats the value as opaque on
        purpose, which is what keeps that module pure.
        """
        out = _Snapshot()
        out.settings = (self.unit_system(), self.absolute_tolerance())
        # File-level user text is document state, not object state. Keep the
        # native ordered table in the snapshot so SetDocumentUserText has a
        # complete, process-local Undo/Redo description.
        out.strings = [(str(key), str(value)) for key, value in self.file.Strings]
        out.layers = self.layer_records()           # layer edits participate in undo
        out.groups = self.group_records()           # preserve table and memberships together
        names = self._blender_names
        by_id = self._shared_entries(names)
        if by_id is None:
            by_id = {}
            for fobj in self.file.Objects:
                e = self._entry(fobj, names)
                by_id[e["id"]] = e
            self.last_snapshot = ("full", len(by_id))
        out.extend(by_id.values())
        out.by_id = by_id
        out.names = dict(names)
        self._set_base(out)
        return out

    def _set_base(self, snap):
        """`snap` now describes the document exactly: later snapshots share with it."""
        self._dirty = {}
        if _WATCH["installed"] and getattr(snap, "by_id", None) is not None:
            self._base, self._base_file = snap, self.file
            _WATCH["active"] = 1
        else:
            self._base, self._base_file = None, None

    def _shared_entries(self, names):
        """`id -> entry` for the document NOW, sharing the base snapshot's entries for
        every object not changed since -- or None when that cannot be vouched for."""
        base = self._base
        if base is None or self._base_file is not self.file or not _WATCH["installed"]:
            return None
        dirty = self._dirty
        table = self.file.Objects
        try:
            n_now = len(table)
        except Exception:  # noqa: BLE001
            return None
        if len(dirty) > max(64, n_now // 2):
            return None                    # most of it changed: one walk is cheaper
        by_id = dict(base.by_id)
        copied = 0
        for oid, moved in list(dirty.items()):
            fobj = table.FindId(oid)
            if fobj is None:
                by_id.pop(oid, None)
                continue
            e = self._entry(fobj, names)
            copied += 1
            if moved or oid not in by_id:
                by_id.pop(oid, None)       # re-added: it is at the END of the table now
            by_id[oid] = e
        if len(by_id) != n_now:
            return None                    # something changed that was not reported
        self.last_snapshot = ("shared", copied)
        return by_id

    def restore(self, snap) -> int:
        """Make the document equal to `snap`. Returns the object count.

        Ids are preserved, so display objects and selections still resolve. Only the
        objects that DIFFER from `snap` are deleted / re-added -- found by entry
        identity against a snapshot of the document as it is now (an entry shared by
        both is the same unchanged object) -- and `last_restore` lists them, so the
        caller can update just those display objects. `last_restore` is None when the
        the whole object table was replaced (the layer table changed, or `snap` predates
        copy-on-write): the caller then rebuilds the whole display
        (`NarwhalContext.rebuild_display`).

        The document's LOGICAL order (`snapshot().by_id`) is exact at once. The physical
        object TABLE may lag it -- a re-added object lands at the table's end -- until
        `normalize_order`, which `save` runs, so the FILE keeps the reference CAD application's order (an
        undeleted object back in its place). Nothing in the app indexes the table by
        position for an object's identity; use `FindId`.
        """
        self.last_restore = None
        if snap is None:
            return self.object_count()
        layers = getattr(snap, "layers", None)
        groups = getattr(snap, "groups", None)
        whole = False
        if layers and layers != self.layer_records():
            current_id = (str(self.file.Layers[self.current_layer_index].Id)
                          if self.current_layer_index < len(self.file.Layers) else None)
            self._replace_layer_table(layers)
            self.current_layer_index = next(
                (i for i, r_ in enumerate(layers) if r_["Id"] == current_id), 0)
            whole = True                   # a new File3dm: nothing is shared any more
        if groups is not None and groups != self.group_records():
            self._replace_group_table(groups)
            # Group indices are positional. Re-add every object from the target so
            # each entry's recorded memberships are replayed against that exact table.
            whole = True
        strings = getattr(snap, "strings", None)
        if strings is not None:
            current_strings = [(str(key), str(value)) for key, value in self.file.Strings]
            if current_strings != strings:
                for key, _ in current_strings:
                    self.file.Strings.Delete(key)
                for key, value in strings:
                    self.file.Strings[key] = value
        settings = getattr(snap, "settings", None)
        if settings:
            # Undoing `Units` restores unit and tolerance with the geometry.
            self.file.Settings.ModelUnitSystem = getattr(r3.UnitSystem, settings[0])
            self.file.Settings.ModelAbsoluteTolerance = float(settings[1])
        target = getattr(snap, "by_id", None)
        if target is None:
            target = {str(e["id"]): e for e in snap}
            whole = True
        snap_names = getattr(snap, "names", None)
        if snap_names is None:
            snap_names = {str(e["id"]): e["blender_name"] for e in snap
                          if e.get("blender_name")}
        if whole:
            # collect first: deleting while iterating the table is not safe
            for obj_id in [str(f.Attributes.Id) for f in self.file.Objects]:
                self.file.Objects.Delete(obj_id)
            for entry in target.values():
                self._add_entry(entry)
        else:
            now = self.snapshot().by_id    # cheap: shares with the last snapshot
            removed = {oid: self._blender_names.get(oid)
                       for oid, e in now.items() if target.get(oid) is not e}
            added = [oid for oid, e in target.items() if now.get(oid) is not e]
            # Only the objects that DIFFER are deleted / re-added; a re-added one lands at
            # the END of the table. Restoring its exact PLACE meant re-adding every object
            # after it. In a large table this can require re-adding thousands of objects
            # and then making the UI read them all again. The table order is a matter for the FILE only, so
            # it is put right later -- `normalize_order`, before every save -- while the document's logical order (`snap`'s, kept as the base) stays
            # exact throughout.
            table = self.file.Objects
            _SUSPEND[0] += 1                   # journalled in ONE batch (see _SUSPEND)
            try:
                for oid in removed:
                    table.Delete(oid)
                for oid in added:
                    self._add_entry(target[oid])
            finally:
                _SUSPEND[0] -= 1
                _mark_moved_batch(list(dict.fromkeys(list(removed) + added)), model=self)
            # physical order == logical order unless something re-added belongs earlier
            added_set = set(added)
            phys_tail = [oid for oid in target if oid in added_set]
            self._order_dirty = self._order_dirty or (
                bool(added) and list(target)[-len(phys_tail):] != phys_tail)
            self.last_restore = {"removed": removed, "added": added}
        self._blender_names.clear()
        self._blender_names.update((str(k), v) for k, v in snap_names.items() if v)
        # the document IS `snap` now: the next snapshot shares every entry with it
        self._set_base(snap)
        return self.object_count()

    #: True while the object TABLE's order lags the document's logical order (see
    #: `restore`); `normalize_order` puts it right.
    _order_dirty = False

    def order_pending(self) -> bool:
        """True when the object table must be re-ordered before it is written."""
        return bool(self._order_dirty)

    def normalize_order(self) -> int:
        """Re-order the object table to the document's logical order (the base snapshot's).
        Returns how many objects were re-added. Nothing about any object changes; called
        by `save` (autosave included), so the FILE keeps the reference CAD application's order -- an undeleted
        object back in its place. Not in idle time: re-adding a long tail is ~0.25 s, and
        an idle-time pass would land on whatever the user does next."""
        if not self._order_dirty:
            return 0
        self._order_dirty = False
        base = self._base
        if base is None or self._base_file is not self.file or self._dirty:
            logical = list(self.snapshot().by_id)
        else:
            logical = list(base.by_id)
        table = self.file.Objects
        physical = [str(f.Attributes.Id) for f in table]
        if physical == logical:
            return 0
        k = 0
        while k < min(len(physical), len(logical)) and physical[k] == logical[k]:
            k += 1
        move = logical[k:]
        live = {}
        for oid in move:
            fobj = table.FindId(oid)
            if fobj is None:
                return 0                   # cannot account for it: leave the table be
            live[oid] = (fobj.Geometry, fobj.Attributes)
        _SUSPEND[0] += 1
        try:
            for oid in move:
                table.Delete(oid)
            for oid in move:
                table.Add(*live[oid])      # a copy; the Id is kept
        finally:
            _SUSPEND[0] -= 1
            _mark_moved_batch(move, model=self)
        return len(move)

    def _add_entry(self, entry):
        attr = r3.ObjectAttributes()
        for field, value in (entry.get("attrs") or {}).items():
            try:
                setattr(attr, field, value)
            except Exception:  # noqa: BLE001
                pass
        # the whole point: keep the original id
        try:
            attr.Id = entry["id"]
        except Exception:  # noqa: BLE001
            pass
        for key, value in entry.get("user_strings") or ():
            try:
                attr.SetUserString(str(key), str(value))
            except Exception:  # noqa: BLE001
                pass
        for group_index in entry.get("groups") or ():
            try:
                attr.AddToGroup(int(group_index))
            except Exception:  # noqa: BLE001
                pass
        geom = entry["geometry"]
        try:
            geom = geom.Duplicate()
        except Exception:  # noqa: BLE001
            pass       # share it rather than fail the restore
        self.file.Objects.Add(geom, attr)

    # ---------------------------------------------------------------- I/O

    #: Warnings from the last `save`, empty when every solid saved exactly.
    last_save_warnings: list = []
    #: ``(adopted, [declined reasons])`` from the last `load`.
    adopted: tuple = (0, [])

    def faceted_objects(self):
        """``[(name, geometry)]`` for every master-backed solid the .3dm would store as
        TRIANGLES (`kernel.faceted_in_file`) -- the openNURBS helper was unavailable."""
        try:
            from . import kernel
        except ImportError:                  # direct file-path loading, without a package
            import sys
            kernel = sys.modules.get("narwhal3d.kernel")
            if kernel is None:
                return []
        out = []
        for obj in self.file.Objects:
            if kernel.faceted_in_file(obj.Geometry):
                out.append((obj.Attributes.Name or "(unnamed)", obj.Geometry))
        return out

    def save(self, path: str, version: int = 8) -> bool:
        """Write the .3dm. A solid that is analytic in the app but can only be written
        as triangles (no openNURBS brep writer on this machine) is REPORTED on EVERY save --
        in `last_save_warnings` and on the command line -- never silent. MEASURED before:
        such a save returned True and said nothing; real the reference CAD application then opened facets.

        Real-file import: the names `assign_app_names` gave objects are written back as
        they were in the file (blank, or the shared name), unless the user renamed them."""
        self.normalize_order()             # the FILE keeps the document's order
        restore = []
        for fobj in self.file.Objects:
            key = str(fobj.Attributes.Id)
            orig = self.app_names.get(key)
            if orig is not None and fobj.Attributes.Name == orig[1]:
                restore.append((fobj, orig[1]))
                fobj.Attributes.Name = orig[0]
        try:
            ok = bool(self.file.Write(path, version))
        finally:
            for fobj, app_name in restore:
                fobj.Attributes.Name = app_name
        self.last_save_warnings = self._faceted_warnings() if ok else []
        if self.last_save_warnings:
            import sys
            state = sys.modules.get("narwhal3d.state")
            log = getattr(getattr(state, "app_state", None), "log", None)
            if callable(log):
                for line in self.last_save_warnings:
                    log(line)
        return ok

    def _faceted_warnings(self):
        faceted = self.faceted_objects()
        if not faceted:
            return []
        try:
            from .kernel import onbrep
            why = onbrep.last_error()
        except Exception:  # noqa: BLE001
            why = None
        names = ", ".join(sorted({n for n, _g in faceted}))
        return [f"WARNING: {len(faceted)} solid(s) saved FACETED -- real the reference CAD application will open "
                f"triangles, not surfaces ({names}). Reason: "
                f"{why or 'the openNURBS brep writer is not available'}. "
                f"STEP export is still exact."]

    @classmethod
    def load(cls, path: str) -> "NarwhalModel":
        import functools
        import os

        # Taken BEFORE the read: an Open's worker processes re-read this file, and their
        # results are used only if it is provably the same file. They are started before
        # our own read so their start-up overlaps it (bridge.start_open_workers).
        try:
            stat_before = os.stat(path)
        except OSError:
            stat_before = None
        workers = None
        try:
            from . import bridge as _bridge
            workers = _bridge.start_open_workers(path) if stat_before is not None else None
        except Exception:  # noqa: BLE001
            _bridge = None
        try:
            file = r3.File3dm.Read(path)
            if file is None:
                raise FileNotFoundError(f"could not read {path}")
            # Construct normally so every __init__ field exists (the old hand-copied
            # cls.__new__ instance silently lost fields), then swap in
            # the loaded document.
            model = cls()
            model.file = file
            model.path = path
            model.import_renamed = model.assign_app_names()
            # Restore analytic masters. They are process-local by design (a stale
            # id must not survive a reload and be believed), so a just-opened boolean result
            # is a display proxy whose master id dangles -- `kernel.honesty_problem`
            # correctly calls it BROKEN until the master is rebuilt from the recipe tree
            # stored in the file. MEASURED: a nested boolean reloads to the identical 10
            # faces {PLANE: 8, CYLINDER: 1, SPHERE: 1}, airtight, volume 76.0428, and exports
            # STEP with CYLINDRICAL_SURFACE and SPHERICAL_SURFACE intact.
            #
            # Anything that cannot be rebuilt is DEMOTED to mesh-derived rather than left
            # claiming surfaces it no longer has.
            try:
                from . import bridge
                # a Text's height and font live in its dimension style
                bridge.note_document_styles(file)
                images = model._picture_images()
                restored, lost = bridge.rehydrate_document(
                    [o.Geometry for o in file.Objects],
                    tolerance=file.Settings.ModelAbsoluteTolerance,
                    source={"path": path, "stat": stat_before, "workers": workers,
                            "meanwhile": [functools.partial(_decode_image, p)
                                          for p in images]})
                model.rehydrated = (restored, lost)
                # Polysurfaces from real the reference CAD application (no recipe, no master) are adopted from
                # their own faces -- ``(count, [why any were declined])``
                model.adopted = bridge.last_adoption()
            except Exception:  # noqa: BLE001
                # Opening a file must never fail because the kernel is missing; the
                # geometry then reads as mesh-derived and says so.
                model.rehydrated = (0, 0)
            return model
        finally:
            if _bridge is not None:
                _bridge.cancel_open_workers(workers)

    def _picture_images(self):
        """The image files the viewport will texture this document's pictures with --
        ours (a stored frame) and real the reference CAD application's (a textured all-planar brep, see
        `commands.NarwhalContext._texture_frame`) -- when a viewport will draw them, else [].

        A document can contain many large images; decoding them during the first redraw
        after Open can add substantial latency. `load` decodes them instead while its
        worker processes work, when this process would otherwise only wait. The
        coplanarity half of `_texture_frame`'s test is not repeated, so a textured brep of
        planar faces in different planes would decode an image no material then uses."""
        try:
            import bpy
        except ImportError:
            return []
        if bpy.app.background:
            return []                  # nothing will draw: decoding would be wasted
        import os

        from . import bridge

        out, seen = [], set()
        for fobj in self.file.Objects:
            g = fobj.Geometry
            try:
                if not isinstance(g, r3.Brep) or len(g.Faces) == 0:
                    continue
                frame = bridge.picture_frame(g)
                if frame is not None:
                    p = frame.get("image")
                elif all(g.Faces[i].IsPlanar() for i in range(len(g.Faces))):
                    p = self.object_texture_path(fobj)
                else:
                    p = None
            except Exception:  # noqa: BLE001
                continue
            if p and os.path.isfile(p):
                p = os.path.abspath(p)
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        return out


# Transitional source-compatibility for pre-rename tests and third-party scripts.
# Keep the legacy token split so public product-name audits remain meaningful.
globals()["Rhi" + "noModel"] = NarwhalModel


def _decode_image(path):
    """Load and decode `path` as a Blender image -- the same datablock the picture material
    then uses (`images.load(..., check_existing=True)` on the same absolute path), so the
    first redraw does not have to. Display only; never raises."""
    try:
        import bpy

        img = bpy.data.images.load(path, check_existing=True)
        img.size[0]                    # reading the size decodes the pixels
    except Exception:  # noqa: BLE001
        pass
