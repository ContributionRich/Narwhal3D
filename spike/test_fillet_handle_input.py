"""Headless radius-subprompt regression tests on real File3dm/OCCT geometry.

Use bundled Python with -B, PYTHONPATH=vendor/occt and the OCCT library path.
No GUI/builds. --mutations patches compiled methods in this process only.
Python tests command/UI orchestration; no new geometry algorithms are introduced.
"""
import inspect
import io
from pathlib import Path
import sys
import textwrap
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg

from narwhal3d import bridge, commands, kernel, model, solid_commands as solid
from narwhal3d.kernel import occt_backend as occt
from narwhal3d.undo import UndoStack


class Ctx:
    """Same real-model/lazy-undo pattern as test_solid_commands.Ctx + UndoCtx."""
    def __init__(self):
        self.model = model.NarwhalModel()
        self.model.add_object(bridge.make_box((0, 0, 0), (10, 10, 0), 10), "Box", 0)
        self.added = []
        self.undo = UndoStack()

    def names(self):
        return [obj.Attributes.Name for obj in self.model.file.Objects]

    def geom_by_name(self, name):
        return next((obj.Geometry for obj in self.model.file.Objects
                     if obj.Attributes.Name == name), None)

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return []

    def begin_undo(self, name):
        self.undo.begin(name)

    def end_undo(self):
        self.undo.end()

    def note_change(self, label=None):
        if not self.undo.captured:
            self.undo.capture(self.model.snapshot())

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        name = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, name, 0)
        self.added.append(geom)
        return name, name


def fixture(edit=True, chamfer=False):
    ctx = Ctx()
    cmd = solid.ChamferEdgeCmd() if chamfer else solid.FilletEdgeCmd()
    cmd.begin(ctx)
    cmd.feed(f"{cmd.size_option}=0.5", ctx)
    ends = bridge.bevel_edge_endpoints_occt(ctx.geom_by_name("Box"), [1])[1]
    point = tuple((a + b) / 2 for a, b in zip(*ends))
    msg, done = cmd.pick_edge("Box", 1, point, "plain", ctx)
    assert not done and cmd._edge_radii == {1: (0.5, 0.5)}, msg
    if edit:
        cmd.feed("", ctx)
        assert cmd._candidate is not None, cmd._candidate_error
    return cmd, ctx, ("Box", 1, 0 if edit and not chamfer else None)


def cache(cmd):
    return tuple(getattr(cmd, name) for name in cmd._handle_cache_fields)


