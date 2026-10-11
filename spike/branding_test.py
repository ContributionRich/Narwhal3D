"""Standalone Narwhal 3D identity, launcher, desktop, and icon checks.

Pure Python (no application runtime) so it runs quickly and headlessly.
Run: python3 spike/branding_test.py
"""
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "launch", "narwhal3d.desktop.in")
ICON = os.path.join(ROOT, "launch", "narwhal3d.png")
PRIMARY_LAUNCHER = os.path.join(ROOT, "narwhal3d.sh")

MIME_XML = os.path.join(ROOT, "launch", "narwhal3d-3dm.xml")
APP_ID = "narwhal3d.desktop"
PRODUCT_NAME = "Narwhal 3D"

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def desktop_values(text):
    values = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def binary_contains(path, needles, chunk_size=1024 * 1024):
    """Whether every byte marker occurs, without loading a large runtime into memory."""
    pending = set(needles)
    overlap = max((len(needle) for needle in pending), default=1) - 1
    carry = b""
    with open(path, "rb") as handle:
        while pending:
            block = handle.read(chunk_size)
            if not block:
                break
            data = carry + block
            pending = {needle for needle in pending if needle not in data}
            carry = data[-overlap:] if overlap else b""
    return not pending


# Exact public identity and primary launcher.
check(os.path.basename(TEMPLATE) == "narwhal3d.desktop.in",
      "desktop template must carry the narwhal3d app id")
check(os.path.isfile(TEMPLATE), f"desktop template missing: {TEMPLATE}")
check(os.path.isfile(PRIMARY_LAUNCHER), f"primary launcher missing: {PRIMARY_LAUNCHER}")
check(os.access(PRIMARY_LAUNCHER, os.X_OK), "narwhal3d.sh must be executable")

text = open(TEMPLATE).read() if os.path.isfile(TEMPLATE) else ""
values = desktop_values(text)
check(values.get("Name") == PRODUCT_NAME,
      f"desktop Name must be exactly {PRODUCT_NAME!r}, got {values.get('Name')!r}")
check(values.get("Exec") == "__ROOT__/narwhal3d.sh %f",
      f"desktop Exec must use narwhal3d.sh, got {values.get('Exec')!r}")
check(values.get("Icon") == "__ROOT__/launch/narwhal3d.png",
      f"desktop Icon must be narwhal3d.png, got {values.get('Icon')!r}")
check(values.get("StartupWMClass") == PRODUCT_NAME,
      f"desktop X11 class must be exactly {PRODUCT_NAME!r}")
check(values.get("Type") == "Application", "desktop Type must be Application")
check("application/x-3dm;" in values.get("MimeType", ""),
      "desktop MimeType must list application/x-3dm")
check("__ROOT__" in text, "template must use the __ROOT__ placeholder")
first_line = text.splitlines()[0] if text.splitlines() else ""
check(first_line == "[Desktop Entry]",
      f"first template line must be '[Desktop Entry]', got {first_line!r}")
check(not os.access(TEMPLATE, os.X_OK), "desktop template must not be executable")
for field in ("Comment", "Keywords"):
    public_text = values.get(field, "").lower()
    check("rhino" not in public_text and "blender" not in public_text,
          f"desktop {field} must not expose Rhino or Blender branding")

substituted = text.replace("__ROOT__", ROOT)
check("__ROOT__" not in substituted, "substitution left a __ROOT__ placeholder")
check(os.path.isfile(ICON), "substituted Icon target does not exist")

# Installed app ID and public-only launcher identity.
primary_text = open(PRIMARY_LAUNCHER).read() if os.path.isfile(PRIMARY_LAUNCHER) else ""
check('DESKTOP_FILE="${APP_DIR}/narwhal3d.desktop"' in primary_text,
      f"primary launcher must install app id {APP_ID}")
check('DESKTOP_FILE="${APP_DIR}/narwhal3d.desktop"' in primary_text,
      "primary launcher installs only the Narwhal desktop entry")
check("xdg-mime default narwhal3d.desktop" in primary_text,
      "the .3dm default must use narwhal3d.desktop")
check('MIME_XML="${ROOT}/launch/narwhal3d-3dm.xml"' in primary_text,
      "primary launcher must use narwhal3d-3dm.xml")
check("[narwhal3d]" in primary_text,
      "primary launcher must emit the Narwhal log identity")
check('"${NARWHAL3D_APPIMAGE:-0}" != 1' in primary_text,
      "a mounted AppImage must not install a transient desktop path")


bootstrap_text = open(os.path.join(ROOT, "launch", "bootstrap.py")).read()
check("[narwhal3d]" in bootstrap_text,
      "bootstrap diagnostics must use the Narwhal log identity")
