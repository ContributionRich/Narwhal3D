#!/usr/bin/env python3
"""Public repository organization, roadmap, issue forms, and asset policy checks."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tomllib
from urllib.parse import unquote, urlsplit
import zlib

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


def public_completeness_checks():
    """Offline source/fixture closure; never import native or GUI test modules."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_public_tree as audit
    import create_public_branch as builder

    public = set(builder.public_files())
    native = set()
    for crate in ("automation_core", "geometry_core"):
        base = ROOT / "native" / crate
        native.update(f"native/{crate}/{name}" for name in ("Cargo.toml", "Cargo.lock"))
        # Discovery is a completeness assertion, NOT a publication glob. New source
        # files still require an explicit reviewed addition to both manifests.
        for directory, suffixes in (("src", {".rs"}), ("cpp", {".cpp", ".inc"}),
                                    ("include", {".h"})):
            native.update(path.relative_to(ROOT).as_posix()
                          for path in (base / directory).rglob("*")
                          if path.is_file() and path.suffix in suffixes)
    native.add("native/geometry_core/build.rs")
    focused = {
        "spike/test_ai_workflow.py", "spike/test_ai_workflow_native.py",
        "spike/test_ai_cad.py", "spike/test_ai_cad_native.py",
        "spike/test_ai_box.py", "spike/test_ai_box_native.py",
        "spike/test_ai_ui_workflow.py", "spike/test_ai_workflow_transport.py",
        "spike/test_ai_ui_workflow_gui.py",
        "spike/test_ai_workspace.py", "spike/test_ai_workspace_native.py", "spike/test_ai_workspace_gui.py",
        "spike/test_ai_responses.py", "spike/test_ai_responses_native.py", "spike/test_ai_responses_settings.py",
        "spike/test_ui_v44_icons.py", "spike/test_ui_v44_panels.py", "spike/test_welcome_v44.py",
        "spike/test_pick_candidates.py", "spike/test_selection_menu.py",
        "spike/test_selection_menu_gui.py", "spike/test_ui_bundle.py",
        "spike/test_automation_context.py", "spike/test_automation_bridge.py",
        "spike/test_automation_host.py", "spike/test_automation_native.py",
        "spike/test_automation_mcp.py", "spike/test_automation_modern.py",
        "spike/test_automation_lifecycle.py", "spike/test_automation_live.py",
        "spike/test_automation_startup.py",
        "spike/test_sweep1_command_integration.py", "spike/test_sweep1_command_gui.py",
        "spike/test_sweep1_mouse_inputs.py", "spike/test_sweep1_interaction_checks.py", "spike/test_sweep1_interaction_gui.py",
        "spike/test_undo.py",
        "spike/test_surfacing_parallel.py", "spike/test_surfacing_probe.py",
        "spike/test_surfacing_integration.py", "spike/test_surfacing_freeform.py",
        "spike/test_surfacing_freeform_probe.py", "spike/test_surfacing_freeform_mutations.py",
        "spike/test_surfacing_interior.py", "spike/test_surfacing_step_roundtrip.py",
        "spike/test_surfacing_readiness_mutations.py", "spike/test_surfacing_preparation.py",
        "spike/test_surfacing_preparation_mutations.py",
        "spike/test_windows_release.py", "spike/test_windows_build.py",
        "scripts/validate_windows_icon.py",
    }
    pending = list(focused)
    while pending:
        relative = pending.pop()
        tree = ast.parse((ROOT / relative).read_bytes(), filename=relative)
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if name.startswith("test_"):
                    dependency = "spike/" + name.replace(".", "/") + ".py"
                    if dependency not in focused:
                        focused.add(dependency)
                        pending.append(dependency)
    import build_ui_bundle as bundle
    preview = {"scripts/build_ui_bundle.py", "scripts/ui_bundle_export.py",
               "scripts/ui_bundle_shots.py", "scripts/ui_bundle/test_preview.cjs",
               "scripts/ui_bundle/index.html", "docs/UI_DESIGNER_BUNDLE.md"}
    preview.update("scripts/ui_bundle/" + name for name in bundle.PLAYGROUND_FILES)
    preview_inputs = set(bundle.source_inventory()) | set(bundle.asset_inventory()) | preview
    check(preview_inputs <= public, "editable preview's complete source/asset inventory is public",
          sorted(preview_inputs - public))
    source_entries = {path: ("100644", (ROOT / path).read_bytes()) for path in public
                      if path.endswith(".sh") or path.startswith("spike/") and path.endswith(".py")}
    harness = audit.required_public_paths(source_entries) - audit.REQUIRED
    required = native | focused | preview | harness
    check(required <= public, "explicit public allowlist closes native and focused-test sources",
          sorted(required - public))
    audited = audit.required_public_paths(source_entries)
    check(required <= audited, "public audit requires the complete native/test/harness closure",
          sorted(required - audited))
    # Removing any member must produce a named failure, even if the other files
    # still contain that member's name (e.g. a driver references an absent fixture).
    entries = {path: ("100644", b"pass\n") for path in audited}
    entries.update((path, entry) for path, entry in source_entries.items() if path.endswith(".sh"))
    missing_checks = []
    for path in sorted(required):
        candidate = dict(entries)
        candidate.pop(path, None)
        if f"missing required public file: {path}" not in audit.audit_entries(candidate):
            missing_checks.append(path)
    check(not missing_checks, "every source/fixture omission mutation is rejected by name",
          missing_checks)
    check(not any(path.startswith(("docs/agent_tasks/", "docs/automation/", "docs/surfacing/",
                                  "tmp/", "artifacts/", "vendor/", "tools/"))
                  or "/target/" in path or path.endswith((".so", ".dll", ".exe", ".pyc"))
                  for path in public),
          "native closure does not admit private notes, scratch data or runtime binaries")
    # Exercise source references that are NOT already in either static manifest.
    # This prevents a new conditional suite or transitive fixture from escaping
    # merely because the harness audit and this caller share discovery code.
    fixture = {
        "run_tests.sh": ("100755", b'if test -n "DISPLAY"; then\n bash spike/closure_gui.sh\nfi\n'),
        "spike/closure_gui.sh": ("100755", b'python3 spike/test_closure_case.py\n'),
        "spike/test_closure_case.py": ("100644", b'import test_closure_fixture\n'),
        "spike/test_closure_fixture.py": ("100644", b'pass\n'),
    }
    found = audit.required_public_paths(fixture)
    check(set(fixture) <= found, "conditional shell suite and imported fixture are required")
    for path in list(fixture)[1:]:
        candidate = dict(fixture)
        candidate.pop(path)
        check(f"missing required public file: {path}" in audit.audit_entries(candidate),
              f"harness dependency omission is rejected: {path}")
    commented = {"run_tests.sh": ("100755", b'# python3 spike/test_not_executed.py\n')}
    check("spike/test_not_executed.py" not in audit.required_public_paths(commented),
          "comment-only references do not admit historical fixtures")
    reference = "scripts/ui_bundle/references/workspace-light.png"
    check(audit.path_problem(reference) is None
          and audit.path_problem(reference.replace("workspace-light", "unreviewed")) is not None,
          "only the exact reviewed preview screenshot is permitted")
    for name, digest in {
        "Inter.woff2": "fb865a5087637ba194b14aef6f0558214f3c4b3ec939e3c0812c66de41036a47",
        "Ubuntu-Light.ttf": "80307b8da7649aa4ee4d484b232140e3ce1ec0ca093073d3c53c8f5a5ced7a70",
        "OFL-1.1.txt": "c1a781a6a032bf00b1bbffc388fd6936bf28a0ded472c263bcff88b181dea456",
        "UFL.txt": "2f0015108d68627bd788d313f529c21ff4da2c2c42a5e1f3883acc83480f9002",
    }.items():
        check(hashlib.sha256((ROOT / "scripts/ui_bundle/fonts" / name).read_bytes()).hexdigest() == digest,
              f"preview font/license bytes are unmodified: {name}")
    return required