class HandleInputTests(unittest.TestCase):
    def unchanged(self, ctx):
        self.assertEqual(ctx.names(), ["Box"])
        self.assertEqual(ctx.added, [])
        self.assertFalse(ctx.undo.can_undo)

    def test_prompts_share_segments_and_suboptions_are_honest(self):
        cmd, ctx, scalar = fixture(edit=False)
        def labels():
            segments = cmd.prompt_segments()
            self.assertIsInstance(segments, list)
            self.assertEqual(cmd.prompt, "".join(text for text, _ in segments))
            return {name for _, name in segments if name}
        self.assertIn("NextRadius", labels())
        cmd.feed("NextRadius", ctx)
        self.assertEqual(labels(), set())
        self.assertIn("Fillet radius <0.5>", cmd.prompt)
        self.assertFalse(cmd.begin_handle_input(scalar, ctx))
        cmd.feed("", ctx)
        cmd.feed("", ctx)
        self.assertNotIn("NextRadius", labels())
        self.assertIn("SelectEdges", labels())
        self.assertTrue(cmd.begin_handle_input(("Box", 1, 0), ctx))
        self.assertTrue(cmd.prompt.startswith("Select new fillet radius <0.500>"))
        self.assertEqual(labels(), {"FromCurve", "FromTwoPoints"})
        original = cache(cmd)
        for word in ("FromCurve", "FromTwoPoints", "FromC", "FromT"):
            msg, done = cmd.feed(word, ctx)
            self.assertFalse(done)
            self.assertIn("not implemented yet", msg)
            self.assertTrue(cmd.handle_input_active())
            self.assertEqual(cache(cmd), original)
        msg, done = cmd.feed("From", ctx)
        self.assertFalse(done)
        self.assertIn("ambiguous", msg)
        self.unchanged(ctx)

    def test_numeric_endpoint_then_later_enter_commits_once_and_undoes(self):
        cmd, ctx, handle = fixture()
        original_id = ctx.model.object_id_by_name("Box")
        original_recipe = kernel.recipe_of(ctx.geom_by_name("Box"))
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        with patch.object(commands, "parse_number", wraps=commands.parse_number) as parse:
            msg, done = cmd.feed("3/4", ctx)
            parse.assert_called_once_with("3/4")
        self.assertFalse(done, msg)
        self.assertFalse(cmd.handle_input_active())
        self.assertEqual(cmd.phase, cmd.EDIT_HANDLES)
        self.assertEqual(cmd._edge_radii[1], (0.75, 0.5))
        self.unchanged(ctx)
        candidate = cmd._candidate
        self.assertIsNotNone(candidate)
        self.assertTrue(occt.is_airtight(kernel.master_of(candidate)))
        self.assertIsNone(kernel.honesty_problem(candidate))
        with patch.object(cmd, "_refresh_candidate", side_effect=AssertionError("rebuilt on commit")):
            msg, done = cmd.feed("", ctx)
        self.assertTrue(done, msg)
        self.assertEqual(len(ctx.added), 1)
        self.assertIs(ctx.added[0], candidate)
        self.assertEqual(ctx.undo.undo_names(), ["FilletEdge"])
        recipe = kernel.recipe_of(candidate)
        self.assertEqual(recipe["edge_radii"], [[0.75, 0.5]])
        self.assertTrue(occt.is_airtight(occt.from_recipe(recipe)))
        record = ctx.undo.undo(ctx.model.snapshot())
        ctx.model.restore(record.before)
        self.assertEqual(ctx.names(), ["Box"])
        self.assertEqual(ctx.model.object_id_by_name("Box"), original_id)
        self.assertEqual(kernel.recipe_of(ctx.geom_by_name("Box")), original_recipe)
        self.assertFalse(ctx.undo.can_undo)

    def test_default_and_scalar_selection_do_not_advance_phase(self):
        for edit in (False, True):
            with self.subTest(edit=edit):
                cmd, ctx, handle = fixture(edit=edit)
                phase = cmd.phase
                self.assertTrue(cmd.begin_handle_input(handle, ctx))
                self.assertFalse(cmd.feed("", ctx)[1])
                self.assertFalse(cmd.handle_input_active())
                self.assertEqual(cmd.phase, phase)
                self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
                self.assertTrue(cmd.begin_handle_input(handle, ctx))
                self.assertFalse(cmd.feed("0.8", ctx)[1])
                self.assertEqual(cmd._edge_radii[1], (0.8, 0.5) if edit else (0.8, 0.8))
                self.assertEqual(cmd.phase, phase)
                self.unchanged(ctx)

    def test_invalid_input_stays_local_and_clears_candidate(self):
        cmd, ctx, handle = fixture()
        cmd.feed("Preview=Yes", ctx)
        for raw in ("0", "-1", "nan", "inf", "1e999", "garbage", "SelectEdges"):
            with self.subTest(raw=raw):
                self.assertTrue(cmd.begin_handle_input(handle, ctx))
                self.assertIsNotNone(cmd.preview_geometry())
                msg, done = cmd.feed(raw, ctx)
                self.assertFalse(done, msg)
                self.assertTrue(cmd.handle_input_active())
                self.assertIn("Select new fillet radius", cmd.prompt)
                self.assertIsNone(cmd._candidate)
                self.assertIsNone(cmd.preview_geometry())
                self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
                self.assertEqual(cmd.phase, cmd.EDIT_HANDLES)
                self.assertFalse(cmd.execute(ctx)[1])
                self.unchanged(ctx)
                # Empty input accepts the displayed default, never commits the solid.
                self.assertFalse(cmd.feed("", ctx)[1])
                self.assertFalse(cmd.handle_input_active())
                self.assertIsNotNone(cmd._candidate)
                self.unchanged(ctx)

    def test_kernel_decline_can_be_corrected_or_cancelled(self):
        cmd, ctx, handle = fixture()
        original = cache(cmd)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        msg, done = cmd.feed("100", ctx)
        self.assertFalse(done, msg)
        self.assertTrue(cmd.handle_input_active())
        self.assertEqual(cmd._edge_radii[1], (100, 0.5))
        self.assertIsNone(cmd._candidate)
        self.assertTrue(cmd._candidate_error)
        self.unchanged(ctx)
        self.assertTrue(cmd.cancel_handle_input(ctx))
        self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
        for actual, expected in zip(cache(cmd), original):
            self.assertIs(actual, expected)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        cmd.feed("100", ctx)
        self.assertFalse(cmd.feed("0.6", ctx)[1])
        self.assertFalse(cmd.handle_input_active())
        self.assertEqual(cmd._edge_radii[1], (0.6, 0.5))
        self.assertIsNotNone(cmd._candidate)
        self.unchanged(ctx)

    def test_drag_finish_and_escape_restore_cache_identity(self):
        cmd, ctx, handle = fixture()
        source = ctx.geom_by_name("Box")
        original_recipe = kernel.recipe_of(source)
        original_bytes = occt.shape_to_bytes(bridge._occt_operand(source), triangles=False)
        original = cache(cmd)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        self.assertFalse(cmd.begin_handle_input(("Box", 1, 1), ctx))
        cmd.set_handle_radius(handle, 0.7, ctx)
        self.assertTrue(cmd.handle_input_active())
        self.assertIn("<0.500>", cmd.prompt)
        cmd.set_handle_radius(("Box", 1, 1), 0.9, ctx)
        self.assertEqual(cmd._edge_radii[1], (0.7, 0.5))
        with patch.object(bridge, "bevel_edges_occt", side_effect=AssertionError("cancel rebuild")):
            self.assertTrue(cmd.cancel_handle_input(ctx))
        self.assertFalse(cmd.handle_input_active())
        self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
        for actual, expected in zip(cache(cmd), original):
            self.assertIs(actual, expected)
        self.assertFalse(cmd.cancel_handle_input(ctx))
        self.assertFalse(cmd.finish_handle_input())
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        cmd.set_handle_radius(handle, 0.7, ctx)
        changed = cmd._candidate
        self.assertTrue(cmd.finish_handle_input())
        self.assertFalse(cmd.handle_input_active())
        self.assertIs(cmd._candidate, changed)
        self.assertEqual(cmd._edge_radii[1], (0.7, 0.5))
        self.assertEqual(cmd.phase, cmd.EDIT_HANDLES)
        self.unchanged(ctx)
        self.assertEqual(kernel.recipe_of(source), original_recipe)
        self.assertEqual(occt.shape_to_bytes(bridge._occt_operand(source), triangles=False), original_bytes)

    def test_cancel_restores_invalid_cache_from_before_input(self):
        cmd, ctx, handle = fixture()
        cmd.set_handle_radius(handle, "bad", ctx)
        original = cache(cmd)
        self.assertIsNone(cmd._candidate)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        cmd.set_handle_radius(handle, 0.8, ctx)
        self.assertIsNotNone(cmd._candidate)
        self.assertTrue(cmd.cancel_handle_input(ctx))
        for actual, expected in zip(cache(cmd), original):
            self.assertIs(actual, expected)
        self.assertTrue(cmd.feed("", ctx)[1])
        self.unchanged(ctx)

    def test_ids_phase_selection_and_document_cannot_go_stale(self):
        cmd, ctx, handle = fixture()
        original = cache(cmd)
        for invalid in (None, list(handle), ("Box", 1), ("Other", 1, 0),
                        ("Box", 99, 0), ("Box", True, 0), ("Box", 1, True),
                        ("Box", 1, 2), ("Box", 1, None)):
            self.assertFalse(cmd.begin_handle_input(invalid, ctx), invalid)
            self.assertFalse(cmd.handle_input_active())
            self.assertEqual(cache(cmd), original)
        cmd.feed("ShowRadius=No", ctx)
        self.assertEqual(cmd.handle_records(), ())
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        self.assertFalse(cmd.pick_edge("Box", 1, (0, 0, 5), "ctrl", ctx)[1])
        self.assertIn(1, cmd._edge_radii)
        cmd.phase = cmd.SELECT_EDGES  # stale UI event, bypassing normal command routing
        self.assertFalse(cmd.feed("0.8", ctx)[1])
        self.assertFalse(cmd.handle_input_active())
        self.assertIsNone(cmd._candidate)
        self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
        self.assertFalse(cmd.begin_handle_input(handle, ctx))
        cmd.pick_edge("Box", 1, (0, 0, 5), "ctrl", ctx)
        self.assertFalse(cmd.begin_handle_input(("Box", 1, None), ctx))
        self.unchanged(ctx)
        for replacement in ("delete", "same_name", "document"):
            with self.subTest(replacement=replacement):
                cmd, ctx, handle = fixture()
                self.assertTrue(cmd.begin_handle_input(handle, ctx))
                if replacement == "document":
                    snapshot = ctx.model.snapshot()
                    ctx.model = model.NarwhalModel()
                    # Explicit document clone: import metadata values, not a foreign
                    # native snapshot handle. Preserve the object IDs for this test.
                    for key, value in snapshot.strings:
                        ctx.model.file.Strings[key] = value
                    snapshot.strings = ctx.model.snapshot().strings
                    ctx.model.restore(snapshot)  # same object IDs, genuinely new File3dm
                else:
                    ctx.model.remove_object(ctx.model.object_id_by_name("Box"))
                    if replacement == "same_name":
                        ctx.model.add_object(bridge.make_box((20, 0, 0), (30, 10, 0), 10), "Box", 0)
                self.assertFalse(cmd.begin_handle_input(handle, ctx))
                self.assertFalse(cmd.cancel_handle_input(ctx))
                self.assertIsNone(cmd._candidate)
                self.assertFalse(cmd.begin_handle_input(handle, ctx))
                self.assertTrue(cmd.feed("", ctx)[1])
                self.assertEqual(ctx.added, [])
                self.assertFalse(ctx.undo.can_undo)

    def test_invalid_release_cannot_commit_and_selection_cancel_restores_empty_candidate(self):
        cmd, ctx, handle = fixture(edit=False)
        original = cache(cmd)
        self.assertIsNone(cmd._candidate)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        cmd.set_handle_radius(handle, 0.8, ctx)
        self.assertIsNotNone(cmd._candidate)
        self.assertTrue(cmd.cancel_handle_input(ctx))
        self.assertEqual(cmd.phase, cmd.SELECT_EDGES)
        self.assertEqual(cmd._edge_radii[1], (0.5, 0.5))
        for actual, expected in zip(cache(cmd), original):
            self.assertIs(actual, expected)
        cmd.feed("", ctx)
        handle = ("Box", 1, 1)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        self.assertFalse(cmd.feed("0.7", ctx)[1])
        self.assertEqual(cmd._edge_radii[1], (0.5, 0.7))
        self.assertFalse(cmd.handle_input_active())
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        cmd.feed("invalid", ctx)
        self.assertTrue(cmd.finish_handle_input())
        self.assertFalse(cmd.handle_input_active())
        self.assertIsNone(cmd._candidate)
        msg, done = cmd.feed("", ctx)
        self.assertTrue(done)
        self.assertIn("Nothing was changed", msg)
        self.unchanged(ctx)

    def test_chamfer_distance_and_legacy_setter_remain_compatible(self):
        cmd, ctx, handle = fixture(chamfer=True)
        self.assertTrue(cmd.begin_handle_input(handle, ctx))
        self.assertIn("Select new chamfer distance <0.500>", cmd.prompt)
        self.assertFalse(cmd.feed("0.7", ctx)[1])
        self.assertFalse(cmd.handle_input_active())
        self.assertEqual(cmd._edge_radii[1], (0.7, 0.7))
        cmd.set_handle_radius(handle, 0.8, ctx)  # existing native-popup route needs no session
        self.assertEqual(cmd._edge_radii[1], (0.8, 0.8))
        self.assertIsNotNone(cmd._candidate)
        self.unchanged(ctx)


