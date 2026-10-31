"""Focused history facade tests: real ABI, no Blender/build/staging/large payloads.

Run under a 512 MiB / 90 s bound, e.g.:
  prlimit --as=536870912 --cpu=90 python3 -B spike/test_rust_history_abi.py --mutations
NARWHAL_HISTORY_CORE selects the test artifact; otherwise use the owner-supplied
/tmp history build. --mutations edits only compiled in-memory module copies.
"""
from __future__ import annotations

import ctypes
import gc
import importlib.util
import json
import os

import sys
import types
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "narwhal3d" / "native_history.py"
SPEC = importlib.util.spec_from_file_location("narwhal3d.native_history", SOURCE)
assert SPEC is not None and SPEC.loader is not None
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
CORE = os.environ.get("NARWHAL_HISTORY_CORE", "/tmp/narwhal3d-history-core/target/release/libnarwhal_application_core.so")
LIBRARY = ctypes.CDLL(CORE)


class Payload:
    def __deepcopy__(self, memo):
        raise AssertionError("opaque payload was copied")

    def __eq__(self, other):
        raise AssertionError("payload equality must not be consulted")



class RealHistory(unittest.TestCase):
    def stack(self, limit=100):
        stack = h.NativeUndoStack(limit, library=LIBRARY)
        self.addCleanup(stack.close)
        return stack

    def commit(self, stack, before, name="Change"):
        stack.begin(name)
        stack.capture(before)
        self.assertTrue(stack.end())

    def fields(self, record):
        return None if record is None else (record.serial, record.name, id(record.before), id(record.after))

    def state(self, stack):
        # No escaped record ref survives this helper. Weak-only caches FAIL here.
        return (tuple((id(r), self.fields(r)) for r in stack._undo),
                tuple((id(r), self.fields(r)) for r in stack._redo),
                None if stack._open is None else (id(stack._open), self.fields(stack._open)))

    def test_reference_lifecycle(self):
        from test_rust_history_trace import TRACE
        stack = self.stack(3)
        payloads = [None, Payload(), [], {}, Payload()]

        def fields(data):
            if data is None:
                return None
            serial, name, before, after = data
            return serial, name, id(payloads[before]), id(payloads[after])

        self.assertEqual(len(TRACE), 350)
        for step, (op, arg, result, undo, redo, opened, depth, next_serial, enabled) in enumerate(TRACE):
            with self.subTest(step=step, op=op):
                if op == 'enable':
                    stack.enabled = arg
                elif op in ('undo', 'redo'):
                    self.assertEqual(self.fields(getattr(stack, op)(payloads[arg])), fields(result))
                else:
                    args = (payloads[arg],) if op == 'capture' else (arg,) if op == 'begin' else ()
                    self.assertEqual(getattr(stack, op)(*args), result)
                self.assertEqual([self.fields(r) for r in stack._undo], [fields(r) for r in undo])
                self.assertEqual([self.fields(r) for r in stack._redo], [fields(r) for r in redo])
                self.assertEqual(self.fields(stack._open), fields(opened))
                self.assertEqual(stack._depth, depth)
                self.assertEqual(stack.next_serial, next_serial)
                self.assertEqual(stack.enabled, enabled)
                self.assertEqual(stack.can_undo, bool(undo))
                self.assertEqual(stack.can_redo, bool(redo))
                self.assertEqual(stack.recording, opened is not None)
                self.assertEqual(stack.captured, opened is not None and opened[2] != 0)
                self.assertEqual(stack.current_serial, opened[0] if opened else 0)
                self.assertEqual(stack.top_serial, undo[-1][0] if undo else 0)
                self.assertEqual(stack.undo_names(), [r[1] for r in reversed(undo)])
                self.assertEqual(stack.redo_names(), [r[1] for r in reversed(redo)])
                self.assertEqual(stack.undo_name, undo[-1][1] if undo else None)
                self.assertEqual(stack.redo_name, redo[-1][1] if redo else None)

    def test_null_disabled_nested_and_noop(self):
        s = self.stack()
        initial = s.state_id
        self.assertFalse(s.capture(None))
        self.assertEqual(s.begin(None), 1)
        opened = s._open
        self.assertIs(s._open, opened)
        self.assertEqual(opened.name, "Change")
        self.assertTrue(s.capture(None))
        self.assertFalse(s.captured)
        self.assertTrue(s.capture(None))
        self.assertEqual(s.begin("nested"), 1)
        s.enabled = False
        self.assertEqual(s.begin("disabled"), 0)
        self.assertEqual(s._depth, 2)
        self.assertFalse(s.end())
        self.assertFalse(s.end())
        self.assertEqual(s.state_id, initial)
        self.assertEqual(s.next_serial, 2)
        self.assertNotIn(opened._id, s._cache)
        s.enabled = True
        self.commit(s, Payload())
        s.undo(Payload())
        before = self.state(s)
        s.begin("noop")
        s.end()
        self.assertEqual(self.state(s), before)
        s.begin("abandon")
        self.assertEqual(s._open.name, "abandon")
        s.abandon()
        self.assertEqual(self.state(s), before)

    def test_stable_record_identity(self):
        s = self.stack()
        for _ in range(5):
            self.commit(s, Payload())
        first = self.state(s)
        gc.collect()
        self.assertEqual(self.state(s), first)
        s.begin("open")
        first = self.state(s)
        gc.collect()
        self.assertEqual(self.state(s), first)
        opened = s._open
        self.assertIs(s._open, opened)
        s.capture(Payload())
        s.end()
        self.assertIs(s._undo[-1], opened)
        self.assertIs(s.undo(Payload()), opened)
        self.assertIs(s._redo[-1], opened)
        self.assertIs(s.redo(Payload()), opened)

    def test_record_metadata_writable_and_readonly_views(self):
        s = self.stack()
        s.begin("publication")
        r = s._open
        before, after = Payload(), Payload()
        r.before = before
        self.assertTrue(s.captured)
        self.assertIs(r.before, before)
        r.before = None
        self.assertFalse(s.captured)
        r.name = "failed publication"
        r.before = before
        r.after = after
        self.assertTrue(s.end())
        self.assertEqual(r.name, "failed publication")
        self.assertIs(r.after, after)
        self.assertEqual(s._undo, [r])
        self.assertEqual([r], s._undo)
        self.assertEqual(s._undo[:], [r])
        self.assertEqual(list(s._undo), [r])
        with self.assertRaises(TypeError):
            s._undo[0] = r
        for obj, field in [(r, "serial"), (s, "_depth"), (s, "_limit"), (s, "_next_serial"),
                           (s, "_undo"), (s, "_redo"), (s, "_open"), (s, "backend")]:
            with self.assertRaises(AttributeError):
                setattr(obj, field, 0)
        with self.assertRaises(IndexError):
            _ = s._undo[1]

    def test_state_identity_and_generation(self):
        s = self.stack(1)
        state0, gen0 = s.state_id, s.generation
        s.begin("no change")
        self.assertGreater(s.generation, gen0)
        s.end()
        self.assertEqual(s.state_id, state0)
        self.commit(s, Payload())
        state1 = s.state_id
        self.commit(s, Payload())
        state2 = s.state_id
        self.assertEqual(len({state0, state1, state2}), 3)
        gen = s.generation
        r = s._undo[0]
        self.assertEqual(s.generation, gen)  # reads/leases are not edits
        s.undo(Payload())
        self.assertEqual(s.state_id, state1)  # preceding record already pruned
        s.redo(Payload())
        self.assertEqual(s.state_id, state2)
        s.clear()
        self.assertEqual(s.state_id, state2)
        self.assertIsNotNone(r.before)

    def test_navigation_failure_preserves_every_field_and_id(self):
        s = self.stack()
        self.commit(s, Payload(), "A")
        self.commit(s, Payload(), "B")
        error = ValueError("host failed")
        for direction in ("undo", "redo"):
            if direction == "redo":
                s.undo(Payload())
            original, state, count = self.state(s), s.state_id, len(s._arena)
            def fail(target):
                self.assertIs(target, (s._undo[-1].before if direction == "undo" else s._redo[-1].after))
                raise error
            with self.assertRaises(ValueError) as caught:
                getattr(s, direction)(Payload(), restore=fail)
            self.assertIs(caught.exception, error)
            self.assertEqual(self.state(s), original)
            self.assertEqual(s.state_id, state)
            self.assertFalse(s.uncertain)  # standalone compatibility contract
            self.assertEqual(len(s._arena), count)
            seen = []
            r = getattr(s, direction)(Payload(), restore=seen.append)
            self.assertIs(seen[0], r.before if direction == "undo" else r.after)

    def test_callback_observes_uncommitted_history(self):
        s = self.stack()
        self.commit(s, Payload())
        record = s._undo[-1]
        for direction in ("undo", "redo"):
            before, state_id = self.state(s), s.state_id
            current = Payload()
            target = record.before if direction == "undo" else record.after
            def restore(value):
                self.assertIs(value, target)
                self.assertEqual(self.state(s), before)
                self.assertEqual(s.state_id, state_id)
            self.assertIs(getattr(s, direction)(current, restore=restore), record)
            self.assertIs(record.after if direction == "undo" else record.before, current)

    def test_failed_restore_classifier_preserves_original_exception(self):
        s = self.stack()
        self.commit(s, Payload())
        error = ValueError("original restore error")
        def fail(_):
            raise error
        def broken_classifier(_):
            raise RuntimeError("certification unavailable")
        for classifier in (lambda _: "unchanged", broken_classifier):
            with self.assertRaises(ValueError) as caught:
                s.undo(Payload(), restore=fail, failure_state=classifier)
            self.assertIs(caught.exception, error)
            self.assertEqual(s.uncertain, classifier is broken_classifier)

    def test_unknown_restore_and_recovery(self):
        s = self.stack()
        self.commit(s, Payload())
        self.commit(s, Payload())
        s.undo(Payload())
        self.assertTrue(s.can_undo)
        self.assertTrue(s.can_redo)
        old_state, old = s.state_id, self.state(s)
        error = RuntimeError("partial host change")
        def fail(_):
            raise error
        seen = []
        def classify(exc):
            seen.append(exc)
            return "unknown"
        with self.assertRaises(RuntimeError) as caught:
            s.undo(Payload(), restore=fail, failure_state=classify)
        self.assertIs(caught.exception, error)
        self.assertEqual(seen, [error])
        self.assertTrue(s.uncertain)
        self.assertFalse(s.can_undo)
        self.assertFalse(s.can_redo)
        self.assertEqual((s._info()["undo_count"], s._info()["redo_count"]), (1, 1))
        self.assertTrue(s.uncertain or s.state_id != old_state)
        self.assertEqual(s.state_id, old_state)
        self.assertEqual(self.state(s), old)
        for action in (lambda: s.begin("blocked"), lambda: s.undo(None), lambda: s.capture(None)):
            with self.assertRaisesRegex(h.HistoryError, "history_uncertain"):
                action()
        s.clear()
        self.assertFalse(s.uncertain)
        self.assertNotEqual(s.state_id, old_state)
        self.assertFalse(s.can_undo)
        self.assertFalse(s.can_redo)
        self.assertTrue(s.uncertain or s.state_id != old_state)
        self.commit(s, Payload())
        self.assertTrue(s.can_undo)
        s.undo(Payload())
        self.assertTrue(s.can_redo)
        self.assertFalse(s.uncertain)

    def test_reentrant_mutations_native_refusal(self):
        s = self.stack()
        self.commit(s, Payload())
        r = s._undo[0]
        before = self.state(s)
        actions = [lambda: s.begin("bad"), lambda: s.capture(Payload()), s.end,
                   s.abandon, s.clear, lambda: setattr(s, "enabled", False),
                   lambda: setattr(r, "before", Payload()), lambda: setattr(r, "after", None),
                   lambda: setattr(r, "name", "bad"), lambda: s.undo(Payload()),
                   lambda: s.redo(Payload())]
        def restore(_):
            for action in actions:
                with self.assertRaisesRegex(h.HistoryError, "navigation_busy"):
                    action()
                self.assertEqual(self.state(s), before)
        self.assertIs(s.undo(Payload(), restore=restore), r)
        self.assertEqual(len(s._arena), 2)

    def test_identity_arena_and_refused_attach_cleanup(self):
        s = self.stack()
        value = Payload()
        baseline = sys.getrefcount(value)
        self.assertFalse(s.capture(value))
        self.assertEqual(sys.getrefcount(value), baseline)
        self.assertEqual(len(s._arena), 0)
        self.commit(s, value)
        self.commit(s, value)
        self.assertEqual(len(s._arena), 1)
        self.assertEqual(sys.getrefcount(value), baseline + 1)
        self.assertIs(s._undo[0].before, value)
        s.undo(value)
        self.assertEqual(len(s._arena), 1)
        self.assertIs(s._redo[-1].after, value)
        s.begin("open")
        self.assertIsNone(s.undo(Payload()))
        s.capture(value)
        self.assertFalse(s.capture(Payload()))
        self.assertEqual(len(s._arena), 1)
        s.abandon()
        s.clear()
        self.assertEqual(sys.getrefcount(value), baseline)
        self.assertEqual(len(s._arena), 0)

    def test_gc_ack_batches_and_no_huge_allocations(self):
        s = self.stack(300)
        refs = []
        for _ in range(260):
            value = Payload()
            refs.append(weakref.ref(value))
            self.commit(s, value)
            del value
        self.assertEqual(len(s._arena), 260)
        original = s._request
        sizes = []
        def request(op, **fields):
            if op == "ack_garbage":
                ids = fields["payloads"]
                self.assertTrue(all(i in s._arena for i in ids))
                sizes.append(len(ids))
            return original(op, **fields)
        with patch.object(s, "_request", request):
            s.clear()
        self.assertEqual(sizes, [128, 128, 4])
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertEqual(s._info()["payload_count"], 0)

    def test_pruned_and_escaped_record_lifetime(self):
        s = self.stack(1)
        value = Payload()
        ref = weakref.ref(value)
        self.commit(s, value, "leased")
        escaped = s._undo[0]
        del value
        self.commit(s, Payload())
        self.assertNotIn(escaped._id, s._cache)
        self.assertEqual(escaped.name, "leased")
        self.assertIs(escaped.before, ref())
        self.assertIsNotNone(ref())
        s.clear()
        self.assertIsNotNone(ref())
        del escaped
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(s._info()["record_count"], 0)
        self.assertEqual(len(s._arena), 0)

    def test_branch_prunes_strong_cache(self):
        s = self.stack(2)
        refs = []
        for _ in range(5):
            self.commit(s, Payload())
            refs.append(weakref.ref(s._undo[-1]))
        self.assertTrue(all(ref() is None for ref in refs[:3]))
        s.undo(Payload())
        abandoned = refs[-1]
        self.commit(s, Payload())
        self.assertIsNone(abandoned())
        # Materialize the new record: untouched native records need no wrapper.
        self.assertIsNotNone(s._undo[-1])
        self.assertEqual(set(s._cache), set(s._record_ids("undo")))
        s.clear()
        self.assertEqual(s._cache, {})

    def test_stack_record_cycle_is_collectible(self):
        # Do not use self.stack: unittest cleanup would hold a strong reference.
        s = h.NativeUndoStack(2, library=LIBRARY)
        handle = s._handle
        self.commit(s, Payload())
        payload_ref = weakref.ref(s._undo[0].before)
        record_ref, stack_ref = weakref.ref(s._undo[0]), weakref.ref(s)
        del s
        gc.collect()
        self.assertIsNone(stack_ref())
        self.assertIsNone(record_ref())
        self.assertIsNone(payload_ref())
        with self.assertRaisesRegex(h.HistoryError, "invalid_history"):
            h._call(LIBRARY, {"op": "info", "history": handle})

    def test_escaped_record_keeps_stack_alive(self):
        s = h.NativeUndoStack(1, library=LIBRARY)
        self.commit(s, Payload())
        r = s._undo[0]
        ref = weakref.ref(s)
        s.clear()
        del s
        gc.collect()
        self.assertIsNotNone(ref())
        self.assertIsNotNone(r.before)
        del r
        gc.collect()
        self.assertIsNone(ref())

    def test_cross_history_and_close(self):
        a, b = self.stack(), self.stack()
        self.commit(a, Payload())
        self.commit(b, Payload())
        foreign_record = a._undo[0]
        foreign_payload = next(iter(a._arena))
        before = self.state(b)
        for op, fields in [("record", {"record": foreign_record._id}),
                           ("set_before", {"record": b._undo[0]._id, "payload": foreign_payload}),
                           ("release_record", {"record": foreign_record._id}),
                           ("commit_nav", {"ticket": a._handle})]:
            with self.assertRaises(h.HistoryError):
                b._request(op, **fields)
        self.assertEqual(self.state(b), before)
        a.close()
        a.close()
        with self.assertRaisesRegex(h.HistoryError, "history_closed"):
            _ = foreign_record.before
        self.assertEqual(len(a._arena), 0)

    def traced_stack(self, limit=100):
        core = TracedCore()
        stack = h.NativeUndoStack(limit, library=core)
        self.addCleanup(stack.close)
        return stack, core

    def test_cached_hot_properties_and_mutation_crossings(self):
        s, core = self.traced_stack()
        original = s._info()
        self.assertEqual(core.ops, ["create"])
        with self.assertRaises(TypeError):
            original["captured"] = True
        core.reset()
        s.begin("hot")
        self.assertEqual(s.current_serial, 1)
        self.assertTrue(s.recording)
        self.assertFalse(original["captured"])
        self.assertIsNone(original["open_record"])
        s.capture(Payload())
        self.assertTrue(s.captured)
        captured = s._info()
        for _ in range(200):
            self.assertIs(s._info(), captured)
            self.assertEqual((s.current_serial, s.captured, s._depth, s.next_serial,
                              s._next_serial, s.top_serial, s.enabled, s.recording,
                              s.can_undo, s.can_redo, s.uncertain, len(s._undo), len(s._redo)),
                             (1, True, 1, 2, 2, 0, True, True, False, False, False, 0, 0))
            self.assertEqual(s.state_id, captured["state_id"])
            self.assertEqual(s.generation, captured["generation"])
        s.end()
        # No wrapper was requested: pruning and zero-pending GC require no RPC.
        self.assertEqual(core.ops, ["begin", "register_payload", "capture", "end"])
        self.assertFalse(s.captured)
        self.assertEqual(s.top_serial, 1)
        self.assertEqual(captured["depth"], 1)
        self.assertEqual(s._info()["depth"], 0)
        core.reset()
        s._drain()
        s._prune_cache()
        self.assertEqual(core.ops, [])
        s.clear()
        self.assertEqual(core.ops, ["clear", "garbage", "ack_garbage"])
        self.assertEqual(s._info()["gc_pending"], 0)
        self.assertEqual(s._info()["payload_count"], 0)
        self.assertEqual(s._arena, {})

    def test_repeated_capture_shortcut_and_writable_before_reset(self):
        s, core = self.traced_stack()
        s.begin("capture once")
        record = s._open
        first = Payload()
        self.assertTrue(s.capture(first))
        header = s._info()
        payload_ids = set(s._arena)
        core.reset()
        for _ in range(128):
            ignored = Payload()
            ignored_ref = weakref.ref(ignored)
            self.assertFalse(s.capture(ignored))
            del ignored
            self.assertIsNone(ignored_ref())
        self.assertEqual(core.ops, [])
        self.assertIs(s._info(), header)
        self.assertEqual(set(s._arena), payload_ids)
        self.assertEqual(s._info()["payload_count"], 1)
        self.assertEqual(len(s._identity), 1)
        record.before = None
        self.assertFalse(s.captured)
        self.assertIs(s._open, record)
        self.assertNotIn(id(first), s._identity)
        core.reset()
        second = Payload()
        self.assertTrue(s.capture(second))
        self.assertEqual(core.ops, ["register_payload", "capture"])
        self.assertIs(record.before, second)
        self.assertEqual(s._info()["payload_count"], 1)
        s.end()
        core.reset()
        self.assertFalse(s.capture(None))
        self.assertEqual(core.ops, ["capture"])  # No-open must still reach Rust.
        def restore(_):
            core.reset()
            with self.assertRaisesRegex(h.HistoryError, "navigation_busy"):
                s.capture(None)
            self.assertEqual(core.ops, ["capture"])
        s.undo(None, restore=restore)

    def test_batch_names_escaped_resize_without_payload_copies(self):
        s, core = self.traced_stack()
        escaped = '\x00\n"\\é' * 512
        names = [prefix + escaped for prefix in ("old:", "same:", "same:", "new:")]
        payload = Payload()
        for name in names:
            self.commit(s, payload, name)
        self.assertEqual(s._cache, {})
        def query(method, expected):
            header = dict(s._info())
            payload_ids = set(s._arena)
            wrapper_ids = set(s._cache)
            references = sys.getrefcount(payload)
            sizes = []
            original = core.narwhal_history_json
            def counted(source, length, output, capacity):
                needed = original(source, length, output, capacity)
                sizes.append((capacity, needed))
                return needed
            core.reset()
            with patch.object(core, "narwhal_history_json", Function(counted)):
                result = method()
            self.assertEqual(result, expected)
            self.assertEqual(core.ops, ["names", "names"])
            self.assertEqual(len(sizes), 2)
            needed = sizes[0][1]
            self.assertGreater(needed, 4096)
            self.assertLess(needed, 128 * 1024)  # Small real buffer despite JSON escaping.
            self.assertEqual(sizes, [(4096, needed), (needed, needed)])
            self.assertEqual(core.projected_ids, 0)
            self.assertEqual(s._info(), header)
            self.assertEqual(set(s._arena), payload_ids)
            self.assertEqual(set(s._cache), wrapper_ids)
            self.assertEqual(sys.getrefcount(payload), references)
            self.assertIs(s._arena[next(iter(payload_ids))], payload)
        query(s.undo_names, list(reversed(names)))
        s.undo(payload)
        s.undo(payload)
        query(s.undo_names, list(reversed(names[:2])))
        query(s.redo_names, names[2:])

    def test_every_reply_publishes_immutable_header(self):
        s, core = self.traced_stack()
        def request(op, **fields):
            old = s._info()
            old_values = dict(old)
            reply = s._request(op, **fields)
            snapshot = s._info()
            self.assertIsNot(snapshot, old)
            self.assertEqual(old, old_values)
            self.assertEqual(snapshot, reply["info"])
            actual = h._call(LIBRARY, {"op": "info", "history": s._handle})["info"]
            self.assertEqual(snapshot, actual)
            with self.assertRaises(TypeError):
                snapshot["generation"] = 0
            # A caller can edit its returned wire reply, but never the cached view.
            reply["info"]["generation"] = -1
            self.assertEqual(s._info(), actual)
            return reply["result"]
        # Exercise every successful reply shape, including same-generation
        # lifetime changes. Native IDs only in this isolated wire-level test.
        payload = request("register_payload")["payload"]
        unused = request("register_payload")["payload"]
        request("begin", name="header")
        record = s._info()["open_record"]
        request("capture", payload=payload)
        request("retain_record", record=record)
        request("record", record=record)
        request("records", side="undo")
        request("set_name", record=record, name="renamed")
        request("set_before", record=record, payload=None)
        request("set_before", record=record, payload=payload)
        request("set_after", record=record, payload=payload)
        request("end")
        nav = request("prepare_nav", direction="undo", current=payload)
        request("abort_nav", ticket=nav["ticket"], unknown=False)
        nav = request("prepare_nav", direction="undo", current=payload)
        request("commit_nav", ticket=nav["ticket"])
        nav = request("prepare_nav", direction="redo", current=None)
        request("commit_nav", ticket=nav["ticket"])
        request("release_record", record=record)
        request("clear")
        garbage = request("garbage", limit=128)["payloads"]
        self.assertEqual(garbage, [payload])
        request("ack_garbage", payloads=garbage)
        request("drop_payload", payload=unused)
        request("enable", value=False)
        request("abandon")
        core.reset()
        request("info")  # Explicit diagnostic refresh, never implicit in _info.
        self.assertEqual(core.ops, ["info"])
        old = s._info()
        s.close()
        self.assertIsNone(s._header)
        self.assertFalse(old["enabled"])
        with self.assertRaisesRegex(h.HistoryError, "history_closed"):
            s._info()

    def test_header_survives_refusal_but_not_protocol_failure(self):
        s, core = self.traced_stack()
        before = s._info()
        with self.assertRaises(h.HistoryError):
            s._request("capture", payload=0)
        self.assertIs(s._info(), before)
        s.begin("still usable")
        self.assertEqual(s.current_serial, 1)
        core.corrupt = "end"
        with self.assertRaises(h.HistoryProtocolError):
            s.end()
        core.reset()
        for read in (s._info, lambda: s.current_serial, lambda: s.captured,
                     lambda: s.generation, lambda: s._request("info")):
            with self.assertRaisesRegex(h.HistoryProtocolError, "history_protocol_failed"):
                read()
        self.assertEqual(core.ops, [])
        s.close()
        core.reset()
        with self.assertRaisesRegex(h.HistoryError, "history_closed"):
            s._info()
        self.assertEqual(core.ops, [])

    def test_reverse_iteration_crossings_and_snapshot(self):
        s, core = self.traced_stack()
        for i in range(12):
            self.commit(s, Payload(), str(i))
        core.reset()
        cold = list(reversed(s._undo))
        self.assertEqual(core.ops, ["records"] + ["retain_record"] * 12)
        self.assertEqual(core.projected_ids, 12)
        core.reset()
        self.assertEqual(list(reversed(s._undo)), cold)
        self.assertEqual(core.ops, ["records"])
        self.assertEqual(core.projected_ids, 12)
        core.reset()
        self.assertEqual(s.undo_names(), [str(i) for i in reversed(range(12))])
        self.assertEqual(core.ops, ["names"])
        self.assertEqual(core.projected_ids, 0)
        for _ in range(3):
            s.undo(Payload())
        core.reset()
        self.assertEqual(s.redo_names(), ["9", "10", "11"])
        self.assertEqual(core.ops, ["names"])
        self.assertEqual(core.projected_ids, 0)
        snapshot = reversed(s._redo)
        s.clear()
        self.assertEqual([r.name for r in snapshot], ["9", "10", "11"])

    def test_committed_navigation_bad_reply_cannot_certify_abort(self):
        for direction in ("undo", "redo"):
            s, core = self.traced_stack()
            self.commit(s, Payload())
            self.commit(s, Payload())
            s.undo(Payload())
            self.assertTrue(s.can_undo)
            self.assertTrue(s.can_redo)
            saved_state = s.state_id
            restored, classified = [], []
            core.reset()
            core.corrupt = "commit_nav"
            with self.assertRaisesRegex(h.HistoryProtocolError, "invalid_reply"):
                getattr(s, direction)(Payload(), restore=restored.append,
                                     failure_state=lambda exc: classified.append(exc) or "unchanged")
            self.assertEqual(len(restored), 1)
            self.assertEqual(classified, [])
            self.assertEqual(core.ops.count("commit_nav"), 1)
            self.assertNotIn("abort_nav", core.ops)
            actual = h._call(LIBRARY, {"op": "info", "history": s._handle})["info"]
            expected_counts = (0, 2) if direction == "undo" else (2, 0)
            self.assertEqual((actual["undo_count"], actual["redo_count"]), expected_counts)
            core.reset()
            self.assertTrue(s.uncertain)
            self.assertFalse(s.can_undo)
            self.assertFalse(s.can_redo)
            # AppState's dirty check must short-circuit before the forbidden ID read.
            self.assertTrue(s.uncertain or s.state_id != saved_state)
            for read in (s._info, lambda: s.state_id, lambda: s.current_serial):
                with self.assertRaisesRegex(h.HistoryProtocolError, "history_protocol_failed"):
                    read()
            for mutate in (s.clear, lambda: s.begin("blocked"), lambda: s.capture(None),
                           lambda: s.undo(None), lambda: s.redo(None)):
                with self.assertRaisesRegex(h.HistoryProtocolError, "history_protocol_failed"):
                    mutate()
            self.assertEqual(core.ops, [])

    def test_closed_safety_flags_are_conservative_without_ffi(self):
        s, core = self.traced_stack()
        self.commit(s, Payload())
        s.undo(Payload())
        self.assertTrue(s.can_redo)
        self.assertFalse(s.uncertain)
        saved_state = s.state_id
        s.close()
        core.reset()
        self.assertTrue(s.uncertain)
        self.assertFalse(s.can_undo)
        self.assertFalse(s.can_redo)
        self.assertTrue(s.uncertain or s.state_id != saved_state)
        for read in (s._info, lambda: s.state_id, lambda: s.current_serial):
            with self.assertRaisesRegex(h.HistoryError, "history_closed"):
                read()
        for mutate in (s.clear, lambda: s.begin("blocked"), lambda: s.capture(None),
                       lambda: s.undo(None), lambda: s.redo(None)):
            with self.assertRaisesRegex(h.HistoryError, "history_closed"):
                mutate()
        s.close()  # Idempotent cleanup is the sole permitted closed operation.
        self.assertEqual(core.ops, [])

    def assert_broken_dirty(self, s, saved_state):
        self.assertTrue(s._broken)
        self.assertTrue(s.uncertain or s.state_id != saved_state)
        self.assertFalse(s.can_undo)
        self.assertFalse(s.can_redo)
        for read in (s._info, lambda: s.state_id, lambda: s.current_serial):
            with self.assertRaisesRegex(h.HistoryProtocolError, "history_protocol_failed"):
                read()
        with self.assertRaises(h.HistoryProtocolError):
            s.begin("must not continue")

    def test_post_call_decode_and_header_failures_break_cache(self):
        for op in ("end", "commit_nav"):
            for phase in ("decode", "header", "header_typed_error"):
                s, core = self.traced_stack()
                s.begin("fault")
                s.capture(Payload())
                if op == "commit_nav":
                    s.end()
                saved_state = s.state_id
                error = (h.HistoryError("publication_failure") if phase == "header_typed_error"
                         else MemoryError("injected one-shot allocation failure"))
                armed = True
                original_loads, original_proxy = json.loads, h.MappingProxyType
                def fail_decode(data, **kwargs):
                    nonlocal armed
                    reply = original_loads(data, **kwargs)
                    if armed and reply.get("status") == "ok" and core.ops[-1] == op:
                        armed = False
                        raise error
                    return reply
                def fail_header(value):
                    nonlocal armed
                    if armed and core.ops[-1] == op:
                        armed = False
                        raise error
                    return original_proxy(value)
                target, name, injected = ((h.json, "loads", fail_decode) if phase == "decode"
                                          else (h, "MappingProxyType", fail_header))
                core.reset()
                classified = []
                with patch.object(target, name, injected):
                    with self.assertRaises(type(error)) as caught:
                        if op == "end":
                            s.end()
                        else:
                            s.undo(Payload(), restore=lambda _: None,
                                   failure_state=lambda exc: classified.append(exc) or "unchanged")
                self.assertIs(caught.exception, error)
                self.assertFalse(armed)
                self.assertEqual(core.ops.count(op), 1)
                self.assertNotIn("abort_nav", core.ops)
                self.assertEqual(classified, [])
                actual = h._call(LIBRARY, {"op": "info", "history": s._handle})["info"]
                self.assertEqual((actual["undo_count"], actual["redo_count"]),
                                 (1, 0) if op == "end" else (0, 1))
                core.reset()
                self.assert_broken_dirty(s, saved_state)
                self.assertEqual(core.ops, [])

    def test_fatal_wire_errors_invalidate_but_admission_remains_retryable(self):
        for code in ("panic", "registry_poisoned", "invalid_history"):
            s, core = self.traced_stack()
            s.begin("fatal")
            s.capture(Payload())
            saved_state = s.state_id
            core.wire_errors["end"] = code
            core.reset()
            with self.assertRaises(h.HistoryError) as caught:
                s.end()
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(core.ops, ["end"])
            self.assert_broken_dirty(s, saved_state)
        s, core = self.traced_stack()
        initial = s._info()
        with self.assertRaisesRegex(h.HistoryError, "name_limit"):
            s.begin("x" * 16385)
        self.assertIs(s._info(), initial)
        self.assertFalse(s.uncertain)
        self.commit(s, Payload())
        self.assertTrue(s.can_undo)

    def test_live_handle_lost_in_native_registry_breaks_cache(self):
        s = h.NativeUndoStack(library=LIBRARY)
        saved_state = s.state_id
        s._finalizer()  # Simulate external native close; no live resource remains.
        with self.assertRaisesRegex(h.HistoryError, "invalid_history"):
            s.begin("lost")
        self.assert_broken_dirty(s, saved_state)

    def test_registration_insert_failure_rolls_back_both_indexes(self):
        for index in ("_arena", "_identity"):
            for after_insert in (False, True):
                s, core = self.traced_stack()
                s.begin("allocation")
                error = MemoryError("one-shot dict insertion")
                failing = InsertFailureDict(error, after_insert)
                setattr(s, index, failing)
                value = Payload()
                ref = weakref.ref(value)
                core.reset()
                with self.assertRaises(MemoryError) as caught:
                    s.capture(value)
                self.assertIs(caught.exception, error)
                self.assertEqual(s._arena, {})
                self.assertEqual(s._identity, {})
                self.assertEqual(core.ops, ["register_payload", "drop_payload"])
                actual = h._call(LIBRARY, {"op": "info", "history": s._handle})["info"]
                self.assertEqual(actual["payload_count"], 0)
                self.assertEqual(s._info(), actual)
                self.assertFalse(s.uncertain)
                self.assertFalse(s.captured)
                self.assertTrue(s.capture(value))  # Rolled back, not silently poisoned.
                self.assertEqual(len(s._arena), 1)
                s.abandon()
                del value
                gc.collect()
                self.assertIsNone(ref())
                self.assertEqual(s._arena, {})
                self.assertEqual(s._identity, {})

    def test_failed_registration_rollback_preserves_error_and_breaks_cache(self):
        s, core = self.traced_stack()
        s.begin("rollback")
        saved_state = s.state_id
        error = MemoryError("original insertion failure")
        s._identity = InsertFailureDict(error, after_insert=True)
        core.wire_errors["drop_payload"] = "allocation_failed"
        core.reset()
        with self.assertRaises(MemoryError) as caught:
            s.capture(Payload())
        self.assertIs(caught.exception, error)
        self.assertEqual(s._arena, {})
        self.assertEqual(s._identity, {})
        self.assertEqual(core.ops, ["register_payload", "drop_payload"])
        self.assert_broken_dirty(s, saved_state)

    def test_gc_batch_finalizers_see_consistent_indexes_and_cannot_mutate(self):
        s, core = self.traced_stack()
        s.begin("leased record")
        record_holder = [s._open]
        s.abandon()
        observed, outcomes, callback_errors = [], [], []
        payload_ids, object_ids = set(), set()
        def on_delete(later):
            observed.append((set(s._arena) & payload_ids, set(s._identity) & object_ids))
            actions = [("begin", lambda: s.begin("reentrant")),
                       ("capture", lambda: s.capture(later)),
                       ("close", s.close), ("end", s.end), ("abandon", s.abandon),
                       ("clear", s.clear), ("undo", lambda: s.undo(later)),
                       ("redo", lambda: s.redo(later)),
                       ("enable", lambda: setattr(s, "enabled", False)),
                       ("set_name", lambda: setattr(record_holder[0], "name", "bad")),
                       ("set_before", lambda: setattr(record_holder[0], "before", later)),
                       ("set_after", lambda: setattr(record_holder[0], "after", later))]
            for name, action in actions:
                try:
                    action()
                except h.HistoryError as exc:
                    outcomes.append((name, exc.code))
                else:
                    outcomes.append((name, "incorrectly allowed"))
            # Required read/lifetime operations stay usable while destructors run.
            observed.append(record_holder[0].name)
            observed.append(s._request("info")["info"]["payload_count"])
            s._request("garbage", limit=128)
            s._request("ack_garbage", payloads=[])
            record_holder.clear()  # Real NativeRecord finalizer releases its lease.
            observed.append(s._info()["record_count"])
        later = Payload()
        first = RetiredObserver(later, on_delete, callback_errors)
        first_ref, later_ref = weakref.ref(first), weakref.ref(later)
        self.commit(s, first)
        self.commit(s, later)
        payload_ids.update(s._arena)
        object_ids.update(s._identity)
        self.assertEqual(len(payload_ids), 2)
        del first, later
        core.reset()
        clear_errors = []
        try:
            s.clear()
        except BaseException as exc:
            clear_errors.append(exc)
        self.assertEqual(clear_errors, [])
        self.assertEqual(callback_errors, [])
        self.assertEqual(observed, [(set(), set()), "leased record", 0, 0])
        self.assertEqual(len(outcomes), 12)
        self.assertTrue(all(code == "history_cleanup_busy" for _, code in outcomes), outcomes)
        self.assertNotIn("close", core.ops)
        self.assertNotIn("begin", core.ops)
        self.assertNotIn("register_payload", core.ops)
        self.assertIn("release_record", core.ops)
        self.assertIsNone(first_ref())
        self.assertIsNone(later_ref())
        self.assertEqual(s._arena, {})
        self.assertEqual(s._identity, {})
        self.assertFalse(s.uncertain)
        self.commit(s, Payload())  # Cleanup guard has been released.

    def test_large_read_bounded_retry(self):
        s = self.stack()
        name = "é" * 8192
        self.commit(s, Payload(), name)
        self.assertEqual(s._undo[0].name, name)
        self.assertEqual(s._info()["undo_count"], 1)


