"""Minimal actual-host Cap admission; no geometry math implementation here.

Run bundled Python -B with NARWHAL_GEOMETRY_CORE pointing at an unactivated
candidate. Main must run run_admission_probe() before activation. Python is needed
only for rhino3dm/OCP handles, bridge integration and real .3dm/STEP persistence.
Native admission, exact geometry, nesting and resource tests live in cap.rs.
"""
import copy
import math
import base64
import importlib
import re
import struct
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_join_surfaces import PrivateHost, ROOT, CYLINDER, MOVE


def host():
    h = PrivateHost()
    h.occt._cap_native_binding()
    return h


class CapAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = host()
        cls.b, cls.k, cls.o = cls.h.bridge, cls.h.kernel, cls.h.occt

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def cap(self, geom, tolerance=.01):
        out = self.b.cap_planar_holes_occt(geom, tolerance=tolerance)
        self.assertIsNotNone(out, self.b.last_occt_decline())
        self.assertEqual(out.GetUserString(self.k.DOCUMENT_FORM_KEY), "exact")
        self.assertIsNone(self.k.honesty_problem(out))
        for shape in (self.k.master_of(out), self.o.from_recipe(self.k.recipe_of(out))):
            self.assertTrue(self.o.is_airtight(shape))
            self.assertTrue(self.o.is_valid(shape))
            self.assertGreater(self.o.volume(shape), 0)
        self.assertEqual(self.k.recipe_of(out)["tolerance"], tolerance)
        return out

    def loft(self):
        import rhino3dm as r3
        curves = [r3.Circle(r3.Point3d(0, 0, z), radius).ToNurbsCurve()
                  for z, radius in [(0, 2), (3, 3), (7, 2.5), (10, 2)]]
        return self.b.loft_brep(curves, style="Normal")

    def test_normal_closed_sections_preserve_source_parametric_surface(self):
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        geom = self.loft()
        source = self.k.master_of(geom)
        self.assertIsNotNone(source)
        surf = BRepAdaptor_Surface(self.o.unique_faces(source)[0])
        self.assertGreaterEqual(surf.VDegree(), 2)
        self.assertTrue(surf.IsURational() or surf.IsVRational())
        before = geom.Encode(), self.o.shape_to_bytes(source, triangles=False), copy.deepcopy(self.k.recipe_of(geom))
        out = self.cap(geom)
        master = self.k.master_of(out)
        self.assertEqual(self.o.face_types(master), {"BSPLINE": 1, "PLANE": 2})
        self.assertEqual(before, (geom.Encode(), self.o.shape_to_bytes(source, triangles=False), self.k.recipe_of(geom)))
        # Native cap admission itself checks unchanged surface handle, location,
        # UV domain and exact trim coefficients; these SDK degree/rational checks
        # would additionally expose a producer reverting to a ruled/faceted loft.
        curved = [f for f in self.o.unique_faces(master) if BRepAdaptor_Surface(f).GetType().name == "GeomAbs_BSplineSurface"]
        self.assertEqual(len(curved), 1)
        capped = BRepAdaptor_Surface(curved[0])
        self.assertEqual((surf.UDegree(), surf.VDegree(), surf.IsURational(), surf.IsVRational()),
                         (capped.UDegree(), capped.VDegree(), capped.IsURational(), capped.IsVRational()))

    def test_straight_conical_loft_caps_to_valid_exact_document(self):
        # Existing manufacturing round-trip fixture: do not replace it with a
        # cylinder/Normal loft that cannot expose the converted seam defect.
        geom=self.b.loft_brep([self.b.make_circle((20,0,0),5.0),
                               self.b.make_circle((20,0,6),3.0)],style="Straight")
        source=self.k.master_of(geom)
        self.assertEqual(self.o.face_types(source),{"CONE":1})
        before=self.o.shape_to_bytes(source,triangles=False)
        from OCP.BRep import BRep_Tool
        from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
        from OCP.TopAbs import TopAbs_FORWARD
        from OCP.TopoDS import TopoDS
        face=self.o.unique_faces(source)[0]
        original=BRep_Tool.Surface_s(face)
        u0,u1,v0,v1=BRepTools.UVBounds_s(face)
        converted=self.o._nurbs_convert(source)
        self.assertTrue(self.o.is_valid(converted))
        self.assertEqual(self.o.face_types(converted),{"BSPLINE":1})
        nf=TopoDS.Face(self.o.unique_faces(converted)[0].Oriented(TopAbs_FORWARD))
        surface=BRep_Tool.Surface_s(nf)
        # An angular cut of a periodic rational basis does NOT preserve the 3-D
        # seam locus: the original converter missed by up to 0.192343 units.
        # This tests actual coordinates, not merely trim flags/UV classifications.
        for u in (u0,u1):
            for i in range(17):
                v=v0+(v1-v0)*i/16
                self.assertLess(original.Value(u,v).Distance(surface.Value(u,v)),1e-11)
        explorer=BRepTools_WireExplorer(BRepTools.OuterWire_s(nf),nf)
        seams=0
        while explorer.More():
            edge=explorer.Current(); explorer.Next()
            if not BRep_Tool.IsClosed_s(edge,nf):
                continue
            seams+=1
            first,last=BRep_Tool.Range_s(edge)
            curve=BRep_Tool.Curve_s(edge,first,last)
            pc=BRep_Tool.CurveOnSurface_s(edge,nf,first,last)
            for i in range(17):
                t=first+(last-first)*i/16; uv=pc.Value(t)
                self.assertLess(min(abs(uv.X()-u0),abs(uv.X()-u1)),1e-12)
                self.assertLess(curve.Value(t).Distance(surface.Value(uv.X(),uv.Y())),1e-11)
        self.assertEqual(seams,2)
        for tolerance in (1e-6,.01):
            with self.subTest(tolerance=tolerance):
                out=self.cap(geom,tolerance)
                self.assertEqual(self.o.face_types(self.k.master_of(out)),{"CONE":1,"PLANE":2})
                self.assertAlmostEqual(self.o.volume(self.k.master_of(out)),98*math.pi,places=8)
                self.assertEqual(len(out.Faces),3)
                self.assertEqual(self.o.face_types(self.o._nurbs_convert(self.k.master_of(out))),{"BSPLINE":3})
        self.assertEqual(before,self.o.shape_to_bytes(source,triangles=False))

    def test_joined_open_normal_loft_patches_then_cap(self):
        from OCP.BRep import BRep_Tool
        from OCP.BRepTools import BRepTools
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        # Exact trimmed patches of the same Normal loft. Independently lofting
        # quarter curves can differ numerically; Join correctly refuses those
        # under its existing unchanged-trim contract, which we do not relax.
        face = self.o.unique_faces(self.k.master_of(self.loft()))[0]
        surface = BRep_Tool.Surface_s(face)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        pieces = []
        for i in range(4):
            face = BRepBuilderAPI_MakeFace(surface, u0+(u1-u0)*i/4,
                                           u0+(u1-u0)*(i+1)/4, v0, v1, 1e-7).Face()
            pieces.append((str(i), self.b.occt_backed(face, self.o.native_master_recipe(face))))
        before = [g.Encode() for _, g in pieces]
        joined = self.b.join_breps(pieces, .01)
        self.assertEqual(len(joined), 1)
        self.assertFalse(joined[0][2])
        out = self.cap(joined[0][1])
        self.assertEqual(self.o.face_types(self.k.master_of(out)), {"BSPLINE": 4, "PLANE": 2})
        self.assertEqual(self.k.recipe_of(out)["a"]["kind"], "join_exact")
        self.assertEqual(before, [g.Encode() for _, g in pieces])

    def test_cylinder_legacy_and_tampered_trees(self):
        source = self.h.faces(CYLINDER)[0][1]  # unique_faces: cylinder wall first
        self.assertEqual(self.o.face_types(self.k.master_of(source)), {"CYLINDER": 1})
        out = self.cap(source)
        self.assertEqual(self.o.face_types(self.k.master_of(out)), {"CYLINDER": 1, "PLANE": 2})
        legacy = {"kind": "cap", "a": self.k.recipe_of(source)}
        self.assertTrue(self.o.is_airtight(self.o.from_recipe(legacy)))
        tree = self.k.recipe_of(out)
        closed_before = out.Encode()
        nested = self.cap(out)
        self.assertEqual(self.k.recipe_of(nested)["a"]["kind"], "cap")
        self.assertEqual(closed_before,out.Encode())
        self.assertAlmostEqual(self.o.volume(self.k.master_of(nested)),12*math.pi,places=9)
        for mutate in (lambda r: r.update(version=2),
                       lambda r: r["source"].update(xform=MOVE),
                       lambda r: r["result"].update(xform=MOVE),
                       lambda r: r.update(source=self.o.native_master_recipe(self.o.make_box(1, 1, 1))),
                       lambda r: r.update(result=self.o.native_master_recipe(self.o.make_box(1, 1, 1))),
                       lambda r: r["a"]["a"].update(radius=7)):
            broken = copy.deepcopy(tree); mutate(broken)
            with self.assertRaises(self.k.KernelError):
                self.o.from_recipe(broken)
        with patch.object(self.o, "_cap_native_binding", side_effect=self.k.KernelError("unavailable")):
            self.assertIsNone(self.b.cap_planar_holes_occt(source, .01))
            self.assertIn("unavailable", self.b.last_occt_decline())
        with patch.object(self.b._onbrep(), "exact_brep", return_value=None):
            self.assertIsNone(self.b.cap_planar_holes_occt(source, .01))
            self.assertIn("no faceted fallback", self.b.last_occt_decline())

    def test_live_master_must_match_source_tree(self):
        source = self.h.faces(CYLINDER)[0][1]
        self.k.record_recipe(source, {"kind": "face", "index": 1,
                                     "a": {"kind": "cylinder", "radius": 9, "height": 3}})
        self.assertIsNone(self.b.cap_planar_holes_occt(source, .01))
        self.assertIn("binding refused", self.b.last_occt_decline())

    def test_native_annular_bore_and_nonplanarity_via_actual_sdk(self):
        from OCP.BRep import BRep_Builder
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.TopAbs import TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        for kind in (1, 2, 4):
            if kind == 1:
                hollow = self.o.boolean(self.o.make_cylinder(3, 5), self.o.make_cylinder(1, 5), "DIFFERENCE")
                sew = BRepBuilderAPI_Sewing(1e-7)
                for face in self.o.unique_faces(hollow):
                    props = GProp_GProps(); BRepGProp.SurfaceProperties_s(face, props)
                    if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane and props.CentreOfMass().Z() > 4:
                        continue
                    sew.Add(face)
                sew.Perform(); shape = sew.SewedShape()
            elif kind == 2:
                geom = self.b.make_srf_pt([(0,0,0), (2,0,0), (2,2,1), (0,2,0)])
                shape = self.k.master_of(geom)
            else:
                shape = self.k.master_of(self.h.faces(CYLINDER)[0][1])
                ex = TopExp_Explorer(shape, TopAbs_VERTEX)
                while ex.More():
                    BRep_Builder().UpdateVertex(TopoDS.Vertex(ex.Current()), .005)
                    ex.Next()
            geom = self.b.occt_backed(shape, self.o.native_master_recipe(shape))
            before = geom.Encode(), self.o.shape_to_bytes(shape, triangles=False)
            if kind == 1:
                out = self.cap(geom)
                self.assertEqual(self.o.face_types(self.k.master_of(out)), {"CYLINDER": 2, "PLANE": 2})
                self.assertAlmostEqual(self.o.volume(self.k.master_of(out)), 40*math.pi, places=9)
            elif kind == 2:
                self.assertIsNone(self.b.cap_planar_holes_occt(geom, .01))
                self.assertIn("refused", self.b.last_occt_decline())
            else:
                self.cap(geom, .01)
                self.assertIsNone(self.b.cap_planar_holes_occt(geom, .001))
            self.assertEqual(before, (geom.Encode(), self.o.shape_to_bytes(shape, triangles=False)))

    def hostile_shapes(self):
        from OCP.BRep import BRep_Builder, BRep_Tool
        from OCP.Geom2d import Geom2d_BSplineCurve, Geom2d_Line
        from OCP.gp import gp_Pnt2d, gp_Dir2d
        from OCP.collections import Array1_gp_Pnt2d, Array1_double, Array1_int
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Wire
        poles = Array1_gp_Pnt2d(1,5000)
        knots = Array1_double(1,5000); mults = Array1_int(1,5000)
        for i in range(1,5001):
            poles.SetValue(i,gp_Pnt2d(0,i-1)); knots.SetValue(i,i-1)
            mults.SetValue(i,2 if i in (1,5000) else 1)
        huge = Geom2d_BSplineCurve(poles,knots,mults,1)
        small = Geom2d_Line(gp_Pnt2d(0,0),gp_Dir2d(0,1))
        for mode in ('pcurve1', 'pcurve2', 'wires', 'edges'):
            face = self.k.master_of(self.h.faces(CYLINDER)[0][1])
            builder = BRep_Builder()
            if mode.startswith('pcurve'):
                ex = TopExp_Explorer(face,TopAbs_EDGE)
                while ex.More():
                    edge = TopoDS.Edge(ex.Current())
                    if BRep_Tool.IsClosed_s(edge,face):
                        builder.UpdateEdge(edge, huge if mode=='pcurve1' else small,
                                           small if mode=='pcurve1' else huge, face,1e-7)
                        break
                    ex.Next()
                self.assertTrue(ex.More(), 'fixture must actually contain a seam')
            else:
                original = face; face = TopoDS_Face()
                builder.MakeFace(face,BRep_Tool.Surface_s(original),1e-7)
                wire = TopExp_Explorer(original,TopAbs_WIRE).Current()
                if mode == 'wires':
                    for _ in range(4097):
                        builder.Add(face,wire)
                else:
                    repeated = TopoDS_Wire(); builder.MakeWire(repeated)
                    edge = TopExp_Explorer(wire,TopAbs_EDGE).Current()
                    for _ in range(17000):
                        builder.Add(repeated,edge)
                    builder.Add(face,repeated)
            yield mode, self.o.shape_to_bytes(face,triangles=False)

    def test_isolated_pcurves_occurrences_and_every_binding_operand(self):
        worker = importlib.import_module(self.h.name+'.cap_worker')
        source = self.k.master_of(self.h.faces(CYLINDER)[0][1])
        before = self.o.shape_to_bytes(source,triangles=False)
        target = self.o.shape_to_bytes(self.o.cap_planar_holes(source,.01),triangles=False)
        for name, data in self.hostile_shapes():
            started = time.monotonic()
            with self.subTest(name=name), self.assertRaisesRegex(self.k.KernelError, r'cap_worker_refused:'):
                worker.submit('cap',source=data,tolerance=.01)
            self.assertLess(time.monotonic()-started,worker.TIMEOUT+12)
            for slot in ('source','snapshot','target'):
                inputs = dict(source=before,snapshot=before,target=target)
                inputs[slot] = data
                with self.subTest(name=name,slot=slot), self.assertRaisesRegex(self.k.KernelError, r'cap_worker_refused:') as refusal:
                    worker.submit('verify',tolerance=.01,**inputs)
                self.assertIn('(-2)',str(refusal.exception))
        self.assertEqual(before,self.o.shape_to_bytes(source,triangles=False))

    def test_malformed_bintools_counts_never_decode_in_parent(self):
        worker = importlib.import_module(self.h.name+'.cap_worker')
        source = self.k.master_of(self.h.faces(CYLINDER)[0][1])
        data = self.o.shape_to_bytes(source,triangles=False)
        marker = re.search(rb'Curve2ds \d+\n',data)
        self.assertIsNotNone(marker)
        # MEASURED OCCT 8.0.1 BinTools_Curve2dSet.cxx: tag 7, rational/periodic
        # bools, uint16 degree, int32 pole/knot counts. Allocation precedes reads.
        # Tiny serialized input requests >32 GiB of poles. Never feed it to a
        # parent OCP/native decoder, even in a test. Rust limits the actual child.
        malformed = data[:marker.start()] + b'Curve2ds 1\n' + bytes([7,0,0]) + struct.pack('<Hii',1,2147483647,2)
        snapshot = self.o.native_master_recipe(source)
        bad = dict(snapshot,data=base64.b64encode(malformed).decode())
        tree = {'kind':'cap','version':1,'a':snapshot,'source':snapshot,
                'result':bad,'tolerance':.01}
        # Build the trusted SDK fixture before installing the hostile-input spy:
        # exact document conversion itself transports a freshly converted shape.
        geom = self.h.faces(CYLINDER)[0][1]
        self.k.record_recipe(geom,bad)
        before = geom.Encode()
        with patch.object(self.o,'shape_from_bytes',side_effect=AssertionError('parent BinTools decode')) as parent_decoder:
            started=time.monotonic()
            with self.assertRaisesRegex(self.k.KernelError,'cap_worker_refused|cap_timeout'):
                worker.submit('cap',source=malformed,tolerance=.01)
            self.assertLess(time.monotonic()-started,worker.TIMEOUT+12)
            for slot in ('source','snapshot','target'):
                inputs = dict(source=data,snapshot=data,target=data); inputs[slot]=malformed
                started=time.monotonic()
                with self.subTest(slot=slot), self.assertRaisesRegex(self.k.KernelError,'cap_worker_refused|cap_timeout'):
                    worker.submit('verify',tolerance=.01,**inputs)
                self.assertLess(time.monotonic()-started,worker.TIMEOUT+12)
            with self.assertRaisesRegex(self.k.KernelError,'cap_worker_refused|cap_timeout'):
                self.o.from_recipe(tree)
            with patch.object(self.k,'master_of',return_value=None):
                self.assertIsNone(self.b.cap_planar_holes_occt(geom,.01))
            self.assertIn('cap_worker_refused',self.b.last_occt_decline())
            self.assertEqual(before,geom.Encode())
            parent_decoder.assert_not_called()
        with self.assertRaisesRegex(self.k.KernelError,'requires_supervised_worker'):
            self.o._cap_native_bytes(data,.01)

    def test_rust_worker_timeout_memory_and_missing_supervisor_fail_closed(self):
        worker = importlib.import_module(self.h.name+'.cap_worker')
        converter = importlib.import_module(self.h.name+'.converter_process')
        source = self.k.master_of(self.h.faces(CYLINDER)[0][1])
        before = self.o.shape_to_bytes(source,triangles=False)
        with patch.object(worker,'TIMEOUT',.2), patch.object(worker,'_command',return_value=[sys.executable,'-c','import time; time.sleep(60)']):
            started=time.monotonic()
            with self.assertRaisesRegex(self.k.KernelError,'cap_timeout'):
                worker.submit('cap',source=before)
            self.assertLess(time.monotonic()-started,5)
        with patch.object(worker,'MEMORY_MIB',128), patch.object(worker,'_command',return_value=[sys.executable,'-c','a=bytearray(512*1024*1024)']):
            with self.assertRaisesRegex(self.k.KernelError,'cap_worker_refused') as refusal:
                worker.submit('cap',source=before)
            self.assertIn('MemoryError',str(refusal.exception))
        with patch.object(converter,'_supervisor',return_value=None):
            with self.assertRaisesRegex(self.k.KernelError,'cap_supervisor_unavailable'):
                self.o.cap_planar_holes(source,.01)
        self.assertEqual(before,self.o.shape_to_bytes(source,triangles=False))

    def test_aggregate_input_budget_precedes_process_launch(self):
        worker = importlib.import_module(self.h.name+'.cap_worker')
        with patch.object(worker,'MAX_INPUT',64), patch.object(worker.subprocess,'run',
                side_effect=AssertionError('over-budget input reached supervisor')) as launch:
            with self.assertRaisesRegex(self.k.KernelError,'cap_input_size'):
                worker.submit('cap',source=b'x'*65,tolerance=.01)
            launch.assert_not_called()

    def test_local_native_entry_requires_worker(self):
        source=self.o.shape_to_bytes(self.k.master_of(self.h.faces(CYLINDER)[0][1]),triangles=False)
        with self.assertRaisesRegex(self.k.KernelError,'requires_supervised_worker'):
            self.o._cap_native_bytes(source,.01)

    def test_runtime_containment_producer_mutations_red_restored_green(self):
        # Mutate actual transport functions in isolated module namespaces, never
        # decode the hostile malformed fixture outside the supervised worker.
        import inspect
        worker=importlib.import_module(self.h.name+'.cap_worker')
        submit=inspect.getsource(worker.submit)
        variants=[('deadline','str(round(TIMEOUT*1000))','str(30000)',
                   self.test_rust_worker_timeout_memory_and_missing_supervisor_fail_closed),
                  ('memory','str(MEMORY_MIB)','str(4096)',
                   self.test_rust_worker_timeout_memory_and_missing_supervisor_fail_closed)]
        for name,old,new,probe in variants:
            self.assertEqual(submit.count(old),1)
            namespace=worker.__dict__
            broken=submit.replace(old,new,1).replace('def submit(', 'def _mutation_submit(',1)
            exec(compile(broken,'<cap-'+name+'-mutation>','exec'),namespace)
            with self.subTest(mutation=name), patch.object(worker,'submit',namespace['_mutation_submit']):
                with self.assertRaises(AssertionError):
                    probe()
            probe()
        namespace=worker.__dict__
        broken=submit.replace('if not 0 < len(data) <= MAX_INPUT or total > MAX_INPUT:', 'if False:').replace('if total > MAX_INPUT:', 'if False:')
        self.assertNotEqual(broken,submit)
        exec(compile(broken.replace('def submit(', 'def _mutation_submit(',1),'<cap-input-budget-mutation>','exec'),namespace)
        with patch.object(worker,'submit',namespace['_mutation_submit']):
            with self.assertRaises(AssertionError):
                self.test_aggregate_input_budget_precedes_process_launch()
        self.test_aggregate_input_budget_precedes_process_launch()
        source=inspect.getsource(self.o._cap_native_bytes)
        old="if not cap_worker._ACTIVE:"
        self.assertEqual(source.count(old),1)
        namespace=dict(self.o.__dict__)
        exec(compile(source.replace(old,'if False:',1),'<cap-worker-entry-mutation>','exec'),namespace)
        with patch.object(self.o,'_cap_native_bytes',namespace['_cap_native_bytes']):
            with self.assertRaises(AssertionError):
                self.test_local_native_entry_requires_worker()
        self.test_local_native_entry_requires_worker()

    def test_document_fresh_reload_transform_boolean_step(self):
        import rhino3dm as r3
        conical=self.b.loft_brep([self.b.make_circle((20,0,0),5.),
                                  self.b.make_circle((20,0,6),3.)],style="Straight")
        geometries = [self.cap(self.h.faces(CYLINDER)[0][1]), self.cap(self.loft()),self.cap(conical)]
        transform = r3.Transform.Identity()
        for i in range(4):
            for j in range(4):
                setattr(transform, f"M{i}{j}", MOVE[4*i+j])
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        for geom in geometries:
            props = GProp_GProps(); BRepGProp.VolumeProperties_s(self.k.master_of(geom), props)
            geom.SetUserString("cap_probe_center_x", repr(props.CentreOfMass().X() + 100))
            geom.SetUserString("cap_probe_volume", repr(self.o.volume(self.k.master_of(geom))))
            self.b.transform_geometry(geom, transform)
        with tempfile.TemporaryDirectory(prefix="cap-document-", dir=ROOT/"tmp") as tmp:
            path = Path(tmp)/"cap.3dm"
            document = r3.File3dm()
            for geom in geometries:
                document.Objects.AddBrep(geom, r3.ObjectAttributes())
            self.assertTrue(document.Write(str(path), 8))
            process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--reload", str(path)],
                                     capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout+process.stderr)
            self.assertIn("fresh Cap recipe reload PASS", process.stdout)