check('print("[narwhal3d] Narwhal 3D session ready")' in bootstrap_text,
      "bootstrap must report the exact Narwhal 3D ready line")

# App metadata, canonical data/log variables, report title, and export metadata.
addon_text = open(os.path.join(ROOT, "narwhal3d", "__init__.py")).read()
file_text = open(os.path.join(ROOT, "narwhal3d", "file_commands.py")).read()
log_text = open(os.path.join(ROOT, "narwhal3d", "sessionlog.py")).read()
model_text = open(os.path.join(ROOT, "narwhal3d", "model.py")).read()
stl_text = open(os.path.join(ROOT, "narwhal3d", "stl_writer.py")).read()

obj_text = open(os.path.join(ROOT, "narwhal3d", "obj_writer.py")).read()
vector_text = open(os.path.join(ROOT, "narwhal3d", "vector_export.py")).read()
commands_text = open(os.path.join(ROOT, "narwhal3d", "commands.py")).read()
report_text = open(os.path.join(ROOT, "spike", "public_ui_report.py")).read()
check('"name": "Narwhal 3D"' in addon_text,
      "built-in application metadata must use the exact product name")
check('"Narwhal 3D", f"Save changes to' in file_text,
      "file confirmation dialogs must use the exact product name")
check('os.environ.get("NARWHAL3D_DATA_DIR")' in file_text
      and '"share", "narwhal3d"' in file_text,
      "user data must use NARWHAL3D_DATA_DIR and default to narwhal3d")
check('"NARWHAL3D_LOG_DIR"' in log_text
      and '"share", "narwhal3d", "logs"' in log_text,
      "logs must use NARWHAL3D_LOG_DIR and default to narwhal3d")
check('~/.cache/narwhal3d/embedded' in model_text
      and 're.match(r"^([A-Za-z]):' in model_text
      and 'm.group(2).replace("\\\\", "/")' in model_text,
      "embedded textures must use the Narwhal cache and retain drive-letter mapping")
check('name="Narwhal 3D"' in stl_text
      and 'name = "Narwhal 3D"' in stl_text,
      "STL default and binary-header fallback must identify Narwhal 3D")

check('out = ["# Narwhal 3D"]' in obj_text,
      "OBJ export metadata must identify Narwhal 3D")
check('b"/Producer (Narwhal 3D)' in vector_text,
      "PDF/AI export metadata must identify Narwhal 3D")
check("[narwhal3d]" in commands_text,
      "command import diagnostics must use [narwhal3d]")
check('"Narwhal 3D public UI report"' in report_text,
      "the rendered public UI report must carry Narwhal 3D branding")

# MIME metadata remains a format description.
check(os.path.isfile(MIME_XML), f"MIME package missing: {MIME_XML}")
if os.path.isfile(MIME_XML):
    namespace = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}
    try:
        mime_type = ET.parse(MIME_XML).getroot().find("m:mime-type", namespace)
    except ET.ParseError as error:
        mime_type = None
        failures.append(f"MIME package is not valid XML: {error}")
    if mime_type is not None:
        check(mime_type.get("type") == "application/x-3dm",
              "MIME type must be application/x-3dm")
        globs = {node.get("pattern") for node in mime_type.findall("m:glob", namespace)}
        check("*.3dm" in globs, f"MIME package must glob *.3dm ({globs})")

# Strict desktop validation, when available.
if os.path.isfile(TEMPLATE):
    validator = None
    for candidate in ("desktop-file-validate", "/usr/bin/desktop-file-validate"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=False)
            validator = candidate
            break
        except (FileNotFoundError, OSError):
            continue
    if validator is None:
        print("note: desktop-file-validate not found; skipping strict validation")
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".desktop", delete=False) as handle:
            handle.write(substituted)
            temporary_path = handle.name
        try:
            result = subprocess.run([validator, temporary_path], capture_output=True, text=True)
            if result.returncode != 0:
                failures.append(f"desktop-file-validate failed:\n{result.stdout}{result.stderr}")
        finally:
            os.unlink(temporary_path)

# Icon filename, dimensions, generator identity, and original narwhal source art.
check(os.path.basename(ICON) == "narwhal3d.png", "icon filename must be narwhal3d.png")
check(os.path.isfile(ICON), f"icon missing: {ICON}")
if os.path.isfile(ICON):
    with open(ICON, "rb") as source:
        header = source.read(24)
    check(header[:8] == b"\x89PNG\r\n\x1a\n", "icon is not a PNG")
    if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", header[16:24])
        check((width, height) == (256, 256),
              f"icon is {width}x{height}, expected 256x256")

icon_spec = importlib.util.spec_from_file_location(
    "narwhal3d_icon", os.path.join(ROOT, "launch", "make_icon.py"))
if icon_spec is None or icon_spec.loader is None:
    failures.append("could not load the Narwhal 3D icon generator")
