"""Opt-in, main-thread application metadata reads for the private automation channel.

Python is required for Blender/rhino3dm integration, not geometry computation or a
competing protocol/security system. No UI imports, registration, document loading,
geometry measurements, recipe reconstruction or mutation are performed here.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import sys
import threading

MAX_SCAN = 10_000
MAX_SELECTION = 64
MAX_SELECTION_BYTES = 48 * 1024
MAX_NAME = 256
MAX_PAGE = 32
MAX_CONTEXT_BYTES = 48 * 1024
# MEASURED: subobject.py's canonical selection kinds; no geometry/UI import.
COMPONENT_TYPES = frozenset({"Segment", "Face", "Edge", "Vertex"})
TOOLS = frozenset({
    "narwhal.application_info", "narwhal.document_summary",
    "narwhal.selection_summary", "narwhal.object_summary",
    "narwhal.objects", "narwhal.layers", "narwhal.workspace", "narwhal.components",
})
SAFE_ERRORS = frozenset({
    "tool_not_allowed", "invalid_arguments", "scan_limit", "object_not_found",
    "selection_unavailable", "application_unavailable", "read_failed",
    "result_too_large",
})
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class ReadError(Exception):
    """Only static codes from SAFE_ERRORS may cross the channel."""


def require_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("main_thread_required")


def validate_arguments(tool, arguments):
    if type(tool) is not str or tool not in TOOLS:
        raise ReadError("tool_not_allowed")
    if type(arguments) is not dict:
        raise ReadError("invalid_arguments")
    if tool == "narwhal.object_summary":
        if set(arguments) != {"object_id"}:
            raise ReadError("invalid_arguments")
        value = arguments["object_id"]
        if type(value) is not str or not _UUID.fullmatch(value):
            raise ReadError("invalid_arguments")
    elif tool in {"narwhal.objects", "narwhal.layers"}:
        if (set(arguments) != {"offset", "limit"}
                or type(arguments["offset"]) is not int
                or not 0 <= arguments["offset"] <= MAX_SCAN
                or type(arguments["limit"]) is not int
                or not 1 <= arguments["limit"] <= MAX_PAGE):
            raise ReadError("invalid_arguments")
    elif arguments:
        raise ReadError("invalid_arguments")


def _text(value):
    if value is None:
        return ""
    if type(value) is not str:
        raise ReadError("read_failed")
    return value[:MAX_NAME]


def _object_id(attrs):
    value = str(attrs.Id)
    if not _UUID.fullmatch(value):
        raise ReadError("read_failed")
    return value.lower()


class ApplicationReader:
    """Bounded metadata only; native wrappers never escape read().

    Selection is whole-object scope, at most 65 iterator probes and 10,000 document
    rows, with at most 48 KiB of ASCII-escaped compact JSON including all result
    fields. ctx.selected_objects() itself currently builds a Blender selection list;
    this adapter cannot bound that upstream allocation without changing its API.
    """

    def __init__(self, app_state, application_version: str, *, component_snapshot=None,
                 workspace_snapshot=None):
        require_main_thread()
        if (type(application_version) is not str or not application_version
                or len(application_version) > MAX_NAME):
            raise ValueError("invalid_application_version")
        if component_snapshot is not None and not callable(component_snapshot):
            raise ValueError("invalid_component_snapshot")
        if workspace_snapshot is not None and not callable(workspace_snapshot):
            raise ValueError("invalid_workspace_snapshot")
        self._app = app_state
        self._version = application_version
        self._component_snapshot = component_snapshot
        self._workspace_snapshot = workspace_snapshot
        from .ai_workspace import WorkspaceSnapshots
        self._snapshots = WorkspaceSnapshots(app_state)

    def read(self, tool, arguments):
        require_main_thread()
        validate_arguments(tool, arguments)
        if not self._snapshots.current():
            raise ReadError("application_unavailable")
        try:
            if tool == "narwhal.application_info":
                # Blender has already loaded bpy. Importing the application package or
                # UI to discover a version would introduce unwanted startup side effects.
                bpy = sys.modules.get("bpy")
                if bpy is None:
                    raise ReadError("application_unavailable")
                return {"application_version": self._version,
                        "blender_version": _text(bpy.app.version_string),
                        "permissions": {"read_only": True, "mutations": False}}
            model = self._app.model
            if tool == "narwhal.document_summary":
                tolerance = float(model.absolute_tolerance())
                if not math.isfinite(tolerance):
                    raise ReadError("read_failed")
                return {"object_count": model.object_count(),
                        "layer_count": len(model.file.Layers),
                        "unit_system": _text(model.unit_system()),
                        "tolerance": tolerance,
                        "current_layer_index": int(model.current_layer_index),
                        "title": _text(self._app.doc_title()),
                        "modified": bool(self._app.doc_modified()), "revision": None}
            if tool == "narwhal.objects":
                return self._page(model.file.Objects, "objects", arguments, self._object)
            if tool == "narwhal.layers":
                result = self._page(model.file.Layers, "layers", arguments, self._layer)
                if self._snapshots.ready():
                    flags = self._snapshots.layer_flags([row["layer_id"] for row in result["layers"]])
                    for row in result["layers"]:
                        row["effective_flags"] = flags[row["layer_id"]]
                    while result["layers"] and self._encoded_size(result) > MAX_CONTEXT_BYTES:
                        result["layers"].pop()
                        end = result["offset"] + len(result["layers"])
                        result.update(next_offset=end if end <= MAX_SCAN else None,
                                      truncated=True, truncation_reason="byte_limit")
                if not self._snapshots.current():
                    raise ReadError("application_unavailable")
                return result
            if tool == "narwhal.workspace":
                return self._workspace(model)
            if tool == "narwhal.components":
                return self._components()
            objects = model.file.Objects
            if len(objects) > MAX_SCAN:
                raise ReadError("scan_limit")
            if tool == "narwhal.selection_summary":
                return self._selection(model, objects)
            wanted = arguments["object_id"].lower()
            for obj in itertools.islice(objects, MAX_SCAN):
                if _object_id(obj.Attributes) == wanted:
                    return self._object(obj)
            raise ReadError("object_not_found")
        except ReadError as exc:
            code = exc.args[0] if exc.args else None
            raise ReadError(code if type(code) is str and code in SAFE_ERRORS
                            else "read_failed") from None
        except Exception:
            raise ReadError("read_failed") from None

    @staticmethod
    def snapshot_contract():
        """OURS: Main's optional ``component_snapshot(*, limit=32)`` contract.

        Called once, synchronously on the main thread. Return None when unavailable,
        or exactly {"components": list, "truncated": bool}, with at most limit rows.
        Each row is exactly {"object_id": UUID string, "component_type": canonical
        Segment/Face/Edge/Vertex, "component_index": nonnegative int32}. IDs refer
        to the current File3dm, indices to canonical topology, NEVER display facets.
        Main must resolve names to UUIDs and report unknown/stale mappings as None,
        not empty selection. An available empty list means known empty selection.
        Truncated means additional selected components were omitted. Control grips
        are not covered by this subobject contract (their UI getter prunes state).

        The callback must bound upstream work, not build a full selection then slice;
        must not register/import UI, prune selection, compute geometry or mutate any
        state. This adapter validates/copies metadata, not topology or authorization.
        No independent revision counter, snapshot cache or action permission exists.

        Optional ``workspace_snapshot()`` is called once per valid workspace read,
        on the main thread, with no arguments. Return None or EXACTLY:
        {"cplane": frame_or_none, "view": view_or_none, "command_phase": str_or_none}.
        A frame is exactly {"origin": list3, "xaxis": list3, "yaxis": list3,
        "zaxis": list3}; each list contains three finite builtin int/float values,
        never bools. A view is exactly {"name": str (<=256 characters),
        "projection": "PERSP" | "ORTHO" | "CAMERA"}. command_phase is a string
        of <=64 characters or None. All three keys are required; extras anywhere
        are rejected. No coordinates are calculated, normalized or coerced here.
        Main owns canonical API reads and bounds upstream cost without state edits.

        Non-null values populate result.cplane, result.view and
        result.active_command.phase as {"available": True, "value": copied_value,
        "provenance": {"source": "workspace_snapshot", ...}}. Null values stay
        unavailable, with no value or snapshot provenance. With no callback the
        original unavailable reasons are preserved. Callback None/null fields use
        reason "workspace_snapshot_unavailable". Model/command-name provenance
        remains canonical; a callback cannot override those fields. This is a
        live metadata read, not an atomic snapshot or execution authorization.
        """
        return {"limit": MAX_PAGE, "component_types": sorted(COMPONENT_TYPES)}

    @staticmethod
    def _provenance(source):
        return {"source": source, "consistency": "live_not_snapshot",
                "geometry_measured": False}

    @staticmethod
    def _unavailable(reason):
        return {"available": False, "reason": reason}

    @staticmethod
    def _encoded_size(value):
        return len(json.dumps(value, ensure_ascii=True, allow_nan=False,
                              separators=(",", ":")).encode("ascii"))

    def _page(self, table, key, arguments, describe):
        """MEASURED: rhino3dm 8.35 supports both native tables' __getitem__.

        At most limit native rows are requested, never a Python prefix scan, model
        snapshot/Encode or whole-table conversion. Native internal lookup complexity
        is not asserted. Positions are live table offsets, NOT stable identifiers;
        UUIDs are stable. Concurrent edits between requests may change ordering.
        If the next offset exceeds the schema's 10000 ceiling, report truncation
        with no resumable cursor rather than emitting invalid arguments.
        """
        offset, limit = arguments["offset"], arguments["limit"]
        total = len(table)
        result = {key: [], "offset": offset, "limit": limit, "total_count": total,
                  "next_offset": MAX_SCAN, "truncated": False,
                  "truncation_reason": "offset_limit",
                  "provenance": dict(self._provenance("NarwhalModel.file." + key.title()),
                                     access="indexed")}
        # Reserve the longest pagination shell before adding bounded metadata rows.
        size = self._encoded_size(result)
        byte_limited = False
        for index in range(offset, min(total, offset + limit)):
            entry = describe(table[index])
            cost = self._encoded_size(entry) + bool(result[key])
            if size + cost > MAX_CONTEXT_BYTES:
                byte_limited = True
                break
            result[key].append(entry)
            size += cost
        end = offset + len(result[key])
        more = end < total
        result["truncated"] = more
        result["next_offset"] = end if more and end <= MAX_SCAN else None
        result["truncation_reason"] = (
            "offset_limit" if more and end > MAX_SCAN else
            "byte_limit" if byte_limited else "page_limit" if more else None)
        return result

    @staticmethod
    def _layer(layer):
        return {"layer_id": _object_id(layer), "index": int(layer.Index),
                "name": _text(layer.Name), "parent_layer_id": str(layer.ParentLayerId),
                "visible_local": bool(layer.Visible), "locked_local": bool(layer.Locked),
                "effective_flags": {"available": False,
                                    "reason": "inherited_flags_not_resolved"}}

    def _workspace(self, model):
        """Read canonical model accessors and AppState's live command reference.

        Command.step_index alone is not a phase API (pending options and variable
        point commands differ). UI.active_cplane/get_cplane/active_pane_area exist,
        but require initialized UI context and may default when no pane exists;
        importing UI here is forbidden. Do not substitute a fabricated World Top.
        """
        tolerance = float(model.absolute_tolerance())
        if not math.isfinite(tolerance):
            raise ReadError("read_failed")
        command = self._app.active_command
        result = {"unit_system": _text(model.unit_system()), "tolerance": tolerance,
                "current_layer_index": int(model.current_layer_index),
                "active_command": {"busy": command is not None,
                                   "name": _text(command.name) if command is not None else None,
                                   "phase": self._unavailable("canonical_phase_read_api_unavailable")},
                "cplane": self._unavailable("ui_snapshot_not_connected"),
                "view": self._unavailable("ui_snapshot_not_connected"),
                "provenance": self._provenance("NarwhalModel/AppState.active_command")}
        snapshot_reader = self._workspace_snapshot
        if snapshot_reader is None and self._snapshots.ready():
            snapshot_reader = self._snapshots.workspace
        if snapshot_reader is not None:
            snapshot = snapshot_reader()
            self._validate_workspace_snapshot(snapshot)
            frame = snapshot["cplane"] if snapshot is not None else None
            view = snapshot["view"] if snapshot is not None else None
            phase = snapshot["command_phase"] if snapshot is not None else None
            # Copy only after the whole snapshot passes its exact bounded schema.
            result["cplane"] = self._workspace_value(
                {key: list(value) for key, value in frame.items()} if frame is not None else None)
            result["view"] = self._workspace_value(dict(view) if view is not None else None)
            result["active_command"]["phase"] = self._workspace_value(phase)
        if self._snapshots.ready():
            index = int(model.current_layer_index)
            if 0 <= index < len(model.file.Layers):
                layer_id = str(model.file.Layers[index].Id)
                result["current_layer_effective"] = self._snapshots.layer_flags([layer_id])[layer_id]
            else:
                result["current_layer_effective"] = self._unavailable("current_layer_missing")
        if not self._snapshots.current():
            raise ReadError("application_unavailable")
        return result

    @staticmethod
    def _validate_workspace_snapshot(snapshot):
        if snapshot is None:
            return
        if (type(snapshot) is not dict or len(snapshot) != 3
                or set(snapshot) != {"cplane", "view", "command_phase"}):
            raise ReadError("read_failed")
        frame, view, phase = snapshot["cplane"], snapshot["view"], snapshot["command_phase"]
        if frame is not None:
            if (type(frame) is not dict or len(frame) != 4
                    or set(frame) != {"origin", "xaxis", "yaxis", "zaxis"}):
                raise ReadError("read_failed")
            for vector in frame.values():
                if type(vector) is not list or len(vector) != 3:
                    raise ReadError("read_failed")
                for coordinate in vector:
                    if type(coordinate) not in (int, float) or not math.isfinite(coordinate):
                        raise ReadError("read_failed")
        if view is not None:
            if (type(view) is not dict or len(view) != 2 or set(view) != {"name", "projection"}
                    or type(view["name"]) is not str or len(view["name"]) > MAX_NAME
                    or type(view["projection"]) is not str
                    or view["projection"] not in {"PERSP", "ORTHO", "CAMERA"}):
                raise ReadError("read_failed")
        if phase is not None and (type(phase) is not str or len(phase) > 64):
            raise ReadError("read_failed")

    def _workspace_value(self, value):
        if value is None:
            return self._unavailable("workspace_snapshot_unavailable")
        return {"available": True, "value": value,
                "provenance": self._provenance("workspace_snapshot")}

    def _components(self):
        provenance = self._provenance("component_snapshot")
        snapshot_reader = self._component_snapshot
        if snapshot_reader is None and self._snapshots.ready():
            snapshot_reader = self._snapshots.components
        if snapshot_reader is None:
            return dict(self._unavailable("component_snapshot_not_connected"),
                        provenance=provenance)
        snapshot = snapshot_reader(limit=MAX_PAGE)
        if not self._snapshots.current():
            raise ReadError("application_unavailable")
        if snapshot is None:
            return dict(self._unavailable("canonical_components_unavailable"),
                        provenance=provenance)
        if (type(snapshot) is not dict or set(snapshot) != {"components", "truncated"}
                or type(snapshot["components"]) is not list
                or len(snapshot["components"]) > MAX_PAGE
                or type(snapshot["truncated"]) is not bool):
            raise ReadError("read_failed")
        entries = []
        for entry in snapshot["components"]:
            if (type(entry) is not dict
                    or set(entry) != {"object_id", "component_type", "component_index"}
                    or type(entry["object_id"]) is not str
                    or not _UUID.fullmatch(entry["object_id"])
                    or type(entry["component_type"]) is not str
                    or entry["component_type"] not in COMPONENT_TYPES
                    or type(entry["component_index"]) is not int
                    or not 0 <= entry["component_index"] <= 2_147_483_647):
                raise ReadError("read_failed")
            entries.append(dict(entry, object_id=entry["object_id"].lower()))
        result = {"available": True, "scope": "subobjects", "components": entries,
                  "truncated": snapshot["truncated"], "provenance": provenance}
        if self._encoded_size(result) > MAX_CONTEXT_BYTES:
            raise ReadError("result_too_large")
        return result

    def _selection(self, model, objects):
        selected = list(itertools.islice(self._app.ctx.selected_objects(), MAX_SELECTION + 1))
        truncated = len(selected) > MAX_SELECTION
        names = [ob.name for ob in selected[:MAX_SELECTION]]
        if any(type(name) is not str for name in names):
            raise ReadError("selection_unavailable")
        wanted = set(names)
        found = {}
        if wanted:
            for obj in itertools.islice(objects, MAX_SCAN):
                attrs = obj.Attributes
                oid = _object_id(attrs)
                name = model.blender_name_for(oid) or attrs.Name or ""
                if name not in wanted:
                    continue
                if name in found:
                    raise ReadError("selection_unavailable")
                found[name] = {"object_id": oid, "name": _text(attrs.Name),
                               "layer_index": int(attrs.LayerIndex)}
            if wanted != set(found):
                raise ReadError("selection_unavailable")
        result = {"scope": "whole_objects", "objects": [], "truncated": False}
        # Match the channel's ASCII escaping: a non-BMP character costs twelve
        # bytes, not one. Reserve the whole result shell (false is longer than true),
        # then count each record and comma once, leaving room for the reply envelope.
        encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        encoded_size = len(encoder.encode(result).encode("ascii"))
        for name in dict.fromkeys(names):
            entry = found[name]
            entry_size = len(encoder.encode(entry).encode("ascii")) + (1 if result["objects"] else 0)
            if encoded_size + entry_size > MAX_SELECTION_BYTES:
                truncated = True
                break
            result["objects"].append(entry)
            encoded_size += entry_size
        result["truncated"] = truncated
        return result

    @staticmethod
    def _object(obj):
        from . import kernel

        attrs = obj.Attributes
        geom = obj.Geometry
        declared = kernel.representation_of(geom)
        master_present = kernel.master_of(geom) is not None
        missing = declared == kernel.ANALYTIC_MASTER and not master_present
        return {"object_id": _object_id(attrs), "name": _text(attrs.Name),
                "layer_index": int(attrs.LayerIndex), "storage_type": _text(type(geom).__name__),
                "representation_declared": declared, "master_present": master_present,
                "geometry_source": (None if missing else
                                    "occt_master" if master_present else "document_geometry"),
                "integrity": "missing_master" if missing else "not_audited"}
