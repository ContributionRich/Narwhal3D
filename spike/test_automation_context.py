"""M3 bounded metadata adapter tests; no GUI, builds, actions or disk mutations.

python3 -B spike/test_automation_context.py
python3 -B spike/test_automation_context.py --pure  # mocks/mutations only
Also runnable with bundled Python 3.13. Real tests use disposable NarwhalModel /
rhino3dm Line/Point documents only; no OCCT. Mutants execute in memory. Native
indexing API support is tested, not its undocumented internal time complexity.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import types
import unittest
import uuid
from unittest.mock import patch

from test_automation_bridge import (ROOT, SOURCES, Controls as BridgeControls,
                                    app_fixture, load_modules, row, unload, worker_call)


class IndexedTable:
    def __init__(self, rows=None, count=None, factory=None):
        self.rows = rows
        self.count = len(rows) if count is None else count
        self.factory = factory
        self.indices = []

    def __len__(self):
        return self.count

    def __iter__(self):
        raise AssertionError("must not scan or copy the native table")

    def __getitem__(self, index):
        if not 0 <= index < self.count:
            raise AssertionError("out of range native lookup")
        self.indices.append(index)
        return self.factory(index) if self.factory else self.rows[index]


def layer(index=0, name="Layer"):
    return types.SimpleNamespace(Id=uuid.UUID(int=index + 1), Index=index, Name=name,
                                 ParentLayerId=uuid.UUID(int=0), Visible=True, Locked=False)


def fixture(count=70, name="Part"):
    app = app_fixture()
    app.active_command = None
    app.model.file.Objects = IndexedTable(count=count, factory=lambda i: row(i + 1, name))
    app.model.file.Layers = IndexedTable(count=count, factory=lambda i: layer(i, name))
    return app


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


class Controls(unittest.TestCase):
    # Reuse only the private-channel helpers, not another copy of its 21 tests.
    pair = BridgeControls.pair
    request = BridgeControls.request
    receive = BridgeControls.receive

    def reader(self, app=None, **kwargs):
        return self.tools.ApplicationReader(app or fixture(), "M3-test", **kwargs)

    def test_exact_catalog_and_arguments(self):
        self.assertEqual(self.tools.TOOLS, frozenset({
            "narwhal.application_info", "narwhal.document_summary",
            "narwhal.selection_summary", "narwhal.object_summary", "narwhal.objects",
            "narwhal.layers", "narwhal.workspace", "narwhal.components"}))
        app = fixture()
        reader = self.reader(app)
        invalid = [None, [], {}, {"offset": 0}, {"limit": 1},
                   {"offset": 0, "limit": 1, "extra": 0}]
        for key, values in (("offset", (-1, 10001, True, False, 0.0, "0", None, [])),
                            ("limit", (0, 33, True, False, 1.0, "1", None, []))):
            invalid.extend(dict({"offset": 0, "limit": 1}, **{key: value}) for value in values)
        for tool in ("narwhal.objects", "narwhal.layers"):
            for args in invalid:
                with self.subTest(tool=tool, args=args):
                    with self.assertRaisesRegex(self.tools.ReadError, "^invalid_arguments$"):
                        reader.read(tool, args)
            for offset in (0, 10000):
                for limit in (1, 32):
                    self.tools.validate_arguments(tool, {"offset": offset, "limit": limit})
        for tool in ("narwhal.workspace", "narwhal.components"):
            for args in (None, [], {"limit": 1}, {"extra": False}):
                with self.assertRaisesRegex(self.tools.ReadError, "^invalid_arguments$"):
                    reader.read(tool, args)
        self.assertEqual(app.model.file.Objects.indices, [])
        self.assertEqual(app.model.file.Layers.indices, [])

    def test_indexed_page_only_and_live_provenance(self):
        for key in ("objects", "layers"):
            app = fixture(count=20000)
            result = self.reader(app).read("narwhal." + key, {"offset": 9000, "limit": 32})
            table = getattr(app.model.file, key.title())
            self.assertEqual(table.indices, list(range(9000, 9032)))
            self.assertEqual(len(result[key]), 32)
            self.assertEqual((result["offset"], result["limit"], result["total_count"]), (9000, 32, 20000))
            self.assertEqual((result["next_offset"], result["truncated"], result["truncation_reason"]),
                             (9032, True, "page_limit"))
            self.assertEqual(result["provenance"], {
                "source": "NarwhalModel.file." + key.title(), "access": "indexed",
                "consistency": "live_not_snapshot", "geometry_measured": False})
            id_key = "object_id" if key == "objects" else "layer_id"
            self.assertEqual(result[key][0][id_key], str(uuid.UUID(int=9001)))

    def test_page_ends_empty_and_schema_cursor_ceiling(self):
        for key in ("objects", "layers"):
            for count, offset, limit, expected, cursor, truncated, reason in (
                    (0, 0, 32, 0, None, False, None),
                    (4, 4, 32, 0, None, False, None),
                    (4, 9, 32, 0, None, False, None),
                    (4, 3, 32, 1, None, False, None),
                    (10050, 9999, 1, 1, 10000, True, "page_limit"),
                    (10050, 10000, 32, 32, None, True, "offset_limit")):
                with self.subTest(key=key, count=count, offset=offset):
                    result = self.reader(fixture(count)).read("narwhal." + key,
                                                            {"offset": offset, "limit": limit})
                    self.assertEqual(len(result[key]), expected)
                    self.assertEqual((result["next_offset"], result["truncated"], result["truncation_reason"]),
                                     (cursor, truncated, reason))

    def test_unicode_byte_budget_and_resume_without_skips(self):
        for key in ("objects", "layers"):
            app = fixture(32, "\U0001f300" * 300)
            reader = self.reader(app)
            offset, seen, byte_limited_pages = 0, [], 0
            while True:
                probes_before = len(getattr(app.model.file, key.title()).indices)
                result = reader.read("narwhal." + key, {"offset": offset, "limit": 32})
                probes = len(getattr(app.model.file, key.title()).indices) - probes_before
                self.assertLessEqual(probes, 32)
                self.assertLessEqual(len(encoded(result)), 48 * 1024)
                self.assertGreater(len(result[key]), 0)
                self.assertTrue(all(len(entry["name"]) == 256 for entry in result[key]))
                id_key = "object_id" if key == "objects" else "layer_id"
                seen.extend(entry[id_key] for entry in result[key])
                if result["next_offset"] is None:
                    self.assertFalse(result["truncated"])
                    break
                self.assertEqual(result["next_offset"], offset + len(result[key]))
                self.assertTrue(result["truncated"])
                self.assertEqual(result["truncation_reason"], "byte_limit")
                byte_limited_pages += 1
                self.assertEqual(probes, len(result[key]) + 1)
                self.assertLess(len(result[key]), 32)
                offset = result["next_offset"]
                self.assertLess(offset, 32)
            self.assertEqual(seen, [str(uuid.UUID(int=i + 1)) for i in range(32)])
            # One rejected record per byte-limited page is re-read on resumption.
            self.assertEqual(len(getattr(app.model.file, key.title()).indices), 32 + byte_limited_pages)

    def test_object_metadata_reuses_honest_reader(self):
        for declared, present in (("analytic-master", False), ("analytic-master", True),
                                  ("analytic", False), ("mesh-derived", False), ("", False)):
            obj = row(tags={"Narwhal3D:Representation": declared, "Narwhal3D:KernelShape": "master"})
            app = fixture()
            app.model.file.Objects = IndexedTable([obj])
            self.kernel._MASTERS.clear()
            if present:
                self.kernel._MASTERS["master"] = object()
            before = (copy.deepcopy(vars(obj.Attributes)), dict(obj.Geometry.tags), dict(self.kernel._MASTERS))
            reader = self.reader(app)
            with patch.object(reader, "_object", wraps=reader._object) as describe:
                result = reader.read("narwhal.objects", {"offset": 0, "limit": 32})["objects"][0]
            describe.assert_called_once_with(obj)
            missing = declared == "analytic-master" and not present
            self.assertEqual(result["geometry_source"], None if missing else
                             "occt_master" if present else "document_geometry")
            self.assertEqual(result["integrity"], "missing_master" if missing else "not_audited")
            self.assertEqual(result["representation_declared"], declared or "unknown")
            self.assertEqual(before, (vars(obj.Attributes), obj.Geometry.tags, self.kernel._MASTERS))

    def test_local_layer_flags_not_inherited(self):
        parent, child = layer(), layer(1)
        parent.Visible, parent.Locked = False, True
        child.ParentLayerId = parent.Id
        app = fixture()
        app.model.file.Layers = IndexedTable([parent, child])
        result = self.reader(app).read("narwhal.layers", {"offset": 1, "limit": 1})["layers"][0]
        self.assertEqual(result, {"layer_id": str(child.Id), "index": 1, "name": "Layer",
                                 "parent_layer_id": str(parent.Id), "visible_local": True,
                                 "locked_local": False, "effective_flags": {
                                     "available": False, "reason": "inherited_flags_not_resolved"}})
        self.assertEqual(app.model.file.Layers.indices, [1])
        parent_result = self.reader(app).read("narwhal.layers", {"offset": 0, "limit": 1})["layers"][0]
        self.assertEqual((parent_result["visible_local"], parent_result["locked_local"]), (False, True))

    def test_workspace_canonical_values_no_invented_phase_or_ui(self):
        app = fixture()
        app.model.current_layer_index = 19
        app.model.unit_system = lambda: "Inches"
        app.model.absolute_tolerance = lambda: .123
        reader = self.reader(app)
        for command in (None, types.SimpleNamespace(name="Circle")):
            app.active_command = command
            result = reader.read("narwhal.workspace", {})
            self.assertEqual((result["unit_system"], result["tolerance"], result["current_layer_index"]),
                             ("Inches", .123, 19))
            self.assertEqual(result["active_command"], {
                "busy": command is not None, "name": "Circle" if command else None,
                "phase": {"available": False, "reason": "canonical_phase_read_api_unavailable"}})
            for key in ("cplane", "view"):
                self.assertEqual(result[key], {"available": False, "reason": "ui_snapshot_not_connected"})
            self.assertFalse(result["provenance"]["geometry_measured"])
        self.assertEqual(app.model.file.Objects.indices, [])
        self.assertEqual(app.model.file.Layers.indices, [])
        for bad in (float("nan"), float("inf")):
            app.model.absolute_tolerance = lambda: bad
            with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
                reader.read("narwhal.workspace", {})

    @staticmethod
    def workspace_snapshot():
        # Deliberately not an orthonormal frame: the adapter must only copy it.
        return {"cplane": {"origin": [12, -3.5, 1e100], "xaxis": [2, 3, 4],
                           "yaxis": [-7, 0, 1], "zaxis": [0, 0, 0]},
                "view": {"name": "\U0001f300" * 256, "projection": "PERSP"},
                "command_phase": "\U0001f300" * 64}

    def test_workspace_callback_frame_values_provenance_and_copy(self):
        snapshot = self.workspace_snapshot()
        app = fixture()
        app.active_command = types.SimpleNamespace(name="Circle")
        calls = []
        def callback():
            calls.append(True)
            return snapshot
        reader = self.reader(app, workspace_snapshot=callback)
        for projection in ("PERSP", "ORTHO", "CAMERA"):
            snapshot["view"]["projection"] = projection
            before = copy.deepcopy(snapshot)
            result = reader.read("narwhal.workspace", {})
            for field, value in (("cplane", result["cplane"]), ("view", result["view"]),
                                 ("command_phase", result["active_command"]["phase"])):
                self.assertEqual(value, {"available": True, "value": snapshot[field],
                                        "provenance": {"source": "workspace_snapshot",
                                                       "consistency": "live_not_snapshot",
                                                       "geometry_measured": False}})
            self.assertEqual(result["active_command"]["name"], "Circle")
            self.assertTrue(result["active_command"]["busy"])
            self.assertEqual(result["provenance"]["source"], "NarwhalModel/AppState.active_command")
            self.assertLessEqual(len(encoded(result)), 48 * 1024)
            result["cplane"]["value"]["origin"][0] = 999
            result["view"]["value"]["name"] = "changed"
            self.assertEqual(snapshot, before)
        self.assertEqual(calls, [True, True, True])
        self.assertEqual(app.model.file.Objects.indices, [])
        self.assertEqual(app.model.file.Layers.indices, [])
        # A later callback result, not an adapter cache, supplies the next context.
        snapshot["command_phase"] = "Radius"
        self.assertEqual(reader.read("narwhal.workspace", {})["active_command"]["phase"]["value"], "Radius")

    def test_workspace_callback_nullable_fields_and_empty_strings(self):
        for mask in range(8):
            snapshot = self.workspace_snapshot()
            for index, field in enumerate(snapshot):
                if mask & (1 << index):
                    snapshot[field] = None
            result = self.reader(workspace_snapshot=lambda: snapshot).read("narwhal.workspace", {})
            for field, value in (("cplane", result["cplane"]), ("view", result["view"]),
                                 ("command_phase", result["active_command"]["phase"])):
                if snapshot[field] is None:
                    self.assertEqual(value, {"available": False, "reason": "workspace_snapshot_unavailable"})
                else:
                    self.assertTrue(value["available"])
                    self.assertEqual(value["value"], snapshot[field])
        result = self.reader(workspace_snapshot=lambda: None).read("narwhal.workspace", {})
        for value in (result["cplane"], result["view"], result["active_command"]["phase"]):
            self.assertEqual(value, {"available": False, "reason": "workspace_snapshot_unavailable"})
        snapshot = {"cplane": None, "view": {"name": "", "projection": "ORTHO"}, "command_phase": ""}
        result = self.reader(workspace_snapshot=lambda: snapshot).read("narwhal.workspace", {})
        self.assertTrue(result["view"]["available"])
        self.assertEqual(result["view"]["value"]["name"], "")
        self.assertEqual(result["active_command"]["phase"]["value"], "")

    def test_workspace_callback_rejects_malformed_bounds_and_extras(self):
        valid = self.workspace_snapshot()
        invalid = [[], {}, False, dict(valid, credentials="private-secret")]
        for field in valid:
            invalid.append({key: value for key, value in valid.items() if key != field})
            invalid.append({("credentials" if key == field else key): value for key, value in valid.items()})
        for field, values in (("cplane", ([], False, dict(valid["cplane"], credentials="private-secret"))),
                              ("view", ([], False, dict(valid["view"], credentials="private-secret"))),
                              ("command_phase", (False, 1, [], "p" * 65))):
            invalid.extend(dict(valid, **{field: value}) for value in values)
        for axis in valid["cplane"]:
            for vector in ([], [0, 0], [0, 0, 0, 0], (0, 0, 0), "xyz", None,
                           [True, 0, 0], ["1", 0, 0], [None, 0, 0],
                           [float("nan"), 0, 0], [float("inf"), 0, 0], [-float("inf"), 0, 0],
                           [10 ** 1000, 0, 0]):
                invalid.append(dict(valid, cplane=dict(valid["cplane"], **{axis: vector})))
            invalid.append(dict(valid, cplane={key: value for key, value in valid["cplane"].items() if key != axis}))
            invalid.append(dict(valid, cplane={("credentials" if key == axis else key): value
                                               for key, value in valid["cplane"].items()}))
        for field, values in (("name", (None, 1, [], "v" * 257)),
                              ("projection", (None, 1, [], "persp", "UNKNOWN"))):
            invalid.extend(dict(valid, view=dict(valid["view"], **{field: value})) for value in values)
            invalid.append(dict(valid, view={key: value for key, value in valid["view"].items() if key != field}))
            invalid.append(dict(valid, view={("credentials" if key == field else key): value
                                             for key, value in valid["view"].items()}))
        for index, snapshot in enumerate(invalid):
            with self.subTest(case=index):
                reader = self.reader(workspace_snapshot=lambda: snapshot)
                with patch.object(reader, "_workspace_value", wraps=reader._workspace_value) as copy_value:
                    with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
                        reader.read("narwhal.workspace", {})
                    copy_value.assert_not_called()

    def test_workspace_callback_thread_arguments_and_secret_safe_failures(self):
        calls = []
        def callback():
            calls.append(True)
            return self.workspace_snapshot()
        reader = self.reader(workspace_snapshot=callback)
        errors = worker_call(lambda: reader.read("narwhal.workspace", {}))
        self.assertEqual([str(e) for e in errors], ["main_thread_required"])
        errors = worker_call(lambda: self.reader(workspace_snapshot=callback))
        self.assertEqual([str(e) for e in errors], ["main_thread_required"])
        with self.assertRaisesRegex(self.tools.ReadError, "^invalid_arguments$"):
            reader.read("narwhal.workspace", {"credentials": "private-secret"})
        reader.read("narwhal.objects", {"offset": 0, "limit": 1})
        self.assertEqual(calls, [])
        reader.read("narwhal.workspace", {})
        self.assertEqual(calls, [True])
        for callback in (False, [], "callback"):
            with self.assertRaisesRegex(ValueError, "^invalid_workspace_snapshot$"):
                self.reader(workspace_snapshot=callback)
        def fail():
            raise RuntimeError("private-secret")
        with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
            self.reader(workspace_snapshot=fail).read("narwhal.workspace", {})

    def test_workspace_callback_private_frame_roundtrip(self):
        snapshot = self.workspace_snapshot()
        bridge, peer, _sock = self.pair(self.reader(workspace_snapshot=lambda: snapshot))
        peer.sendall(self.request(tool="narwhal.workspace"))
        wire = bytearray()
        for _ in range(20):
            bridge.pump()
            wire.extend(self.receive(peer))
            if b"\n" in wire:
                break
        self.assertFalse(bridge.closed)
        reply = json.loads(wire)
        self.assertEqual(set(reply), {"session", "seq", "result"})
        self.assertEqual(reply["result"]["cplane"]["value"], snapshot["cplane"])
        self.assertLessEqual(len(encoded(reply["result"])), 48 * 1024)
        self.assertLessEqual(len(wire), 65536)

    def test_components_unknown_is_not_empty(self):
        for kwargs, reason in (({}, "component_snapshot_not_connected"),
                               ({"component_snapshot": lambda **kw: None}, "canonical_components_unavailable")):
            result = self.reader(**kwargs).read("narwhal.components", {})
            self.assertEqual(result, {"available": False, "reason": reason,
                                     "provenance": {"source": "component_snapshot",
                                                    "consistency": "live_not_snapshot", "geometry_measured": False}})
            self.assertNotIn("components", result)
        with self.assertRaisesRegex(ValueError, "invalid_component_snapshot"):
            self.reader(component_snapshot=[])

    def test_component_callback_limit_ids_types_indices_and_copy(self):
        calls = []
        entries = [{"object_id": str(uuid.UUID(int=0xABC)).upper(),
                    "component_type": kind, "component_index": i}
                   for i, kind in enumerate(("Edge", "Face", "Vertex", "Segment"))]
        snapshot = {"components": entries, "truncated": True}
        def callback(*, limit):
            calls.append(limit)
            return snapshot
        reader = self.reader(component_snapshot=callback)
        result = reader.read("narwhal.components", {})
        self.assertEqual(calls, [32])
        self.assertEqual(result["components"], [dict(e, object_id=e["object_id"].lower()) for e in entries])
        self.assertTrue(result["available"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scope"], "subobjects")
        self.assertLessEqual(len(encoded(result)), 48 * 1024)
        result["components"][0]["component_index"] = 77
        self.assertEqual(entries[0]["component_index"], 0)
        snapshot.update(components=[], truncated=False)
        empty = reader.read("narwhal.components", {})
        self.assertTrue(empty["available"])
        self.assertEqual(empty["components"], [])
        self.assertFalse(empty["truncated"])
        self.assertEqual(reader.snapshot_contract(), {"limit": 32,
                         "component_types": ["Edge", "Face", "Segment", "Vertex"]})

    def test_invalid_components_fail_closed_and_safe(self):
        entry = {"object_id": str(uuid.UUID(int=1)), "component_type": "Edge", "component_index": 0}
        invalid = [{}, [], {"components": (), "truncated": False},
                   {"components": [], "truncated": 0}, {"components": [], "truncated": False, "extra": 1},
                   {"components": [entry] * 33, "truncated": True}]
        for key, values in (("object_id", ("name", "0" * 32, 1, None)),
                            ("component_type", ("edge", "DisplayTriangle", "ControlPoint", None, [])),
                            ("component_index", (-1, True, 1.0, "1", 2147483648))):
            invalid.extend({"components": [dict(entry, **{key: value})], "truncated": False} for value in values)
        invalid.append({"components": [dict(entry, extra=True)], "truncated": False})
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
                    self.reader(component_snapshot=lambda **kw: snapshot).read("narwhal.components", {})
        def fail(**kw):
            raise RuntimeError("private-secret")
        with self.assertRaisesRegex(self.tools.ReadError, "^read_failed$"):
            self.reader(component_snapshot=fail).read("narwhal.components", {})

    def test_main_thread_guard_precedes_all_reads_and_callbacks(self):
        app = fixture()
        calls = []
        reader = self.reader(app, component_snapshot=lambda **kw: calls.append(kw))
        for tool, args in (("narwhal.objects", {"offset": 0, "limit": 1}),
                           ("narwhal.layers", {"offset": 0, "limit": 1}),
                           ("narwhal.workspace", {}), ("narwhal.components", {})):
            errors = worker_call(lambda: reader.read(tool, args))
            self.assertEqual([str(e) for e in errors], ["main_thread_required"])
        self.assertEqual(calls, [])
        self.assertEqual(app.model.file.Objects.indices, [])
        self.assertEqual(app.model.file.Layers.indices, [])

    def test_new_tools_private_framing_and_strict_dispatch(self):
        app = fixture(32, "\U0001f300" * 256)
        bridge, peer, _sock = self.pair(self.reader(app))
        for seq, (tool, args) in enumerate((
                ("narwhal.objects", {"offset": 0, "limit": 32}),
                ("narwhal.layers", {"offset": 0, "limit": 32}),
                ("narwhal.workspace", {}), ("narwhal.components", {})), 1):
            peer.sendall(self.request(seq=seq, tool=tool, arguments=args))
            wire = bytearray()
            for _ in range(20):
                bridge.pump()
                wire.extend(self.receive(peer))
                if b"\n" in wire:
                    break
            self.assertFalse(bridge.closed)
            reply = json.loads(wire)
            self.assertEqual(set(reply), {"session", "seq", "result"})
            self.assertEqual(reply["seq"], seq)
            self.assertLessEqual(len(encoded(reply["result"])), 48 * 1024)
            self.assertLessEqual(len(wire), 65536)
        for key in ("objects", "layers"):
            indices = list(getattr(app.model.file, key.title()).indices)
            peer.sendall(self.request(seq=5 if key == "objects" else 6, tool="narwhal." + key,
                                      arguments={"offset": False, "limit": 1}))
            bridge.pump()
            self.assertEqual(json.loads(self.receive(peer))["error"], "invalid_arguments")
            self.assertEqual(getattr(app.model.file, key.title()).indices, indices)

    def test_adapter_imports_no_ui_or_actions(self):
        source = SOURCES["automation_tools"]
        import ast
        tree = ast.parse(source)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = {alias.name for node in imports for alias in node.names}
        self.assertFalse(names.intersection({"ui", "bpy", "commands", "state", "bridge", "occt_backend"}))
        self.assertNotIn("narwhal.operations", self.tools.TOOLS)
        self.assertFalse(any(name.endswith(".ui") for name in sys.modules if name.startswith(self.tools.__package__)))


class RealDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import rhino3dm as r3
        except ImportError:
            raise unittest.SkipTest("rhino3dm unavailable; run with bundled Python")
        cls.r3 = r3
        cls.package, cls.tools, cls.bridge, cls.kernel = load_modules(SOURCES)
        spec = importlib.util.spec_from_file_location(cls.package + ".model", ROOT / "narwhal3d/model.py")
        cls.model_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.model_module
        spec.loader.exec_module(cls.model_module)

    @classmethod
    def tearDownClass(cls):
        unload(cls.package)

    def document(self):
        model = self.model_module.NarwhalModel()
        ids = []
        for i in range(67):
            attrs = self.r3.ObjectAttributes()
            attrs.Name = "duplicate" if i % 2 else "\U0001f300" * 256
            attrs.Id = uuid.UUID(int=i + 1)
            geom = (self.r3.LineCurve(self.r3.Point3d(i, 0, 0), self.r3.Point3d(i, 1, 0))
                    if i % 2 else self.r3.Point(self.r3.Point3d(i, 0, 0)))
            ids.append(str(model.file.Objects.Add(geom, attrs)))
        app = types.SimpleNamespace(model=model, active_command=None)
        return model, self.tools.ApplicationReader(app, "real-test"), ids

    def test_real_native_pagination_uuids_and_unchanged_encoding(self):
        model, reader, ids = self.document()
        before = model.file.Encode()
        dirty = dict(model._dirty)
        seen, offset = [], 0
        while True:
            result = reader.read("narwhal.objects", {"offset": offset, "limit": 32})
            self.assertLessEqual(len(encoded(result)), 48 * 1024)
            for entry in result["objects"]:
                self.assertEqual(entry["geometry_source"], "document_geometry")
                self.assertEqual(entry["integrity"], "not_audited")
                self.assertIn(entry["storage_type"], ("LineCurve", "Point"))
            seen.extend(entry["object_id"] for entry in result["objects"])
            if result["next_offset"] is None:
                break
            self.assertEqual(result["next_offset"], offset + len(result["objects"]))
            offset = result["next_offset"]
        self.assertEqual(seen, ids)
        self.assertEqual([str(model.file.Objects[i].Attributes.Id) for i in (0, 32, 66)],
                         [ids[i] for i in (0, 32, 66)])
        self.assertEqual(model.file.Encode(), before)
        self.assertEqual(model._dirty, dirty)

    def test_real_layers_parent_local_flags_and_workspace(self):
        model, reader, _ids = self.document()
        parent = self.r3.Layer()
        parent.Name, parent.Visible, parent.Locked = "Parent", False, True
        pi = model.file.Layers.Add(parent)
        child = self.r3.Layer()
        child.Name = "Child"
        child.ParentLayerId = model.file.Layers[pi].Id
        ci = model.file.Layers.Add(child)
        model.current_layer_index = ci
        model.file.Settings.ModelUnitSystem = self.r3.UnitSystem.Inches
        model.file.Settings.ModelAbsoluteTolerance = .0025
        before = model.file.Encode()
        p = reader.read("narwhal.layers", {"offset": pi, "limit": 1})
        c = reader.read("narwhal.layers", {"offset": ci, "limit": 1})
        self.assertEqual(p["next_offset"], ci)
        self.assertIsNone(c["next_offset"])
        self.assertEqual((p["layers"][0]["visible_local"], p["layers"][0]["locked_local"]), (False, True))
        self.assertEqual((c["layers"][0]["visible_local"], c["layers"][0]["locked_local"]), (True, False))
        self.assertEqual(c["layers"][0]["parent_layer_id"], p["layers"][0]["layer_id"])
        self.assertFalse(c["layers"][0]["effective_flags"]["available"])
        workspace = reader.read("narwhal.workspace", {})
        self.assertEqual((workspace["unit_system"], workspace["tolerance"], workspace["current_layer_index"]),
                         ("Inches", .0025, ci))
        self.assertEqual(model.file.Encode(), before)
        self.assertFalse(reader.read("narwhal.components", {})["available"])


MUTATIONS = (
    ("page byte budget", "if size + cost > MAX_CONTEXT_BYTES:", "if False:"),
    ("page count bound", "MAX_PAGE = 32", "MAX_PAGE = 64"),
    ("name byte amplification", "return value[:MAX_NAME]", "return value"),
    ("offset boolean rejected", 'type(arguments["offset"]) is not int', 'not isinstance(arguments["offset"], int)'),
    ("limit boolean rejected", 'type(arguments["limit"]) is not int', 'not isinstance(arguments["limit"], int)'),
    ("exact page keys", 'set(arguments) != {"offset", "limit"}', 'not {"offset", "limit"}.issubset(arguments)'),
    ("next offset no skipping", "end = offset + len(result[key])", "end = offset + limit"),
    ("truncation honest", 'result["truncated"] = more', 'result["truncated"] = False'),
    ("cursor ceiling", "end if more and end <= MAX_SCAN else None", "end if more else None"),
    ("provenance is live", '"consistency": "live_not_snapshot"', '"consistency": "atomic_snapshot"'),
    ("no geometry measurement claim", '"geometry_measured": False', '"geometry_measured": True'),
    ("missing master", "missing = declared == kernel.ANALYTIC_MASTER and not master_present", "missing = False"),
    ("local layer visibility", '"visible_local": bool(layer.Visible)', '"visible_local": True'),
    ("unknown components", 'return {"available": False, "reason": reason}', 'return {"available": True, "reason": reason}'),
    ("callback bounded", "self._component_snapshot(limit=MAX_PAGE)", "self._component_snapshot(limit=10000)"),
    ("callback row bound", 'len(snapshot["components"]) > MAX_PAGE', 'False'),
    ("canonical component type", 'entry["component_type"] not in COMPONENT_TYPES', 'False'),
    ("component index boolean", 'type(entry["component_index"]) is not int', 'not isinstance(entry["component_index"], int)'),
    ("component copy", 'entries.append(dict(entry, object_id=entry["object_id"].lower()))', 'entries.append(entry)'),
    ("workspace busy", '"busy": command is not None', '"busy": False'),
    ("workspace units", '"unit_system": _text(model.unit_system()), "tolerance": tolerance',
     '"unit_system": "Millimeters", "tolerance": tolerance'),
    ("workspace callback validation", "self._validate_workspace_snapshot(snapshot)", "pass"),
    ("workspace snapshot exact keys", 'len(snapshot) != 3\n                or set(snapshot) != {"cplane", "view", "command_phase"}', 'False'),
    ("workspace frame exact keys", 'set(frame) != {"origin", "xaxis", "yaxis", "zaxis"}', 'False'),
    ("workspace vector length", 'len(vector) != 3', 'False'),
    ("workspace coordinate boolean", 'type(coordinate) not in (int, float)', 'not isinstance(coordinate, (int, float))'),
    ("workspace coordinate finite", 'not math.isfinite(coordinate)', 'False'),
    ("workspace view exact keys", 'len(view) != 2 or set(view) != {"name", "projection"}', 'False'),
    ("workspace view name bound", 'len(view["name"]) > MAX_NAME', 'False'),
    ("workspace projection enum", 'view["projection"] not in {"PERSP", "ORTHO", "CAMERA"}', 'False'),
    ("workspace phase bound", 'len(phase) > 64', 'False'),
    ("workspace frame independent copy", '{key: list(value) for key, value in frame.items()}', 'frame'),
    ("workspace view independent copy", 'dict(view) if view is not None else None', 'view if view is not None else None'),
    ("workspace provided flag", 'return {"available": True, "value": value,', 'return {"available": False, "value": value,'),
    ("workspace null unavailable", 'if value is None:\n            return self._unavailable("workspace_snapshot_unavailable")',
     'if False:\n            return self._unavailable("workspace_snapshot_unavailable")'),
    ("workspace callback provenance", 'self._provenance("workspace_snapshot")', 'self._provenance("invented_context")'),
    ("workspace callback once", 'snapshot = self._workspace_snapshot()', 'snapshot = self._workspace_snapshot(); self._workspace_snapshot()'),
    ("main thread", "threading.current_thread() is not threading.main_thread()", "False"),
)


def run_controls(sources):
    package, tools, bridge, kernel = load_modules(sources)
    cls = type("IsolatedContext", (Controls,), {"tools": tools, "bridge_module": bridge, "kernel": kernel})
    output = io.StringIO()
    try:
        result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
        return result, output.getvalue()
    finally:
        unload(package)


def main(*, pure=False):
    result, text = run_controls(SOURCES)
    print(text, end="")
    if not result.wasSuccessful():
        return 1
    for label, old, new in MUTATIONS:
        if SOURCES["automation_tools"].count(old) != 1:
            print("FAIL mutation anchor:", label)
            return 1
        sources = dict(SOURCES)
        sources["automation_tools"] = sources["automation_tools"].replace(old, new, 1)
        mutant, _text = run_controls(sources)
        if mutant.wasSuccessful():
            print("FAIL surviving mutation:", label)
            return 1
        print("RED confirmed:", label)
    restored, text = run_controls(SOURCES)
    if not restored.wasSuccessful():
        print(text)
        return 1
    print(f"PASS: {result.testsRun} mock controls; {len(MUTATIONS)} in-memory mutations RED; original GREEN")
    if pure:
        return 0
    real = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RealDocument))
    return 0 if real.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(pure="--pure" in sys.argv[1:]))
