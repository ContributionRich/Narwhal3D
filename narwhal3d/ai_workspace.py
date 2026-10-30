"""Main-thread adapters for the initialized UI, not a selection/revision service.

Python is required to reach Blender/UI and rhino3dm objects. Layer inheritance is
computed by the shared Rust core. No imports register UI, no reads prune selection,
and a missing pane never becomes a fabricated World Top. Returned scalars are copies.
"""
from __future__ import annotations

import os
import sys

NIL_UUID = "00000000-0000-0000-0000-000000000000"
MAX_LAYERS = 512


def _main_thread():
    from .automation_tools import require_main_thread
    require_main_thread()


class WorkspaceSnapshots:
    def __init__(self, app_state):
        _main_thread()
        self.app = app_state
        self.model = app_state.model
        self.file = self.model.file

    def current(self):
        _main_thread()
        ui = sys.modules.get(__package__ + ".ui")
        if ui is not None and getattr(ui, "app_state", self.app) is not self.app:
            return False
        return self.app.model is self.model and self.model.file is self.file

    def _ui(self):
        _main_thread()
        if not self.current() or os.environ.get("NARWHAL3D_UI_READY") != "1":
            return None
        ui = sys.modules.get(__package__ + ".ui")
        bpy = sys.modules.get("bpy")
        if (ui is None or getattr(ui, "app_state", None) is not self.app or bpy is None
                or getattr(bpy.app, "background", True)):
            return None
        return ui

    def ready(self):
        return self._ui() is not None

    def workspace(self):
        ui = self._ui()
        if ui is None:
            return None
        result = {"cplane": None, "view": None, "command_phase": self._phase()}
        area = ui.active_pane_area()
        # Validate every condition under which get_cplane would fabricate its
        # fallback. A valid viewport's default CPlane is the UI's canonical plane.
        if area is not None and area.type == "VIEW_3D":
            space = next((s for s in area.spaces if s.type == "VIEW_3D"), None)
            region = getattr(space, "region_3d", None)
            if region is not None and region.view_perspective in ("PERSP", "ORTHO", "CAMERA"):
                plane = ui.get_cplane(area)
                if plane is not None:
                    result["cplane"] = {key: list(getattr(plane, key)) for key in
                                         ("origin", "xaxis", "yaxis", "zaxis")}
                    result["view"] = {"name": str(ui.pane_label(area))[:256],
                                      "projection": region.view_perspective}
        return result if self.current() else None

    def _phase(self):
        command = self.app.active_command
        if command is None:
            return "idle"
        pending = getattr(command, "pending_option", None)
        if pending is not None:
            return "option:" + str(pending.name)[:56]
        if getattr(command, "variable_points", False):
            return "points"
        index = getattr(command, "step_index", None)
        steps = getattr(command, "steps", ())
        if type(index) is int and 0 <= index < len(steps):
            return "input:" + str(steps[index][1])[:57]
        return "awaiting_completion"

    def components(self, *, limit=32):
        ui = self._ui()
        if ui is None:
            return None
        # Existing public getters sort the whole set and return name/index only,
        # with no selection-time UUID/topology generation pin. An O(1) emptiness
        # preflight avoids unbounded enumeration; it does NOT synthesize selection.
        # Nonempty IDs cannot safely be certified, even when a proxy index fits an
        # analytic master's count. Main must supply a pinned, bounded public getter.
        selected = getattr(ui, "_selected_subobjects", None)
        if type(selected) is not set or selected:
            return None
        if ui.selected_subobjects() or not self.current():
            return None
        return {"components": [], "truncated": False}

    def layer_flags(self, targets):
        if not self.ready():
            return {key: {"available": False, "reason": "workspace_unavailable"} for key in targets}
        if len(self.file.Layers) > MAX_LAYERS or len(targets) > 32:
            return {key: {"available": False, "reason": "layer_snapshot_limit"} for key in targets}
        rows = []
        for index in range(len(self.file.Layers)):
            layer = self.file.Layers[index]
            parent = str(layer.ParentLayerId)
            rows.append({"id": str(layer.Id), "parent_id": None if parent == NIL_UUID else parent,
                         "visible": bool(layer.Visible), "locked": bool(layer.Locked)})
        from .ai_assistant import workspace_layer_flags, WorkflowUnavailable
        try:
            flags = workspace_layer_flags(rows, targets)
        except WorkflowUnavailable:
            return {key: {"available": False, "reason": "native_layer_flags_unavailable"} for key in targets}
        if not self.current():
            return {key: {"available": False, "reason": "document_replaced"} for key in targets}
        return flags
