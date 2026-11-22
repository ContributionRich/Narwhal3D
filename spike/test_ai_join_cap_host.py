#!/usr/bin/env python3
"""Join and cap typed batch actions: SDK dispatch + real publish/Undo/Redo/reload.

Two layers are covered:

1. `JoinCapSDKDispatch` -- the narrow Python SDK dispatch
   (`ai_modeling._construct` for `join` and `cap`) against the real bridge / OCCT /
   native geometry stack. These prove the dispatch calls the exact producers, enforces
   the single-connected-group join contract, raises a stable canonical code (never a
   raw kernel path) on cap refusal, leaves source geometry unchanged, and that a capped
   solid survives save/reload.

2. `JoinCapHostPublish` -- the actual-host `publish_steps` boundary (Blender, document,
   Undo/Redo, save/reload). These drive the full Review/Build/typed-batch MCP contract:
   a 4-patch join->cap yields one airtight solid (then Undo/Redo + save/reload); a
   Normal loft->cap dependency publishes an airtight solid; a non-planar cap is refused
   with the canonical `cap_planar_closure_refused` and no document change; a
   disconnected join is refused with `join_connected_group_required` and no document
   change.

Rust owns the schema, validation, dependencies, receipt policy (cap must be a solid,
join may be open) and authority -- see `native/automation_core/tests/join_cap_policy.rs`.

Python is used only for rhino3dm/OCP handle glue and SDK dispatch -- no geometry math.

Run under the Blender runtime (the publish harness needs bpy):

    env LD_LIBRARY_PATH=<cadquery_ocp_novtk.libs> \
        PYTHONPATH=<vendor/occt> \
        NARWHAL_GEOMETRY_CORE=<native geometry core .so> \
        NARWHAL3D_BREP_WRITER=<brep_writer-*> \
        <blender> -b --factory-startup --python-exit-code 1 \
        --python spike/test_ai_join_cap_host.py
"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spike'))
sys.path.insert(0, str(ROOT))
import test_ai_workflow as base
from test_ai_primitives import ui_fixture
from unittest.mock import patch
from types import SimpleNamespace
import unittest
import tempfile
import bpy
import rhino3dm as r3
from narwhal3d import model, commands, ai_modeling, ai_workspace, kernel, bridge, cplane
from narwhal3d.kernel import occt_backend as ob

DOC = 'joincap-owned-document'
LIBRARY = ROOT / 'native/automation_core/target/debug/libnarwhal_automation.so'
TOL = 0.01


def step(id, action, publish=True, replace=None):
    return dict(id=id, name=id, action=action, publish=publish, replace=replace)


def make_loft():
    """A closed Normal loft of four circular sections -- open at both ends."""
    curves = [r3.Circle(r3.Point3d(0, 0, z), rad).ToNurbsCurve()
              for z, rad in [(0, 2), (3, 3), (7, 2.5), (10, 2)]]
    return bridge.loft_brep(curves, style='Normal')


def loft_patches(loft, count=4):
    """Exact trimmed patches of one Normal loft (the exact shared-trim fixture).

    Splitting one exact surface into patches keeps the trims shared and the join honest;
    independently lofting quarter curves can differ numerically and Join would refuse
    them under its unchanged-trim contract.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    master = ob.from_recipe(kernel.recipe_of(loft))
    face = ob.unique_faces(master)[0]
    surface = BRep_Tool.Surface_s(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    pieces = []
    for i in range(count):
        pf = BRepBuilderAPI_MakeFace(surface, u0 + (u1 - u0) * i / count,
                                     u0 + (u1 - u0) * (i + 1) / count, v0, v1, 1e-7).Face()
        pieces.append((str(i), bridge.occt_backed(pf, ob.native_master_recipe(pf))))
    return pieces


class JoinCapSDKDispatch(unittest.TestCase):
    """The narrow Python dispatch for `join` and `cap` against the real stack."""

    @staticmethod
    def _resolve(mapping):
        return lambda sid: mapping[sid]

    # ── positive: closed Normal loft -> cap is a verified airtight solid ────
    def test_closed_loft_cap_via_sdk(self):
        loft = make_loft()
        self.assertFalse(ai_modeling.inspect_geometry(loft, TOL)['airtight'],
                         'a Normal loft of closed sections is open at the ends')
        capped = ai_modeling._construct({'op': 'cap', 'source': 'L'},
                                        self._resolve({'L': loft}), TOL)
        facts = ai_modeling.inspect_geometry(capped, TOL)
        self.assertTrue(facts['airtight'])
        self.assertTrue(facts['solid'])
        self.assertGreater(facts['volume'], 0)
        self.assertEqual(facts['face_types'], {'BSPLINE': 1, 'PLANE': 2})
        self.assertIsNotNone(kernel.master_of(capped), 'capped solid must carry an analytic master')
        self.assertEqual(kernel.recipe_of(capped)['kind'], 'cap')

    # ── positive: disjoint trimmed patches -> join (open) -> cap (solid) ───
    def test_joined_patches_cap_via_sdk(self):
        loft = make_loft()
        pieces = loft_patches(loft, 4)
        joined = ai_modeling._construct(
            {'op': 'join', 'sources': [str(i) for i in range(4)]},
            self._resolve(dict(pieces)), TOL)
        jf = ai_modeling.inspect_geometry(joined, TOL)
        self.assertFalse(jf['airtight'], 'a joined open surface must stay open')
        self.assertFalse(jf['solid'])
        self.assertEqual(jf['recipe_kind'], 'join_exact')
        capped = ai_modeling._construct({'op': 'cap', 'source': 'J'},
                                        self._resolve({'J': joined}), TOL)
        cf = ai_modeling.inspect_geometry(capped, TOL)
        self.assertTrue(cf['airtight'])
        self.assertTrue(cf['solid'])
        self.assertGreater(cf['volume'], 0)
        self.assertEqual(cf['face_types'], {'BSPLINE': 4, 'PLANE': 2})

    # ── refusal: a join must yield exactly ONE group consuming ALL sources ──
    def test_disconnected_join_refused_and_sources_unchanged(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln, gp_Ax3
        f1 = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 0., 1., 0., 1.).Face()
        f2 = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 100., 101., 0., 1.).Face()
        items = [(str(i), bridge.occt_backed(f, ob.native_master_recipe(f)))
                 for i, f in enumerate((f1, f2))]
        mapping = dict(items)
        encoded = [g.Encode() for _, g in items]
        with self.assertRaisesRegex(ValueError, r'join_connected_group_required'):
            ai_modeling._construct({'op': 'join', 'sources': ['0', '1']},
                                   self._resolve(mapping), TOL)
        for i, (_, geom) in enumerate(items):
            self.assertEqual(geom.Encode(), encoded[i], 'source geometry must be unchanged by a refused join')

    def test_disconnected_solid_join_refused_and_sources_unchanged(self):
        b1 = bridge.make_box((0, 0, 0), (2, 3, 0), 3, plane=cplane.CPlane(origin=(0, 0, 0)))
        b2 = bridge.make_box((100, 0, 0), (102, 3, 0), 3, plane=cplane.CPlane(origin=(100, 0, 0)))
        mapping = {'a': b1, 'b': b2}
        encoded = (b1.Encode(), b2.Encode())
        with self.assertRaisesRegex(ValueError, r'join_connected_group_required'):
            ai_modeling._construct({'op': 'join', 'sources': ['a', 'b']},
                                   self._resolve(mapping), TOL)
        self.assertEqual((b1.Encode(), b2.Encode()), encoded,
                         'refused join must not modify its solid inputs')

    # ── cap refusal is actionable, never a silent faceted fallback ──────────
    def test_cap_decline_on_non_brep_is_actionable(self):
        circle = bridge.make_circle([0, 0, 0], 5.0)
        self.assertIsInstance(circle, r3.Curve, 'a bare circle is a curve, not a Brep')
        with self.assertRaisesRegex(ValueError, r'cap_planar_closure_refused'):
            ai_modeling._construct({'op': 'cap', 'source': 'c'},
                                   self._resolve({'c': circle}), TOL)
        self.assertIsNotNone(bridge.last_occt_decline(), 'a cap refusal must carry an actionable reason')

    def test_cap_decline_on_non_planar_surface_is_actionable(self):
        nonplanar = bridge.make_srf_pt([(0, 0, 0), (2, 0, 0), (2, 2, 1), (0, 2, 0)])
        with self.assertRaisesRegex(ValueError, r'cap_planar_closure_refused'):
            ai_modeling._construct({'op': 'cap', 'source': 'np'},
                                   self._resolve({'np': nonplanar}), TOL)
        self.assertIsNotNone(bridge.last_occt_decline())

    # ── honesty: join may be open, cap must be a solid (SDK facts) ─────────
    def test_join_open_and_cap_solid_honesty(self):
        loft = make_loft()
        pieces = loft_patches(loft, 4)
        joined = ai_modeling._construct({'op': 'join', 'sources': ['0', '1', '2', '3']},
                                        self._resolve(dict(pieces)), TOL)
        self.assertFalse(ai_modeling.inspect_geometry(joined, TOL)['solid'],
                         'a join is not a boolean and may legitimately be open')
        capped = ai_modeling._construct({'op': 'cap', 'source': 'J'},
                                        self._resolve({'J': joined}), TOL)
        self.assertTrue(ai_modeling.inspect_geometry(capped, TOL)['solid'],
                        'a cap result must be an airtight solid, not an open surface')

    # ── a capped solid survives save/reload with its recipe and volume ─────
    def test_capped_solid_survives_save_reload(self):
        loft = make_loft()
        capped = ai_modeling._construct({'op': 'cap', 'source': 'L'},
                                        self._resolve({'L': loft}), TOL)
        expected_vol = ai_modeling.inspect_geometry(capped, TOL)['volume']
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as tmp:
            path = Path(tmp) / 'cap.3dm'
            document = r3.File3dm()
            document.Objects.AddBrep(capped, r3.ObjectAttributes())
            self.assertTrue(document.Write(str(path), 8))
            kernel.forget_masters()
            reopened = r3.File3dm.Read(str(path)).Objects[0].Geometry
            self.assertIsInstance(reopened, r3.Brep)
            recipe = kernel.recipe_of(reopened)
            self.assertIsNotNone(recipe, 'a reopened capped solid must carry its recipe')
            self.assertEqual(recipe['kind'], 'cap')
            master = ob.from_recipe(recipe)
            self.assertTrue(ob.is_airtight(master))
            self.assertEqual(ob.face_types(master), {'BSPLINE': 1, 'PLANE': 2})
            self.assertAlmostEqual(ob.volume(master), expected_vol, places=9)