else:
    icon_module = importlib.util.module_from_spec(icon_spec)
    icon_spec.loader.exec_module(icon_module)
    check(icon_module.ICON_FILENAME == "narwhal3d.png",
          "icon generator must output narwhal3d.png")
    check(icon_module.OCEAN != icon_module.WHALE != icon_module.TUSK,
          "narwhal icon must retain distinct ocean, whale, and tusk colors")
native_icon = os.path.join(ROOT, "blender_patches", "narwhal3d_app_icon.svg")
check(os.path.isfile(native_icon), "native narwhal SVG is missing")
if os.path.isfile(native_icon):
    native_icon_text = open(native_icon, encoding="utf-8").read()
    check("<circle" in native_icon_text and native_icon_text.count("<path") >= 3,
          "native icon must contain the original narwhal body, tusk, eye, and mouth art")

# Native patch: exercise a stock source fixture; never touch ignored source.
native_path = os.path.join(ROOT, "blender_patches", "apply_narwhal3d_branding.py")
native_spec = importlib.util.spec_from_file_location("narwhal3d_native_branding", native_path)
if native_spec is None or native_spec.loader is None:
    failures.append("could not load the Narwhal 3D native branding patcher")
else:
    native = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native)

    def exercise_native():
        with tempfile.TemporaryDirectory(prefix="narwhal3d_stock_") as source:
            fixtures = {}
            for edit in native.EDITS:
                rel, stock, _new, count = edit
                fixtures.setdefault(rel, []).extend([stock] * count)
            for rel, chunks in fixtures.items():
                path = os.path.join(source, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(chunks) + "\n")
            icon_dest = os.path.join(source, native.ICON_DEST)
            os.makedirs(os.path.dirname(icon_dest), exist_ok=True)
            with open(icon_dest, "w", encoding="utf-8") as handle:
                handle.write("<svg><!-- old icon --></svg>\n")
            changed = native.apply(source)
            expected_paths = set(fixtures) | {native.ICON_DEST}
            check(set(changed) == expected_paths,
                  f"native branding changed the complete expected set ({changed})")
            for rel, _stock, new, count in native.EDITS:
                branded = open(os.path.join(source, rel), encoding="utf-8").read()
                check(branded.count(new) == count,
                      f"native Narwhal identity missing from {rel}: {new!r}")
            required_native = {
                "source/blender/windowmanager/intern/wm_window.cc": (
                    'return "Narwhal 3D";', 'win_title.append(" - Narwhal 3D");'),
                "intern/ghost/intern/GHOST_SystemX11.hh": (
                    'GHOST_X11_RES_NAME "narwhal3d"',
                    'GHOST_X11_RES_CLASS "Narwhal 3D"'),
                "intern/ghost/intern/GHOST_SystemWayland.cc": ('"narwhal3d"',),
                "source/creator/creator_args.cc": (
                    'printf("Narwhal 3D %s', 'PRINT("Narwhal 3D %s',
                    'Usage: narwhal3d'),
                "source/blender/windowmanager/intern/wm_init_exit.cc": (
                    "Narwhal 3D quit",),
            }
            for rel, snippets in required_native.items():
                branded = open(os.path.join(source, rel), encoding="utf-8").read()
                for snippet in snippets:
                    check(snippet in branded,
                          f"required native identity {snippet!r} missing from {rel}")
            check(open(icon_dest, "rb").read() == open(native.ICON_SOURCE, "rb").read(),
                  "native Wayland window icon is not the Narwhal 3D SVG")
            check(native.apply(source) == [], "native branding patch is not idempotent")

    exercise_native()

    # Validate an installed native runtime only when explicitly requested.
    if os.environ.get("NARWHAL3D_VALIDATE_NATIVE_RUNTIME"):
        native_runtime = os.path.join(ROOT, "tools", "blender-patched", "blender")
        check(os.path.isfile(native_runtime) and os.access(native_runtime, os.X_OK),
              "native runtime validation requested, but no executable is installed")
        if os.path.isfile(native_runtime) and os.access(native_runtime, os.X_OK):
            version = subprocess.run([native_runtime, "--version"],
                                     capture_output=True, text=True)
            first = version.stdout.splitlines()[0] if version.stdout.splitlines() else ""
            check(version.returncode == 0 and first.startswith("Narwhal 3D "),
                  f"installed native runtime has the wrong version banner: {first!r}")
            check(binary_contains(native_runtime,
                                  {b" - Narwhal 3D", b"Narwhal 3D quit"}),
                  "installed native runtime lacks Narwhal title/quit markers")

print("\n" + "=" * 50)
if failures:
    for message in failures:
        print("FAIL:", message)
    sys.exit(1)
print("BRANDING PASSED")
