#!/usr/bin/env python3
"""Bounded workspace adapter tests with the real native layer calculator; no GUI."""
import argparse
from contextlib import contextmanager
import copy
import importlib
import io
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch
import uuid

import test_ai_workflow as base
ai = base.ai
workspace = importlib.import_module("narwhal3d.ai_workspace")
tools = importlib.import_module("narwhal3d.automation_tools")
LIBRARY = None
FRAMES = {
    "Top": ((0,0,0),(1,0,0),(0,1,0),(0,0,1)),
    "Front": ((0,0,0),(1,0,0),(0,0,1),(0,-1,0)),
    "Right": ((0,0,0),(0,1,0),(0,0,1),(1,0,0)),
    "Perspective": ((0,0,0),(1,0,0),(0,1,0),(0,0,1)),
    "Custom": ((3,4,5),(0,1,0),(0,0,1),(1,0,0)),
}


@contextmanager
def fixture(label="Top", ready=True):
    layers = [types.SimpleNamespace(Id=uuid.UUID(int=1), ParentLayerId=uuid.UUID(int=0),
                                   Index=0, Name="Root", Visible=False, Locked=True),
              types.SimpleNamespace(Id=uuid.UUID(int=2), ParentLayerId=uuid.UUID(int=1),
                                    Index=1, Name="Child", Visible=True, Locked=False)]
    model = types.SimpleNamespace(file=types.SimpleNamespace(Layers=layers, Objects=[]),
        current_layer_index=1, unit_system=lambda:"Millimeters", absolute_tolerance=lambda:.01)
    app = types.SimpleNamespace(model=model, active_command=None)
    region = types.SimpleNamespace(view_perspective="PERSP" if label=="Perspective" else "ORTHO")
    area = types.SimpleNamespace(type="VIEW_3D", spaces=[types.SimpleNamespace(type="VIEW_3D", region_3d=region)])
    frame = types.SimpleNamespace(**dict(zip(("origin","xaxis","yaxis","zaxis"), FRAMES[label])))
    ui = types.SimpleNamespace(app_state=app, active_pane_area=lambda:area,
        get_cplane=lambda a:frame if a is area else (_ for _ in ()).throw(AssertionError("invalid pane fallback")),
        pane_label=lambda a:label, _selected_subobjects=set(), selected_subobjects=lambda:[])
    loader = ai._load_workflow_library
    with patch.dict(sys.modules, {"narwhal3d.ui":ui,"bpy":types.SimpleNamespace(app=types.SimpleNamespace(background=False))}), \
         patch.dict(os.environ, {"NARWHAL3D_UI_READY":"1" if ready else "0"}), \
         patch.object(ai, "_load_workflow_library", lambda path=None:loader(LIBRARY)):
        yield tools.ApplicationReader(app,"fixture"), app, ui, frame


