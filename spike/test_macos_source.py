#!/usr/bin/env python3
"""Static and self-check gates for the experimental macOS source build path."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_macos_source.sh"
GUIDE = ROOT / "docs" / "macos-build.md"
LAUNCHER = ROOT / "narwhal3d.sh"
PUBLIC = ROOT / "scripts" / "create_public_branch.py"
AUDIT = ROOT / "scripts" / "audit_public_tree.py"

results = []


def check(label, condition, detail=""):
    results.append((label, bool(condition), detail))


script = SCRIPT.read_text(encoding="utf-8")
guide = GUIDE.read_text(encoding="utf-8")
launcher = LAUNCHER.read_text(encoding="utf-8")
public = PUBLIC.read_text(encoding="utf-8")
audit = AUDIT.read_text(encoding="utf-8")

self_check = subprocess.run(
    [str(SCRIPT), "--self-check"], cwd=ROOT, text=True,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
check("macOS builder self-check runs on a non-Mac validation host",
      self_check.returncode == 0 and "self-check: OK" in self_check.stdout,
      self_check.stdout)

non_mac = subprocess.run(
    [str(SCRIPT), "--source-dir", "/does/not/matter"], cwd=ROOT, text=True,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
check("real macOS build fails closed on a non-Darwin host",
      non_mac.returncode == 2 and "must run on macOS" in non_mac.stdout,
      non_mac.stdout)

check("builder pins the exact Blender runtime revision",
      "d13f752e3b9c4f8c261cda552b1021f8bcc0382c" in script)
check("builder supports only native Apple Silicon or Intel",
      "arm64|x86_64" in script and "universal" not in script.lower())
check("builder installs exact CPython geometry versions",
      all(value in script for value in (
          "rhino3dm==8.35.0", "cadquery-ocp-proxy==8.0.1.0.0",
          "cadquery-ocp-novtk==8.0.1.0.0")))
check("builder applies every native viewport/identity patch",
      all(value in script for value in (
          "apply_narwhal3d_branding.py", "apply_app_drawn.py",
          "apply_cad_boundary_wires.py", "apply_deferred_first_present.py")))
check("builder compiles document, display, file and CDR native helpers",
      all(value in script for value in (
          "pure_core selection_core cdr_core document_core",
          "converter_supervisor cdr_multiversion")))
check("launcher knows the macOS app bundle executable",
      "tools/blender-patched/Blender.app/Contents/MacOS/Blender" in launcher)
check("launcher exports host-native runtime component paths",
      all(value in launcher for value in (
          "NARWHAL_PURE_CORE", "NARWHAL_SELECTION_CORE",
          "NARWHAL_CDR_CORE", "NARWHAL_DOCUMENT_CORE")))
check("guide is explicit that this is not a macOS release",
      all(value in guide for value in (
          "not currently publish a macOS application",
          "not a supported release recipe", "unsigned", "unnotarized",
          "does not create a universal application")))
check("guide records measured wheel and OS constraints",
      all(value in guide for value in (
          "macOS 14 or newer", "CPython 3.13", "arm64", "x86_64")))
check("guide asks for GUI and exact-geometry evidence",
      "UI report 23/23" in guide and "exact representation audit" in guide
      and ".narwhal" in guide)
check("public generator and audit require the macOS guide/builder/test",
      all(value in public and value in audit for value in (
          "docs/macos-build.md", "scripts/build_macos_source.sh",
          "spike/test_macos_source.py")))

for label, passed, detail in results:
    print(("  ok   " if passed else "  FAIL ") + label
          + ((f" ({detail.strip()})") if detail and not passed else ""))
failed = sum(not passed for _, passed, _ in results)
print(f"\n{len(results) - failed}/{len(results)} macOS source-build checks passed")
raise SystemExit(1 if failed else 0)