class InsertFailureDict(dict):
    """One-shot insertion failure, including an insertion that already took effect."""

    def __init__(self, error, after_insert):
        super().__init__()
        self.error, self.after_insert, self.armed = error, after_insert, True

    def __setitem__(self, key, value):
        if self.armed:
            self.armed = False
            if self.after_insert:
                super().__setitem__(key, value)
            raise self.error
        super().__setitem__(key, value)


class RetiredObserver:
    def __init__(self, later, callback, errors):
        self.later, self.callback, self.errors = later, callback, errors

    def __del__(self):
        try:
            self.callback(self.later)
        except BaseException as exc:
            self.errors.append(exc)


class Function:
    def __init__(self, impl):
        self.impl = impl

    def __call__(self, *args):
        return self.impl(*args)


class Fake:
    def __init__(self, response):
        self.calls = []
        self.response = response
        self.narwhal_history_abi = Function(lambda: 1)
        self.narwhal_history_json = Function(self.call)

    def call(self, source, length, output, capacity):
        request = json.loads(bytes(source[:length]))
        self.calls.append((request, capacity))
        value = self.response(request, len(self.calls))
        if isinstance(value, (dict, list)):
            value = json.dumps(value).encode()
        if isinstance(value, bytes):
            if len(value) <= capacity:
                ctypes.memmove(output, value, len(value))
            return len(value)
        return value


