"""Persistent transcript focused controls; run with the additive development library.

Uses real bindings/owners and an independent list/reference class. No bpy or GUI.
"""
import copy
import ctypes as C
import gc
import importlib.util
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

ROOT = Path(os.environ.get('UI_TRANSCRIPT_SOURCE', Path(__file__).resolve().parents[1]))
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'narwhal3d' / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
ch = load('transcript_history_tests', 'command_history.py')
module = sys.modules['_narwhal_command_transcript']
native = module.native
Transcript = module.Transcript


def stats(lib):
    out = (C.c_uint64 * 4)()
    assert lib.narwhal_transcript_stats(out, 4) == 0
    return tuple(out)


class TranscriptTests(unittest.TestCase):
    def field(self, **kw):
        f = Transcript(**kw)
        self.addCleanup(f.close)
        self.assertIsNotNone(f._native)
        return f

    def test_policies_and_sustained_ring(self):
        raw = self.field()
        raw.append_many(['> same\r\n', '', '> same', 'a\nb'] + [str(i) for i in range(600)])
        self.assertEqual(len(raw), 604)
        self.assertEqual(raw[:4], ['> same\r\n', '', '> same', 'a\nb'])
        del raw[:len(raw)-200]
        self.assertEqual(raw[:], [str(i) for i in range(400, 600)])
        h = ch.CommandHistory(3)
        self.addCleanup(h.close)
        handle = h._lines._native.reply.handle
        for i in range(600):
            h.append(str(i)+'\n\r\n')
        self.assertEqual(h.lines(), ['597', '598', '599'])
        self.assertEqual(h._lines._native.reply.handle, handle)
        h.clear()
        h.append('next')
        self.assertEqual(h._lines._native.reply.handle, handle)
        self.assertEqual(h.lines(), ['next'])

    def test_seeded_list_equivalence(self):
        rng = random.Random(707)
        f = self.field()
        ref = []
        for _ in range(3000):
            op = rng.randrange(5)
            if op == 0:
                line = rng.choice(['', 'x\n\n', '> Line', '\ud800😀\x00', 'ΑΣ İ e\u0301'])
                f.append(line); ref.append(line)
            elif op == 1:
                lines = [str(rng.randrange(99)) for _ in range(rng.randrange(8))]
                f.append_many(iter(lines)); ref.extend(lines)
            elif op == 2:
                n = rng.randrange(-20, 20)
                del f[:n]; del ref[:n]
            elif op == 3:
                sl = slice(rng.randrange(-30, 30), rng.randrange(-30, 30), rng.choice([-5, -1, 1, 3]))
                self.assertEqual(f[sl], ref[sl])
            else:
                f.clear(); ref.clear()
            self.assertEqual(f[:], ref)
            self.assertEqual(len(f), len(ref))
            self.assertEqual(bool(f), bool(ref))

    def test_indices_and_detached_snapshots(self):
        f = self.field()
        f.append_many(['a', 'b', 'c'])
        self.assertEqual(f[-1], 'c')
        self.assertEqual(f[True], 'b')
        self.assertEqual(f[::-1], ['c', 'b', 'a'])
        self.assertEqual(f[::10**100], ['a'])
        self.assertEqual(f[::-10**100], ['c'])
        self.assertEqual(f[-10**100:10**100], ['a', 'b', 'c'])
        with self.assertRaises(IndexError): _ = f[3]
        with self.assertRaises(ValueError): _ = f[::0]
        with self.assertRaises(TypeError): _ = f[1.5]
        with self.assertRaises(NotImplementedError): f[0] = 'x'
        with self.assertRaises(NotImplementedError): del f[1:]
        view, iterator = f[:], iter(f)
        f.clear()
        view[0] = 'changed'
        self.assertEqual(list(iterator), ['a', 'b', 'c'])
        self.assertEqual(f[:], [])
        self.assertEqual(f.tail(0), [])

    def test_unicode_lower_not_casefold(self):
        f = self.field()
        lines = ['ΑΣ', 'ΣΑ', '1\u0345Σ', 'A\u0345Σ', 'İ', 'ẞ', 'e\u0301😀', '\ud800A', '> Box']
        f.append_many(lines)
        for needle in ['ας', 'ασ', 'σα', 'i\u0307', 'ß', 'ss', '\ud800a', '😀', 'Box']:
            self.assertEqual(f.search(needle), [i for i, line in enumerate(lines) if needle.lower() in line.lower()])
        self.assertEqual(f.last_command(), 'Box')
        f.append('> ')
        self.assertEqual(f.last_command(), '')
        f.clear()
        self.assertIsNone(f.last_command())

    def test_full_unicode_lower_domain(self):
        f = self.field()
        for start in range(0, 0x110000, 8192):
            text = ''.join(map(chr, range(start, min(start+8192, 0x110000))))
            f.clear(); f.append(text)
            self.assertEqual(f[0], text)
            self.assertEqual(f.search(text.lower()), [0], start)

    def test_append_many_atomic_admission(self):
        f = self.field()
        f.append('original')
        with self.assertRaises(TypeError): f.append_many(['yes', 2])
        def bad():
            yield 'not committed'
            raise RuntimeError('iterator failed')
        with self.assertRaises(RuntimeError): f.append_many(bad())
        self.assertEqual(f[:], ['original'])
        with self.assertRaises(TypeError): f.append(7)
        self.assertEqual(f[:], ['original'])
        f.append_many([])
        self.assertEqual(f[:], ['original'])

    def test_projection_traffic(self):
        f = self.field()
        f.append_many([str(i) for i in range(20000)])
        n = f._native
        before = n.calls, n.uploaded, n.downloaded
        self.assertEqual(f.tail(15), [str(i) for i in range(19985, 20000)])
        self.assertEqual(n.calls-before[0], 3) # query, one batch read, release
        self.assertEqual(n.uploaded, before[1])
        self.assertLess(n.downloaded-before[2], 1000)
        counts = n.calls, n.uploaded, n.downloaded
        for _ in range(100):
            self.assertEqual(f.tail(15)[-1], '19999')
            self.assertEqual(len(f), 20000)
        self.assertEqual((n.calls, n.uploaded, n.downloaded), counts)
        f.append('new')
        self.assertEqual(n.calls-counts[0], 1)
        self.assertEqual(n.uploaded-counts[1], 4+4+3*4)
        self.assertEqual(f.tail(1), ['new'])

    def test_iteration_is_one_batch(self):
        f = self.field()
        f.append_many([str(i) for i in range(30)])
        before = f._native.calls
        self.assertEqual(list(f), [str(i) for i in range(30)])
        self.assertEqual(f._native.calls-before, 3)

    def test_native_snapshot_capacity_revision_and_cross_owner(self):
        f, other = self.field(), self.field()
        f.append_many(['one', 'two'])
        n = f._native
        snap = n.command(4, a=0, b=2, c=1)
        needed = C.c_size_t()
        self.assertEqual(n.lib.narwhal_transcript_read(n.reply.handle, snap.token, snap.revision, None, 0, C.byref(needed)), 3)
        self.assertEqual(needed.value, snap.bytes)
        buf = C.create_string_buffer(snap.bytes)
        self.assertEqual(n.lib.narwhal_transcript_read(other._native.reply.handle, snap.token, snap.revision, buf, len(buf), C.byref(needed)), 2)
        self.assertEqual(n.lib.narwhal_transcript_read(n.reply.handle, snap.token, snap.revision+1, buf, len(buf), C.byref(needed)), 2)
        f.clear()
        self.assertEqual(n.consume(snap), ('one', 'two'))
        request = native.Request(1, 0, n.reply.revision, 0, 0, 0)
        data = native.encode_lines(['once'])
        reply = native.Reply()
        rc = n.lib.narwhal_transcript_apply(n.reply.handle, C.byref(request), C.sizeof(request), data, len(data), C.byref(reply), C.sizeof(reply)-1)
        self.assertEqual(rc, 1)
        self.assertEqual(f[:], [])
        self.assertEqual(C.sizeof(request), 40)
        self.assertEqual(C.sizeof(reply), 56)

    def test_lifetime_copy_and_retained_view(self):
        lib = native.library()
        gc.collect()
        before = stats(lib)
        f = Transcript()
        f.append('data')
        view = f[:]
        a, b = copy.copy(f), copy.deepcopy(f)
        self.assertEqual(a[:], ['data'])
        self.assertEqual(b[:], ['data'])
        self.assertNotEqual(a._native.reply.handle, f._native.reply.handle)
        a.append('independent')
        self.assertEqual(f[:], ['data'])
        handle = f._native.reply.handle
        ref = weakref.ref(f._native)
        del f
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(lib.narwhal_transcript_close(handle), 2)
        a.close(); b.close()
        self.assertEqual(view, ['data'])
        self.assertEqual(stats(lib), before)
        with self.assertRaises(RuntimeError): a.append('closed')

    def test_required_native_and_unicode_admission_are_atomic(self):
        lib = native.library()
        baseline = stats(lib)
        with patch.object(native._editor, 'library', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'requires.*native'): native.library()
        for constructor in (Transcript, ch.CommandHistory):
            with patch.object(native, 'library', return_value=None), patch.object(native, 'Owner') as owner:
                with self.assertRaisesRegex(RuntimeError, 'requires.*native'): constructor()
                owner.assert_not_called()
            with patch.object(native._editor, 'library', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'requires.*native'): constructor()
            with patch.object(module.unicodedata, 'unidata_version', '99.0'), patch.object(native, 'Owner') as owner:
                with self.assertRaisesRegex(RuntimeError, 'Unicode'): constructor()
                owner.assert_not_called()
        self.assertEqual(stats(lib), baseline)

    def test_finalizer_allocation_failure_releases_created_handle(self):
        f = self.field()
        lib = f._native.lib
        gc.collect()
        baseline = stats(lib)
        for action in (Transcript, lambda: copy.copy(f)):
            with patch.object(native.weakref, 'finalize', side_effect=MemoryError('finalizer allocation')):
                with self.assertRaises(MemoryError): action()
            self.assertEqual(stats(lib), baseline)
        f.append('still healthy')
        self.assertEqual(f[:], ['still healthy'])

    def test_actual_missing_default_admission_and_live_handle(self):
        env = dict(os.environ)
        env.pop('NARWHAL_UI_CORE_LIBRARY', None)
        real = native._editor.library
        with patch.dict(os.environ, env, clear=True), patch.object(native._editor, '_loaded_library', None), patch.object(Path, 'is_file', return_value=False):
            with patch.object(native._editor, 'library', wraps=real) as loader:
                with self.assertRaisesRegex(RuntimeError, 'required|requires|missing|unavailable'): native.library()
                loader.assert_called_once()
                with self.assertRaises(RuntimeError): Transcript()
                with self.assertRaises(RuntimeError): ch.CommandHistory()
        existing = native.library()
        with patch.dict(os.environ, env, clear=True), patch.object(Path, 'is_file', return_value=False):
            self.assertIs(native.library(), existing, 'loaded shared handle survives artifact removal')

    def test_ffi_exception_freezes_before_stale_projection(self):
        f = self.field(); f.append('before'); self.assertEqual(f[:], ['before'])
        n, real = f._native, f._native.lib
        class Raised:
            calls = 0
            def narwhal_transcript_apply(self, *args):
                self.calls += 1
                real.narwhal_transcript_apply(*args)
                raise ValueError('raised after commit')
            def narwhal_transcript_close(self, *args): return real.narwhal_transcript_close(*args)
        wrapper = Raised(); n.lib = wrapper
        with self.assertRaises(ValueError): f.append('committed')
        self.assertTrue(n.failed)
        with self.assertRaises(RuntimeError): _ = f[:]
        with self.assertRaises(RuntimeError): f.append('duplicate')
        self.assertEqual(wrapper.calls, 1)

    def test_release_and_close_exceptions_are_terminal(self):
        for action in ('release', 'close'):
            f = self.field(); f.append('x')
            n, real = f._native, f._native.lib
            class Raised:
                fired = False
                def narwhal_transcript_release(self, *args):
                    rc = real.narwhal_transcript_release(*args)
                    if action == 'release' and not self.fired:
                        self.fired = True
                        raise ValueError('release raised')
                    return rc
                def narwhal_transcript_close(self, *args):
                    rc = real.narwhal_transcript_close(*args)
                    if action == 'close' and not self.fired:
                        self.fired = True
                        raise ValueError('close raised')
                    return rc
            snap = n.command(4, a=0, b=1, c=1) if action == 'release' else None
            wrapper = Raised()
            wrapper.narwhal_transcript_read = real.narwhal_transcript_read
            n.lib = wrapper
            with self.assertRaises(ValueError):
                if action == 'release': n.consume(snap)
                else: f.close()
            self.assertTrue(n.failed)
            with self.assertRaises(RuntimeError): _ = f[:]
            n.lib = real # cleanup after the already observed terminal failure

    def test_standalone_special_python_values(self):
        class Limit(int): pass
        limit = Limit(3)
        h = ch.CommandHistory(limit)
        self.addCleanup(h.close)
        self.assertIs(h.limit, limit)
        self.assertIsNotNone(h._lines._native)
        for line in ('0', '1', '2', '3'): h.append(line)
        self.assertEqual(h.lines(Limit(2)), ['2', '3'])
        class Line(str):
            def rstrip(self, chars): raise AssertionError('must not execute subclass callback')
        class Needle(str):
            def lower(self): raise AssertionError('must not execute subclass callback')
        handle, revision = h._lines._native.reply.handle, h._lines._native.reply.revision
        for operation in (lambda: h.append(Line('original')), lambda: h.search(Needle('unused'))):
            with self.assertRaisesRegex(TypeError, 'exact string'): operation()
        self.assertEqual(h.lines(), ['1', '2', '3'])
        self.assertEqual((h._lines._native.reply.handle, h._lines._native.reply.revision), (handle, revision))
        class DerivedHistory(ch.CommandHistory): pass
        derived = DerivedHistory(1)
        self.addCleanup(derived.close)
        derived.append('old'); derived.append('new')
        self.assertIsNotNone(derived._lines._native)
        self.assertEqual(derived.lines(), ['new'])
        huge = 10**100
        h = ch.CommandHistory(huge)
        self.addCleanup(h.close)
        h.append('x\r\n\r\n')
        self.assertIs(h.limit, huge)
        self.assertEqual(h.lines(huge), ['x'])

    def test_save_and_file_failure_compatibility(self):
        with tempfile.TemporaryDirectory(prefix='transcript-save-') as directory:
            # Golden bytes/errors measured from the archived pre-retirement model;
            # no executable retired model is needed by the public suite.
            for lines, expected in (([], b''), (['a\ninside', 'İ😀'], b'a\ninside\n\xc4\xb0\xf0\x9f\x98\x80\n')):
                h = ch.CommandHistory()
                self.addCleanup(h.close)
                for line in lines: h.append(line)
                path = Path(directory) / 'golden'
                h.save(path)
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(h.lines(), lines)
            h = ch.CommandHistory(); h.append('\ud800')
            self.addCleanup(h.close)
            path = Path(directory) / 'surrogate'
            path.write_bytes(b'old')
            with self.assertRaises(UnicodeEncodeError) as caught: h.save(path)
            error = caught.exception
            self.assertEqual((error.encoding, error.object, error.start, error.end, error.reason),
                             ('utf-8', '\ud800\n', 0, 1, 'surrogates not allowed'))
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(h.lines(), ['\ud800'])
            h = ch.CommandHistory(); h.append('preserved')
            self.addCleanup(h.close)
            path = Path(directory) / 'existing'
            path.write_text('old')
            with patch('os.replace', side_effect=OSError('denied')):
                with self.assertRaises(OSError): h.save(path)
            self.assertEqual(path.read_text(), 'old')
            self.assertEqual(h.lines(), ['preserved'])

    def test_close_error_status_freezes_cached_owner(self):
        f = self.field(); f.append('cached'); self.assertEqual(f[:], ['cached'])
        n, real = f._native, f._native.lib
        class BadClose:
            def narwhal_transcript_close(self, handle): return 5
        n.lib = BadClose()
        with self.assertRaisesRegex(RuntimeError, 'close failed'): f.close()
        self.assertTrue(n.failed)
        with self.assertRaises(RuntimeError): _ = f[:]
        with self.assertRaises(RuntimeError): f.append('not replayed')
        n.lib = real

    def test_bad_loader_and_old_library(self):
        with patch.object(native._editor, '_loaded_library', None), patch.dict(os.environ, {'NARWHAL_UI_CORE_LIBRARY': '/definitely/missing-transcript.so'}):
            with self.assertRaises(RuntimeError): Transcript()
        class Old: pass
        with patch.object(native._editor, 'library', return_value=Old()):
            with self.assertRaisesRegex(RuntimeError, 'transcript ABI'): native.library()
            with self.assertRaisesRegex(RuntimeError, 'transcript ABI'): Transcript()
            with self.assertRaisesRegex(RuntimeError, 'transcript ABI'): ch.CommandHistory()

    def test_reply_bound_before_clone_projection(self):
        f = self.field(); f.append('x')
        n, real = f._native, f._native.lib
        created = []
        class Capture:
            def narwhal_transcript_apply(self, *args):
                rc = real.narwhal_transcript_apply(*args)
                created.append(args[-2]._obj.handle)
                return rc
            def narwhal_transcript_close(self, *args): return real.narwhal_transcript_close(*args)
        n.lib = Capture()
        try:
            with patch.object(native, 'MAX_LINES', 0):
                with self.assertRaises(RuntimeError): n.command(6)
        finally:
            for handle in created: real.narwhal_transcript_close(handle)

    def test_bad_mutation_reply_is_not_replayed(self):
        f = self.field()
        n, real = f._native, f._native.lib
        class Bad:
            calls = 0
            def narwhal_transcript_apply(self, *args):
                self.calls += 1
                rc = real.narwhal_transcript_apply(*args)
                args[-2]._obj.len = native.MAX_LINES + 1
                return rc
            def narwhal_transcript_close(self, *args): return real.narwhal_transcript_close(*args)
        bad = Bad(); n.lib = bad
        with self.assertRaisesRegex(RuntimeError, 'invalid transcript reply'): f.append('once')
        with self.assertRaises(RuntimeError): f.append('twice')
        self.assertEqual(bad.calls, 1)
        # Inspect real native state without changing the failed adapter.
        request = native.Request(4, 0, n.reply.revision+1, 0, 1, 1)
        out = native.Reply()
        self.assertEqual(real.narwhal_transcript_apply(n.reply.handle, C.byref(request), 40, None, 0, C.byref(out), 56), 0)
        self.assertEqual(out.len, 1)
        real.narwhal_transcript_release(n.reply.handle, out.token)

    def test_read_decode_and_allocation_failures_release_snapshot(self):
        for kind in ('needed', 'data', 'allocation'):
            f = self.field(); f.append('abc')
            n, real = f._native, f._native.lib
            snap = n.command(4, a=0, b=1, c=1)
            class BadRead:
                def narwhal_transcript_read(self, *args):
                    rc = real.narwhal_transcript_read(*args)
                    if kind == 'needed': args[-1]._obj.value = native.MAX_WIRE + 1
                    if kind == 'data': C.memmove(args[3], struct.pack('<I', 99), 4)
                    return rc
                def narwhal_transcript_release(self, *args): return real.narwhal_transcript_release(*args)
                def narwhal_transcript_close(self, *args): return real.narwhal_transcript_close(*args)
            n.lib = BadRead()
            if kind == 'allocation':
                with patch.object(C, 'create_string_buffer', side_effect=MemoryError):
                    with self.assertRaises(MemoryError): n.consume(snap)
            else:
                string_at = C.string_at
                def bounded_read(buffer, length):
                    self.assertLessEqual(length, C.sizeof(buffer), 'native read exceeds actual allocation')
                    return string_at(buffer, length)
                with patch.object(C, 'string_at', bounded_read):
                    with self.assertRaises(RuntimeError): n.consume(snap)
            self.assertEqual(real.narwhal_transcript_release(n.reply.handle, snap.token), 2)
            with self.assertRaises(RuntimeError): f.append('not replayed')


if __name__ == '__main__':
    unittest.main()
