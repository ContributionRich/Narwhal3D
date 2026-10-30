#!/usr/bin/env python3
"""Bootstrap reports the actual backend; layout readiness remains a separate event."""
import ast
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "launch/bootstrap.py"

def report(backend, background=False):
    tree = ast.parse(SOURCE.read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_report_presentation_backend"]
    ns = dict(bpy=SimpleNamespace(app=SimpleNamespace(background=background)), sys=sys)
    exec(compile(tree, str(SOURCE), "exec"), ns)
    output = io.StringIO()
    gpu = SimpleNamespace(platform=SimpleNamespace(backend_type_get=lambda: backend))
    with patch.dict(sys.modules, gpu=gpu), redirect_stderr(output):
        ns["_report_presentation_backend"]()
    return output.getvalue()

class BackendTests(unittest.TestCase):
    def test_only_actual_metal_reports_continuous_presentation(self):
        self.assertEqual(report("METAL"), "[narwhal3d] continuous presentation backend: METAL\n")
        self.assertEqual(report("OPENGL"), "")
        self.assertEqual(report("VULKAN"), "")
        self.assertEqual(report("METAL", background=True), "")
        self.assertNotIn("layout presentation ready", report("METAL"))

if __name__ == "__main__":
    unittest.main()