class JoinCapHostPublish(unittest.TestCase):
    """The actual-host publish/Undo/Redo/save-reload boundary for join and cap."""

    def setUp(self):
        bpy.data.batch_remove(list(bpy.data.objects))
        self.m = model.NarwhalModel()
        self.ctx = commands.NarwhalContext(self.m)
        self.app = SimpleNamespace(model=self.m, ctx=self.ctx, active_command=None)
        self.ui = ui_fixture(self.app)
        self.ui.__enter__()
        self.addCleanup(self.ui.__exit__, None, None, None)
        self.ready = patch.object(ai_workspace.WorkspaceSnapshots, 'ready', lambda _: True)
        self.ready.start(); self.addCleanup(self.ready.stop)
        loader = base.ai._load_workflow_library
        self.loader = patch.object(base.ai, '_load_workflow_library', lambda path=None: loader(LIBRARY))
        self.loader.start(); self.addCleanup(self.loader.stop)
        self.state = base.ai.AssistantState(provider=base.ai.set_provider('openai_compatible', 'deterministic', credential_env='TEST_KEY'))
        self.w = base.ai.WorkflowSession(self.state, DOC, allow_reads=True, library_path=LIBRARY)
        self.addCleanup(self.w.close)
        self.call = 0

    def _ensure_build_mode(self):
        if self.call == 0:
            self.w.modeling_exchange('modeling_mode', DOC, mode='build')

    def stage(self, steps, continuation=False):
        self.call += 1
        prepared = self.w.prepare(DOC, user_text=None if continuation else 'Build the fixture',
                                  continuation=continuation)
        batch = dict(label='Design', steps=steps)
        call_id = 'batch_' + str(self.call)
        result = self.w.finish(base.send(prepared, base.payload('openai_compatible', '',
                                 [(call_id, 'narwhal_model', batch)])), DOC)
        self.assertEqual(result['status'], 'tool_calls', result)
        descriptor = self.w.modeling_exchange('stage_modeling', DOC, owner=result['token'],
                    call_id=call_id, context=ai_modeling.context(self.app, DOC))
        return result['token'], descriptor

    def apply(self, steps, continuation=False):
        self._ensure_build_mode()
        token, descriptor = self.stage(steps, continuation)
        result = ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        self.w.record_results(token, [result], DOC)
        self.assertFalse(result['is_error'], result)
        self.assertEqual(self.ctx.undo.current_serial, 0, 'modeling left an Undo record open')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        return result['output']['objects']

    def refuse(self, steps, continuation=False):
        self._ensure_build_mode()
        token, descriptor = self.stage(steps, continuation)
        result = ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        self.w.record_results(token, [result], DOC)
        return result

    def test_patches_join_cap_publish_undo_redo_reload(self):
        loft = make_loft()
        pieces = loft_patches(loft, 4)
        uuids = []
        for i, (sid, geom) in enumerate(pieces):
            oid, _ = self.m.add_object(geom, f'patch{i}')
            uuids.append(oid)
        baseline = self.m.object_count()
        rows = self.apply([
            step('join', dict(op='join', sources=uuids), publish=False),
            step('cap', dict(op='cap', source='join'), publish=True),
        ])
        self.assertEqual(self.m.object_count(), baseline + 1)
        self.assertTrue(rows[0]['airtight'])
        self.assertTrue(rows[0]['solid'])
        self.assertEqual(rows[0]['face_types'], {'BSPLINE': 4, 'PLANE': 2})
        self.assertGreater(rows[0]['volume'], 0)
        # Undo removes the published capped solid; Redo restores it.
        self.ctx.undo_last()
        self.assertEqual(self.m.object_count(), baseline)
        self.ctx.redo_last()
        self.assertEqual(self.m.object_count(), baseline + 1)
        # A fresh save/reload keeps the airtight capped solid and its cap recipe.
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as tmp:
            path = Path(tmp) / 'joincap.3dm'
            self.assertTrue(self.m.save(str(path)))
            kernel.forget_masters()
            reopened = model.NarwhalModel.load(str(path))
            capped = None
            for obj in reopened.file.Objects:
                recipe = kernel.recipe_of(obj.Geometry)
                if recipe and recipe.get('kind') == 'cap':
                    capped = obj.Geometry
                    break
            self.assertIsNotNone(capped, 'the capped solid must survive save/reload')
            master = ob.from_recipe(kernel.recipe_of(capped))
            self.assertTrue(ob.is_airtight(master))
            self.assertEqual(ob.face_types(master), {'BSPLINE': 4, 'PLANE': 2})

    def test_loft_cap_dependency_publish(self):
        curves = [r3.Circle(r3.Point3d(0, 0, z), rad).ToNurbsCurve()
                  for z, rad in [(0, 2), (3, 3), (7, 2.5), (10, 2)]]
        uuids = []
        for i, c in enumerate(curves):
            oid, _ = self.m.add_object(c, f'section{i}')
            uuids.append(oid)
        baseline = self.m.object_count()
        rows = self.apply([
            step('loft', dict(op='loft', sections=uuids), publish=False),
            step('cap', dict(op='cap', source='loft'), publish=True),
        ])
        self.assertEqual(self.m.object_count(), baseline + 1)
        self.assertTrue(rows[0]['airtight'])
        self.assertTrue(rows[0]['solid'])
        self.assertEqual(rows[0]['face_types'], {'BSPLINE': 1, 'PLANE': 2})
        self.assertGreater(rows[0]['volume'], 0)

    def test_nonplanar_cap_refused_canonical_no_publish(self):
        nonplanar = bridge.make_srf_pt([(0, 0, 0), (2, 0, 0), (2, 2, 1), (0, 2, 0)])
        oid, _ = self.m.add_object(nonplanar, 'nonplanar')
        baseline = self.m.object_count()
        result = self.refuse([step('cap', dict(op='cap', source=oid), publish=True)])
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'], 'cap_planar_closure_refused')
        self.assertFalse(result['output']['applied'])
        self.assertEqual(self.m.object_count(), baseline, 'a refused cap must not change the document')
        self.assertEqual(self.ctx.undo.current_serial, 0, 'a refused cap must leave no Undo record')

    def test_disconnected_join_refused_canonical_no_publish(self):
        b1 = bridge.make_box((0, 0, 0), (2, 3, 0), 3, plane=cplane.CPlane(origin=(0, 0, 0)))
        b2 = bridge.make_box((100, 0, 0), (102, 3, 0), 3, plane=cplane.CPlane(origin=(100, 0, 0)))
        oid1, _ = self.m.add_object(b1, 'box1')
        oid2, _ = self.m.add_object(b2, 'box2')
        baseline = self.m.object_count()
        enc1 = self.m.file.Objects.FindId(oid1).Geometry.Encode()
        enc2 = self.m.file.Objects.FindId(oid2).Geometry.Encode()
        result = self.refuse([step('join', dict(op='join', sources=[oid1, oid2]), publish=True)])
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'], 'join_connected_group_required')
        self.assertFalse(result['output']['applied'])
        self.assertEqual(self.m.object_count(), baseline, 'a refused join must not change the document')
        self.assertEqual(self.ctx.undo.current_serial, 0, 'a refused join must leave no Undo record')
        self.assertEqual(self.m.file.Objects.FindId(oid1).Geometry.Encode(), enc1,
                         'a refused join must not modify its sources')
        self.assertEqual(self.m.file.Objects.FindId(oid2).Geometry.Encode(), enc2,
                         'a refused join must not modify its sources')


if __name__ == '__main__':
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(JoinCapSDKDispatch))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(JoinCapHostPublish))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('join/cap host failed')
    print('JOIN_CAP_HOST_PASS', flush=True)
