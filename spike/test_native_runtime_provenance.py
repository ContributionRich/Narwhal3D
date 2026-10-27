#!/usr/bin/env python3
"""Pure build/provenance orchestration tests; no tools/, compiler, GUI or network.

Python is used to exercise the existing shell/Python packaging interface with
isolated source/runtime fixtures, not to implement runtime computation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
COMMIT = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
INPUTS = (
    "blender_patches/apply_app_drawn.py",
    "blender_patches/apply_cad_boundary_wires.py",
    "blender_patches/apply_deferred_first_present.py",
    "blender_patches/apply_narwhal3d_branding.py",
    "blender_patches/apply_statusbar_height.py",
    "blender_patches/apply_window_overlay.py",
    "blender_patches/narwhal3d_app_icon.svg",
)
BINARY = """#!/bin/sh
# - Narwhal 3D
# Narwhal 3D quit
# NARWHAL3D_DEFER_FIRST_PRESENT
# NARWHAL3D_STATUSBAR_HEIGHT
# RNA_WINDOW_DRAW_HANDLE
printf 'Narwhal 3D 5.2.2 fixture\\n'
"""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assemble = load("provenance_assemble", ROOT / "scripts/assemble_linux_release.py")
verify = load("provenance_verify", ROOT / "scripts/verify_linux_release.py")
statusbar = load("provenance_statusbar", ROOT / INPUTS[4])
overlay = load("provenance_overlay", ROOT / INPUTS[5])


def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


class NativeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="narwhal-provenance-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.script = self.root / "scripts/build_blender_linux.sh"
        self.script.parent.mkdir()
        shutil.copyfile(ROOT / "scripts/build_blender_linux.sh", self.script)
        for name in INPUTS:
            destination = self.root / name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        # Statusbar and overlay patch application is real. Legacy patches assert that the
        # driver supplies the right source path/icon, without needing Blender.
        write(self.root / INPUTS[3], """from pathlib import Path
def apply(source, icon):
    assert Path(source).name == 'source fixture'
    assert Path(icon).name == 'narwhal3d_app_icon.svg'
    return []
""")
        for name in INPUTS[:3]:
            write(self.root / name, """from pathlib import Path
def main():
    assert Path(SRC).parts[-2:] == ('source', 'blender')
""")
        self.source = self.root / "source fixture"
        (self.source / ".git").mkdir(parents=True)
        write(self.source / "CMakeLists.txt", "fixture\n")
        self.screen = self.source / "source/blender" / statusbar.RELATIVE
        self.original_screen = "\n".join(old for _, old, _ in statusbar.EDITS)
        write(self.screen, self.original_screen)
        self.native_source = self.source / "source/blender"
        self.overlay_originals = {}
        for relative, old, _ in overlay.EDITS:
            self.overlay_originals[relative] = self.overlay_originals.get(relative, "") + old + "\n"
        for relative, text in self.overlay_originals.items():
            write(self.native_source / relative, text)
        self.overlay_header = self.native_source / overlay.HEADER
        self.install = self.root / "installed fixture"
        self.binary = self.install / "blender"
        write(self.binary, BINARY, executable=True)
        self.marker = self.install / ".narwhal3d-native-runtime.json"
        self.commands = self.root / "commands"
        self.log = self.root / "build-commands.log"
        write(self.commands / "git", f"""#!/bin/sh
case "$*" in
  *'rev-parse HEAD') printf '%s\\n' "${{FIXTURE_COMMIT:-{COMMIT}}}" ;;
  *'status --porcelain --untracked-files=no') printf '%s' "${{FIXTURE_DIRTY:-}}" ;;
  *) exit 90 ;;
esac
""", executable=True)
        write(self.commands / "cmake", """#!/bin/sh