if "--completeness-only" in sys.argv:
    public_completeness_checks()
    print(f"\n{checks - len(failures)}/{checks} public completeness checks passed")
    raise SystemExit(bool(failures))


required_docs = (
    "README.md", "STATUS.md", "COMPATIBILITY.md", "ROADMAP.md", "CHANGELOG.md", "CONTRIBUTING.md",
    "SECURITY.md", "SUPPORT.md", "GOVERNANCE.md", "CODE_OF_CONDUCT.md",
    "docs/gaps.md", "docs/github-project.md", "docs/windows-build.md", "docs/macos-build.md",
    "docs/technical-reference.md",
)
check(all((ROOT / path).is_file() for path in required_docs),
      "the public project and governance document set is complete")


def markdown_targets(text, *, images=False):
    """Extract inline/reference destinations, not examples inside fenced code."""
    text = re.sub(r"```.*?```|~~~.*?~~~|<!--.*?-->", "", text, flags=re.S)
    definitions = dict(re.findall(r"^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?", text, re.M))
    prefix = r"!" if images else r"(?<!!)"
    inline = re.findall(prefix + r"\[[^\]\n]+\]\(<?([^\s)>]+)>?(?:\s+\"[^\"]*\")?\)", text)
    references = re.findall(prefix + r"\[([^\]\n]+)\]\[([^\]\n]*)\]", text)
    return set(inline) | {definitions[key or label] for label, key in references
                          if (key or label) in definitions}


