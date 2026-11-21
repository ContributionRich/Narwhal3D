#!/usr/bin/env python3
"""Real-Blender command-glue mutations, always in private temporary copies.

Run with bundled Python, the explicit qualified NARWHAL_GEOMETRY_CORE and
NARWHAL3D_BREP_WRITER, and --blender /absolute/path/to/blender-patched/blender.
No geometry is implemented here: Python is required to drive actual bpy,
rhino3dm document handles and command/Undo callbacks. Native geometry is real.
Every mutant must fail a named assertion in the SAME positive host suite; an
import error/crash/skip is not accepted as mutation evidence.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True)
    args = parser.parse_args()
    assert os.environ.get("NARWHAL_GEOMETRY_CORE"), "pin the qualified candidate explicitly"
    assert os.environ.get("NARWHAL3D_BREP_WRITER"), "pin the exact writer explicitly"
    source = (ROOT / "narwhal3d/commands.py").read_text()
    # Each mutation is confined to JoinCmd, never the shared command engine.
    start, end = source.index("class JoinCmd("), source.index("class JoinCopyCmd(")
    join = source[start:end]
    mutations = [
        ("M1 lose surface 2x", "return 2 * self._tolerance(ctx)",
         "return self._tolerance(ctx)", "gap 0.00015 < 2x"),
        ("M2 delete JoinCopy originals", "for keys, _, _ in ([] if self._keep_inputs else curve_chains + surface_groups):\n            for name in keys:",
         "for keys, _, _ in (curve_chains + surface_groups):\n            for name in keys:", "every input keeps its id"),
        ("M3 omit staged rollback", "for oid, display, ob, mesh in reversed(staged):",
         "for oid, display, ob, mesh in []:", "all original sources retained and staged output removed"),
        ("M4 bypass guarded source publication", "if current != sources:",
         "if False:", "callback failure/edit detected surface raises=False"),
        ("M5 omit curve allocation rollback", "for oid in allocated:",
         "for oid in []:", "only own output rolled back; external addition retained"),
        ("M6 old default ceiling", "return 2 * self._tolerance(ctx)",
         "return min(2 * self._tolerance(ctx), 0.01)", "recipe pins default qualified tolerance once"),
        ("M9 omit display identity guard", "            for name, ob in source_displays.items():",
         "            for name, ob in []:", "source display replacement/rename stops publication"),
        ("M8 omit consumption callback preflight", "                    model._will_change()",
         "                    pass", "deletion callback failure reported before consuming sources"),
        ("M7 partial success on kernel refusal", "if surface_refusal is not None:",
         "if False:", "kernel refusal preserves both families and Undo"),
    ]
    with tempfile.TemporaryDirectory(prefix="join-command-mutations-") as folder:
        root = Path(folder)
        shutil.copytree(ROOT / "narwhal3d", root / "narwhal3d",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for component in ("application-core", "document-core", "document-observer"):
            shutil.copytree(ROOT / "artifacts" / component, root / "artifacts" / component)
        (root / "spike").mkdir()
        shutil.copy2(ROOT / "spike/test_join_surface_command.py", root / "spike")
        target = root / "narwhal3d/commands.py"
        command = [args.blender, "-b", "--factory-startup", "--python-exit-code", "1",
                   "--python", str(root / "spike/test_join_surface_command.py"), "--", "--host"]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

        def run():
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
            return result.returncode, result.stdout + result.stderr

        code, output = run()
        assert code == 0 and "JOIN SURFACE COMMAND TESTS PASSED" in output, output
        print("GREEN actual Blender baseline")
        for label, old, new, expected in mutations:
            assert join.count(old) == 1, (label, "mutation target not unique")
            target.write_text(source[:start] + join.replace(old, new, 1) + source[end:])
            code, output = run()
            assert code != 0 and any("FAIL" in line and expected in line for line in output.splitlines()), (label, output)
            print(f"RED {label}: {expected}")
            target.write_text(source)
        code, output = run()
        assert code == 0 and "JOIN SURFACE COMMAND TESTS PASSED" in output, output
        print("GREEN restored actual Blender baseline; 9 mutants killed")


if __name__ == "__main__":
    main()