class TracedCore:
    """Count actual ABI crossings and returned IDs, without mirroring history."""

    def __init__(self):
        self.ops = []
        self.projected_ids = 0
        self.corrupt = None
        self.wire_errors = {}
        self.narwhal_history_abi = Function(LIBRARY.narwhal_history_abi)
        self.narwhal_history_json = Function(self.call)

    def reset(self):
        self.ops.clear()
        self.projected_ids = 0

    def call(self, source, length, output, capacity):
        op = json.loads(bytes(source[:length]))["op"]
        self.ops.append(op)
        size = LIBRARY.narwhal_history_json(source, length, output, capacity)
        if op == "records" and 0 < size <= capacity:
            reply = json.loads(bytes(output[:size]))
            if reply["status"] == "ok":
                self.projected_ids += len(reply["result"]["records"])
        if op in self.wire_errors:
            error = json.dumps({"status": "error", "code": self.wire_errors[op]}).encode()
            assert len(error) <= capacity
            ctypes.memmove(output, error, len(error))
            return len(error)
        if op == self.corrupt:
            ctypes.memmove(output, b"{}", 2)
            return 2
        return size


class Protocol(unittest.TestCase):
    def test_malformed_batch_names_fail_closed(self):
        malformed = [None, {}, {"records": []}, {"names": None}, {"names": "one"},
                     {"names": [1]}, {"names": [True]}, {"names": [None]},
                     {"names": ["\ud800"]}, {"names": [], "extra": 0},
                     {"names": [""] * 100001}]
        for result in malformed:
            s = h.NativeUndoStack(library=LIBRARY)
            self.addCleanup(s.close)
            reply = {"status": "ok", "info": dict(s._info()), "result": result}
            fake = Fake(lambda *_: reply)
            with patch.object(s, "_library", fake):
                with self.assertRaisesRegex(h.HistoryProtocolError, "invalid_reply"):
                    s._request("names", side="undo")
                self.assertTrue(s.uncertain)
                self.assertFalse(s.can_undo)
                with self.assertRaises(h.HistoryProtocolError):
                    s.state_id
            self.assertLessEqual(len(fake.calls), 2)
        # Exercise the decoded 16 MiB logical budget with shared small strings,
        # not a giant wire buffer or opaque geometry allocation.
        reply = {"status": "ok", "info": dict(h._call(LIBRARY, {
            "op": "info", "history": s._handle})["info"]),
            "result": {"names": ["x" * 16384] * 1025}}
        with self.assertRaisesRegex(h.HistoryProtocolError, "invalid_reply"):
            h._validate("names", reply)

    def test_names_sizing_bounds_without_large_allocations(self):
        oversized = Fake(lambda *_: 128 * 1024 * 1024 + 1)
        with patch.object(h.ctypes, "string_at", side_effect=AssertionError("unsafe read")):
            with self.assertRaisesRegex(h.HistoryProtocolError, "reply_too_large"):
                h._call(oversized, {"op": "names", "history": 1, "side": "undo"})
        self.assertEqual([cap for _, cap in oversized.calls], [4096])
        growing = Fake(lambda _, count: 4096 + count)
        with patch.object(h.ctypes, "string_at", side_effect=AssertionError("unsafe read")):
            with self.assertRaisesRegex(h.HistoryProtocolError, "reply_retry_limit"):
                h._call(growing, {"op": "names", "history": 1, "side": "redo"})
        self.assertEqual([cap for _, cap in growing.calls], [4096, 4097, 4098])

    def test_malformed_sizes_never_read_arbitrary_memory(self):
        for size in (-1, 0, True, None, 8 * 1024 * 1024 + 1, 1 << 63):
            lib = Fake(lambda *_: size)
            with patch.object(h.ctypes, "string_at", side_effect=AssertionError("unsafe read")):
                with self.assertRaises(h.HistoryProtocolError):
                    h._call(lib, {"op": "info", "history": 1})
            self.assertEqual(len(lib.calls), 1)
            self.assertEqual(lib.calls[0][1], 4096)

    def test_mutations_never_sizing_retry(self):
        for op in ("create", "begin", "end", "capture", "prepare_nav", "commit_nav", "abort_nav",
                   "close", "retain_record", "release_record", "register_payload", "ack_garbage"):
            lib = Fake(lambda *_: 4097)
            with patch.object(h.ctypes, "string_at", side_effect=AssertionError("unsafe read")):
                with self.assertRaisesRegex(h.HistoryProtocolError, "mutation_reply_too_large"):
                    h._call(lib, {"op": op, "history": 1})
            self.assertEqual(len(lib.calls), 1)

    def test_read_retry_limit(self):
        lib = Fake(lambda _, number: 4096 + number)
        with patch.object(h.ctypes, "string_at", side_effect=AssertionError("unsafe read")):
            with self.assertRaisesRegex(h.HistoryProtocolError, "reply_retry_limit"):
                h._call(lib, {"op": "records", "history": 1, "side": "undo"})
        self.assertEqual([cap for _, cap in lib.calls], [4096, 4097, 4098])

    def test_strict_reply_validation(self):
        for value in (b"{}", b"[]", b"not json", b"\xff", b'{"status":"ok","status":"ok"}',
                      b'{"status":"ok","result":NaN,"info":null}',
                      {"status": "ok", "result": None, "info": None},
                      {"status": "error", "code": "bad", "extra": True}):
            with self.assertRaises(h.HistoryProtocolError):
                h._call(Fake(lambda *_: value), {"op": "info", "history": 1})
        s = h.NativeUndoStack(library=LIBRARY)
        self.addCleanup(s.close)
        good = h._call(LIBRARY, {"op": "info", "history": s._handle})
        for key, value in [("enabled", 1), ("state_id", 0), ("undo_record", -1), ("generation", True)]:
            bad = {**good, "info": {**good["info"], key: value}}
            with self.assertRaises(h.HistoryProtocolError):
                h._call(Fake(lambda *_: bad), {"op": "info", "history": 1})

    def test_malformed_mutation_poison_no_fallback(self):
        s = h.NativeUndoStack(library=LIBRARY)
        self.addCleanup(s.close)
        s.begin("one")
        s.capture(Payload())
        count = []
        def call(source, length, output, capacity):
            request = json.loads(bytes(source[:length]))
            size = LIBRARY.narwhal_history_json(source, length, output, capacity)
            if request["op"] == "end":
                count.append(request)
                ctypes.memmove(output, b"{}", 2)
                return 2
            return size
        proxy = types.SimpleNamespace(narwhal_history_json=call)
        s._library = proxy
        with self.assertRaises(h.HistoryProtocolError):
            s.end()
        with self.assertRaisesRegex(h.HistoryProtocolError, "history_protocol_failed"):
            s.begin("must not retry")
        self.assertEqual(len(count), 1)
        info = h._call(LIBRARY, {"op": "info", "history": s._handle})["info"]
        self.assertEqual(info["undo_count"], 1)
        self.assertEqual(info["next_serial"], 2)

    def test_factory_discovery_and_explicit_errors(self):
        loader = types.SimpleNamespace(load_library=lambda: None)
        with patch.dict(os.environ, {}, clear=True), patch.object(h, "_session_module", return_value=loader):
            for library, code in ((None, 'history_core_not_installed'),
                                  (object(), 'history_exports_unavailable')):
                loader.load_library = lambda: library
                with patch.object(h, '_call') as call:
                    with self.assertRaisesRegex(h.HistoryError, 'NARWHAL_HISTORY_CORE') as caught:
                        h.try_create()
                    self.assertEqual(caught.exception.code, code)
                    call.assert_not_called()
            loader.load_library = lambda: types.SimpleNamespace(narwhal_history_abi=Function(lambda: 1))
            with self.assertRaises(h.HistoryError):
                h.try_create()
            bad = Fake(lambda *_: b"{}")
            bad.narwhal_history_abi = Function(lambda: 2)
            loader.load_library = lambda: bad
            with patch.object(h, '_call') as call:
                with self.assertRaisesRegex(h.HistoryError, "history_abi_must_be_1"):
                    h.try_create()
                call.assert_not_called()
            loader.load_library = lambda: Fake(lambda *_: b"{}")
            with self.assertRaises(h.HistoryProtocolError):
                h.try_create()
        for path in ("", str(ROOT / "nonexistent-history-core.so")):
            with patch.dict(os.environ, {"NARWHAL_HISTORY_CORE": path}):
                with self.assertRaises(h.HistoryError):
                    h.try_create()
        with patch.dict(os.environ, {"NARWHAL_HISTORY_CORE": CORE}), patch.object(h, "_session_module", side_effect=AssertionError("wrong discovery")):
            s = h.try_create(2)
            self.assertEqual(s.backend, "rust")
            self.assertEqual(s._limit, 2)
            s.close()
        with patch.dict(os.environ, {"NARWHAL_HISTORY_CORE": CORE}), patch.object(h.ctypes, "CDLL", return_value=object()):
            with self.assertRaises(h.HistoryError):
                h.try_create()

    def test_canonical_direct_import_no_bpy(self):
        module = h._session_module()
        self.assertIs(module, sys.modules["narwhal3d.native_document_session"])
        self.assertIs(module, h._session_module())
        self.assertEqual(Path(module.__file__).resolve(), SOURCE.with_name("native_document_session.py"))
        self.assertNotIn("bpy", sys.modules)
        self.assertNotIn("narwhal3d", sys.modules)