printf '%s\\n' "$*" >> "$FIXTURE_LOG"
grep -q 'narwhal_statusbar_fixed_height();' "$FIXTURE_SCREEN" || exit 91
grep -q 'BPY_callback_window_draw' "$FIXTURE_OVERLAY_HEADER" || exit 93
""", executable=True)
        write(self.commands / "ninja", "#!/bin/sh\nexit 92\n", executable=True)
        self.env = dict(os.environ, PATH=str(self.commands) + os.pathsep + os.environ["PATH"],
                        PYTHONDONTWRITEBYTECODE="1", FIXTURE_LOG=str(self.log),
                        FIXTURE_SCREEN=str(self.screen),
                        FIXTURE_OVERLAY_HEADER=str(self.overlay_header))
        self.env.pop("FIXTURE_COMMIT", None)
        self.env.pop("FIXTURE_DIRTY", None)

    def run_builder(self, *args, ok=True):
        result = subprocess.run(["bash", str(self.script), *map(str, args)],
                                env=self.env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=15)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def build(self, **kwargs):
        return self.run_builder("--source-dir", self.source, "--build-dir", self.root / "build",
                                "--install-dir", self.install, "--jobs", "1",
                                "--source-date-epoch", "1700000000", **kwargs)

    def record(self, **kwargs):
        return self.run_builder("--record-installed-provenance", "--source-dir", self.source,
                                "--install-dir", self.install,
                                "--source-date-epoch", "1700000000", **kwargs)

    def assert_marker(self):
        marker = json.loads(self.marker.read_text())
        self.assertEqual([item["path"] for item in marker["patch_inputs"]], list(INPUTS))
        records, aggregate = assemble.expected_patch_provenance(self.root)
        self.assertEqual(marker["patch_inputs"], records)
        self.assertEqual(marker["patch_aggregate_sha256"], aggregate)
        self.assertEqual(marker["binary_sha256"], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertEqual(marker["blender_source_commit"], COMMIT)
        self.assertEqual(marker["build_source_date_epoch"], 1700000000)
        self.assertEqual(marker["build_timestamp_utc"], "2023-11-14T22:13:20Z")
        return marker

    def test_self_check_requires_regular_parseable_statusbar_and_overlay_patches(self):
        self.run_builder("--self-check")
        for name in INPUTS[4:6]:
            with self.subTest(patch=name):
                path = self.root / name
                original = path.read_text()
                path.unlink()
                self.assertIn("required native patch input", self.run_builder("--self-check", ok=False))
                path.symlink_to(ROOT / name)
                self.assertIn("symlinked", self.run_builder("--self-check", ok=False))
                path.unlink()
                write(path, "def broken syntax\n")
                self.assertIn("SyntaxError", self.run_builder("--self-check", ok=False))
                write(path, original)
                self.run_builder("--self-check")

    def test_clean_build_applies_explicit_source_and_records_all_inputs(self):
        self.build()
        for module in (statusbar, overlay):
            for relative, _, new in module.EDITS:
                self.assertIn(new, (self.native_source / relative).read_text())
        self.assertEqual(self.overlay_header.read_text(), overlay.HEADER_TEXT)
        self.assert_marker()
        commands = self.log.read_text().splitlines()
        self.assertEqual(len(commands), 3)
        self.assertIn("--parallel 1", commands[1])
        self.assertTrue(commands[2].startswith("--install "))

    def test_clean_build_still_refuses_dirty_source(self):
        self.env["FIXTURE_DIRTY"] = " M source/blender/editors/screen/screen_edit.cc\n"
        self.assertIn("tracked modifications", self.build(ok=False))
        self.assertFalse(self.log.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.screen.read_text(), self.original_screen)

    def test_record_only_shares_writer_without_patch_or_build(self):
        self.build()
        first = self.marker.read_bytes()
        self.log.unlink()
        self.env["FIXTURE_DIRTY"] = " M source/blender/editors/screen/screen_edit.cc\n"
        before = {path: path.read_bytes() for path in
                  [self.screen, *(self.native_source / name for name in overlay.NATIVE_INPUTS)]}
        self.record()
        self.assertEqual(self.marker.read_bytes(), first)
        write(self.binary, BINARY + "# incremental install\n", executable=True)
        self.record()
        self.assert_marker()
        self.assertNotEqual(self.marker.read_bytes(), first)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(self.log.exists())

    def test_record_refuses_unpatched_source_without_repairing_it(self):
        write(self.marker, "old marker\n")
        self.assertIn("requires the current statusbar patch", self.record(ok=False))
        self.assertEqual(self.screen.read_text(), self.original_screen)
        self.assertEqual(self.marker.read_text(), "old marker\n")
        self.assertFalse(self.log.exists())

    def test_record_refuses_unpatched_overlay_without_repairing_it(self):
        statusbar.apply(self.native_source)
        overlay.apply(self.native_source)
        for relative, old, new in overlay.EDITS:
            with self.subTest(source=relative, anchor=old.splitlines()[0]):
                path = self.native_source / relative
                patched = path.read_text()
                broken = patched.replace(new, old, 1)
                write(path, broken)
                write(self.marker, "old marker\n")
                self.assertIn("requires the current window-overlay patch", self.record(ok=False))
                self.assertEqual(path.read_text(), broken)
                self.assertEqual(self.marker.read_text(), "old marker\n")
                write(path, patched)
        self.assertFalse(self.log.exists())

    def test_record_refuses_missing_or_modified_overlay_header_without_repair(self):
        statusbar.apply(self.native_source)
        overlay.apply(self.native_source)
        self.overlay_header.unlink()
        write(self.marker, "old marker\n")
        self.assertIn("requires the current window-overlay header", self.record(ok=False))
        self.assertFalse(self.overlay_header.exists())
        self.assertEqual(self.marker.read_text(), "old marker\n")
        write(self.overlay_header, overlay.HEADER_TEXT + "// modified\n")
        self.assertIn("requires the current window-overlay header", self.record(ok=False))
        self.assertEqual(self.overlay_header.read_text(), overlay.HEADER_TEXT + "// modified\n")
        self.assertEqual(self.marker.read_text(), "old marker\n")
        self.assertFalse(self.log.exists())

    def test_record_refuses_stale_binary_and_wrong_commit(self):
        statusbar.apply(self.native_source)
        overlay.apply(self.native_source)
        write(self.marker, "old marker\n")
        for token in ("NARWHAL3D_STATUSBAR_HEIGHT", "NARWHAL3D_DEFER_FIRST_PRESENT",
                      "RNA_WINDOW_DRAW_HANDLE"):
            write(self.binary, BINARY.replace(token, "old-runtime"), executable=True)
            self.assertIn("lacks the native patch marker", self.record(ok=False))
            self.assertEqual(self.marker.read_text(), "old marker\n")
        write(self.binary, BINARY, executable=True)
        self.env["FIXTURE_COMMIT"] = "0" * 40
        self.assertIn("source commit mismatch", self.record(ok=False))
        self.assertEqual(self.marker.read_text(), "old marker\n")
        self.assertFalse(self.log.exists())

    def test_packaging_verifier_requires_exact_statusbar_and_overlay_inputs(self):
        self.build()
        marker = self.assert_marker()
        appdir = self.root / "AppDir"
        lock = appdir / verify.LOCK_RELATIVE
        lock.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "packaging/linux-x86_64.lock.json", lock)
        runtime = appdir / verify.APP_ROOT_RELATIVE / verify.RUNTIME_RELATIVE
        shutil.copytree(self.install, runtime)
        identity = {
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "runtime_required_glibc": "2.35",
            "runtime_build_source_date_epoch": marker["build_source_date_epoch"],
            "runtime_build_timestamp_utc": marker["build_timestamp_utc"],
            "runtime_patch_aggregate_sha256": marker["patch_aggregate_sha256"],
            "runtime_binary_sha256": marker["binary_sha256"],
        }
        with patch.object(verify, "scan_runtime_glibc", return_value=((2, 35), self.binary)):
            verify.verify_lock_and_markers(appdir, identity)
            # Self-consistent six-input markers missing either patch are rejected.
            complete_inputs = marker["patch_inputs"]
            for omitted in INPUTS[4:6]:
                with self.subTest(omitted=omitted):
                    marker["patch_inputs"] = [item for item in complete_inputs
                                              if item["path"] != omitted]
                    aggregate = hashlib.sha256()
                    for item in marker["patch_inputs"]:
                        aggregate.update(item["path"].encode() + b"\0" + item["sha256"].encode() + b"\n")
                    marker["patch_aggregate_sha256"] = aggregate.hexdigest()
                    identity["runtime_patch_aggregate_sha256"] = aggregate.hexdigest()
                    write(runtime / self.marker.name, json.dumps(marker))
                    with self.assertRaisesRegex(verify.VerificationError, "patch input list"):
                        verify.verify_lock_and_markers(appdir, identity)


if __name__ == "__main__":
    unittest.main()