def markdown_anchors(text):
    """GitHub heading fragments for the public guides, including duplicate headings."""
    anchors = set()
    text = re.sub(r"```.*?```|~~~.*?~~~|<!--.*?-->", "", text, flags=re.S)
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        slug, suffix = base, 0
        while slug in anchors:
            suffix += 1
            slug = f"{base}-{suffix}"
        anchors.add(slug)
    return anchors


def screenshot_data(path):
    """Validate the lossless, non-interlaced RGB(A) PNGs used for UI captures.

    Check CRCs and the complete decompressed scanlines, not just an IHDR that
    could belong to an otherwise truncated or unreadable image. No Pillow or
    Blender dependency is needed by this public-source documentation test.
    """
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    offset, chunks = 8, []
    while offset < len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[offset + 8 + size:offset + 12 + size])[0]
        if zlib.crc32(kind + payload) != crc:
            raise ValueError("PNG chunk CRC mismatch")
        chunks.append((kind, payload))
        offset += size + 12
    if chunks[0][0] != b"IHDR" or chunks[-1] != (b"IEND", b""):
        raise ValueError("missing PNG header/end")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1])
    if not (width > 0 and height > 0 and depth == 8 and colour in (2, 6)
            and compression == filtering == interlace == 0):
        raise ValueError("expected a non-interlaced 8-bit RGB/RGBA capture")
    decoder = zlib.decompressobj()
    stride = width * (3 if colour == 2 else 4) + 1
    expected = height * stride
    pixels = decoder.decompress(b"".join(chunk for kind, chunk in chunks if kind == b"IDAT"),
                                expected + 1)
    if (len(pixels) != expected or not decoder.eof or decoder.unused_data
            or any(pixels[row] > 4 for row in range(0, expected, stride))):
        raise ValueError("invalid PNG scanlines")
    return (width, height), hashlib.sha256(pixels).digest()


readme = (ROOT / "README.md").read_text(encoding="utf-8")
reference_path = ROOT / "docs/technical-reference.md"
technical_reference = reference_path.read_text(encoding="utf-8")
readme_links = markdown_targets(readme)
readme_images = markdown_targets(readme, images=True)
reference_links = markdown_targets(technical_reference)
reference_images = markdown_targets(technical_reference, images=True)
for link in ("docs/technical-reference.md", "CONTRIBUTING.md", "LICENSE"):
    check(link in readme_links, f"README has a clickable {link} link")
