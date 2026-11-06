#!/usr/bin/env python3
"""Pure Windows launcher and portable-release verifier regression checks."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re
import runpy
import shutil
import struct
import subprocess
import types
import zlib
from pathlib import Path
import sys
import tempfile
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_windows_icon import RESOLUTIONS, PNG_SIGNATURE, decode_rgba_png, validate_icon
spec = importlib.util.spec_from_file_location(
    "windows_verifier", ROOT / "scripts" / "verify_windows_release.py")
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
assembler_spec = importlib.util.spec_from_file_location(
    "windows_assembler", ROOT / "scripts" / "assemble_windows_release.py")
assert assembler_spec is not None and assembler_spec.loader is not None
assembler = importlib.util.module_from_spec(assembler_spec)
assembler_spec.loader.exec_module(assembler)

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


launcher = (ROOT / "packaging/windows/narwhal3d_launcher.c").read_text(encoding="utf-8")
resource = (ROOT / "packaging/windows/narwhal3d_launcher.rc.in").read_text(encoding="utf-8")
icon = (ROOT / "packaging/windows/narwhal3d.ico").read_bytes()
multiversion_source = (ROOT / "native/cdr_multiversion/src/main.rs").read_text(encoding="utf-8")
document_core_source = (ROOT / "native/document_core/src/lib.rs").read_text(encoding="utf-8")
build_script = (ROOT / "scripts/build_windows_preview.ps1").read_text(encoding="utf-8")

check("CreateProcessW" in launcher and "runtime\\\\blender.exe" in launcher
      and "app\\\\launch\\\\bootstrap.py" in launcher
      and "narwhal-welcome.exe" in launcher
      and "launch_program = welcome" in launcher
      and 'L"--runtime"' in launcher and 'L"--bootstrap"' in launcher,
      "native launcher routes normal startup through adjacent welcome/runtime/bootstrap")
check("CommandLineToArgvW" in launcher and "append_argument" in launcher
      and "for (int i = 1; i < argc; ++i)" in launcher,
      "native launcher preserves quoted Unicode user file arguments")
check('wcscmp(test_mode, L"headless")' in launcher
      and "launch_program = runtime" in launcher,
      "headless/version acceptance routes remain direct and deterministic")
check("NARWHAL3D_PACKAGE_FORMAT" in launcher and "windows-portable" in launcher
      and "NARWHAL3D_WINDOWS" in launcher
      and '#define NARWHAL3D_VERSION L"development"' in launcher
      and 'SetEnvironmentVariableW(L"NARWHAL3D_VERSION", NARWHAL3D_VERSION)' in launcher
      and 'SetEnvironmentVariableW(L"NARWHAL3D_PACKAGE_PATH", root)' in launcher,
      "launcher identifies the portable Windows environment and exact update target")
check("NARWHAL3D_LAUNCHER_TEST" in launcher
      and 'wcscmp(test_mode, L"headless")' in launcher,
      "launcher has an environment-only packaged-bootstrap test route")
check("requestedExecutionLevel level='asInvoker'" in resource
      and "longPathAware" in resource and "dpiAware" in resource,
      "launcher manifest requests no elevation and opts into DPI/long paths")
check("Narwhal 3D early prerelease launcher" in resource
      and "OriginalFilename\", \"Narwhal3D.exe" in resource
      and resource.count("__VERSION_COMMA__") == 2
      and resource.count("__VERSION__ prerelease") == 2,
      "launcher resource carries independent identity and rendered-version placeholders")
check(validate_icon(icon) == RESOLUTIONS,
      "Windows ICO decodes seven RGBA resolutions with visible artwork and antialiased transparency")


def rejected_icon(candidate, reason, label):
    try:
        validate_icon(candidate)
    except ValueError as exc:
        check(reason in str(exc), f"ICO mutation RED: {label}")
    else:
        check(False, f"ICO mutation escaped validation: {label}")


def directory_mutation(offset, fmt, value):
    candidate = bytearray(icon)
    struct.pack_into(fmt, candidate, offset, value)
    return bytes(candidate)


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload)))


def png_image(size, raw, *, header=None, compressed=None):
    return (PNG_SIGNATURE
            + png_chunk(b"IHDR", header or struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(raw) if compressed is None else compressed)
            + png_chunk(b"IEND", b""))


frames = []
for index in range(len(RESOLUTIONS)):
    length, offset = struct.unpack_from("<II", icon, 14 + index * 16)
    frames.append(icon[offset:offset + length])


def replace_frame(index, image):
    images = list(frames)
    images[index] = image
    offset = 6 + 16 * len(images)
    directory = bytearray(icon[:offset])
    for i, frame in enumerate(images):
        struct.pack_into("<II", directory, 14 + i * 16, len(frame), offset)
        offset += len(frame)
    return bytes(directory) + b"".join(images)


for offset, fmt, value, reason, label in (
    (0, "<H", 1, "header", "reserved header"),
    (2, "<H", 2, "header", "cursor instead of icon"),
    (4, "<H", 1, "header", "missing resolutions"),
    (22, "B", 16, "directory entry", "duplicate/mismatched resolution"),
    (6, "B", 20, "directory entry", "unsupported resolution"),
    (8, "B", 1, "directory entry", "palette metadata"),
    (9, "B", 1, "directory entry", "reserved entry"),
    (10, "<H", 2, "directory entry", "invalid planes"),
    (12, "<H", 24, "directory entry", "lost RGBA depth"),
    (14, "<I", len(icon), "outside payload", "out-of-bounds image length"),
    (18, "<I", 0, "outside payload", "image inside directory"),
    (34, "<I", 118, "overlap", "overlapping images"),
):
    rejected_icon(directory_mutation(offset, fmt, value), reason, label)
rejected_icon(icon[:100], "truncated ICO directory", "truncated directory")
rejected_icon(icon[:-1], "outside payload", "truncated last image")
rejected_icon(icon + b"padding", "trailing bytes", "unreferenced trailing data")

for index, size in enumerate(RESOLUTIONS):
    png = frames[index]
    pixels = decode_rgba_png(png, size)
    raw = b"".join(b"\0" + pixels[row:row + size * 4]
                   for row in range(0, len(pixels), size * 4))
    broken_crc = bytearray(png)
    broken_crc[29] ^= 1
    rejected_icon(replace_frame(index, bytes(broken_crc)), "CRC", f"{size}px PNG CRC")
    for alpha, label in ((255, "opaque"), (0, "invisible"), (128, "no opaque artwork")):
        changed = bytearray(pixels)
        changed[3::4] = bytes([alpha]) * (size * size)
        scanlines = b"".join(b"\0" + changed[row:row + size * 4]
                             for row in range(0, len(changed), size * 4))
        rejected_icon(replace_frame(index, png_image(size, scanlines)), "alpha",
                      f"{size}px {label} with valid PNG CRC/zlib")
    binary_alpha = bytearray(pixels)
    binary_alpha[3::4] = bytes(255 if a else 0 for a in pixels[3::4])
    binary_rows = b"".join(b"\0" + binary_alpha[row:row + size * 4]
                           for row in range(0, len(binary_alpha), size * 4))
    rejected_icon(replace_frame(index, png_image(size, binary_rows)), "alpha",
                  f"{size}px antialiasing lost")
    flat = bytearray(pixels)
    for i in range(0, len(flat), 4):
        flat[i:i + 3] = b"\x42\x42\x42"
    flat_rows = b"".join(b"\0" + flat[row:row + size * 4]
                         for row in range(0, len(flat), size * 4))
    rejected_icon(replace_frame(index, png_image(size, flat_rows)), "colour detail",
                  f"{size}px visible colour detail lost")
    check(validate_icon(replace_frame(index, png_image(size, raw))) == RESOLUTIONS,
          f"ICO restored GREEN: {size}px fully decoded/re-encoded pixels")

# Checksummed corruptions must reach decoding, not merely fail the CRC guard.
size = RESOLUTIONS[0]
pixels = decode_rgba_png(frames[0], size)
raw = b"".join(b"\0" + pixels[row:row + size * 4]
               for row in range(0, len(pixels), size * 4))
for image, reason, label in (
    (png_image(size, raw, header=struct.pack(">IIBBBBB", 32, 32, 8, 6, 0, 0, 0)),
     "dimensions/format", "PNG/directory disagreement"),
    (png_image(size, raw, header=struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)),
     "dimensions/format", "RGB instead of RGBA"),
    (png_image(size, raw, compressed=b"not zlib"), "compressed stream", "invalid zlib"),
    (png_image(size, raw, compressed=zlib.compress(raw)[:-1]), "decoded length/end", "zlib truncation"),
    (png_image(size, raw, compressed=zlib.compress(raw) + zlib.compress(raw)),
     "decoded length/end", "extra compressed stream"),
    (png_image(size, raw[:-1]), "decoded length/end", "short decoded image"),
    (png_image(size, raw + b"\0"), "decoded length/end", "oversized decoded image"),
    (png_image(size, b"\5" + raw[1:]), "scanline filter", "invalid decoded filter"),
    (frames[0][:-12], "terminal IEND", "missing PNG end"),
    (frames[0] + png_chunk(b"IEND", b""), "terminal IEND", "duplicate PNG end"),
):
    rejected_icon(replace_frame(0, image), reason, label)

# Independent fixture encodings exercise every inverse filter (especially alpha:
# filtered alpha bytes are not the actual opacity). No asset files are rewritten.
fixture = bytes((i * 37 + i // 9) % 256 for i in range(16 * 16 * 4))
for filtering in range(5):
    rows = bytearray()
    for y in range(16):
        rows.append(filtering)
        for x in range(64):
            i = y * 64 + x
            a = fixture[i - 4] if x >= 4 else 0
            b = fixture[i - 64] if y else 0
            c = fixture[i - 68] if y and x >= 4 else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            paeth = a if pa <= pb and pa <= pc else b if pb <= pc else c
            predictor = (0, a, b, (a + b) // 2, paeth)[filtering]
            rows.append((fixture[i] - predictor) % 256)
    check(decode_rgba_png(png_image(16, rows), 16) == fixture,
          f"PNG filter {filtering} reconstructs every known RGBA pixel")
check(validate_icon(icon) == RESOLUTIONS, "original ICO restored GREEN after all in-memory mutations")
check(verifier.CDR_MULTIVERSION_PROTOCOL in multiversion_source,
      "Windows verifier pins the bundled multi-version CDR helper protocol")
check(verifier.DOCUMENT_CORE_PROTOCOL in document_core_source,
      "Windows verifier pins the bundled Narwhal document-core protocol")
check("[Parameter(Mandatory=$true)]" in build_script
      and "[ValidatePattern('^[0-9a-f]{40}$')]" in build_script
      and "[string]$NarwhalRevision" in build_script
      and '$NarwhalRevision = "' not in build_script
      and "[string]$Version = \"0.4.10\"" in build_script
      and "[ValidatePattern('^0\\.4\\.[0-9]+$')]" in build_script,
      "Windows build requires immutable source and a validated 0.4.x version")
check("native\\converter_supervisor\\Cargo.toml" in build_script
      and '"--memory-mib", "64"' in build_script
      and "narwhal-converter-supervisor.exe" in build_script
      and "run packaged native-document Save/Open round trip" in build_script,
      "Windows build packages a memory-bounded supervisor and proves native Save/Open")
check("b8f597c677811d1f9f4d8a97f5ae2825c0353a42" in build_script
      and "native\\geometry_core\\Cargo.toml" in build_script
      and "build exact geometry core with pinned OCCT and MSVC" in build_script
      and "narwhal_geometry_core.dll" in build_script
      and '"geometry_move": "airtight"' in build_script,
      "Windows build compiles, packages and executes exact Rust geometry against pinned OCCT")

OBSERVER_PHASE_MARKER = "# Document observer: qualified here"
OBSERVER_SAVE_OPEN = '$PackagedDocumentTest = Join-Path'
OBSERVER_REMOVE = "Remove-Item -LiteralPath $PackagedObserverDirectory -Recurse -Force\n"
OBSERVER_SDIST_SHA256 = "8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a"


def observer_phase_ok(script: str) -> bool:
    """Structural contract for the document-observer phase; every observer_mutants() case must fail it."""
    if script.count(OBSERVER_PHASE_MARKER) != 1 or script.count(OBSERVER_SAVE_OPEN) != 1:
        return False
    start = script.index(OBSERVER_PHASE_MARKER)
    save_open = script.index(OBSERVER_SAVE_OPEN)
    phase = script[start:save_open]
    required = (
        '[string]$ObserverSdist = ""',
        f'$ObserverSdistSha256 = "{OBSERVER_SDIST_SHA256}"',
        'Join-Path $Workspace "inputs\\rhino3dm-8.35.0.tar.gz"',
        "$ObserverSdistActual -ne $ObserverSdistSha256",
        '"-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL"',
        '"-DCMAKE_POLICY_VERSION_MINIMUM=3.5"',
        '"--target", "opennurbsStatic", "zlib"',
        'Join-Path $BlenderSource "lib\\windows_x64\\python\\313\\include"',
        'Join-Path $BlenderSource "lib\\windows_x64\\python\\313\\libs\\python313.lib"',
        '"--example", "observation_check"',
        '$ObserverDecoder = Join-Path $ObserverDecoderTarget "debug\\examples\\observation_check.exe"',
        'Join-Path $NarwhalSource "native\\document_observer\\qualify.py"',
        '"--mutations"',
        '"--core", $ApplicationCoreTarget',
        '"--vcvars", $Vcvars64',
        '"--rust-check", $ObserverDecoder',
        '"--sdist", $ObserverSdist',
        '"--opennurbs-lib-dir", $ObserverLibraries',
        '"--python-lib", $ObserverPythonLib',
        "'^OBSERVER_RECEIPT (.+)$'",
        '$Restored.label -ne "restored" -or $Restored.status -ne "GREEN"',
        'stage(source, native, app, "windows-x86_64", observer_directory=observer, core_path=core)',
        '$ReturnInputs += $ObserverReturnDirectory',
        '"Narwhal_3D-$Version-Windows-x86_64-document-observer"',
    )
    try:
        ordering = (
            script.index('"install locked geometry wheels"') < start,
            script.index("$ApplicationCoreTarget = Join-Path") < start,
            start < script.index('"qualify document observer against the exact packaged application core"') < save_open,
            start < script.index('"stage qualified document observer into the packaged application"') < save_open,
            start < script.index("$ObserverReturnDirectory = Join-Path") < save_open,
            save_open < script.index('"run packaged native-document Save/Open round trip"'),
            script.index('"run packaged native-document Save/Open round trip"')
            < script.rindex(OBSERVER_REMOVE) < script.index('"create engineering package archive"'),
            # Packaged-runtime probes import narwhal3d, which loads the observer: remove it after all of them.
            max(script.rindex("$RuntimePythonPath @("), script.rindex("verify_packaged_native_cores.py"))
            < script.rindex(OBSERVER_REMOVE),
        )
    except ValueError:
        return False
    return (all(token in script for token in required) and all(ordering)
            and "$LibraryDirectory" not in phase and "$DependencyPython" not in phase)


def observer_mutants(script: str):
    def once(old, new):
        count = script.count(old)
        if count != 1:
            raise AssertionError(f"mutation anchor must be unique, found {count}: {old[:60]!r}")
        return script.replace(old, new, 1)

    start = script.index(OBSERVER_PHASE_MARKER)
    phase = script[start:script.index(OBSERVER_SAVE_OPEN)]
    yield ("phase moved after the packaged Save/Open round trip",
           script.replace(phase, "", 1).replace("$Launcher = Join-Path", phase + "$Launcher = Join-Path", 1))
    yield ("sdist hash pin altered", once(f'"{OBSERVER_SDIST_SHA256}"', '"' + "0" * 64 + '"'))
    yield ("sdist SHA-256 check bypassed", once("if ($ObserverSdistActual -ne $ObserverSdistSha256) {", "if ($false) {"))
    yield ("MultiThreaded instead of MultiThreadedDLL",
           once('"-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL"', '"-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"'))
    yield ("decoder built without the observation_check example",
           once('"--example", "observation_check"', '"--bin", "observation_check"'))
    yield ("qualification run without --mutations",
           once('"--output-root", $ObserverQualification, "--mutations")', '"--output-root", $ObserverQualification)'))
    yield ("qualification without the exact packaged core", once('"--core", $ApplicationCoreTarget, ', ""))
    yield ("qualification receipt not required to end GREEN restored",
           once('if ($Restored.label -ne "restored" -or $Restored.status -ne "GREEN") {', "if ($false) {"))
    yield ("observer staged for the wrong platform",
           once('"windows-x86_64", observer_directory=observer', '"linux-x86_64", observer_directory=observer'))
    yield ("observer folder not returned in the artifact ZIP", once("$ReturnInputs += $ObserverReturnDirectory\n", ""))
    index = script.rindex(OBSERVER_REMOVE)
    yield ("post-Save/Open observer cleanup removed",
           script[:index] + script[index + len(OBSERVER_REMOVE):])
    save_open_end = script.index('"run packaged native-document Save/Open round trip"')
    save_open_end = script.index("\n}\n", script.index("} finally {", save_open_end)) + 3
    yield ("observer removed before the packaged-runtime probes",
           script[:save_open_end] + OBSERVER_REMOVE
           + script[save_open_end:index] + script[index + len(OBSERVER_REMOVE):])
    yield ("phase reads a variable defined only on the clean-build path",
           once(OBSERVER_PHASE_MARKER, OBSERVER_PHASE_MARKER + "\n$null = $LibraryDirectory"))


check(observer_phase_ok(build_script),
      "Windows build qualifies and stages the document observer before the packaged Save/Open round trip")
for label, mutant in observer_mutants(build_script):
    check(not observer_phase_ok(mutant), f"MUTATION RED: {label}")

qualify_help = subprocess.run([sys.executable, "-B", str(ROOT / "native/document_observer/qualify.py"), "--help"],
                              capture_output=True, text=True, timeout=60, check=True).stdout
qualify_flags = set(re.findall(r"--[a-z][a-z-]*", qualify_help))
qualify_call = build_script[build_script.index("$QualifyArguments = @("):build_script.index("$QualifyLines = ")]
used_flags = set(re.findall(r'"(--[a-z][a-z-]*)"', qualify_call))
check(used_flags and used_flags <= qualify_flags,
      "Windows qualification passes only flags that native/document_observer/qualify.py accepts")
check((ROOT / "native/application_core/examples/observation_check.rs").is_file()
      and "observation_check.exe" in build_script,
      "Windows observer decoder is built from the real application-core example")

stage_spec = importlib.util.spec_from_file_location("observer_stage_contract",
                                                    ROOT / "scripts/stage_document_observer.py")
assert stage_spec is not None and stage_spec.loader is not None
stage_contract = importlib.util.module_from_spec(stage_spec)
stage_spec.loader.exec_module(stage_contract)
driver_match = re.search(r'\$ObserverStageDriver = Join-Path \$Workspace "stage_document_observer_package\.py"\n@\'\n'
                         r"(.*?)\n'@ \| Set-Content", build_script, re.S)
check(driver_match is not None, "observer staging driver is embedded in the Windows build")
if driver_match is not None:
    driver_text = driver_match.group(1)
    with tempfile.TemporaryDirectory(prefix="narwhal_observer_driver_") as temporary:
        root = Path(temporary)
        source = root / "source"
        for relative, data in {
            "native/document_observer/observer.cpp": b"observer\n",
            "narwhal3d/native_object_registry.py": b"registry\n",
            "narwhal3d/native_document_observer.py": b"loader\n",
        }.items():
            (source / relative).parent.mkdir(parents=True, exist_ok=True)
            (source / relative).write_bytes(data)
        (source / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/stage_document_observer.py", source / "scripts/stage_document_observer.py")
        native = root / "runtime-bin"
        native.mkdir()
        core = native / "narwhal_application_core.dll"
        core.write_bytes(b"core")
        observer = root / "stage-input"
        observer.mkdir()
        library = b"observer-pyd"
        (observer / "_narwhal_document_observer.pyd").write_bytes(library)

        def digest(data):
            return hashlib.sha256(data).hexdigest()

        receipt = {
            "upstream_sdist_sha256": stage_contract._SDIST, "scope": "fixture", "pid": 1,
            "source_sha256": digest(b"observer\n"),
            "host": {"version": "8.35.0", "sha256": stage_contract._HOSTS["windows-x86_64"],
                     "cache_tag": "cpython-313", "soabi": "cp313-win_amd64", "extension": "fixture"},
            "entries": [{"label": "control", "status": "GREEN", "sha256": digest(library), "library": "x"},
                        {"label": "restored", "status": "GREEN", "sha256": digest(library), "library": "x"}],
            "integration": {"loader": {"status": "GREEN"}, "registry": {"status": "GREEN"},
                            "core_sha256": digest(b"core"), "adapter_sha256": digest(b"registry\n"),
                            "loader_sha256": digest(b"loader\n")},
        }
        (observer / "build.json").write_text(json.dumps(receipt), encoding="utf-8")
        driver = root / "driver.py"
        driver.write_text(driver_text, encoding="utf-8")

        def stage_with(driver_path, app):
            # Same argument order as the PowerShell call: source, native, app, observer, core.
            return subprocess.run([sys.executable, "-B", str(driver_path), str(source), str(native),
                                   str(app), str(observer), str(core)],
                                  capture_output=True, text=True, timeout=60)

        app = root / "app"
        result = stage_with(driver, app)
        staged = app / "narwhal3d/native/document-observer"
        staged_build = json.loads((staged / "build.json").read_text(encoding="utf-8")) if result.returncode == 0 else {}
        check(result.returncode == 0 and (staged / "_narwhal_document_observer.pyd").read_bytes() == library
              and "pid" not in staged_build and "scope" not in staged_build
              and staged_build.get("host", {}).get("sha256") == stage_contract._HOSTS["windows-x86_64"],
              "embedded observer driver stages the pair into the packaged app with the PowerShell argument order")
        wrong = driver_text.replace('"windows-x86_64", observer_directory=observer',
                                    '"linux-x86_64", observer_directory=observer', 1)
        check(wrong != driver_text, "MUTATION anchor present: observer driver platform")
        wrong_driver = root / "wrong_driver.py"
        wrong_driver.write_text(wrong, encoding="utf-8")
        wrong_app = root / "wrong-app"
        wrong_result = stage_with(wrong_driver, wrong_app)
        check(wrong_result.returncode != 0 and not (wrong_app / "narwhal3d/native/document-observer").exists(),
              "MUTATION RED: observer driver staged for the wrong platform is refused")

powershell_build = (ROOT / "scripts/build_brep_writer_windows.ps1").read_text(encoding="utf-8")
check(re.search(r'ArchiveSha256 = "1a8bb8dbe363f4969f679f11810251433416b62e595a6f02c447381a9f2128fa"', powershell_build)
      and re.search(r'ArchiveDigest -ne \x24ArchiveSha256', powershell_build)
      and re.search(r'\[switch\]\x24SelfCheck', powershell_build),
      "exact-save helper build pins the openNURBS archive hash and fails closed on mismatch")
check(re.search(r'CmakeOptions = @\([^\n]*"-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"\)', powershell_build)
      and re.search(r'CompileOptions = @\([^\n]*"/MT"[^\n]*"/DNOMINMAX"\)', powershell_build)
      and 'ProtocolPrefix = "brep_writer ONBREP 1"' in powershell_build,
      "exact-save helper build uses the MultiThreaded runtime, /DNOMINMAX and the ONBREP 1 identity")
check("narwhal3d\\kernel\\bin" not in powershell_build.lower()
      and "narwhal3d/kernel/bin" not in powershell_build.lower(),
      "exact-save helper build never references the source kernel/bin tree")

chooser_routes = {
    "Open/Save": ROOT / "narwhal3d/file_commands.py",
    "Import": ROOT / "narwhal3d/import_commands.py",
    "Export": ROOT / "narwhal3d/export_commands.py",
    "Picture": ROOT / "narwhal3d/commands.py",
    "Print command": ROOT / "narwhal3d/print_commands.py",
    "Print dialog": ROOT / "narwhal3d/ui.py",
}
for label, path in chooser_routes.items():
    text = path.read_text(encoding="utf-8")
    check("filedialog.ask_open_path" in text or "filedialog.ask_save_path" in text,
          f"{label} routes its chooser through the centralized native backend")
    check("GetOpenFileName" not in text and "GetSaveFileName" not in text,
          f"{label} does not bypass platform_core with a private Windows chooser")

with tempfile.TemporaryDirectory(prefix="narwhal_windows_verifier_test_") as temporary:
    root = Path(temporary)
    compiled_launcher = root / "Narwhal3D.exe"
    compiled_launcher.write_bytes(
        b"MZ" + "NARWHAL3D_VERSION".encode("utf-16le")
        + "NARWHAL3D_PACKAGE_PATH".encode("utf-16le")
        + "0.4.6".encode("utf-16le"))
    assembler.validate_launcher_version(compiled_launcher, "0.4.6")
    check(True, "Windows assembler accepts only a launcher carrying exact release identity")
    try:
        assembler.validate_launcher_version(compiled_launcher, "0.4.4")
    except assembler.AssemblyError:
        stale_launcher_refused = True
    else:
        stale_launcher_refused = False
    check(stale_launcher_refused, "Windows assembler rejects a stale launcher version")
    (root / "windows-release-manifest.json").write_text("{}", encoding="utf-8")
    base = [{"path": path, "size": 1, "sha256": "0" * 64}
            for path in verifier.REQUIRED if path != "windows-release-manifest.json"]
    verifier.validate_paths(root, base)
    check(verifier.GENERATED_PACKAGE_FILES == {
        "narwhal3d/native/narwhal_application_core.dll",
        "narwhal3d/native/narwhal_ui_core.dll",
        "narwhal3d/native/narwhal_geometry_core.dll",
        "narwhal3d/native/narwhal_platform_core.dll",
        "narwhal3d/native/narwhal_pure_core.dll",
        "narwhal3d/native/narwhal_selection_core.dll"},
        "Windows source comparison excludes only manifest-verified native DLLs")
    check(all(any(item["path"] == path for item in base) for path in (
        "app/narwhal3d/cdr_reader.py", "app/narwhal3d/cdr_writer.py",
        "app/runtime/windows-x86_64/bin/narwhal_cdr_core.dll",
        "app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
        "app/runtime/windows-x86_64/bin/narwhal-cdr-multiversion.exe",
        "app/runtime/windows-x86_64/bin/narwhal-document-core.exe",
        "app/runtime/windows-x86_64/bin/narwhal_ui_core.dll",
        "app/narwhal3d/native/narwhal_ui_core.dll",
        "app/narwhal3d/native/narwhal_geometry_core.dll",
        "app/narwhal3d/native/narwhal_platform_core.dll",
        "app/narwhal3d/native/narwhal_pure_core.dll",
        "app/narwhal3d/native/narwhal_selection_core.dll")),
    "Windows release requires CDR, document, platform and Rust accelerator components")
    for bad in (
        "runtime/blender.pdb", "app/launch/__pycache__/open_arg.pyc",
        "runtime/makesrna.exe", "bug_" + "reports/private.txt",
    ):
        try:
            verifier.validate_paths(root, base + [{"path": bad, "size": 1, "sha256": "0" * 64}])
        except verifier.VerificationError:
            refused = True
        else:
            refused = False
        check(refused, f"Windows verifier refuses private/cache/debug/build payload: {bad}")

    def refuses(action, error=verifier.VerificationError) -> bool:
        try:
            action()
        except error:
            return True
        return False

    check(verifier.EXACT_SAVE_HELPER == "app/narwhal3d/kernel/bin/brep_writer-win32-amd64.exe"
          and verifier.EXACT_SAVE_LICENSE in {item["path"] for item in base},
          "Windows release requires the exact-save helper path and its openNURBS license")
    check(not refuses(lambda: verifier.validate_paths(root, base)),
          "Windows verifier admits the exact-save helper below kernel/bin")
    for bad in (
        "app/narwhal3d/kernel/bin/helper.exe",
        "app/narwhal3d/kernel/bin/brep_writer-win32-amd64.exe.bak",
        "app/narwhal3d/kernel/bin/nested/brep_writer-win32-amd64.exe",
        "app/narwhal3d/kernel/bin/.gitignore",
        "app/other/kernel/bin/brep_writer-win32-amd64.exe",
        "app/narwhal3d/kernel/brep_writer.cpp",
    ):
        check(refuses(lambda bad=bad: verifier.validate_paths(
            root, base + [{"path": bad, "size": 1, "sha256": "0" * 64}])),
            f"Windows verifier refuses kernel payload other than the bound helper: {bad}")

    with tempfile.TemporaryDirectory(prefix="narwhal_exact_save_fixture_") as fixture_text:
        fixture = Path(fixture_text)
        helper_file = fixture / verifier.EXACT_SAVE_HELPER
        license_file = fixture / verifier.EXACT_SAVE_LICENSE
        helper_file.parent.mkdir(parents=True)
        license_file.parent.mkdir(parents=True)
        helper_bytes = b"MZ" + verifier.EXACT_SAVE_PROTOCOL.encode("ascii") + b" helper"
        helper_file.write_bytes(helper_bytes)
        license_file.write_text("openNURBS license\n", encoding="utf-8")
        record = {"size": len(helper_bytes), "sha256": verifier.sha256(helper_file),
                  "license_sha256": verifier.sha256(license_file), "protocol": None}
        check(not refuses(lambda: verifier.verify_exact_save_helper(fixture, record)),
              "exact-save helper is accepted only when bound to its matching record and license")
        helper_file.write_bytes(helper_bytes[:-1] + b"X")
        check(refuses(lambda: verifier.verify_exact_save_helper(fixture, record)),
              "tampered exact-save helper is refused by its manifest hash")
        identity_less = b"MZ no protocol identity"
        helper_file.write_bytes(identity_less)
        check(refuses(lambda: verifier.verify_exact_save_helper(fixture, {
                  **record, "size": len(identity_less), "sha256": verifier.sha256(helper_file)})),
              "exact-save helper without the ONBREP 1 identity is refused")
        helper_file.write_bytes(helper_bytes)
        license_file.unlink()
        check(refuses(lambda: verifier.verify_exact_save_helper(fixture, record)),
              "exact-save helper without its openNURBS license is refused")
        license_file.write_text("openNURBS license\n", encoding="utf-8")
        check(refuses(lambda: verifier.verify_exact_save_helper(fixture, None)),
              "exact-save helper without a manifest record is refused")
        check(refuses(lambda: verifier.verify_exact_save_helper(
                  fixture, {**record, "license_sha256": "0" * 64})),
              "exact-save helper with a mismatched license hash is refused")
        check(refuses(lambda: verifier.verify_exact_save_helper(
                  fixture, {**record, "protocol": "brep_writer ONBREP 2"})),
              "exact-save helper record naming another protocol is refused")

        observer_file = fixture / verifier.DOCUMENT_OBSERVER_LIBRARY
        receipt_file = fixture / verifier.DOCUMENT_OBSERVER_RECEIPT
        observer_file.parent.mkdir(parents=True)
        observer_file.write_bytes(b"MZ observer")
        receipt_file.write_text("{}\n", encoding="utf-8")
        observer_record = {"sha256": verifier.sha256(observer_file)}
        real_inspector = verifier.inspect_pe_dll
        try:
            verifier.inspect_pe_dll = lambda _path: {"exports": (verifier.DOCUMENT_OBSERVER_EXPORT,)}
            check(not refuses(lambda: verifier.verify_document_observer(fixture, observer_record)),
                  "document observer is accepted when its library matches the manifest record")
            check(refuses(lambda: verifier.verify_document_observer(fixture, None)),
                  "document observer without a manifest record is refused")
            check(refuses(lambda: verifier.verify_document_observer(fixture, {"sha256": "0" * 64})),
                  "document observer with a mismatched manifest hash is refused")
            verifier.inspect_pe_dll = lambda _path: {"exports": ("PyInit_other",)}
            check(refuses(lambda: verifier.verify_document_observer(fixture, observer_record)),
                  "document observer without its Python module export is refused")
        finally:
            verifier.inspect_pe_dll = real_inspector
        observer_file.unlink()
        check(refuses(lambda: verifier.verify_document_observer(fixture, observer_record)),
              "missing document observer library is refused")
    for required in (verifier.DOCUMENT_OBSERVER_LIBRARY, verifier.DOCUMENT_OBSERVER_RECEIPT):
        check(refuses(lambda required=required: verifier.validate_paths(
            root, [row for row in base if row["path"] != required])),
            f"Windows release requires the packaged observer file: {required}")
    verify_directory_source = inspect.getsource(verifier.verify_directory)
    check("verify_document_observer(root," in verify_directory_source
          and "verify_exact_save_helper(root," in verify_directory_source
          and "DOCUMENT_OBSERVER_RECORD}" in verify_directory_source,
          "Windows directory verification binds the observer and exact-save helper to the manifest")

    with tempfile.TemporaryDirectory(prefix="narwhal_windows_assembler_test_") as assembly_text:
        assembly = Path(assembly_text)
        source = assembly / "source"
        for directory in assembler.SOURCE_DIRECTORIES:
            (source / directory).mkdir(parents=True)
        (source / "narwhal3d/__init__.py").write_text("# public source\n", encoding="utf-8")
        (source / "native/application_core").mkdir(parents=True)
        (source / "native/application_core/OPENNURBS_LICENSE").write_text("ordinal license\n", encoding="utf-8")
        for filename in assembler.SOURCE_FILES:
            (source / filename).write_text(filename + "\n", encoding="utf-8")
        check(assembler.validate_source_package(source) is None,
              "clean public source passes the private-payload guard")
        for payload in ("kernel/bin/helper.exe", "kernel/bin/nested/helper.exe", "kernel/brep_writer.cpp"):
            poisoned = source / "narwhal3d" / payload
            poisoned.parent.mkdir(parents=True, exist_ok=True)
            poisoned.write_bytes(b"private payload")
            check(refuses(lambda: assembler.validate_source_package(source), assembler.AssemblyError),
                  f"Windows assembler refuses private source payload instead of deleting it: {payload}")
            poisoned.unlink()
        poisoned = source / "narwhal3d/kernel/bin/helper.exe"
        poisoned.parent.mkdir(parents=True, exist_ok=True)
        poisoned.write_bytes(b"private payload")
        check(refuses(lambda: assembler.replace_public_source(assembly / "poisoned-package", source),
                      assembler.AssemblyError),
              "Windows assembler refuses to copy private source payload into the package")
        poisoned.unlink()

        package = assembly / "package"
        (package / "app/narwhal3d/kernel/bin").mkdir(parents=True)
        kept = package / "app/narwhal3d/kernel/bin/keep.exe"
        kept.write_bytes(b"MZ")
        assembler.remove_forbidden_payload(package)
        check(kept.is_file(), "package-side kernel/bin payload is no longer silently deleted")

        helper_dir = assembly / "helper"
        helper_dir.mkdir()
        helper_bytes = b"MZ" + verifier.EXACT_SAVE_PROTOCOL.encode("ascii") + b" helper"
        (helper_dir / assembler.EXACT_SAVE_HELPER_NAME).write_bytes(helper_bytes)
        (helper_dir / assembler.EXACT_SAVE_LICENSE_NAME).write_text("openNURBS license\n", encoding="utf-8")
        exact_record = {"size": len(helper_bytes),
                        "sha256": verifier.sha256(helper_dir / assembler.EXACT_SAVE_HELPER_NAME),
                        "license_sha256": verifier.sha256(helper_dir / assembler.EXACT_SAVE_LICENSE_NAME),
                        "protocol": None}
        installed = assembly / "installed"
        (installed / "app").mkdir(parents=True)
        assembler.install_exact_save_helper(installed, helper_dir, exact_record)
        stale_package = assembly / "stale-package"
        (stale_package / "app/narwhal3d/kernel/bin").mkdir(parents=True)
        (stale_package / "app/narwhal3d/kernel/bin/stale.exe").write_bytes(b"MZ")
        check(refuses(lambda: assembler.install_exact_save_helper(stale_package, helper_dir, exact_record),
                      assembler.AssemblyError),
              "exact-save helper install refuses a package that already holds kernel/bin payload")
        check((installed / verifier.EXACT_SAVE_HELPER).read_bytes() == helper_bytes
              and (installed / verifier.EXACT_SAVE_LICENSE).read_bytes() == b"openNURBS license\n",
              "exact-save helper and openNURBS license are copied from the verified directory")
        mismatched = assembly / "mismatched"
        (mismatched / "app").mkdir(parents=True)
        check(refuses(lambda: assembler.install_exact_save_helper(
                  mismatched, helper_dir, {**exact_record, "sha256": "0" * 64}), assembler.AssemblyError),
              "exact-save helper install refuses a copy that differs from its verified record")

        if shutil.which("x86_64-w64-mingw32-g++") and shutil.which("wine"):
            # A private Wine prefix: the check must neither depend on nor write to the
            # user's default prefix (which may not exist, or be redirected away on purpose:
            # with ~/.wine -> /dev/null wine exits 1 before running anything).
            saved_wine_env = {k: os.environ.get(k) for k in ("WINEPREFIX", "WINEDEBUG")}
            os.environ["WINEPREFIX"] = str(assembly / "wine-prefix")
            os.environ["WINEDEBUG"] = "-all"
            wine_dir = assembly / "wine"
            wine_dir.mkdir()
            source_text = ('#include <cstdio>\n#include <cstring>\n'
                           'int main(int argc, char** argv) {\n'
                           '  if (argc == 2 && std::strcmp(argv[1], "--version") == 0) {\n'
                           '    std::puts("brep_writer ONBREP 1 opennurbs 8");\n    return 0;\n  }\n'
                           '  return 2;\n}\n')
            (wine_dir / "helper.cpp").write_text(source_text, encoding="utf-8")
            subprocess.run(["x86_64-w64-mingw32-g++", "-O2", "-static", "-static-libgcc", "-static-libstdc++",
                            "-o", str(wine_dir / assembler.EXACT_SAVE_HELPER_NAME),
                            str(wine_dir / "helper.cpp")], check=True, timeout=300, capture_output=True)
            (wine_dir / assembler.EXACT_SAVE_LICENSE_NAME).write_text("license\n", encoding="utf-8")
            wine_record = assembler.validate_exact_save_helper(wine_dir)
            check(wine_record["protocol"] == "brep_writer ONBREP 1 opennurbs 8",
                  "Windows assembler executes the exact-save helper under wine and records its protocol")
            wrong_dir = assembly / "wrong"
            wrong_dir.mkdir()
            (wrong_dir / "helper.cpp").write_text('#include <cstdio>\nint main() { std::puts("not the helper"); return 0; }\n',
                                                  encoding="utf-8")
            subprocess.run(["x86_64-w64-mingw32-g++", "-O2", "-static", "-static-libgcc", "-static-libstdc++",
                            "-o", str(wrong_dir / assembler.EXACT_SAVE_HELPER_NAME),
                            str(wrong_dir / "helper.cpp")], check=True, timeout=300, capture_output=True)
            (wrong_dir / assembler.EXACT_SAVE_LICENSE_NAME).write_text("license\n", encoding="utf-8")
            check(refuses(lambda: assembler.validate_exact_save_helper(wrong_dir), assembler.AssemblyError),
                  "Windows assembler refuses an executed exact-save helper with the wrong protocol")
            subprocess.run(["wineserver", "-k"], check=False, timeout=60, capture_output=True)
            for key, value in saved_wine_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        else:
            print("  SKIP  wine execution of the exact-save helper (mingw or wine unavailable)")

        stub = types.ModuleType("stage_document_observer")
        staged = {}
        observer_result = {"sha256": "f" * 64, "qualification": "test fixture"}

        def fake_stage(source_root, native_directory, app_root, platform, *,
                       observer_directory=None, core_path=None):
            staged.update(source_root=source_root, native_directory=native_directory, app_root=app_root,
                          platform=platform, observer_directory=observer_directory, core_path=core_path)
            return observer_result

        stub.stage = fake_stage
        observer_dir = assembly / "observer"
        with patch.dict(sys.modules, {"stage_document_observer": stub}):
            returned = assembler.stage_document_observer(package, source, observer_dir)
        check(returned == observer_result and staged["platform"] == "windows-x86_64"
              and staged["observer_directory"] == observer_dir
              and staged["core_path"] == package / "app/runtime/windows-x86_64/bin/narwhal_application_core.dll"
              and staged["app_root"] == package / "app",
              "Windows assembler stages the observer through the platform interface with explicit inputs")

        pkg = assembly / "manifest-package"
        (pkg / "runtime").mkdir(parents=True)
        (pkg / "runtime/blender.exe").write_bytes(b"blender")
        (pkg / "Narwhal3D.exe").write_bytes(b"launcher")
        (source / "packaging/windows").mkdir(parents=True)
        (source / "packaging/windows/narwhal3d_launcher.c").write_text("// launcher\n", encoding="utf-8")
        with patch.object(assembler, "native_records", return_value={}):
            assembler.write_manifest(pkg, source, "0.4.10", "a" * 40, observer_record | {"qualification": "x"},
                                     exact_record)
        manifest = json.loads((pkg / "windows-release-manifest.json").read_text(encoding="utf-8"))
        check(manifest["native_components"]["document-observer"] == {**observer_record, "qualification": "x"}
              and manifest["exact_save_helper"] == exact_record
              and manifest["excluded_components"] == ["optional-converters", "installer-file-associations"],
              "Windows manifest records the staged observer, exact-save helper and exact exclusions")

    public_root = Path(tempfile.mkdtemp(prefix="narwhal_windows_public_source_"))
    try:
        app = public_root / "app"
        git_files = {
            "narwhal3d/__init__.py": b"# public\n", "launch/bootstrap.py": b"# launch\n",
            "licenses/GPL-3.0-or-later.txt": b"gpl\n", "LICENSE": b"license\n",
            "THIRD_PARTY_NOTICES": b"notices\n", "CHANGELOG.md": b"changes\n", "STATUS.md": b"status\n",
        }
        packaged = {relative: b"packaged only\n" for relative in verifier.PACKAGED_ONLY_FILES}
        for relative, data in {**git_files, **packaged}.items():
            (app / relative).parent.mkdir(parents=True, exist_ok=True)
            (app / relative).write_bytes(data)

        def fake_git(args, **_):
            relative = args[-1].split(":", 1)[1]
            if relative in packaged:
                raise subprocess.CalledProcessError(128, args)
            return subprocess.CompletedProcess(args, 0, stdout=git_files[relative])

        with patch.object(verifier.subprocess, "run", fake_git):
            check(not refuses(lambda: verifier.verify_public_source(public_root, "a" * 40)),
                  "public source comparison exempts only the packaged-only exact-save and observer files")
            (app / "launch/bootstrap.py").write_bytes(b"edited\n")
            check(refuses(lambda: verifier.verify_public_source(public_root, "a" * 40)),
                  "public source comparison still refuses an edited maintained source file")
    finally:
        shutil.rmtree(public_root, ignore_errors=True)

    cli = subprocess.run([sys.executable, str(ROOT / "scripts/assemble_windows_release.py"), "--self-check"],
                         text=True, capture_output=True, timeout=120)
    check(cli.returncode == 0 and "self-check: OK" in cli.stdout,
          "Windows assembler self-check passes")
    check(all(flag in (ROOT / "scripts/assemble_windows_release.py").read_text(encoding="utf-8")
              for flag in ("--exact-save-helper-dir", "--document-observer-dir")),
          "Windows assembler exposes the exact-save helper and document observer inputs")

    good_zip = root / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as archive:
        archive.writestr("Package/file.txt", "ok")
    with zipfile.ZipFile(good_zip) as archive:
        verifier.safe_zip_members(archive, "Package")
    check(True, "Windows verifier accepts a confined CRC-valid ZIP")

    bad_zip = root / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    try:
        with zipfile.ZipFile(bad_zip) as archive:
            verifier.safe_zip_members(archive, "Package")
    except verifier.VerificationError:
        traversal_refused = True
    else:
        traversal_refused = False
    check(traversal_refused, "Windows verifier refuses ZIP traversal")

old_inspector = verifier.inspect_pe_dll
try:
    good_dependencies = tuple(sorted((
        "kernel32.dll", "tkbo-a95b458a.dll", "tkbrep-20caede0.dll",
        "tkernel-17e892f6.dll", "tkg3d-bffb89c7.dll", "tkgeomalgo-b6e9f0c2.dll",
        "tkgeombase-8b58f227.dll", "tkmath-0d10d50e.dll", "tkprim-589f0279.dll",
        "tkshhealing-a2154896.dll", "tktopalgo-460f2aa9.dll",
    )))
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": ("??_FBRepBuilderAPI_Sewing@@QEAAXXZ",
                    *verifier.GEOMETRY_CORE_EXPORTS),
        "dependencies": good_dependencies,
        "import_symbols": {},
    }
    identity = verifier.verify_geometry_core_identity(Path("geometry.dll"))
    check(identity["dependencies"] == good_dependencies,
          "geometry verifier requires exact public ABI, bounded MSVC thunks and hashed OCCT imports")
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": (*verifier.GEOMETRY_CORE_EXPORTS, "unexpected_export"),
        "dependencies": good_dependencies,
        "import_symbols": {},
    }
    try:
        verifier.verify_geometry_core_identity(Path("geometry.dll"))
    except verifier.VerificationError:
        extra_refused = True
    else:
        extra_refused = False
    check(extra_refused, "geometry verifier refuses arbitrary non-ABI exports")
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": verifier.GEOMETRY_CORE_EXPORTS,
        "dependencies": ("TKBin.dll",),
        "import_symbols": {},
    }
    try:
        verifier.verify_geometry_core_identity(Path("geometry.dll"))
    except verifier.VerificationError:
        unpinned_refused = True
    else:
        unpinned_refused = False
    check(unpinned_refused, "geometry verifier refuses unpinned or incomplete OCCT imports")
finally:
    verifier.inspect_pe_dll = old_inspector

check(verifier.normalized(b"a\r\nb\r\n") == b"a\nb\n",
      "public source comparison normalizes Windows checkout line endings only")
check(verifier.FORMAT == "portable-zip" and verifier.PLATFORM == "windows-x86_64",
      "Windows verifier pins portable ZIP and x86-64 identity")

runpy.run_path(str(ROOT / "spike/test_windows_build.py"))["run_checks"](check)

print(f"\n{checks}/{checks} Windows release checks passed")
