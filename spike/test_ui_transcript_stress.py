"""Finite long-session lifecycle/reference trace; no GUI, no timing acceptance claim."""
import copy
import ctypes as C
import gc
import importlib.util
import os
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transcript_stress_model', ROOT/'spike/test_ui_transcript_model.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StressTests(unittest.TestCase):
    def test_lifecycle_memory_and_interrupted_reads(self):
        rng = random.Random(70717)
        lib = module.native.library()
        gc.collect()
        baseline = module.stats(lib)
        last_handle = 0
        for cycle in range(200):
            f = module.Transcript()
            n = f._native
            self.assertGreater(n.reply.handle, last_handle)
            last_handle = n.reply.handle
            ref = [f'{cycle}:{i}:ΑΣ😀\ud800' for i in range(512)]
            f.append_many(ref)
            snapshot = n.command(4, a=0, b=len(ref), c=1)
            for _ in range(30):
                count = rng.randrange(1, 20)
                new = ['x'*rng.randrange(50) for _ in range(count)]
                f.append_many(new); ref.extend(new)
                cut = rng.randrange(0, len(ref)+1)
                del f[:cut]; del ref[:cut]
                self.assertEqual(f[-15:], ref[-15:])
            other = copy.copy(f)
            self.assertEqual(other[:], ref)
            other.clear(); other.append('independent')
            self.assertEqual(f[:], ref)
            if cycle % 2:
                # Pending snapshot still describes pre-prune history, independent
                # of the latest revision and the independent transcript's contents.
                self.assertEqual(n.consume(snapshot), tuple(f'{cycle}:{i}:ΑΣ😀\ud800' for i in range(512)))
            else:
                # Interrupt after snapshot creation; close must release it without
                # requiring a matching read or a process exit.
                f.close()
                needed = C.c_size_t()
                self.assertEqual(lib.narwhal_transcript_read(n.reply.handle, snapshot.token,
                    snapshot.revision, None, 0, C.byref(needed)), 2)
            other.close(); f.close()
            if cycle % 25 == 0:
                gc.collect()
                self.assertEqual(module.stats(lib), baseline)
        gc.collect()
        self.assertEqual(module.stats(lib), baseline)
        print('seed70717:200 owners,6000 append/prune/read steps; native counts returned to baseline', baseline)


if __name__ == '__main__': unittest.main()