check(len(readme.splitlines()) < 150, "README remains a short landing page (under 150 lines)")
check("GPL-3.0-or-later" in readme, "README identifies the project GPL license")
check(re.search(r"early\s+(?:preview|prerelease|pre-release)", readme, re.I),
      "README warns that the application is an early preview")
check(re.search(r"View\W+Theme\b", readme), "README explains the View → Theme switch")
for asset in ("Narwhal_3D-0.4.7-x86_64.AppImage", "Narwhal_3D-0.4.7-Windows-x86_64.zip"):
    check(f"https://github.com/ContributionRich/Narwhal3D/releases/download/v0.4.7/{asset}"
          in readme_links, f"README offers a direct Markdown download for {asset}")
check(not any("/releases/download/" in link and "/v0.4.7/" not in link
              for link in readme_links), "README has no stale release download targets")
check("docs/readme-command-loft.gif" not in readme_images,
      "README uses the workspace screenshots rather than the old command GIF")
screenshots = ("docs/images/workspace-light.png", "docs/images/workspace-dark.png")
image_data = []
for image in screenshots:
    check(image in readme_images, f"README embeds {image}")
    try:
        dimensions, pixels_digest = screenshot_data(ROOT / image)
        check(dimensions[0] >= 1200, f"{image} is at least 1200 pixels wide", dimensions)
        image_data.append((dimensions, pixels_digest))
    except (OSError, ValueError, IndexError, struct.error, zlib.error) as exc:
        check(False, f"{image} is a readable screenshot PNG", str(exc))
check(len(image_data) == 2 and image_data[0][0] == image_data[1][0],
      "light/dark screenshots have the same frame dimensions")
check(len(image_data) == 2 and image_data[0][1] != image_data[1][1],
      "light/dark screenshots have different image data, not just metadata")

for link in ("../STATUS.md", "../COMPATIBILITY.md", "../ROADMAP.md", "gaps.md",
             "../CONTRIBUTING.md", "../SUPPORT.md", "../SECURITY.md", "images/bugreport.png",
             "windows-build.md", "macos-build.md"):
    check(link in reference_links | reference_images, f"technical reference links {link}")
check("**Release: 0.4.7.**" in technical_reference
      and "chmod +x Narwhal_3D-0.4.7-x86_64.AppImage" in technical_reference
      and "./Narwhal_3D-0.4.7-x86_64.AppImage" in technical_reference
      and "Narwhal_3D-0.4.7-Windows-x86_64.zip" in technical_reference
      and "native startup welcome window" in technical_reference,
      "technical reference identifies 0.4.7 and gives matching Linux/Windows asset names")
check("--version 0.4.7" in technical_reference
      and "dist/Narwhal_3D-0.4.7-x86_64.AppImage" in technical_reference
      and "--expected-version 0.4.7" in technical_reference
      and "The examples below build **0.4.7**" in technical_reference
      and "--version 0.4.6" not in technical_reference
      and "--expected-version 0.4.6" not in technical_reference,
      "technical reference assembly and verification examples target release 0.4.7")
check(all(term in technical_reference for term in (
    "document geometry separate from viewport tessellation",
    "Triangles generated for shaded display are not treated as manufacturing geometry",
    "STEP export refuses geometry that has no exact analytic representation",
    "exact checksum-protected OCCT BReps for every analytic master",
    "some OCCT-created multi-face solids cannot be encoded as exact `.3dm` Breps",
    "It never presents a faceted STEP fallback as exact geometry",
)), "technical reference retains exact-geometry guarantees and packaged interchange limitations")
check(all(term in technical_reference for term in (
    "GPL-3.0-or-later", "THIRD_PARTY_NOTICES", "separately licensed open-source components",
)), "technical reference retains project and third-party license information")
status = (ROOT / "STATUS.md").read_text(encoding="utf-8")
check("**Release: 0.4.7.**" in status
      and "Narwhal_3D-0.4.7-x86_64.AppImage" in status
      and "Narwhal_3D-0.4.7-Windows-x86_64.zip" in status
      and "Final Linux source release gates, including the full suite" in status
      and "AppDir/extracted-AppImage verification passed" in status
      and "Package inventory and archive digest were unchanged after acceptance" in status,
      "STATUS identifies 0.4.7 and records completed frozen-candidate Linux gates")
