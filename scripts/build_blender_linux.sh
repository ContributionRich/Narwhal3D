#!/usr/bin/env bash
# Build the publishable native Narwhal 3D runtime from one exact Blender commit.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$ROOT_DIR/blender_patches"
BRANDING_PATCH="$PATCH_DIR/apply_narwhal3d_branding.py"
APP_DRAWN_PATCH="$PATCH_DIR/apply_app_drawn.py"
BOUNDARY_PATCH="$PATCH_DIR/apply_cad_boundary_wires.py"
DEFERRED_PRESENT_PATCH="$PATCH_DIR/apply_deferred_first_present.py"
STATUSBAR_HEIGHT_PATCH="$PATCH_DIR/apply_statusbar_height.py"
ICON_SOURCE="$PATCH_DIR/narwhal3d_app_icon.svg"
WINDOW_OVERLAY_PATCH="$PATCH_DIR/apply_window_overlay.py"
REQUIRED_COMMIT="d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
SOURCE_DIR=""
BUILD_DIR=""
INSTALL_DIR=""
EXPECTED_COMMIT="$REQUIRED_COMMIT"
SOURCE_DATE_EPOCH=""
JOBS=""
SELF_CHECK=0
RECORD_INSTALLED=0

usage() {
  cat <<'EOF'
Usage: scripts/build_blender_linux.sh OPTIONS

Apply every required Narwhal 3D patch to Blender 5.2.2 at commit
d13f752e3b9c4f8c261cda552b1021f8bcc0382c, then configure, compile, install,
validate native branding, and write .narwhal3d-native-runtime.json.

Required:
  --source-dir PATH         Blender checkout at the required commit (clean for build)
  --build-dir PATH          Out-of-tree build directory (build mode only)
  --install-dir PATH        Installation destination

Optional:
  --expected-commit SHA     Must equal the fixed required commit when supplied
  --source-date-epoch N     Optional native-build epoch recorded as build_source_date_epoch
  --jobs N                  Parallel build jobs
  --self-check              Validate patch inputs and local script logic only
  --record-installed-provenance
                            Attest an already patched, built and installed runtime;
                            validate and write its marker without patching/building.
                            Requires --source-dir and --install-dir. Caller must
                            have built/installed ALL current patch inputs from this
                            source; checks cannot prove source-to-binary equivalence.
  -h, --help                Show this help
EOF
}

while (($#)); do
  case "$1" in
    --source-dir|--build-dir|--install-dir|--expected-commit|--source-date-epoch|--jobs)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
      case "$1" in
        --source-dir) SOURCE_DIR="$2" ;;
        --build-dir) BUILD_DIR="$2" ;;
        --install-dir) INSTALL_DIR="$2" ;;
        --expected-commit) EXPECTED_COMMIT="$2" ;;
        --source-date-epoch) SOURCE_DATE_EPOCH="$2" ;;
        --jobs) JOBS="$2" ;;
      esac
      shift 2
      ;;
    --record-installed-provenance)
      RECORD_INSTALLED=1
      shift
      ;;
    --self-check)
      SELF_CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || { echo "native build requires Linux" >&2; exit 2; }
[[ "$(uname -m)" == "x86_64" ]] || { echo "native build requires x86_64" >&2; exit 2; }
for patch_input in "$BRANDING_PATCH" "$APP_DRAWN_PATCH" "$BOUNDARY_PATCH" "$DEFERRED_PRESENT_PATCH" "$STATUSBAR_HEIGHT_PATCH" "$ICON_SOURCE" "$WINDOW_OVERLAY_PATCH"; do
  [[ -f "$patch_input" && ! -L "$patch_input" ]] || { echo "required native patch input is missing or symlinked: $patch_input" >&2; exit 2; }
done

if ((SELF_CHECK)); then
  python3 - "$REQUIRED_COMMIT" "$BRANDING_PATCH" "$APP_DRAWN_PATCH" "$BOUNDARY_PATCH" "$DEFERRED_PRESENT_PATCH" "$STATUSBAR_HEIGHT_PATCH" "$ICON_SOURCE" "$WINDOW_OVERLAY_PATCH" <<'PY'
import ast
import hashlib
from pathlib import Path
import re
import sys

commit = sys.argv[1]
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("invalid fixed Blender source commit")
if commit != "d13f752e3b9c4f8c261cda552b1021f8bcc0382c":
    raise SystemExit("fixed Blender source commit changed")
for value in sys.argv[2:]:
    path = Path(value)
    if path.suffix == ".py":
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
for value in sys.argv[2:]:
    hashlib.sha256(Path(value).read_bytes()).hexdigest()
PY
  echo "build_blender_linux.sh self-check: OK"
  exit 0
fi