class WorkspaceTests(unittest.TestCase):
    def test_standard_and_arbitrary_planes_are_copied_not_guessed(self):
        for label in FRAMES:
            with self.subTest(label=label), fixture(label) as (reader, app, _ui, frame):
                before = copy.deepcopy(frame.__dict__)
                result = reader.read("narwhal.workspace", {})
                self.assertEqual(result["view"]["value"]["name"],label)
                self.assertEqual(result["view"]["value"]["projection"], "PERSP" if label=="Perspective" else "ORTHO")
                self.assertEqual(result["cplane"]["value"], {k:list(v) for k,v in before.items()})
                self.assertEqual(result["active_command"]["phase"]["value"],"idle")
                result["cplane"]["value"]["origin"][0]=900
                self.assertEqual(frame.__dict__,before)
                self.assertEqual(app.model.current_layer_index,1)

    def test_unavailable_ui_or_pane_never_reads_fallback(self):
        with fixture(ready=False) as (reader, _app, ui, _frame):
            ui.get_cplane=lambda _:self.fail("fallback read")
            self.assertFalse(reader.read("narwhal.workspace", {})["cplane"]["available"])
        with fixture() as (reader, _app, ui, _frame):
            ui.active_pane_area=lambda:None
            ui.get_cplane=lambda _:self.fail("fallback read")
            self.assertFalse(reader.read("narwhal.workspace", {})["cplane"]["available"])

    def test_pending_option_precedes_step_and_variable_point_phase(self):
        with fixture() as (reader, app, _ui, _frame):
            cmd=types.SimpleNamespace(name="Circle",pending_option=None,variable_points=False,step_index=0,steps=[("Center:","point"),("Radius:","distance")])
            app.active_command=cmd
            self.assertEqual(reader.read("narwhal.workspace", {})["active_command"]["phase"]["value"],"input:point")
            cmd.pending_option=types.SimpleNamespace(name="Area")
            self.assertEqual(reader.read("narwhal.workspace", {})["active_command"]["phase"]["value"],"option:Area")
            cmd.pending_option=None; cmd.variable_points=True
            self.assertEqual(reader.read("narwhal.workspace", {})["active_command"]["phase"]["value"],"points")

    def test_document_replacement_refuses_retained_reader(self):
        with fixture() as (reader, app, _ui, _frame):
            app.model.file=types.SimpleNamespace(Layers=[],Objects=[])
            with self.assertRaisesRegex(tools.ReadError,"application_unavailable"):
                reader.read("narwhal.workspace", {})
        with fixture() as (reader, _app, ui, _frame):
            ui.app_state = object()
            with self.assertRaisesRegex(tools.ReadError,"application_unavailable"):
                reader.read("narwhal.document_summary", {})

    def test_empty_selection_known_nonempty_proxy_or_stale_mapping_unavailable(self):
        with fixture() as (reader, _app, ui, _frame):
            result=reader.read("narwhal.components", {})
            self.assertTrue(result["available"])
            self.assertEqual(result["components"],[])
            ui._selected_subobjects.add(("DisplayProxy", "Face", 1))
            ui.selected_subobjects=lambda:self.fail("unbounded/ambiguous selection enumeration")
            self.assertFalse(reader.read("narwhal.components", {})["available"])

    def test_document_inheritance_not_flat_display_flags(self):
        with fixture() as (reader, _app, _ui, _frame):
            layers=reader.read("narwhal.layers",{"offset":0,"limit":2})["layers"]
            child=layers[1]
            self.assertTrue(child["visible_local"])
            self.assertFalse(child["locked_local"])
            self.assertEqual(child["effective_flags"],{"available":True,"visible":False,"locked":True,"source":"document_layer_parent_chain"})
            self.assertEqual(reader.read("narwhal.workspace",{})["current_layer_effective"], child["effective_flags"])

    def test_missing_parent_cycle_and_layer_budget_are_unavailable(self):
        with fixture() as (reader, app, _ui, _frame):
            app.model.file.Layers[1].ParentLayerId=uuid.UUID(int=99)
            self.assertFalse(reader.read("narwhal.workspace",{})["current_layer_effective"]["available"])
            app.model.file.Layers[1].ParentLayerId=app.model.file.Layers[1].Id
            self.assertEqual(reader.read("narwhal.workspace",{})["current_layer_effective"]["reason"],"cycle")
            app.model.file.Layers=app.model.file.Layers*257
            self.assertEqual(reader.read("narwhal.workspace",{})["current_layer_effective"]["reason"],"layer_snapshot_limit")

    def test_snapshot_cannot_run_on_worker(self):
        with fixture() as (reader, _app, _ui, _frame):
            errors=[]
            def worker():
                try: reader._snapshots.workspace()
                except RuntimeError: errors.append(True)
            thread=threading.Thread(target=worker);thread.start();thread.join(1)
            self.assertEqual(errors,[True])


def mutations():
    original=workspace.WorkspaceSnapshots.workspace
    def wrong_plane(self):
        value=original(self)
        if value is not None: value["cplane"]=dict(zip(("origin","xaxis","yaxis","zaxis"),map(list,FRAMES["Top"])))
        return value
    original_components=workspace.WorkspaceSnapshots.components
    def proxy_components(self, **kwargs):
        if self._ui()._selected_subobjects:
            return {"components":[{"object_id":str(uuid.UUID(int=1)),"component_type":"Face","component_index":1}],"truncated":False}
        return original_components(self, **kwargs)
    variants=[("wrong_plane","workspace",wrong_plane,"test_standard_and_arbitrary_planes_are_copied_not_guessed"),
              ("stale_identity","current",lambda _self:True,"test_document_replacement_refuses_retained_reader"),
              ("proxy_component_substitution","components",proxy_components,"test_empty_selection_known_nonempty_proxy_or_stale_mapping_unavailable")]
    for label,name,mutant,test in variants:
        with patch.object(workspace.WorkspaceSnapshots,name,mutant):
            result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([WorkspaceTests(test)]))
            if result.wasSuccessful(): raise AssertionError("mutation survived: "+label)
        print("RED confirmed:",label)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library",type=Path,required=True)
    parser.add_argument("--mutations",action="store_true")
    args=parser.parse_args();LIBRARY=args.library.resolve()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceTests))
    if not result.wasSuccessful(): raise SystemExit(1)
    if args.mutations:
        mutations()
        result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceTests))
    raise SystemExit(not result.wasSuccessful())