packaging = (ROOT / "packaging/README.md").read_text(encoding="utf-8")
check("this release is `0.4.7`" in packaging
      and "Narwhal_3D-0.4.7-x86_64.AppImage" in packaging
      and "--version 0.4.7" in packaging
      and "--expected-version 0.4.7" in packaging
      and "frozen 0.4.7 shipping candidate passed the final sanitized-source and asset gates" in packaging
      and "Future candidates still require their own gates" in packaging
      and "does not claim byte-for-byte native build reproducibility" in packaging,
      "packaging guide pins 0.4.7 and distinguishes completed gates from future builds")
changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
release_notes = changelog.split("## 0.4.6 —", 1)[0]
check("## 0.4.7 — 2026-10-27\n" in release_notes
      and "Narwhal_3D-0.4.7-x86_64.AppImage" in release_notes
      and "Narwhal_3D-0.4.7-Windows-x86_64.zip" in release_notes
      and "## 0.4.6 — 2026-10-24\n" in changelog
      and "5a40d6d1eee16b26f4e6b5714862c711cafad797" in release_notes,
      "changelog dates release 0.4.7 and preserves historical 0.4.6 provenance")
check(all(term in release_notes for term in (
    "Loft", "SrfPt", "CageEdit", "Segment/Edge", "FilletEdge",
    "Theme and visibility preferences", "tooltips", "light/dark palette",
    "status bar", "Gumball", "window-level overlay", "CPlane grids",
    "layer colour picker", "all six geometry gates",
    "Gumball-created curve extrusions now scale correctly after being displayed",
    "detached exact master without inherited display triangulation",
    "Final Linux source release gates, including the full suite",
    "AppDir/extracted-AppImage verification passed",
    "clean-copy and overlay mutations went RED",
)), "0.4.7 release notes retain feature, fix and validation coverage")
for name, text in (("technical reference", technical_reference), ("STATUS", status),
                   ("packaging guide", packaging), ("changelog", release_notes)):
    lower = text.lower()
    check("0.4.7 appimage has been built and its inventory verified" in lower
          and "290 bundled elfs" in lower and "glibc 2.28" in lower
          and "glibc 2.35" in lower and "glibc 2.43" in lower
          and "must not ship" in lower,
          f"{name} records measured Linux portability and excludes development binaries")
welcome_manifest = tomllib.loads((ROOT / "native/welcome/Cargo.toml").read_text(encoding="utf-8"))
welcome_lock = tomllib.loads((ROOT / "native/welcome/Cargo.lock").read_text(encoding="utf-8"))
welcome_source = (ROOT / "native/welcome/src/main.rs").read_text(encoding="utf-8")
check(welcome_manifest["package"]["version"] == "0.4.7"
      and [package["version"] for package in welcome_lock["package"]
           if package["name"] == "narwhal-welcome"] == ["0.4.7"]
      and 'assert_eq!(env!("CARGO_PKG_VERSION"), "0.4.7");' in welcome_source,
      "welcome manifest, lock and Rust version assertion agree on 0.4.7")