def fresh_reload(path):
    import rhino3dm as r3
    h = host()
    try:
        document = r3.File3dm.Read(path)
        assert len(document.Objects) == 3
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        for index, obj in enumerate(document.Objects):
            geom = obj.Geometry
            master = h.occt.from_recipe(h.kernel.recipe_of(geom))
            assert h.occt.is_airtight(master) and h.occt.is_valid(master)
            expected = [{"CYLINDER":1,"PLANE":2},{"BSPLINE":1,"PLANE":2},{"CONE":1,"PLANE":2}][index]
            assert h.occt.face_types(master) == expected
            assert abs(h.occt.volume(master)-float(geom.GetUserString("cap_probe_volume"))) < 1e-9
            if index == 0:
                assert abs(h.occt.volume(master)-12*math.pi) < 1e-9
            props = GProp_GProps(); BRepGProp.VolumeProperties_s(master, props)
            assert abs(props.CentreOfMass().X()-float(geom.GetUserString("cap_probe_center_x"))) < 1e-9
            if index == 2:
                assert abs(h.occt.volume(master)-98*math.pi) < 1e-9
            placement=list(MOVE)
            placement[3]=props.CentreOfMass().X(); placement[7]=props.CentreOfMass().Y()
            inner = h.occt.transform(h.occt.make_box(.5, .5, .5), placement)
            fused = h.occt.boolean(master, inner, "UNION")
            assert h.occt.is_airtight(fused) and h.occt.face_types(fused) == expected
            output = Path(path).with_suffix(f".{index}.step")
            h.occt.export_step(master, str(output))
            assert ["CYLINDRICAL_SURFACE","B_SPLINE_SURFACE_WITH_KNOTS","CONICAL_SURFACE"][index] in output.read_text()
        print("fresh Cap recipe reload PASS")
    finally:
        h.close()


def run_admission_probe():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CapAdmission))
    return result.wasSuccessful()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reload":
        fresh_reload(sys.argv[2])
    else:
        raise SystemExit(0 if run_admission_probe() else 1)
