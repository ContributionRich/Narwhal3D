#!/usr/bin/env python3
"""Public repository organization, roadmap, issue forms, and asset policy checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f" ({detail})" if detail else ""))


required_docs = (
    "README.md", "STATUS.md", "COMPATIBILITY.md", "ROADMAP.md", "CHANGELOG.md", "CONTRIBUTING.md",
    "SECURITY.md", "SUPPORT.md", "GOVERNANCE.md", "CODE_OF_CONDUCT.md",
    "docs/gaps.md", "docs/github-project.md", "docs/windows-build.md",
)
check(all((ROOT / path).is_file() for path in required_docs),
      "the public project and governance document set is complete")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
for link in ("STATUS.md", "COMPATIBILITY.md", "ROADMAP.md", "docs/gaps.md", "CONTRIBUTING.md",
             "SUPPORT.md", "SECURITY.md", "docs/images/bugreport.png", "docs/windows-build.md"):
    check(link in readme, f"README links {link}")
check("Narwhal_3D-0.4.1-x86_64.AppImage" in readme
      and "--version 0.4.1" in readme
      and "--expected-version 0.4.1" in readme,
      "README install and release examples use version 0.4.1")
check("extremely early prerelease" in readme
      and "frozen on the `0.4.x` line" in readme,
      "README pins the owner-required early-prerelease 0.4.x release line")

gif_path = ROOT / "docs/readme-command-loft.gif"
gif_bytes = gif_path.read_bytes()
check(gif_bytes.startswith(b"GIF8") and len(gif_bytes) > 100_000,
      "the README command demo is a non-empty GIF")
capture = (ROOT / "scripts/capture_readme_demo.py").read_text(encoding="utf-8")
recorder = (ROOT / "scripts/build_readme_demo.sh").read_text(encoding="utf-8")
check("CAPTURE.write_text" in capture and "DONE.write_text" in capture
      and '[[ ! -f "$CAPTURE" ]]' in recorder and '[[ ! -f "$DONE" ]]' in recorder,
      "README capture starts and stops on explicit in-app markers")
check("--activewindow" in recorder and "--pointer" in recorder,
      "README capture records only the active application window with the pointer")

roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
check(all(f"## Phase {phase} " in roadmap for phase in range(1, 11)),
      "the outcome roadmap contains all ten ordered phases")
check("Stable-release gates" in roadmap and "Exact manufacturing geometry" in roadmap,
      "the roadmap pins release and manufacturing-geometry gates")

gaps = (ROOT / "docs/gaps.md").read_text(encoding="utf-8")
check(all(term in gaps for term in ("Complete", "Partial", "Missing", "Deferred")),
      "the gaps inventory defines honest status categories")
check(all(section in gaps for section in (
    "Curve creation", "Surface creation and editing", "Solids and B-rep topology",
    "Mesh domain", "SubD domain", "Drafting and layouts", "UI and accessibility",
    "Performance and reliability", "Import, export, and print", "Platform and packaging",
)), "the gaps inventory spans the full product scope")

logo = (ROOT / "docs/images/bugreport.png").read_bytes()
check(logo[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", logo[16:24]) == (96, 96),
      "the bug-report logo is a 96x96 PNG")

forms = sorted((ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml"))
form_names = {path.name for path in forms}
check({"bug.yml", "command.yml", "ui.yml", "performance.yml", "import-export.yml",
       "feature.yml", "config.yml"} <= form_names,
      "focused GitHub issue forms and template configuration are present", form_names)
check(all("\nabout:" not in ("\n" + path.read_text(encoding="utf-8")) for path in forms),
      "issue forms use the current description schema")
check((ROOT / ".github/PULL_REQUEST_TEMPLATE.md").is_file()
      and (ROOT / ".github/CODEOWNERS").is_file(),
      "pull-request guidance and review ownership are present")

spec = importlib.util.spec_from_file_location(
    "public_branch", ROOT / "scripts/create_public_branch.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
public = set(module.public_files())
check(set(required_docs) <= public and "docs/images/bugreport.png" in public
      and "scripts/collect_windows_build_info.ps1" in public
      and "scripts/build_windows_preview.ps1" in public
      and "scripts/verify_windows_release.py" in public
      and "scripts/release_policy.py" in public
      and "spike/test_windows_collector.py" in public
      and "spike/test_windows_release.py" in public
      and "spike/test_release_policy.py" in public,
      "the public allowlist includes roadmap/governance docs, the bug logo, and Windows collector")
check({"spike/test_picture.py", "spike/test_cmdbar_hits.py",
       "spike/test_extrude_distance.py", "spike/test_extrude_options.py",
       "spike/test_picture_display.py", "spike/test_gumball_region_extrude.py"} <= public,
      "the public allowlist includes regressions for the post-0.3 interaction fixes")
check({"native/welcome/.gitignore", "native/welcome/Cargo.lock",
       "native/welcome/Cargo.toml", "native/welcome/src/main.rs",
       "native/welcome/src/native_stacking.rs", "spike/test_welcome.py"} <= public
      and not any(path.startswith("native/welcome/target/") for path in public),
      "the public allowlist includes exact native welcome sources but no build tree")
check({"native/brep_writer/main.cpp", "native/brep_writer/README.md",
       "scripts/build_brep_writer_source.sh", "scripts/verify_brep_writer.py"} <= public
      and not any(path.startswith("artifacts/brep-writer/") for path in public)
      and "narwhal3d/kernel/brep_writer.cpp" not in public,
      "the public allowlist includes bounded BRep helper source but no linked binary")
check({"native/document_core/Cargo.toml", "native/document_core/README.md",
       "native/document_core/src/main.rs", "native/document_core/src/container.rs",
       "native/document_core/src/sha256.rs", "native/document_core/tests/document_core.rs",
       "narwhal3d/native_document.py", "narwhal3d/narwhal_document.py",
       "narwhal3d/document_worker.py", "scripts/build_document_core.py", "scripts/verify_document_core.py",
       "spike/test_narwhal_document.py", "spike/test_document_core_artifacts.py"} <= public
      and not any(path.startswith("native/document_core/target/") for path in public),
      "the public allowlist includes native document source and exact round-trip tests")
check({"native/cdr_core/Cargo.toml", "native/cdr_core/src/lib.rs",
       "native/cdr_core/src/reader.rs", "native/converter_supervisor/Cargo.toml",
       "native/converter_supervisor/src/main.rs", "narwhal3d/cdr_reader.py",
       "narwhal3d/cdr_writer.py", "spike/test_cdr.py", "spike/test_native_cdr.py",
       "spike/test_converter_supervisor.py", "scripts/stage_fedora_inkscape.py",
       "native/vendor/tracedraw/core/Cargo.toml",
       "native/vendor/tracedraw/cdr/Cargo.toml",
       "native/vendor/tracedraw/LICENSE-MIT",
       "native/vendor/tracedraw/LICENSE-APACHE",
       "native/vendor/tracedraw/SOURCE_MANIFEST.json",
       "native/cdr_multiversion/Cargo.toml", "native/cdr_multiversion/src/main.rs",
       "narwhal3d/embedded_cdr.py", "spike/test_embedded_cdr.py",
       "licenses/TraceDraw-MIT.txt", "licenses/TraceDraw-Rust-Dependencies.txt"} <= public
      and not any("/target/" in f"/{path}/" for path in public),
      "the public allowlist includes native CDR helpers/vendored source and no build trees")

trace_root = ROOT / "native/vendor/tracedraw"
trace_manifest = json.loads((trace_root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
trace_files = {path.relative_to(trace_root).as_posix() for directory in (
    trace_root / "core/src", trace_root / "cdr/src") for path in directory.rglob("*")
    if path.is_file()} | {"LICENSE-MIT", "LICENSE-APACHE", "CLEANROOM.md"}
check(trace_manifest.get("revision") == "821ba1545e3d3f75843fe7d3f912214800929821"
      and set(trace_manifest.get("files", {})) == trace_files
      and all(hashlib.sha256((trace_root / path).read_bytes()).hexdigest() == digest
              for path, digest in trace_manifest["files"].items())
      and set(trace_manifest.get("adapted_files", {}))
          == {"core/Cargo.toml", "core/.gitignore", "cdr/Cargo.toml",
              "cdr/.gitignore", "README.md"}
      and all(hashlib.sha256((trace_root / path).read_bytes()).hexdigest() == digest
              for path, digest in trace_manifest["adapted_files"].items())
      and trace_manifest.get("dependency_notices_sha256")
          == hashlib.sha256((ROOT / "licenses/TraceDraw-Rust-Dependencies.txt").read_bytes()).hexdigest(),
      "vendored TraceDraw source matches its pinned cryptographic inventory")

helper_lock = tomllib.loads((ROOT / "native/cdr_multiversion/Cargo.lock").read_text(encoding="utf-8"))
helper_notices = (ROOT / "licenses/TraceDraw-Rust-Dependencies.txt").read_text(encoding="utf-8")
registry_packages = [(package["name"], package["version"]) for package in helper_lock["package"]
                     if str(package.get("source", "")).startswith("registry+")]
check(registry_packages and all(f"PACKAGE {name} {version}\n" in helper_notices
                                for name, version in registry_packages),
      "static multi-version helper notices cover every locked registry package")
check("POLICY.md" not in public and not any(path.startswith("artifacts/") for path in public),
      "instruction inputs and temporary planning artifacts stay outside public source")

legacy = re.compile(r"rhi" + r"no(?!3dm)", re.IGNORECASE)
compatibility = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
target_name = "Rhi" + "noceros 3D"
target_url = "https://www.rhi" + "no3d.com/"
check(compatibility.count(target_name) == 1 and target_url in compatibility
      and "independently developed" in compatibility
      and "not affiliated" in compatibility,
      "the compatibility target is identified once in its dedicated independence statement")
hits = []
for relative in sorted(public):
    path = ROOT / relative
    if legacy.search(relative):
        hits.append(relative)
        continue
    if path.suffix.lower() in {".png", ".gif", ".svg"} or relative == "COMPATIBILITY.md":
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    if legacy.search(text):
        hits.append(relative)
check(not hits, "legacy target naming is confined to COMPATIBILITY.md", hits[:10])
artifact_suite = subprocess.run(
    [sys.executable, str(ROOT / "spike/test_document_core_artifacts.py")],
    cwd=ROOT, text=True, capture_output=True, timeout=300)
check(artifact_suite.returncode == 0,
      "document-core artifact provenance and functionality suite passes",
      (artifact_suite.stdout + artifact_suite.stderr)[-2000:])

print(f"\n{checks - len(failures)}/{checks} public-project checks passed")
if failures:
    raise SystemExit(1)