check("extremely early prerelease" in technical_reference
      and "frozen on the `0.4.x` line" in technical_reference,
      "technical reference pins the owner-required early-prerelease 0.4.x release line")

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
form_text = {path.name: path.read_text(encoding="utf-8") for path in forms}
cross_platform_forms = ("bug.yml", "ui.yml", "performance.yml", "import-export.yml")
check(all("AppImage" in form_text[name] and "Windows portable ZIP" in form_text[name]
          and "macOS source build" in form_text[name]
          for name in cross_platform_forms)
      and "0.2.0 AppImage" not in form_text["bug.yml"],
      "runtime issue forms accept Linux, Windows, macOS-source, and source reports")
windows_guide = (ROOT / "docs/windows-build.md").read_text(encoding="utf-8")
check("0.4.7 Windows x86_64 portable ZIP" in windows_guide
      and "Narwhal_3D-0.4.7-Windows-x86_64.zip" in windows_guide
      and "native startup welcome window" in windows_guide
      and "-NarwhalRevision $Revision" in windows_guide
      and "-Version 0.4.7" in windows_guide
      and "-Version 0.4.6" not in windows_guide
      and "## Platform limitations" in windows_guide
      and "shipping 0.4.7 ZIP passed those gates" in windows_guide
      and "not a claim of a fresh full runtime compile" in windows_guide
      and "7,705-file package inventory restored" in windows_guide
      and "Neither driver DLL is in the shipping ZIP" in windows_guide
      and "that result does not certify a newly assembled candidate" in windows_guide,
      "Windows guide records verified runtime reuse, acceptance and exact inventory restoration")
for name, text in (("technical reference", technical_reference), ("STATUS", status),
                   ("Windows guide", windows_guide), ("changelog", release_notes)):
    lower = text.lower()
    check("engineering build completed using a verified, unchanged native runtime" in lower
          and "directory and zip verifiers passed" in lower
          and "actual extracted-final-zip winboat acceptance passed" in lower
          and "all six representation gates" in lower
          and "unsigned" in lower and "portable" in lower
          and "early preview" in lower.replace("**", "")
          and "physical-gpu acceptance" in lower
          and "file associations" in lower,
          f"{name} records independent Windows acceptance without dropping preview limitations")
    check("temporary mesa llvmpipe software rendering" in lower
          and "not physical-gpu certification" in lower.replace("**", "")
          and "temporary driver was removed" in lower
          and "package inventory restored" in lower,
          f"{name} distinguishes real VM GUI acceptance from physical-GPU certification")
for name, text in (("technical reference", technical_reference), ("STATUS", status),
                   ("packaging guide", packaging), ("changelog", release_notes)):
    lower = text.lower()
    check("pristine ubuntu-desktop" in lower or "pristine ubuntu desktop" in lower,
          f"{name} retains the untested clean-Ubuntu limitation")
    check("optional gpu-backend" in lower or "optional gpu backend" in lower,
          f"{name} retains optional GPU-backend limitations")
    check("unsigned" in lower and "full suite" in lower,
          f"{name} records full Linux source gates without implying a signed AppImage")
for name, text, size, digest in (
    ("technical reference Linux", technical_reference, "437,832,184", "6097aa60cdc8702e59370f83b66d13a6de42da2c3b10c0a4cc3cded753cd67a9"),
    ("technical reference Windows", technical_reference, "355,197,176", "23015f7338358f7fd62fcabada325bd615d06f0eb9f0898cac3279d0eb638290"),
    ("packaging guide", packaging, "437,832,184", "6097aa60cdc8702e59370f83b66d13a6de42da2c3b10c0a4cc3cded753cd67a9"),
    ("Windows guide", windows_guide, "355,197,176", "23015f7338358f7fd62fcabada325bd615d06f0eb9f0898cac3279d0eb638290"),
):
    check(size in text and f"`{digest}`" in text,
          f"{name} pins the shipping asset size and SHA-256")
for name, text in (("technical reference", technical_reference), ("STATUS", status), ("changelog", release_notes)):
    check(all(term in text for term in ("24/24", "184/194", "112/112")),
          f"{name} records actual Windows Gumball, preferences and overlay counts")