def run_mutations():
    """Require assertion RED, not loader/runtime errors, then restore baseline."""
    global h
    baseline, source = h, SOURCE.read_text()
    mutants = [
        ('frozen lifecycle names reversed',
         "return self._request('names', side='undo')['result']['names']",
         "return self._request('names', side='redo')['result']['names']",
         RealHistory, 'test_reference_lifecycle'),
        ('missing native history accepted',
         'raise _unavailable("history_core_not_installed")', 'return None',
         Protocol, 'test_factory_discovery_and_explicit_errors'),
        ('old core accepted without history exports',
         '        library = _configure(library)\n    return NativeUndoStack',
         '        if not hasattr(library, "narwhal_history_abi"):\n            return None\n        library = _configure(library)\n    return NativeUndoStack',
         Protocol, 'test_factory_discovery_and_explicit_errors'),
        ("captured fast path removed",
         "if not header['uncertain'] and header['captured']:", "if False:",
         RealHistory, "test_repeated_capture_shortcut_and_writable_before_reset"),
        ("batch undo names swapped to redo",
         "return self._request('names', side='undo')['result']['names']",
         "return self._request('names', side='redo')['result']['names']",
         RealHistory, "test_batch_names_escaped_resize_without_payload_copies"),
        ("batch redo names swapped to undo",
         "return self._request('names', side='redo')['result']['names']",
         "return self._request('names', side='undo')['result']['names']",
         RealHistory, "test_batch_names_escaped_resize_without_payload_copies"),
        ("batch names regresses to per-record RPCs",
         "return self._request('names', side='undo')['result']['names']",
         "return [r.name for r in reversed(self._undo)]",
         RealHistory, "test_reverse_iteration_crossings_and_snapshot"),
        ("batch names skips validation",
         'valid_result = _shape(result, {"names": _names})', 'valid_result = True',
         Protocol, "test_malformed_batch_names_fail_closed"),
        ("decode failure leaves cache trusted",
         '# Decoding/allocation can fail after Rust has already committed.\n                self._broken = True',
         '# Decoding/allocation can fail after Rust has already committed.\n                pass',
         RealHistory, "test_post_call_decode_and_header_failures_break_cache"),
        ("header publication failure leaves cache trusted",
         '# Even a valid wire reply is unusable if its header cannot publish.\n                self._broken = True',
         '# Even a valid wire reply is unusable if its header cannot publish.\n                pass',
         RealHistory, "test_post_call_decode_and_header_failures_break_cache"),
        ("fatal wire code treated as admission refusal",
         'or exc.code in _FATAL_CODES', 'or False',
         RealHistory, "test_fatal_wire_errors_invalidate_but_admission_remains_retryable"),
        ("registration rollback leaves identity index",
         'self._identity.pop(id(value), None)', 'None',
         RealHistory, "test_registration_insert_failure_rolls_back_both_indexes"),
        ("registration rollback leaves arena reference",
         'self._arena.pop(payload, None)', 'None',
         RealHistory, "test_registration_insert_failure_rolls_back_both_indexes"),
        ("registration rollback leaks native ID",
         '                    self._request("drop_payload", payload=payload)',
         '                    pass',
         RealHistory, "test_registration_insert_failure_rolls_back_both_indexes"),
        ("failed rollback leaves cache trusted",
         'if rollback_failed:\n                    self._broken = True',
         'if False:\n                    self._broken = True',
         RealHistory, "test_failed_registration_rollback_preserves_error_and_breaks_cache"),
        ("GC frees objects before all indexes are consistent",
         'retired = [self._arena[payload] for payload in ids]', 'retired = []',
         RealHistory, "test_gc_batch_finalizers_see_consistent_indexes_and_cannot_mutate"),
        ("destructors may mutate during cleanup",
         'if self._draining and op not in _CLEANUP_OPS:', 'if False:',
         RealHistory, "test_gc_batch_finalizers_see_consistent_indexes_and_cannot_mutate"),
        ("uncertain undo remains eligible",
         'return not self.uncertain and bool(self._info()["undo_count"])',
         'return bool(self._info()["undo_count"])',
         RealHistory, "test_unknown_restore_and_recovery"),
        ("uncertain redo remains eligible",
         'return not self.uncertain and bool(self._info()["redo_count"])',
         'return bool(self._info()["redo_count"])',
         RealHistory, "test_unknown_restore_and_recovery"),
        ("broken history claims known state",
         'if self._closed or self._broken:\n                return True',
         'if self._closed or self._broken:\n                return False',
         RealHistory, "test_committed_navigation_bad_reply_cannot_certify_abort"),
        ("closed history claims known state",
         'if self._closed or self._broken:\n                return True',
         'if self._closed or self._broken:\n                return not self._closed',
         RealHistory, "test_closed_safety_flags_are_conservative_without_ffi"),
        ("stale cached header",
         'else MappingProxyType(dict(reply["info"])))', 'else self._header)',
         RealHistory, "test_every_reply_publishes_immutable_header"),
        ("mutable cached header",
         'else MappingProxyType(dict(reply["info"])))', 'else dict(reply["info"]))',
         RealHistory, "test_every_reply_publishes_immutable_header"),
        ("lying zero garbage pending",
         'else MappingProxyType(dict(reply["info"])))',
         'else MappingProxyType(dict(reply["info"], gc_pending=0)))',
         RealHistory, "test_cached_hot_properties_and_mutation_crossings"),
        ("hot properties cross ABI",
         'return self._header', 'return self._request("info")["info"]',
         RealHistory, "test_cached_hot_properties_and_mutation_crossings"),
        ("empty cache still scans native timeline",
         'if not self._cache:\n            return', 'if False:\n            return',
         RealHistory, "test_cached_hot_properties_and_mutation_crossings"),
        ("quadratic reverse projection", 'def __reversed__(self):', 'def unused_reverse(self):',
         RealHistory, "test_reverse_iteration_crossings_and_snapshot"),
        ("broken header still readable",
         'if self._broken:\n                raise HistoryProtocolError("history_protocol_failed")',
         'if False:\n                raise HistoryProtocolError("history_protocol_failed")',
         RealHistory, "test_header_survives_refusal_but_not_protocol_failure"),
        ("classify malformed commit as host failure",
         'if self._broken:\n                    # Commit', 'if False:\n                    # Commit',
         RealHistory, "test_committed_navigation_bad_reply_cannot_certify_abort"),
        ("weak-only record cache", "self._cache = {}  # STRONG", "self._cache = weakref.WeakValueDictionary()  # STRONG", RealHistory, "test_stable_record_identity"),
        ("strong finalizer cycle", "_cleanup_record, weakref.ref(stack), record", "_cleanup_record, (lambda: stack), record", RealHistory, "test_stack_record_cycle_is_collectible"),
        ("omit committed cache pruning", "self._prune_cache()", "pass", RealHistory, "test_branch_prunes_strong_cache"),
        ("discard unknown restoration", "unknown = failure_state(exc) == \"unknown\"", "unknown = False", RealHistory, "test_unknown_restore_and_recovery"),
        ("omit garbage acknowledgement", "self._request(\"ack_garbage\", payloads=ids)", "return", RealHistory, "test_gc_ack_batches_and_no_huge_allocations"),
        ("null capture falsely refused", "return result[\"captured\"]", "return False if snapshot is None else result[\"captured\"]", RealHistory, "test_null_disabled_nested_and_noop"),
        ("retry mutation", "if op not in _READ_OPS:", "if False:", Protocol, "test_mutations_never_sizing_retry"),
        ("unbounded read retries", "range(_MAX_ATTEMPTS)", "range(_MAX_ATTEMPTS + 1)", Protocol, "test_read_retry_limit"),
        ("wrong payload identity reuse", "payload = self._identity.get(id(value))", "payload = None", RealHistory, "test_identity_arena_and_refused_attach_cleanup"),
        ("skip restore callback", "restore(target)", "pass", RealHistory, "test_navigation_failure_preserves_every_field_and_id"),
        ("commit before host restoration",
         'if restore is not None:\n                    restore(target)\n                self._request("commit_nav", ticket=nav["ticket"])',
         'self._request("commit_nav", ticket=nav["ticket"])\n                if restore is not None:\n                    restore(target)',
         RealHistory, "test_callback_observes_uncommitted_history"),
        ("name setter ignored", "self._stack._request(\"set_name\", record=self._id, name=value)", "pass", RealHistory, "test_record_metadata_writable_and_readonly_views"),
    ]
    try:
        for label, old, new, case, name in mutants:
            if source.count(old) != 1:
                raise AssertionError(f"mutation anchor not unique: {label}")
            module = types.ModuleType("_history_mutant")
            module.__file__ = str(SOURCE)
            exec(compile(source.replace(old, new), str(SOURCE), "exec"), module.__dict__)
            h = module
            result = unittest.TestResult()
            case(name).run(result)
            if not result.failures or result.errors:
                raise AssertionError(f"mutant {label}: expected assertion RED, got failures={result.failures}, errors={result.errors}")
            print(f"RED observed: {label}")
            # Force finalizer callbacks for the deliberate strong-cycle mutant;
            # baseline uses weak arguments and needs no such intervention.
            if label == "strong finalizer cycle":
                for finalizer in list(weakref.finalize._registry):  # pyright: ignore[reportAttributeAccessIssue]
                    peek = finalizer.peek()
                    if peek and getattr(peek[1], "__module__", None) == module.__name__:
                        finalizer()
            gc.collect()
    finally:
        h = baseline


def suite():
    return unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c)
                              for c in (RealHistory, Protocol))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(suite())
    if not result.wasSuccessful():
        sys.exit(1)
    if "--mutations" in sys.argv:
        run_mutations()
        print("Restored baseline:")
        result = unittest.TextTestRunner(verbosity=1).run(suite())
        sys.exit(not result.wasSuccessful())