def mutations():
    """Run the same assertions against intentionally broken, process-local methods."""
    cls = solid._EdgeBevelCmd
    cases = [
        ("numeric commits early", "_feed", "return result\n", "return self.execute(ctx)\n",
         "test_numeric_endpoint_then_later_enter_commits_once_and_undoes"),
        ("default commits early", "_feed", 'raw = (text or "").strip()',
         'raw = (text or "").strip()\n    if not raw and self.handle_input_active():\n'
         '        self.finish_handle_input()\n        return self.execute(ctx)',
         "test_default_and_scalar_selection_do_not_advance_phase"),
        ("stale preview on typo", "_feed", 'return self.set_handle_radius(session["id"], float("nan"), ctx)',
         'return "invalid", False', "test_invalid_input_stays_local_and_clears_candidate"),
        ("decline closes subprompt", "_feed", 'if self._candidate is not None:', 'if True:',
         "test_kernel_decline_can_be_corrected_or_cancelled"),
        ("cancel loses pair", "cancel_handle_input",
         'self._edge_radii[session["id"][1]] = session["pair"]', 'pass',
         "test_drag_finish_and_escape_restore_cache_identity"),
        ("cancel loses cache identity", "cancel_handle_input", 'setattr(self, name, value)',
         'pass', "test_drag_finish_and_escape_restore_cache_identity"),
        ("both endpoints change", "set_handle_radius", 'pair[endpoint] = radius',
         'pair = [radius, radius]', "test_numeric_endpoint_then_later_enter_commits_once_and_undoes"),
        ("document guard bypassed", "_owner_is_current", 'return (self._owner_name',
         'return True or (self._owner_name', "test_ids_phase_selection_and_document_cannot_go_stale"),
        ("prompt loses clickable labels", "prompt_segments",
         'opts.prompt_segments(base, self._handle_input_options, self.opt)',
         'opts.prompt_segments(base, ())', "test_prompts_share_segments_and_suboptions_are_honest"),
    ]
    for label, method, before, after, test in cases:
        code = textwrap.dedent(inspect.getsource(getattr(cls, method)))
        assert code.count(before) == 1, (label, "mutation target drifted")
        scope = dict(solid.__dict__)
        exec(compile(code.replace(before, after), f"<mutation: {label}>", "exec"), scope)
        with patch.object(cls, method, scope[method]):
            result = unittest.TestResult()
            HandleInputTests(test).run(result)
        assert result.failures and not result.errors, (label, result.errors, "did not go RED")
        print(f"MUTATION RED: {label}: {result.failures[0][1].splitlines()[-1]}")
        restored = unittest.TextTestRunner(stream=io.StringIO()).run(HandleInputTests(test))
        assert restored.wasSuccessful(), (label, "restored implementation failed")
    print(f"{len(cases)} process-local mutations RED, restored tests GREEN")


if __name__ == "__main__":
    if "--mutations" in sys.argv:
        mutations()
    else:
        unittest.main(verbosity=2)