check(all(term in windows_guide for term in (
    "24/24", "184 checks", "194 checks", "112/112", "47/47", "28/28",
    "Mesa 26.2.4 llvmpipe", "OpenGL 4.6", "fresh-process write/reopen",
)), "Windows guide retains the measured extracted-package acceptance detail")
for name, text in (("technical reference", technical_reference), ("STATUS", status),
                   ("packaging guide", packaging), ("Windows guide", windows_guide),
                   ("changelog", release_notes)):
    check(not re.search(r"Narwhal_3D-0\.4\.6-(?:x86_64\.AppImage|Windows-x86_64\.zip)", text)
          and "0.4.7 is in preparation" not in text
          and "0.4.7` is in preparation" not in text
          and "0.4.7 in preparation" not in text
          and "0.4.7 — pending release" not in text
          and "no 0.4.7 assets are available yet" not in text.lower()
          and not any(stale in text.lower() for stale in (
              "full windows compile and extracted-zip validation are pending",
              "no new windows validation pass is claimed",
              "full tests are still required after the metadata freeze",
              "publication waits for", "remain publication gates after the metadata freeze",
              "publishing 0.4.7 remains gated", "physical-gpu/real-window acceptance",
          )),
          f"{name} has no stale asset filenames or pending-release framing")
    check("94534c954dc77f1f1d22f2be6064bedcc8cd1f5b" in text
          and "1793144099" in text
          and "asset-build revision" in text
          and "documentation follow-up" in text,
          f"{name} separates frozen asset source/epoch from the documentation follow-up")
check((ROOT / ".github/PULL_REQUEST_TEMPLATE.md").is_file()
      and (ROOT / ".github/CODEOWNERS").is_file(),
      "pull-request guidance and review ownership are present")

