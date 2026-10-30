#!/usr/bin/env python3
"""Prove cache priming prevents import writes; no Blender or Mac GUI required."""
import hashlib
import importlib.util
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import assemble_macos_release as assemble
PYTHON = ROOT / "tools/blender-patched/5.2/python/bin/python3.13"


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class BytecodeTests(unittest.TestCase):
    def test_primed_cache_survives_relocation_and_timestamp_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "cache_probe.py").write_text("VALUE = 42\n")
            code = "import sys; sys.path.insert(0,sys.argv[1]); import cache_probe; assert cache_probe.VALUE == 42"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            before = hashes(source)
            subprocess.run([str(PYTHON), "-I", "-c", code, str(source)], env=env, check=True, timeout=20)
            self.assertNotEqual(hashes(source), before, "negative control must create a cache despite ignored environment")
            assemble.prime_bytecode(PYTHON, source)
            moved = Path(temporary) / "Relocated 日本語"
            shutil.copytree(source, moved)
            file = moved / "cache_probe.py"
            old = file.stat()
            os.utime(file, (old.st_atime + 10, old.st_mtime + 10))
            before = hashes(moved)
            subprocess.run([str(PYTHON), "-I", "-c", code, str(moved)], env=env, check=True, timeout=20)
            self.assertEqual(hashes(moved), before, "loading valid signed caches must not rewrite package bytes")

    def test_timestamp_cache_mutation_is_detected(self):
        source = inspect.getsource(assemble.prime_bytecode)
        self.assertEqual(source.count("CHECKED_HASH"), 1)
        original = assemble.prime_bytecode
        try:
            exec(source.replace("CHECKED_HASH", "TIMESTAMP"), assemble.__dict__)
            result = unittest.TestResult()
            BytecodeTests("test_primed_cache_survives_relocation_and_timestamp_changes").run(result)
            self.assertTrue(result.failures, result.errors)
        finally:
            assemble.prime_bytecode = original
        result = unittest.TestResult()
        BytecodeTests("test_primed_cache_survives_relocation_and_timestamp_changes").run(result)
        self.assertTrue(result.wasSuccessful(), result.errors)

if __name__ == "__main__":
    unittest.main()