for command_name in git python3; do
  command -v "$command_name" >/dev/null || { echo "required command is missing: $command_name" >&2; exit 2; }
done
[[ -n "$SOURCE_DIR" && -n "$INSTALL_DIR" ]] && { ((RECORD_INSTALLED)) || [[ -n "$BUILD_DIR" ]]; } || {
  echo "--source-dir and --install-dir are required; build mode also requires --build-dir" >&2
  exit 2
}
[[ "${EXPECTED_COMMIT,,}" == "$REQUIRED_COMMIT" ]] || {
  echo "--expected-commit must equal $REQUIRED_COMMIT" >&2
  exit 2
}
if [[ -n "$SOURCE_DATE_EPOCH" && ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
  echo "--source-date-epoch must be a non-negative integer" >&2
  exit 2
fi
if [[ -n "$JOBS" && ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--jobs must be a positive integer" >&2
  exit 2
fi

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
if ((RECORD_INSTALLED)); then
  [[ -d "$INSTALL_DIR" ]] || { echo "installed runtime directory is missing: $INSTALL_DIR" >&2; exit 2; }
else
  for command_name in cmake ninja; do
    command -v "$command_name" >/dev/null || { echo "required command is missing: $command_name" >&2; exit 2; }
  done
  mkdir -p "$BUILD_DIR" "$INSTALL_DIR"
  BUILD_DIR="$(realpath "$BUILD_DIR")"
fi
INSTALL_DIR="$(realpath "$INSTALL_DIR")"
[[ -f "$SOURCE_DIR/CMakeLists.txt" && -d "$SOURCE_DIR/.git" ]] || {
  echo "source directory is not a Blender Git checkout: $SOURCE_DIR" >&2
  exit 2
}
ACTUAL_COMMIT="$(git --no-pager -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "${ACTUAL_COMMIT,,}" == "$REQUIRED_COMMIT" ]] || {
  echo "source commit mismatch: required $REQUIRED_COMMIT, got $ACTUAL_COMMIT" >&2
  exit 2
}
build_native() {
[[ -z "$(git --no-pager --no-optional-locks -C "$SOURCE_DIR" status --porcelain --untracked-files=no)" ]] || {
  echo "source checkout has tracked modifications; refusing a non-reproducible patch/build" >&2
  exit 2
}

python3 - "$SOURCE_DIR" "$BRANDING_PATCH" "$APP_DRAWN_PATCH" "$BOUNDARY_PATCH" "$DEFERRED_PRESENT_PATCH" "$STATUSBAR_HEIGHT_PATCH" "$ICON_SOURCE" "$WINDOW_OVERLAY_PATCH" <<'PY'
import importlib.util
from pathlib import Path
import sys

source = Path(sys.argv[1])
branding_path = Path(sys.argv[2])
app_drawn_path = Path(sys.argv[3])
boundary_path = Path(sys.argv[4])
deferred_path = Path(sys.argv[5])
statusbar_path = Path(sys.argv[6])
icon = Path(sys.argv[7])
overlay_path = Path(sys.argv[8])

def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load native patch: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

branding = load("narwhal3d_branding", branding_path)
changed = branding.apply(str(source), str(icon))
print(f"branding patch applied ({len(changed)} changed paths)")
for name, path in (("narwhal3d_app_drawn", app_drawn_path),
                   ("narwhal3d_boundary_wires", boundary_path),
                   ("narwhal3d_deferred_present", deferred_path)):
    module = load(name, path)
    module.SRC = str(source / "source" / "blender")
    module.main()
# Unlike the legacy patches, main() parses sys.argv. Call the explicit API so
# the build driver's positional arguments never reach the patch's argparse.
statusbar = load("narwhal3d_statusbar_height", statusbar_path)
changed = statusbar.apply(source / "source" / "blender")
print(f"statusbar-height patch applied ({len(changed)} changed paths)")
overlay = load("narwhal3d_window_overlay", overlay_path)
changed = overlay.apply(source / "source" / "blender")
print(f"window-overlay patch applied ({len(changed)} changed paths)")
PY

if [[ -n "$SOURCE_DATE_EPOCH" ]]; then
  export SOURCE_DATE_EPOCH
fi
cmake -S "$SOURCE_DIR" -B "$BUILD_DIR" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR" \
  -DWITH_INSTALL_PORTABLE=ON
if [[ -n "$JOBS" ]]; then
  cmake --build "$BUILD_DIR" --parallel "$JOBS"
else
  cmake --build "$BUILD_DIR" --parallel
fi
cmake --install "$BUILD_DIR"
}

if ((RECORD_INSTALLED)); then
  # Read-only anchor verification: do not repair source after its binary was built.
  python3 - "$SOURCE_DIR/source/blender" "$STATUSBAR_HEIGHT_PATCH" "$WINDOW_OVERLAY_PATCH" <<'PY'
import importlib.util
from pathlib import Path
import sys

source = Path(sys.argv[1])
for name, patch_path in (("statusbar", sys.argv[2]), ("window-overlay", sys.argv[3])):
    spec = importlib.util.spec_from_file_location(name, patch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative, old, new in module.EDITS:
        path = source / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if text.count(new) != 1 or text.count(old) != new.count(old):
            raise SystemExit(f"installed provenance requires the current {name} patch in source: {relative}")
    if name == "window-overlay":
        header = source / module.HEADER
        if not header.is_file() or header.read_text(encoding="utf-8") != module.HEADER_TEXT:
            raise SystemExit(f"installed provenance requires the current window-overlay header: {module.HEADER}")
PY
else
  build_native
fi

BRANDED_BINARY="$INSTALL_DIR/blender"
[[ -x "$BRANDED_BINARY" && ! -L "$BRANDED_BINARY" ]] || {
  echo "installed native executable is missing or symlinked: $BRANDED_BINARY" >&2
  exit 2
}
VERSION_FIRST_LINE="$("$BRANDED_BINARY" --version | sed -n '1p')"
case "$VERSION_FIRST_LINE" in
  "Narwhal 3D 5.2.2"*) ;;
  *) echo "native version banner mismatch: $VERSION_FIRST_LINE" >&2; exit 2 ;;
esac
LC_ALL=C grep -aFq " - Narwhal 3D" "$BRANDED_BINARY" || {
  echo "installed executable lacks the Narwhal window-title marker" >&2
  exit 2
}
LC_ALL=C grep -aFq "Narwhal 3D quit" "$BRANDED_BINARY" || {
  echo "installed executable lacks the Narwhal quit marker" >&2
  exit 2
}

for native_marker in NARWHAL3D_DEFER_FIRST_PRESENT NARWHAL3D_STATUSBAR_HEIGHT RNA_WINDOW_DRAW_HANDLE; do
  LC_ALL=C grep -aFq "$native_marker" "$BRANDED_BINARY" || {
    echo "installed executable lacks the native patch marker: $native_marker" >&2
    exit 2
  }
done

python3 - \
  "$INSTALL_DIR/.narwhal3d-native-runtime.json" \
  "$BRANDED_BINARY" \
  "$REQUIRED_COMMIT" \
  "$SOURCE_DATE_EPOCH" \
  "$VERSION_FIRST_LINE" \
  "$BRANDING_PATCH" \
  "$APP_DRAWN_PATCH" \
  "$BOUNDARY_PATCH" \
  "$DEFERRED_PRESENT_PATCH" \
  "$STATUSBAR_HEIGHT_PATCH" \
  "$ICON_SOURCE" \
  "$WINDOW_OVERLAY_PATCH" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

marker_path = Path(sys.argv[1])
binary = Path(sys.argv[2])
commit = sys.argv[3]
epoch_text = sys.argv[4]
version_first_line = sys.argv[5]
logical_inputs = (
    ("blender_patches/apply_narwhal3d_branding.py", Path(sys.argv[6])),
    ("blender_patches/apply_app_drawn.py", Path(sys.argv[7])),
    ("blender_patches/apply_cad_boundary_wires.py", Path(sys.argv[8])),
    ("blender_patches/apply_deferred_first_present.py", Path(sys.argv[9])),
    ("blender_patches/apply_statusbar_height.py", Path(sys.argv[10])),
    ("blender_patches/narwhal3d_app_icon.svg", Path(sys.argv[11])),
    ("blender_patches/apply_window_overlay.py", Path(sys.argv[12])),
)
records = []
aggregate = hashlib.sha256()
for name, path in sorted(logical_inputs):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    records.append({"path": name, "sha256": digest})
    aggregate.update(name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
build_source_date_epoch = int(epoch_text) if epoch_text else None
if build_source_date_epoch is None:
    timestamp = datetime.now(timezone.utc)
else:
    timestamp = datetime.fromtimestamp(build_source_date_epoch, timezone.utc)
marker = {
    "schema": 1,
    "product": "Narwhal 3D",
    "platform": "linux-x86_64",
    "runtime_flavor": "native-narwhal-runtime",
    "blender_version": "5.2.2",
    "blender_source_commit": commit,
    "build_source_date_epoch": build_source_date_epoch,
    "build_timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
    "version_first_line": version_first_line,
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "patch_inputs": records,
    "patch_aggregate_sha256": aggregate.hexdigest(),
}
temporary = marker_path.with_suffix(marker_path.suffix + ".tmp")
temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(marker_path)
PY

echo "Native Narwhal 3D runtime provenance recorded: $INSTALL_DIR"