spec = importlib.util.spec_from_file_location(
    "public_branch", ROOT / "scripts/create_public_branch.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
public = set(module.public_files())
public_completeness_checks()
check(set(screenshots) | {"docs/technical-reference.md"} <= public,
      "the public allowlist includes both README screenshots and the technical reference")
for document, targets in ((ROOT / "README.md", readme_links | readme_images),
                          (reference_path, reference_links | reference_images),
                          (ROOT / "STATUS.md", markdown_targets(status)),
                          (ROOT / "CHANGELOG.md", markdown_targets(changelog)),
                          (ROOT / "packaging/README.md", markdown_targets(packaging))):
    for target in sorted(targets):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        resolved = (document.parent / unquote(url.path)).resolve() if url.path else document
        relative = resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else ""
        check(resolved.is_file() and relative in public,
              f"{document.name} local link resolves to a public source file: {target}")
        if url.fragment and resolved.is_file() and resolved.suffix == ".md":
            check(unquote(url.fragment) in markdown_anchors(resolved.read_text(encoding="utf-8")),
                  f"{document.relative_to(ROOT)} local link fragment exists: {target}")
check(set(required_docs) <= public and "docs/images/bugreport.png" in public
      and "scripts/collect_windows_build_info.ps1" in public
      and "scripts/build_windows_preview.ps1" in public
      and "scripts/assemble_windows_release.py" in public
      and "scripts/verify_windows_release.py" in public
      and "scripts/release_policy.py" in public
      and "spike/test_windows_collector.py" in public
      and "spike/test_windows_release.py" in public
      and "spike/test_linux_release.py" in public
      and "spike/test_package_completeness.py" in public
      and "spike/test_release_policy.py" in public
      and "docs/macos-build.md" in public
      and "scripts/build_macos_source.sh" in public
      and "spike/test_macos_source.py" in public,
      "the public allowlist includes platform build guides and their focused gates")
check({"spike/test_picture.py", "spike/test_cmdbar_hits.py",
       "spike/test_extrude_distance.py", "spike/test_extrude_options.py",
       "spike/test_picture_display.py", "spike/test_gumball_region_extrude.py",
       "spike/test_bug102_live.py", "spike/test_local_move_scale.py",
       "spike/test_bug103_curved_move.py", "spike/test_bug104_subobject_move.py",
       "spike/test_bug105.py", "spike/test_bug106.py"} <= public,
      "the public allowlist includes regressions for interaction, exact local moves, and scaling")
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
check({"native/pure_core/Cargo.toml", "native/pure_core/README.md",
       "native/pure_core/src/lib.rs", "native/pure_core/src/stl.rs",
       "native/pure_core/src/obj.rs", "native/pure_core/src/dxf.rs",
       "native/pure_core/src/numparse.rs", "native/pure_core/src/calc.rs",
       "native/pure_core/src/bearing.rs", "native/pure_core/src/coords.rs",
       "native/pure_core/src/macro_parse.rs", "native/pure_core/src/macro_plan.rs",
       "native/pure_core/src/svgpath.rs", "native/pure_core/src/joinchain.rs",
       "native/pure_core/src/vector_export.rs", "native/pure_core/src/command_search.rs",
       "native/pure_core/src/coil.rs", "native/pure_core/src/interpolate.rs",
       "native/pure_core/src/tween.rs", "native/pure_core/src/surface.rs",
       "native/pure_core/src/cage.rs",
       "native/pure_core/src/gridsnap.rs",
       "scripts/check_pure_core.py", "spike/test_pure_core_gate.py",
       "spike/test_native_stl.py",
       "spike/test_native_obj.py", "spike/test_native_dxf.py",
       "spike/test_native_joinchain.py", "spike/test_native_vector_export.py",
       "spike/test_native_command_search.py", "spike/test_native_coil.py",
       "spike/test_native_coil_preview.py",
       "spike/test_native_interpolate.py", "spike/test_native_tween.py",
       "spike/test_curve_options.py", "spike/test_helix.py",
       "spike/test_spiral.py", "spike/test_coil_preview.py",
       "spike/test_curve_through_polyline.py",
       "spike/test_conic.py",
       "spike/test_parabola.py", "spike/test_parabola3pt.py",
       "spike/test_hyperbola.py", "spike/test_catenary.py",
       "spike/test_curve_through_pt.py", "spike/test_tween_curves.py",
       "spike/test_handlecurve.py", "spike/test_curve_through_srf_control_pt.py",
       "spike/test_sequential_object_steps.py", "spike/test_native_gridsnap.py"} <= public
      and not any(path.startswith("native/pure_core/target/") for path in public),
      "the public allowlist includes pure-core source and never-slower route gates")
check({"native/geometry_core/Cargo.toml", "native/geometry_core/build.rs",
       "native/geometry_core/cpp/occt_adapter.cpp",
       "native/geometry_core/include/narwhal_geometry_core.h",
       "native/geometry_core/src/height_field.rs",
       "native/geometry_core/src/lib.rs", "narwhal3d/native_geometry.py",
       "spike/test_native_geometry.py", "spike/test_local_brep_move.py",
       "native/platform_core/Cargo.toml",
       "native/platform_core/include/narwhal_platform_core.h",
       "native/platform_core/src/lib.rs", "narwhal3d/native_platform.py",
       "spike/test_native_platform.py"} <= public
      and not any(path.startswith(("native/geometry_core/target/",
                                   "native/platform_core/target/")) for path in public),
      "the public allowlist includes cross-platform geometry and dialog native source only")
check({"spike/test_ai_assistant.py", "spike/test_ai_transport.py"} <= public,
      "the public allowlist includes bounded AI Assistant state and transport tests")
check({"native/selection_core/Cargo.toml", "native/selection_core/src/lib.rs",
       "scripts/build_selection_core.py", "scripts/verify_selection_core.py",
       "spike/test_rust_selection_core.py", "spike/test_native_selection.py",
       "spike/test_selection_core_packaging.py"} <= public
      and not any(path.startswith("native/selection_core/target/") for path in public),
      "the public allowlist includes selection-core source, verifier and tests")
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
